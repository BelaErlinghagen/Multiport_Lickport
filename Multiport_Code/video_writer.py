"""video_writer.py — Chunked H.264 recording of the camera stream.

Replaces the old one-.npy-per-frame scheme in data_saving.py: a 2000x2000 Mono8
frame is 4 MB, so a session wrote tens of MB per second and the saving process had
to steal frames from DeepLabCut's queue to get them. Encoding happens instead inside
the *camera* process, where the frames already exist — no extra IPC, and DLC keeps
every frame.

Two pieces:

  ChunkedVideoWriter  one long-lived ffmpeg fed raw frames on stdin, writing
                      {prefix}_Video_%04d.mp4 chunks through ffmpeg's segment muxer.
                      Each chunk is finalized as the next one begins, so a crash
                      costs at most one chunk; the fragmented-mp4 flags make even a
                      chunk truncated by SIGKILL playable.

  concat_chunks()     lossless (-c copy) join of the chunks into {prefix}_Video.mp4,
                      run once the recording stops.

Both take a *path prefix* rather than a folder — recordings all share one flat Data
folder (shared_states.recording_basename), so the prefix is the only thing keeping
one session's files apart from another's.

The MP4's own timeline is *nominal* — frames are pushed at whatever rate the camera
delivers them but stamped at a constant `fps`. The real per-frame times live in
{prefix}_frames.csv, which is what maps a video frame back onto a row of the session
CSV (it replaces the information the old "{timestamp}.npy" filenames held).
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time

_ffmpeg_bin = None          # resolved once by _resolve_ffmpeg()


def _resolve_ffmpeg():
    """Locate the ffmpeg binary, preferring the one in this interpreter's env.

    PATH alone is not enough. ffmpeg is a pixi dependency living in
    .pixi/envs/default/bin, which is only on PATH when the app is started through
    `pixi run`. Launched with that env's python directly — from a desktop shortcut
    or an IDE, which is normal here — PATH has no ffmpeg and every recording died
    with "No such file or directory: 'ffmpeg'" at the first frame.

    sys.executable is inside the very same bin directory whichever way the app was
    started, so checking there first makes the lookup independent of PATH.
    """
    global _ffmpeg_bin
    if _ffmpeg_bin is not None:
        return _ffmpeg_bin

    candidates = []
    try:
        import shared_states
        override = getattr(shared_states, "ffmpeg_path", "")
        if override:
            candidates.append(str(override))
    except Exception:
        pass
    # The environment's own bin directory (where pixi puts ffmpeg).
    candidates.append(os.path.join(os.path.dirname(sys.executable), "ffmpeg"))

    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _ffmpeg_bin = path
            return _ffmpeg_bin

    # Fall back to PATH, then to the bare name so the failure names the binary.
    _ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    return _ffmpeg_bin

# Sentinel pushed through the frame queue by close() to end the writer thread.
_STOP = object()


class ChunkedVideoWriter:
    """Encode raw grayscale frames to a sequence of H.264 MP4 chunks.

    write() never blocks: if ffmpeg cannot keep up, the oldest queued frame is
    dropped and counted. That is the whole point of the bounded queue — this runs in
    the camera process, so a stalled encoder must never stall the grab loop and
    starve DeepLabCut and the live preview of frames.
    """

    # Frames waiting to be handed to ffmpeg. Small on purpose: 8 x 4 MB is already
    # 32 MB of backlog, and anything beyond that is better dropped than buffered.
    _QUEUE_DEPTH = 8

    def __init__(self, prefix, width, height, fps=20, chunk_seconds=60, crf=23,
                 max_size=0):
        # *prefix* is a full path stem, e.g.
        # /…/Data/BECU371_20260807_113631_Test2 — every file this writer produces
        # starts with it. Recordings share one flat directory, so the prefix is the
        # only thing separating this session's chunks from another's.
        self.prefix        = prefix
        self.width         = int(width)
        self.height        = int(height)
        self.fps           = int(fps)
        self.chunk_seconds = int(chunk_seconds)
        self.crf           = int(crf)
        self.out_size      = self._scaled_size(self.width, self.height, max_size)

        os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)

        self._queue    = queue.Queue(maxsize=self._QUEUE_DEPTH)
        self._dropped  = 0
        self._written  = 0
        self._closed   = False

        try:
            self._proc = subprocess.Popen(
                self._ffmpeg_cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"ffmpeg not found (tried {_resolve_ffmpeg()!r}). It ships with the "
                "pixi environment — start the app with `pixi run python main.py`, or "
                "set shared_states.ffmpeg_path to the binary's absolute path."
            ) from None

        # Per-frame timestamps. Opened line-buffered off a background flush so a kill
        # mid-session still leaves an index for every chunk already on disk.
        self._index_path = f"{prefix}_frames.csv"
        self._index = open(self._index_path, "w", buffering=1)
        self._index.write("frame_index,timestamp\n")
        self._last_index_flush = time.monotonic()

        self._thread = threading.Thread(target=self._writer_loop, daemon=True,
                                        name="video-writer")
        self._thread.start()
        scaled = (f" → {self.out_size[0]}x{self.out_size[1]}"
                  if self.out_size else "")
        print(f"[Video] recording {self.width}x{self.height}{scaled} @ {self.fps} fps "
              f"→ {os.path.basename(prefix)}_Video_*.mp4 "
              f"({self.chunk_seconds}s chunks, crf {self.crf})")

    # ── ffmpeg ────────────────────────────────────────────────────────────────

    @staticmethod
    def _scaled_size(width, height, max_size):
        """Output size with the longest side capped at *max_size* (0 = unchanged).

        Never upscales. Dimensions are rounded to even numbers because yuv420p
        subsamples chroma by two and libx264 rejects odd ones.
        """
        max_size = int(max_size or 0)
        longest = max(width, height)
        if max_size <= 0 or longest <= max_size:
            return None                      # keep the native size
        scale = max_size / float(longest)
        return (max(2, int(round(width * scale)) & ~1),
                max(2, int(round(height * scale)) & ~1))

    def _ffmpeg_cmd(self):
        """The encoder command line.

        -force_key_frames is what makes the chunking actually work: the segment muxer
        can only cut on a keyframe, so without a forced one every chunk_seconds the
        chunks drift to whatever the GOP length happens to be.

        The +frag_keyframe/+empty_moov flags write each chunk as a fragmented MP4, so
        a chunk interrupted by a hard kill is still playable instead of being a file
        with no moov atom.

        Downscaling is ffmpeg's job, not numpy's: full-resolution frames go down the
        pipe either way, so scaling here costs the camera process nothing and gets a
        properly filtered (grain-averaging) resample instead of the aliased result
        that slicing the array would give.
        """
        scale = (["-vf", f"scale={self.out_size[0]}:{self.out_size[1]}"]
                 if self.out_size else [])
        return [
            _resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "gray",
            "-s", f"{self.width}x{self.height}", "-r", str(self.fps),
            "-i", "-",
            "-an",
            *scale,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(self.crf),
            "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{self.chunk_seconds})",
            "-f", "segment", "-segment_time", str(self.chunk_seconds),
            "-reset_timestamps", "1",
            "-segment_format", "mp4",
            "-segment_format_options",
            "movflags=+frag_keyframe+empty_moov+default_base_moof",
            f"{self.prefix}_Video_%04d.mp4",
        ]

    # ── Public API ────────────────────────────────────────────────────────────

    def write(self, frame, timestamp):
        """Queue one frame for encoding. Never blocks; drops the oldest on overflow."""
        if self._closed:
            return
        # .tobytes() here rather than in the writer thread: the caller may reuse or
        # release the underlying buffer as soon as this returns.
        item = (frame.tobytes(), float(timestamp))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Encoder is behind. Make room by discarding the oldest frame rather than
            # blocking the camera loop; report the total once at close().
            try:
                self._queue.get_nowait()
                self._dropped += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._dropped += 1

    def close(self, timeout=30):
        """Flush the queue, close ffmpeg's stdin and wait for it to finalize."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(_STOP)
        self._thread.join(timeout=timeout)

        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("[Video] ffmpeg did not exit in time; terminating.")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        try:
            self._index.close()
        except Exception:
            pass

        msg = f"[Video] stopped — {self._written} frame(s) encoded"
        if self._dropped:
            msg += f", {self._dropped} dropped (encoder could not keep up)"
        print(msg + ".")

    # ── Internals ─────────────────────────────────────────────────────────────

    def _writer_loop(self):
        """Feed queued frames to ffmpeg and index their timestamps."""
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            payload, timestamp = item
            try:
                self._proc.stdin.write(payload)
            except (BrokenPipeError, ValueError, OSError) as exc:
                # ffmpeg died (bad args, disk full). Stop feeding it rather than
                # raising inside a daemon thread nobody is watching.
                print(f"[Video] encoder pipe closed ({exc}); stopping video writes.")
                self._closed = True
                break
            self._index.write(f"{self._written},{timestamp:.6f}\n")
            self._written += 1
            # The index file is line-buffered; nudge it to disk about once a second
            # so a hard kill leaves timestamps for the chunks that survived.
            now = time.monotonic()
            if now - self._last_index_flush >= 1.0:
                try:
                    os.fsync(self._index.fileno())
                except Exception:
                    pass
                self._last_index_flush = now


