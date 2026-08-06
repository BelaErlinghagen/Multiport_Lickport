"""console_log.py — one timestamped console log per recording, across every process.

The rig runs eight processes (camera, DLC, serial, beamer, screens, state machine,
saving, and the GUI in the parent) and they all print into the same terminal. This
module tees that combined stream into a log file inside the current recording's
folder, so a session's console narrative becomes part of its data.

How the capture works
---------------------
`install_capture()` replaces file descriptors 1 and 2 with the write end of a pipe
and starts a pump thread that reads it. Children inherit fds 1/2 across
multiprocessing's "spawn" (only fds >= 3 are closed), so this single install in the
parent captures every process. Being at the fd level it also catches C-level output
— Pylon, TensorFlow, Qt, ALSA — which a `sys.stdout` swap would miss entirely and
which spawn would not inherit anyway.

One pipe carries both streams. Two would preserve the terminal's stdout/stderr split
for free, but would also scramble the interleaving of a process's own stdout and
stderr lines, and faithful global ordering is the point of the file. The stream kind
travels in a marker instead, so the tee still writes to the right real fd.

`tag_process(name)` wraps this process's `sys.stdout`/`sys.stderr` so each line is
emitted as ONE write carrying a `\\x01<tag>|<o|e>\\x02` marker. That matters twice
over: it is what gives the log per-process tags, and it is what stops concurrent
processes shredding each other mid-line — POSIX only guarantees pipe-write atomicity
up to PIPE_BUF (4096 bytes), and a plain `print()` issues two separate writes.
The pump strips the marker before the terminal sees it, so the console looks exactly
as it did before.

The log window
--------------
Lines printed from launch up to the first `start_log()` are kept as a frozen
preamble and replayed into every session log, so each file opens with the startup
messages (camera init, calibration, driver warnings). After that a file receives only
its own recording's lines — a second recording's log never contains the first one's
trials. Between recordings, output goes to the console only.

Stdlib only, and nothing runs at import: every process imports this module, including
the DLC one, so it must stay free of numpy and Qt.
"""

import atexit
import os
import sys
import threading
from datetime import datetime

# Framing for a tagged line. Neither byte occurs in ordinary console output.
_MARK_START = "\x01"
_MARK_END = "\x02"

# One write must stay under PIPE_BUF (4096) to be atomic, so longer lines are split.
_MAX_CHUNK = 3900

# The preamble is replayed into every session log, so it must not grow without bound.
_PREAMBLE_MAX_LINES = 20000
_PREAMBLE_MAX_BYTES = 4 * 1024 * 1024

_FLUSH_SECONDS = 1.0

_lock = threading.Lock()
_real_out_fd = None          # dup of the original fd 1
_real_err_fd = None          # dup of the original fd 2
_write_fd = None
_read_fd = None
_pump_thread = None
_flush_thread = None
_stop_flush = threading.Event()

_fh = None                   # open session log file, or None
_preamble = []               # frozen once the first recording starts
_preamble_bytes = 0
_preamble_frozen = False
_installed = False

# Barrier used to make the asynchronous pump catch up before a log is opened or
# closed — see _sync().
_sync_token = None
_sync_event = threading.Event()


# ── Per-process tagging ───────────────────────────────────────────────────────

class _TaggedStream:
    """Line-assembling wrapper that stamps each line with its process and stream.

    Buffers until a newline and then issues exactly one `write` per line, which is
    what keeps concurrent processes from interleaving mid-line in the shared pipe.
    """

    def __init__(self, stream, tag, kind):
        self._stream = stream
        self._prefix = f"{_MARK_START}{tag}|{kind}{_MARK_END}"
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        if not text:
            return 0
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._emit(line)
        return len(text)

    def _emit(self, line):
        # Split over-long lines so every individual write stays atomic.
        chunks = ([line[i:i + _MAX_CHUNK] for i in range(0, len(line), _MAX_CHUNK)]
                  or [""])
        for chunk in chunks:
            try:
                self._stream.write(f"{self._prefix}{chunk}\n")
                self._stream.flush()
            except Exception:
                pass

    def flush(self):
        with self._lock:
            if self._buf:
                self._emit(self._buf)
                self._buf = ""
        try:
            self._stream.flush()
        except Exception:
            pass

    # Enough of the file protocol for anything that introspects the stream.
    def isatty(self):
        return False

    def fileno(self):
        return self._stream.fileno()

    def writable(self):
        return True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def tag_process(name: str) -> None:
    """Prefix this process's Python-level output with a source marker.

    Call as the first statement of a process target, before the heavyweight imports
    below it, so their output is tagged too. C-level output cannot be tagged this
    way — the pump labels it "raw".
    """
    if isinstance(sys.stdout, _TaggedStream):
        return
    sys.stdout = _TaggedStream(sys.stdout, name, "o")
    sys.stderr = _TaggedStream(sys.stderr, name, "e")