def concat_chunks(prefix, remove_chunks=True):
    """Join {prefix}_Video_NNNN.mp4 into {prefix}_Video.mp4 without re-encoding.

    Stream copy, so this is I/O bound and takes seconds even for a long session. The
    chunks are deleted only once the concat has actually succeeded — a failed join
    must never be able to destroy the recording.

    The glob is deliberately anchored to *this* recording's prefix. Every recording
    now shares one flat Data folder, so a looser pattern would let one session's
    concat swallow (and then delete) another session's chunks — including those of a
    recording still in progress.

    Returns True on success.
    """
    folder = os.path.dirname(prefix) or "."
    stem   = os.path.basename(prefix)
    # "_Video_" then digits then ".mp4" — never matches the joined _Video.mp4 itself,
    # so re-running this is harmless.
    chunks = sorted(
        f for f in os.listdir(folder)
        if f.startswith(f"{stem}_Video_") and f.endswith(".mp4")
        and f[len(stem) + 7:-4].isdigit()
    )
    if not chunks:
        print("[Video] nothing to concatenate.")
        return False

    out_path  = f"{prefix}_Video.mp4"
    list_path = f"{prefix}_concat.txt"
    try:
        with open(list_path, "w") as fh:
            for name in chunks:
                # ffmpeg's concat demuxer needs single quotes escaped this way.
                fh.write("file '%s'\n" % name.replace("'", r"'\''"))
        subprocess.run(
            [_resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", out_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = f" — {exc.stderr.decode('utf-8', 'ignore').strip()}"
        print(f"[Video] concat failed ({exc}){detail}; keeping the {len(chunks)} chunk(s).")
        return False
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass

    if remove_chunks:
        for name in chunks:
            try:
                os.remove(os.path.join(folder, name))
            except OSError as exc:
                print(f"[Video] could not remove chunk {name}: {exc}")
    print(f"[Video] {len(chunks)} chunk(s) joined → {os.path.basename(out_path)}")
    return True


if __name__ == "__main__":
    # Standalone smoke test: encode synthetic frames into short chunks, then join
    # them. Verifies the ffmpeg command line without needing a camera.
    import numpy as np

    out_dir = "/tmp/video_writer_demo"
    prefix = os.path.join(out_dir, "DemoMouse_20250101_000000_DemoSession")
    W = H = 2000
    FPS, CHUNK, N = 20, 2, 200

    writer = ChunkedVideoWriter(prefix, W, H, fps=FPS, chunk_seconds=CHUNK)
    base = np.random.randint(0, 255, (H, W), dtype=np.uint8)
    for i in range(N):
        frame = np.roll(base, i * 7, axis=1)      # something that actually moves
        writer.write(frame, time.time())
        time.sleep(1.0 / FPS)
    writer.close()

    print("chunks:", sorted(f for f in os.listdir(out_dir) if f.endswith(".mp4")))
    concat_chunks(prefix)
    print("after concat:", sorted(os.listdir(out_dir)))