# ── The pump ──────────────────────────────────────────────────────────────────

def _format(tag: str, payload: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"{stamp}  [{tag}]  {payload}\n"


def _emit(raw: bytes) -> None:
    """Tee one captured line to the real console and into the log."""
    global _preamble_bytes, _fh

    text = raw.decode("utf-8", "replace")
    kind = "o"
    if text.startswith(_MARK_START) and _MARK_END in text:
        head, payload = text[1:].split(_MARK_END, 1)
        tag, _, kind = head.partition("|")
    else:
        # Untagged: C-level output from a library writing straight to the fd.
        tag, payload = "raw", text

    if kind == "s":
        # A sync barrier, not real output: never teed, never logged.
        if payload == _sync_token:
            _sync_event.set()
        return

    # The terminal keeps exactly the output it had before this module existed.
    fd = _real_err_fd if kind == "e" else _real_out_fd
    if fd is not None:
        try:
            os.write(fd, (payload + "\n").encode("utf-8", "replace"))
        except OSError:
            pass

    line = _format(tag, payload)
    with _lock:
        if _fh is not None:
            try:
                _fh.write(line)
            except Exception:
                # A broken log file is survivable; a stalled pump is not.
                try:
                    _fh.close()
                except Exception:
                    pass
                _fh = None
        elif not _preamble_frozen:
            if (len(_preamble) < _PREAMBLE_MAX_LINES
                    and _preamble_bytes < _PREAMBLE_MAX_BYTES):
                _preamble.append(line)
                _preamble_bytes += len(line)
            elif _preamble and not _preamble[-1].endswith("(preamble truncated)\n"):
                _preamble.append(_format("console_log", "… (preamble truncated)"))


def _pump(read_fd: int) -> None:
    """Drain the capture pipe forever.

    This loop must never exit while a process could still write. The pipe holds
    64 kB; if nobody drains it, every process in the rig blocks on its next print
    and the whole thing freezes. So every per-line failure is swallowed and the read
    resumes, and the pump never calls print() — that would recurse into the pipe it
    is draining.
    """
    buf = b""
    while True:
        try:
            chunk = os.read(read_fd, 65536)
        except OSError:
            break
        if not chunk:                      # EOF: every write end is closed
            break
        buf += chunk
        parts = buf.split(b"\n")
        buf = parts.pop()
        for raw in parts:
            try:
                _emit(raw)
            except Exception:
                pass
    if buf:
        try:
            _emit(buf)
        except Exception:
            pass


def _flush_loop() -> None:
    """Flush the open log once a second, bounding loss to one second of output."""
    while not _stop_flush.wait(_FLUSH_SECONDS):
        with _lock:
            if _fh is not None:
                try:
                    _fh.flush()
                except Exception:
                    pass


# ── Public API ────────────────────────────────────────────────────────────────

def install_capture() -> None:
    """Redirect fds 1/2 onto a capture pipe and start the pump. Idempotent.

    MUST run before the first Process.start(): the children inherit fds 1/2, and
    that inheritance is the whole capture mechanism.
    """
    global _installed, _real_out_fd, _real_err_fd, _write_fd, _read_fd
    global _pump_thread, _flush_thread
    if _installed:
        return

    _real_out_fd = os.dup(1)
    _real_err_fd = os.dup(2)
    _read_fd, _write_fd = os.pipe()

    # Children are spawned with a fresh interpreter, and a pipe on fd 1 makes its
    # stdout block-buffered at 8 kB — a quiet process's output would surface minutes
    # late. The variable is inherited through the environment.
    os.environ["PYTHONUNBUFFERED"] = "1"

    os.dup2(_write_fd, 1)
    os.dup2(_write_fd, 2)
    os.close(_write_fd)
    _write_fd = None

    _pump_thread = threading.Thread(target=_pump, args=(_read_fd,), daemon=True,
                                    name="console-log-pump")
    _pump_thread.start()
    _stop_flush.clear()
    _flush_thread = threading.Thread(target=_flush_loop, daemon=True,
                                     name="console-log-flush")
    _flush_thread.start()

    _installed = True
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
    tag_process("GUI")


def _sync(timeout: float = 2.0) -> None:
    """Block until the pump has processed everything written so far.

    The pump runs asynchronously, so without this the lines printed just before a
    recording stops would still be in the pipe when the file is closed and would be
    lost — and lines printed just before one starts could land in the wrong file.
    A pipe is FIFO, so once the sentinel comes out the ordinary output ahead of it
    has already been handled.
    """
    global _sync_token
    if not _installed:
        return
    token = f"sync-{os.getpid()}-{datetime.now().timestamp()}"
    _sync_event.clear()
    _sync_token = token
    try:
        os.write(1, f"{_MARK_START}console_log|s{_MARK_END}{token}\n".encode())
    except OSError:
        _sync_token = None
        return
    _sync_event.wait(timeout)
    _sync_token = None


def start_log(recording_folder: str, name: str = "console.log"):
    """Open <recording_folder>/<name> and replay the launch preamble into it.

    Returns the path, or None if the file could not be opened (never fatal — a
    session must not fail because its log could not be written).
    """
    global _fh, _preamble_frozen
    stop_log()
    _sync()          # anything printed before now belongs to the previous window
    path = os.path.join(recording_folder, name)
    try:
        fh = open(path, "w", encoding="utf-8")
    except Exception as exc:
        os.write(_real_err_fd if _real_err_fd is not None else 2,
                 f"[console_log] could not open {path}: {exc}\n".encode())
        return None

    with _lock:
        _preamble_frozen = True
        try:
            fh.writelines(_preamble)
            fh.write(_format("console_log", f"--- recording started → {path} ---"))
            fh.flush()
        except Exception:
            pass
        _fh = fh
    return path


def stop_log() -> None:
    """Flush and close the current session log. Safe when none is open."""
    global _fh
    if _fh is not None:
        # Let the pump catch up first, or the last lines of the recording — the
        # session-complete summary among them — would never reach the file.
        _sync()
    with _lock:
        if _fh is None:
            return
        try:
            _fh.write(_format("console_log", "--- recording stopped ---"))
            _fh.flush()
            _fh.close()
        except Exception:
            pass
        _fh = None


def shutdown(timeout: float = 2.0) -> None:
    """Restore the real fds, drain the pipe tail and stop the pump.

    Call last, after every child has been joined — with a child still alive the
    pump would wait for an EOF that cannot come.
    """
    global _installed, _pump_thread, _flush_thread, _read_fd
    global _real_out_fd, _real_err_fd
    if not _installed:
        return
    _installed = False

    stop_log()
    _stop_flush.set()

    # Restoring the real fds also drops the parent's last references to the pipe's
    # write end, which is what lets the pump see EOF.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if isinstance(sys.stdout, _TaggedStream):
        sys.stdout = sys.stdout._stream
    if isinstance(sys.stderr, _TaggedStream):
        sys.stderr = sys.stderr._stream
    try:
        os.dup2(_real_out_fd, 1)
        os.dup2(_real_err_fd, 2)
    except OSError:
        pass

    if _pump_thread is not None:
        _pump_thread.join(timeout=timeout)
        _pump_thread = None
    if _flush_thread is not None:
        _flush_thread.join(timeout=0.5)
        _flush_thread = None
    for fd_name in ("_read_fd", "_real_out_fd", "_real_err_fd"):
        fd = globals()[fd_name]
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            globals()[fd_name] = None


atexit.register(shutdown)
