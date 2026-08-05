"""gui/experiment_page.py — Experiment control tab.

Section order (top → bottom):
  0. RSpace bar       — connection status (left) + settings button (right)
  1. Experiment Info  — Mouse / Session editable comboboxes, merged from the local
                        Data folder and RSpace (origin shown by item colour)
  2. Selected Protocol— file picker + summary
  3. Recording        — stream checkboxes, Start/Stop, wired to
                        data_saving.saving_process + the state machine
  4. Tags             — automatic id_<mouse> + m_behavior, plus optional method tags
  5. Comments         — timestamped notes typed during the session (fixed at the
                        bottom so they stay reachable while a session runs)

When a recording stops the session is written up — protocol details, the reward
ports that were actually chosen, and the comments — and that write-up is always
saved as JSON next to the recordings, in Data/<mouse>/<session>/<entry>_rspace.json.
It is additionally uploaded as a new entry in the selected RSpace notebook unless
uploading is switched off in RSpace settings (rspace.load_upload_enabled) or the
upload fails; either way the record stays "pending" and can be sent later from
RSpace settings → Pending entries…. Naming/tag conventions come from rspace.py:
the entry is named "YYYYMMDD_HHMM_<session>" (matching rspace.ENTRY_NAME_RE) and
the mouse is identified by its "id_<mouse>" tag rather than by the name.
"""

import json
import os
from datetime import datetime
from html import escape
from multiprocessing import Process, Value

from PyQt5 import QtWidgets, QtGui, QtCore

import rspace
from data_saving import saving_process


# ── Pure helpers (no Qt, no network — unit-testable) ──────────────────────────

# Mouse records in the RSpace "mice" folder are named with a leading "#"
# (e.g. "#BECT471"). Only that document carries the hash — the mouse ID itself,
# and hence the id_ tag, the entry name and the local Data folder, omit it.
MOUSE_DOC_PREFIX = "#"

# Every session entry is tagged with its mouse (id_<mouse>) and, since this rig only
# runs behaviour, m_behavior. The user may additionally mark a session as recorded
# alongside imaging or ephys — those are the only extra tags on offer.
AUTO_METHOD_TAG = f"{rspace.METHOD_PREFIX}behavior"
OPTIONAL_METHOD_TAGS = (f"{rspace.METHOD_PREFIX}invivo_imaging",
                        f"{rspace.METHOD_PREFIX}invivo_ephys")


def mouse_id_from_doc_name(name):
    """Return the mouse ID for a "mice"-folder document ("#BECT471" → "BECT471")."""
    name = (name or "").strip()
    return name[len(MOUSE_DOC_PREFIX):] if name.startswith(MOUSE_DOC_PREFIX) else name


def build_entry_name(session, when=None):
    """Compose the RSpace entry name "YYYYMMDD_HHMM_<session>".

    The mouse is not part of the name — entries identify their subject through the
    id_<mouse> tag instead.
    """
    when = when or datetime.now()
    return f"{when.strftime('%Y%m%d')}_{when.strftime('%H%M')}_{session}"


def session_from_entry_name(name):
    """Return the session part of "YYYYMMDD_HHMM_<session>", else None."""
    date, time, extra = rspace.parse_entry_name(name or "")
    return extra if (date and time and extra) else None


def _iti_detail(iti):
    """One-line plain-text description of an intertrial config."""
    kind = iti.get("type", "time")
    if kind == "time":
        return f"{iti.get('duration_s', '?')} s"
    region = iti.get("region" if kind == "fixed_region" else "random_region", {})
    bits = [f"sphere {region.get('diameter_cm', '?')} cm"]
    if kind == "fixed_region":
        bits.append(f"at ({region.get('x_cm', '?')}, {region.get('y_cm', '?')}) cm")
    else:
        bits.append(f"within {region.get('margin_radius_cm', '?')} cm of "
                    f"({region.get('margin_x_cm', '?')}, {region.get('margin_y_cm', '?')}) cm")
    if region.get("duration_type", "fixed") == "fixed":
        bits.append(f"dwell {region.get('duration_s', '?')} s")
    else:
        bits.append(f"dwell random 0–{region.get('duration_max_s', '?')} s")
    return ", ".join(str(b) for b in bits)


def build_entry_html(protocol, meta, reward_ports, comments):
    """Build the RSpace entry body (HTML) for one finished session.

    Covers the session identification, the protocol's features, the reward ports
    that were *actually* chosen for this run (the state machine may have picked
    them randomly), and the timestamped comments.
    """
    protocol = protocol or {}
    meta = meta or {}
    e = escape
    out = []

    out.append("<h2>Session</h2><ul>")
    out.append(f"<li><b>Mouse:</b> {e(str(meta.get('mouse_id', '')))}</li>")
    out.append(f"<li><b>Session:</b> {e(str(meta.get('session_id', '')))}</li>")
    for key, label in (("date", "Date"), ("start_time", "Start"), ("end_time", "End")):
        if meta.get(key):
            out.append(f"<li><b>{label}:</b> {e(str(meta[key]))}</li>")
    if meta.get("protocol_path"):
        out.append(f"<li><b>Protocol file:</b> "
                   f"{e(os.path.basename(str(meta['protocol_path'])))}</li>")
    out.append("</ul>")

    sess   = protocol.get("session", {})
    trial  = protocol.get("trial", {})
    rew    = protocol.get("rewards", {})
    dist   = rew.get("distribution", {})
    iti    = protocol.get("intertrial", {})
    beamer = protocol.get("beamer") or {}
    screens = protocol.get("screens") or {}

    trial_detail = (f"{trial.get('duration_s', '?')} s per trial"
                    if trial.get("end_type") == "time"
                    else "ends when all rewards collected")

    out.append("<h2>Protocol</h2><ul>")
    out.append(f"<li><b>Session:</b> {e(str(sess.get('type', '?')))}, "
               f"length {e(str(sess.get('length', '?')))}</li>")
    out.append(f"<li><b>Trial:</b> {e(str(trial.get('end_type', '?')))} — {e(trial_detail)}</li>")
    out.append(f"<li><b>Rewards:</b> {e(str(rew.get('count', '?')))} "
               f"({e(str(dist.get('type', '?')))} distribution)</li>")
    out.append(f"<li><b>LED mode:</b> {e(str(rew.get('led_mode', '?')))}</li>")
    out.append(f"<li><b>Beamer:</b> {'shadow' if beamer.get('shadow') else 'light'}</li>")
    if screens.get("enabled"):
        s_trial = ", ".join(str(p) for p in (screens.get("trial") or []))
        s_iti   = ", ".join(str(p) for p in (screens.get("iti") or []))
        rnd     = " (randomised per trial)" if screens.get("randomize") else ""
        out.append(f"<li><b>Screens:</b> trial [{e(s_trial)}]{e(rnd)} — "
                   f"ITI [{e(s_iti)}]</li>")
    else:
        out.append("<li><b>Screens:</b> off</li>")
    out.append(f"<li><b>Intertrial:</b> {e(str(iti.get('type', '?')))} — "
               f"{e(_iti_detail(iti))}</li>")
    out.append("</ul>")

    configs = rew.get("configs") or []
    if configs:
        out.append("<h3>Reward configuration</h3>")
        out.append("<table border='1' cellpadding='4' cellspacing='0'>"
                   "<tr><th>Reward</th><th>Duration (ms)</th><th>Probability</th></tr>")
        for cfg in configs:
            out.append(f"<tr><td>{e(str(cfg.get('id', '')))}</td>"
                       f"<td>{e(str(cfg.get('duration_ms', '')))}</td>"
                       f"<td>{e(str(cfg.get('probability', '')))}</td></tr>")
        out.append("</table>")

    out.append("<h2>Chosen reward ports</h2>")
    if reward_ports:
        out.append(f"<p>{e(', '.join(str(p) for p in reward_ports))}</p>")
        if dist.get("type") == "random":
            out.append(f"<p><i>Assigned randomly (min spacing "
                       f"{e(str(dist.get('min_spacing', '?')))}).</i></p>")
    else:
        out.append("<p><i>No reward ports recorded.</i></p>")

    out.append("<h2>Comments</h2>")
    if comments:
        out.append("<ul>")
        for c in comments:
            out.append(f"<li><b>{e(str(c.get('time', '')))}</b> — "
                       f"{e(str(c.get('text', '')))}</li>")
        out.append("</ul>")
    else:
        out.append("<p><i>No comments.</i></p>")

    return "\n".join(out)


# ── Session write-up records (local JSON) ─────────────────────────────────────
# Every finished session is written up as a JSON record next to its recordings, in
# Data/<mouse>/<session>/<entry_name>_rspace.json — whether or not it is uploaded.
# The record holds everything the RSpace entry needs (name, tags, HTML body) plus
# the structured session data, so an entry can be created later (via the pending
# uploads dialog, or by hand) without re-deriving anything.

RECORD_SUFFIX = "_rspace.json"


def build_entry_record(name, tags, content, meta, protocol, reward_ports, comments,
                       notebook_id=None):
    """Assemble the local write-up record for one finished session."""
    return {
        "name":          name,
        "tags":          list(tags or []),
        "content":       content,
        "meta":          dict(meta or {}),
        "protocol":      protocol or {},
        "reward_ports":  list(reward_ports or []),
        "comments":      list(comments or []),
        "notebook_id":   notebook_id,
        "saved_at":      datetime.now().isoformat(timespec="seconds"),
        "uploaded_at":   None,
        "rspace_doc_id": None,
    }


def entry_record_path(data_path, mouse, session, name):
    """Path of the write-up file for one session entry."""
    return os.path.join(data_path, mouse, session, f"{name}{RECORD_SUFFIX}")


def write_entry_record(path, record):
    """Write a record to `path`, creating the session folder if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(record, fh, indent=2)
    return path


def read_entry_record(path):
    """Return the record stored at `path` (raises on missing/corrupt file)."""
    with open(path, "r") as fh:
        return json.load(fh)


def find_pending_records(data_path):
    """Return [{"path", "record"}] for every write-up that has not been uploaded.

    Scans Data/<mouse>/<session>/ for *_rspace.json; newest (by saved_at) first.
    Unreadable files are skipped rather than breaking the listing.
    """
    pending = []
    for mouse in sorted(os.listdir(data_path)) if os.path.isdir(data_path) else []:
        session_root = os.path.join(data_path, mouse)
        if not os.path.isdir(session_root):
            continue
        for session in sorted(os.listdir(session_root)):
            folder = os.path.join(session_root, session)
            if not os.path.isdir(folder):
                continue
            for fname in sorted(os.listdir(folder)):
                if not fname.endswith(RECORD_SUFFIX):
                    continue
                path = os.path.join(folder, fname)
                try:
                    record = read_entry_record(path)
                except Exception:
                    continue
                if not record.get("uploaded_at"):
                    pending.append({"path": path, "record": record})
    pending.sort(key=lambda p: p["record"].get("saved_at", ""), reverse=True)
    return pending


def mark_record_uploaded(path, doc_id=None):
    """Stamp a record as uploaded so it drops out of find_pending_records()."""
    record = read_entry_record(path)
    record["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
    record["rspace_doc_id"] = doc_id
    write_entry_record(path, record)
    return record


def document_id(result):
    """Best-effort document id from an RSpace create_document response."""
    if isinstance(result, dict):
        return result.get("id") or result.get("globalId")
    return None


def data_root():
    """Return shared_states.data_path, or "" if it cannot be read."""
    try:
        from shared_states import data_path
        return data_path
    except Exception:
        return ""


# ── Background worker ─────────────────────────────────────────────────────────

class _Task(QtCore.QThread):
    """Run a callable off the GUI thread; deliver (ok, result) back on it."""

    done = QtCore.pyqtSignal(bool, object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(True, self._fn())
        except Exception as exc:      # network/API errors must never kill the GUI
            self.done.emit(False, exc)


# Every in-flight _Task, across all dialogs. Qt aborts the process if a QThread is
# destroyed while still running, and a dialog's own list dies with the dialog — so
# shutdown waits on this registry (see wait_for_all_tasks).
_LIVE_TASKS = []


def _run_task(owner, fn, on_done):
    """Run fn() in a worker thread, then call on_done(ok, result) on the GUI thread.

    The thread is parked on owner._tasks (and the module-wide _LIVE_TASKS) until it
    finishes so it is not garbage collected mid-run.
    """
    task = _Task(fn)
    owner._tasks.append(task)
    _LIVE_TASKS.append(task)

    def _finish(ok, result):
        try:
            on_done(ok, result)
        finally:
            if task in owner._tasks:
                owner._tasks.remove(task)
            if task in _LIVE_TASKS:
                _LIVE_TASKS.remove(task)

    task.done.connect(_finish)
    task.finished.connect(task.deleteLater)
    task.start()
    return task


def wait_for_all_tasks(msec=5000):
    """Let every in-flight request finish before the widgets are torn down."""
    for task in list(_LIVE_TASKS):
        task.wait(msec)


# ── RSpace settings dialog ────────────────────────────────────────────────────

class RSpaceSettingsDialog(QtWidgets.QDialog):
    """Set the API key / server URL and pick the project folder + notebook.

    The project folder is the one holding the "mice" folder; the notebook is where
    session entries get created. Both are persisted via rspace.save_project().

    Uploading can be switched off here: sessions are then only written up locally
    (see build_entry_record) and can be pushed later from the pending dialog.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RSpace settings")
        self.setModal(True)
        self.setMinimumSize(520, 640)
        self._tasks = []

        api_key, url = rspace.load_credentials()
        self._project_id, self._notebook_id = rspace.load_project()

        lay = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self._url_edit = QtWidgets.QLineEdit(url)
        self._key_edit = QtWidgets.QLineEdit(api_key)
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("Server URL:", self._url_edit)
        form.addRow("API key:", self._key_edit)
        lay.addLayout(form)

        # Upload on/off. Sessions are always written up locally; this only decides
        # whether the write-up is also pushed to the notebook when a session ends.
        self._upload_chk = QtWidgets.QCheckBox("Upload finished sessions to RSpace")
        self._upload_chk.setChecked(rspace.load_upload_enabled())
        lay.addWidget(self._upload_chk)
        upload_note = QtWidgets.QLabel(
            "Every session is saved as a JSON write-up in its Data/&lt;mouse&gt;/&lt;session&gt; "
            "folder either way. With this unticked nothing is sent to RSpace — use "
            "“Pending entries…” to upload those sessions later.")
        upload_note.setWordWrap(True)
        upload_note.setStyleSheet("color:#888; font-size:9px;")
        lay.addWidget(upload_note)

        test_row = QtWidgets.QHBoxLayout()
        test_btn = QtWidgets.QPushButton("Test connection")
        test_btn.clicked.connect(self._test)
        self._test_lbl = QtWidgets.QLabel("")
        self._test_lbl.setWordWrap(True)
        self._test_lbl.setStyleSheet("font-size:10px;")
        test_row.addWidget(test_btn)
        test_row.addWidget(self._test_lbl, 1)
        lay.addLayout(test_row)

        lay.addWidget(QtWidgets.QLabel("Project folder (the one containing “mice”):"))
        self._folder_tree = QtWidgets.QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.itemSelectionChanged.connect(self._on_folder_selected)
        lay.addWidget(self._folder_tree, 2)

        lay.addWidget(QtWidgets.QLabel("Experiment notebook (entries are saved here):"))
        self._nb_tree = QtWidgets.QTreeWidget()
        self._nb_tree.setHeaderHidden(True)
        lay.addWidget(self._nb_tree, 1)

        self._load_lbl = QtWidgets.QLabel("")
        self._load_lbl.setStyleSheet("color:#888; font-size:10px;")
        lay.addWidget(self._load_lbl)

        btns = QtWidgets.QHBoxLayout()
        load_btn = QtWidgets.QPushButton("Load folders")
        load_btn.clicked.connect(self._load_tree)
        pending_btn = QtWidgets.QPushButton("Pending entries…")
        pending_btn.clicked.connect(self._open_pending)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QtWidgets.QPushButton("Save")
        save.clicked.connect(self._save)
        btns.addWidget(load_btn)
        btns.addWidget(pending_btn)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

        if api_key:
            self._load_tree()

    # -- connection test --

    def _test(self):
        self._test_lbl.setText("Testing…")
        key, url = self._key_edit.text().strip(), self._url_edit.text().strip()
        _run_task(self, lambda: rspace.test_credentials(key, url), self._on_test)

    def _on_test(self, ok, result):
        if not ok:
            self._test_lbl.setText(f"<span style='color:#e06c00'>{result}</span>")
            return
        connected, msg = result
        colour = "#1a9e1a" if connected else "#e06c00"
        self._test_lbl.setText(f"<span style='color:{colour}'>{msg}</span>")

    # -- folder tree --

    def _load_tree(self):
        key, url = self._key_edit.text().strip(), self._url_edit.text().strip()
        if not key:
            self._load_lbl.setText("Enter an API key first.")
            return
        self._load_lbl.setText("Loading folder tree…")
        client = rspace.RSpaceClient(key, url)
        _run_task(self, client.create_tree, self._on_tree)

    def _on_tree(self, ok, result):
        if not ok:
            reason = rspace.describe_error(result, self._url_edit.text().strip())
            self._load_lbl.setText(f"Could not load folders: {reason}")
            return
        self._folder_tree.clear()
        self._add_nodes(self._folder_tree.invisibleRootItem(),
                        result if isinstance(result, list) else [])
        self._folder_tree.expandToDepth(0)
        self._load_lbl.setText("Folders loaded.")
        self._select_saved()

    # The nodes below are whatever the server returned. A record that is missing a
    # name or an id, or whose children aren't a list, is skipped instead of being
    # allowed to raise: these run inside Qt slots, where an exception is fatal.

    @staticmethod
    def _node_name(node):
        """A node's display name, tolerant of records that come back without one."""
        name = node.get("name") if isinstance(node, dict) else None
        return str(name) if name else "(unnamed)"

    @staticmethod
    def _child_nodes(node):
        """A node's children as a list (empty when absent or malformed)."""
        children = node.get("children") if isinstance(node, dict) else None
        return children if isinstance(children, list) else []

    def _add_nodes(self, parent, nodes):
        """Add folder/notebook nodes (documents are not selectable locations)."""
        for node in nodes:
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            if node.get("type") not in ("folder", "notebook"):
                continue
            item = QtWidgets.QTreeWidgetItem(parent, [self._node_name(node)])
            item.setData(0, QtCore.Qt.UserRole, node)
            if node.get("notebook"):
                item.setForeground(0, QtGui.QBrush(QtGui.QColor("#7ab7ff")))
            self._add_nodes(item, self._child_nodes(node))

    @classmethod
    def _notebooks_under(cls, node):
        """Every notebook at or below `node`, sorted by name."""
        found = [node] if node.get("notebook") else []
        stack = list(cls._child_nodes(node))
        while stack:
            n = stack.pop()
            if not isinstance(n, dict):
                continue
            if n.get("notebook"):
                found.append(n)
            stack.extend(cls._child_nodes(n))
        return sorted(found, key=lambda n: cls._node_name(n).lower())

    def _on_folder_selected(self):
        items = self._folder_tree.selectedItems()
        self._nb_tree.clear()
        if not items:
            return
        node = items[0].data(0, QtCore.Qt.UserRole)
        if not isinstance(node, dict):
            return
        self._project_id = node.get("id")
        for nb in self._notebooks_under(node):
            item = QtWidgets.QTreeWidgetItem(self._nb_tree, [self._node_name(nb)])
            item.setData(0, QtCore.Qt.UserRole, nb)

    def _select_saved(self):
        """Re-select the previously saved project folder, then its notebook."""
        if self._project_id is None:
            return
        it = QtWidgets.QTreeWidgetItemIterator(self._folder_tree)
        while it.value():
            item = it.value()
            node = item.data(0, QtCore.Qt.UserRole)
            if isinstance(node, dict) and node.get("id") == self._project_id:
                self._folder_tree.setCurrentItem(item)   # repopulates the notebook tree
                break
            it += 1
        if self._notebook_id is None:
            return
        for i in range(self._nb_tree.topLevelItemCount()):
            item = self._nb_tree.topLevelItem(i)
            node = item.data(0, QtCore.Qt.UserRole)
            if isinstance(node, dict) and node.get("id") == self._notebook_id:
                self._nb_tree.setCurrentItem(item)
                break

    # -- pending uploads --

    def _open_pending(self):
        """Show the sessions written up locally but not yet uploaded.

        Uses the *saved* credentials/notebook, so anything typed above has to be
        saved before it applies here.
        """
        PendingUploadsDialog(self).exec_()

    # -- save --

    @staticmethod
    def _selected_id(tree, fallback):
        """The id of `tree`'s selected node, or `fallback` if there isn't a usable one."""
        items = tree.selectedItems()
        node = items[0].data(0, QtCore.Qt.UserRole) if items else None
        if isinstance(node, dict) and node.get("id") is not None:
            return node["id"]
        return fallback

    def _save(self):
        rspace.save_credentials(self._key_edit.text(), self._url_edit.text())
        rspace.save_upload_enabled(self._upload_chk.isChecked())
        # Keep the stored folder/notebook when nothing is selected — the tree may
        # never have loaded (dialog opened offline, or just to flip the upload
        # toggle), and clearing the notebook would silently stop uploads.
        saved_folder, saved_nb = rspace.load_project()
        rspace.save_project(self._selected_id(self._folder_tree, saved_folder),
                            self._selected_id(self._nb_tree, saved_nb))
        self.accept()


# ── Pending uploads dialog ────────────────────────────────────────────────────

class PendingUploadsDialog(QtWidgets.QDialog):
    """Upload session write-ups that were saved locally but never sent to RSpace.

    Fed by find_pending_records(); each upload uses the same
    RSpaceClient.create_document call the automatic upload does, and stamps the
    record so it drops off this list.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pending RSpace entries")
        self.setModal(True)
        self.setMinimumSize(560, 400)
        self._tasks = []          # live _Task threads (kept from the GC)
        self._pending = []

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "Sessions written up locally but not yet uploaded — tick the ones to send:"))

        self._list = QtWidgets.QListWidget()
        lay.addWidget(self._list, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#888; font-size:10px;")
        lay.addWidget(self._status)

        btns = QtWidgets.QHBoxLayout()
        self._all_btn = QtWidgets.QPushButton("Select all")
        self._all_btn.clicked.connect(self._select_all)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        self._upload_btn = QtWidgets.QPushButton("Upload selected")
        self._upload_btn.clicked.connect(self._upload)
        btns.addWidget(self._all_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        btns.addWidget(self._upload_btn)
        lay.addLayout(btns)

        self._refresh()

    # -- listing --

    def _refresh(self):
        self._list.clear()
        self._pending = find_pending_records(data_root())
        for entry in self._pending:
            rec = entry["record"]
            meta = rec.get("meta") or {}
            item = QtWidgets.QListWidgetItem(
                f"{rec.get('name', '?')}      {meta.get('mouse_id', '?')} / "
                f"{meta.get('session_id', '?')}      saved {rec.get('saved_at', '?')}")
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, entry["path"])
            self._list.addItem(item)

        can_upload = bool(self._pending) and rspace.has_credentials()
        self._upload_btn.setEnabled(can_upload)
        self._all_btn.setEnabled(bool(self._pending))
        if not self._pending:
            self._status.setText("Nothing pending — every session write-up has been uploaded.")
        elif not rspace.has_credentials():
            self._status.setText("No API key configured — set one in RSpace settings first.")
        else:
            _, nb_id = rspace.load_project()
            targets = [e for e in self._pending
                       if not (e["record"].get("notebook_id") or nb_id)]
            self._status.setText(
                f"{len(self._pending)} pending."
                + (f"  {len(targets)} of them have no notebook to go to — select one "
                   "in RSpace settings." if targets else ""))

    def _select_all(self):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(QtCore.Qt.Checked)

    def _checked_paths(self):
        return [self._list.item(i).data(QtCore.Qt.UserRole)
                for i in range(self._list.count())
                if self._list.item(i).checkState() == QtCore.Qt.Checked]

    # -- upload --

    def _upload(self):
        paths = self._checked_paths()
        if not paths:
            self._status.setText("Tick at least one entry.")
            return

        _, default_nb = rspace.load_project()
        client = rspace.default_client()

        def work():
            """Upload each record; returns (uploaded names, [(name, error)])."""
            uploaded, failures = [], []
            for path in paths:
                try:
                    record = read_entry_record(path)
                    name = record.get("name", os.path.basename(path))
                    # An entry goes to the notebook it was written for; if it had
                    # none, the currently configured notebook.
                    nb_id = record.get("notebook_id") or default_nb
                    if not nb_id:
                        failures.append((name, "no notebook configured"))
                        continue
                    result = client.create_document(
                        nb_id, name, record.get("tags") or [],
                        record.get("content") or "")
                    mark_record_uploaded(path, document_id(result))
                    uploaded.append(name)
                except Exception as exc:
                    failures.append((os.path.basename(path), str(exc)))
            return uploaded, failures

        self._upload_btn.setEnabled(False)
        self._status.setText(f"Uploading {len(paths)} entr{'y' if len(paths) == 1 else 'ies'}…")
        _run_task(self, work, self._on_uploaded)

    def _on_uploaded(self, ok, result):
        self._refresh()
        if not ok:
            self._status.setText(f"Upload failed: {result}")
            return
        uploaded, failures = result
        msg = f"Uploaded {len(uploaded)} entr{'y' if len(uploaded) == 1 else 'ies'}."
        if failures:
            msg += "  Failed: " + "; ".join(f"{n} ({e})" for n, e in failures)
        self._status.setText(msg)

    def reject(self):
        self._wait_for_tasks()
        super().reject()

    def accept(self):
        self._wait_for_tasks()
        super().accept()

    def _wait_for_tasks(self):
        # Qt aborts if a QThread is destroyed while still running.
        for task in list(self._tasks):
            task.wait(5000)


# ── Experiment page ───────────────────────────────────────────────────────────

class ExperimentPage(QtWidgets.QWidget):
    """Experiment control panel (see module docstring for the section order)."""

    # Emitted when a recording starts, so the lick heat-map can zero its counts and
    # show this session only. Display-only: the saved lick data is read straight from
    # the shared sensor_array by data_saving.saving_process and is untouched by this.
    recording_started = QtCore.pyqtSignal()

    # Item colours marking where a mouse/session ID is known from.
    _COL_BOTH   = QtGui.QColor("#eeeeee")
    _COL_ONLINE = QtGui.QColor("#7ab7ff")
    _COL_LOCAL  = QtGui.QColor("#e0a030")

    def __init__(self, data_sources: dict):
        super().__init__()
        # WA_OpaquePaintEvent: Qt skips its background pre-fill before paintEvent.
        # Our paintEvent fills every pixel, so the widget is never uninitialized.
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self._data_sources = data_sources

        # State-machine flags (Value objects from main.py via data_sources).
        # Stored as instance attrs so the GC does not destroy them while in use.
        self._sm_active    = data_sources.get("sm_active")
        self._sm_stop      = data_sources.get("sm_stop")
        self._session_done = data_sources.get("session_done")
        self._protocol_queue    = data_sources.get("protocol_queue")
        self._loaded_protocol   = None   # dict set by _browse_protocol()
        self._loaded_protocol_path = None  # filesystem path of loaded file

        # Saving-process handles.  All Value objects must be kept alive here:
        # if they go out of scope the GC calls sem_unlink, destroying the POSIX
        # semaphore before the spawned child can open it (FileNotFoundError in
        # SemLock._rebuild).
        self._saving_proc    = None
        self._saving_running = None
        self._cam_flag       = None
        self._sensor_flag    = None
        self._dlc_flag       = None

        # RSpace state
        self._tasks           = []    # live _Task threads (kept from the GC)
        self._rspace_ok       = False
        self._mice_online     = []
        self._sessions_online = []
        self._comments        = []    # [{"time": "HH:MM:SS", "text": str}]

        # Timer that polls session_done so recording stops automatically when
        # the state machine finishes the session.
        self._sm_done_timer = QtCore.QTimer(self)
        self._sm_done_timer.setInterval(500)
        self._sm_done_timer.timeout.connect(self._check_session_done)

        # Timer that waits for the SM to finish writing mouse.json after a stop.
        self._sm_wait_timer = QtCore.QTimer(self)
        self._sm_wait_timer.setInterval(100)
        self._sm_wait_timer.timeout.connect(self._wait_for_sm)
        self._wait_ticks = 0

        # Debounce for online session lookups (the mouse combo is editable, so its
        # text changes on every keystroke — don't hit the API each time).
        self._sess_debounce = QtCore.QTimer(self)
        self._sess_debounce.setSingleShot(True)
        self._sess_debounce.setInterval(400)
        self._sess_debounce.timeout.connect(self._refresh_online_sessions)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        outer.addLayout(self._build_rspace_bar())

        # Everything except the comments panel scrolls.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(content)
        root.setSpacing(12)
        root.setContentsMargins(10, 10, 10, 10)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._build_info_section(root)
        self._build_protocol_section(root)
        self._build_recording_section(root)
        self._build_tags_section(root)
        root.addStretch()

        # Comments stay pinned at the bottom so they're reachable mid-session.
        outer.addWidget(self._build_comments_panel())

        # Populate and kick off the (async) RSpace status check.
        self._refresh_mouse_combo()
        self._mouse_cb.currentTextChanged.connect(self._on_mouse_changed)
        self._refresh_rspace_status()

        # Qt aborts if a QThread is destroyed while still running, so let any
        # in-flight request finish before the app tears the widgets down.
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._wait_for_tasks)

    def _wait_for_tasks(self):
        # Not just this page's tasks: the settings dialog is a child of this widget,
        # so its threads are destroyed in the same teardown.
        wait_for_all_tasks(2000)

    # ── Section builders ──────────────────────────────────────────

    def _build_rspace_bar(self):
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 4)
        self._rs_dot = QtWidgets.QLabel("●")
        self._rs_dot.setStyleSheet("color:#666; font-size:14px;")
        self._rs_lbl = QtWidgets.QLabel("RSpace: checking…")
        self._rs_lbl.setStyleSheet("color:#aaa; font-size:10px;")
        bar.addWidget(self._rs_dot)
        bar.addWidget(self._rs_lbl, 1)
        rs_btn = QtWidgets.QPushButton("RSpace settings…")
        rs_btn.clicked.connect(self._open_rspace_settings)
        bar.addWidget(rs_btn)
        return bar

    def _build_info_section(self, root):
        root.addWidget(self._section_label("Experiment Info"))

        form = QtWidgets.QFormLayout()
        form.setSpacing(6)
        self._mouse_cb   = self._make_id_combo()
        self._session_cb = self._make_id_combo()
        form.addRow("Mouse ID:",   self._mouse_cb)
        form.addRow("Session ID:", self._session_cb)
        root.addLayout(form)

        legend = QtWidgets.QLabel(
            f'<span style="color:{self._COL_BOTH.name()}">●</span> local + RSpace '
            f'&nbsp; <span style="color:{self._COL_ONLINE.name()}">●</span> RSpace only '
            f'&nbsp; <span style="color:{self._COL_LOCAL.name()}">●</span> local only')
        legend.setStyleSheet("font-size:9px;")
        root.addWidget(legend)

        root.addWidget(self._separator())

    def _build_protocol_section(self, root):
        root.addWidget(self._section_label("Selected Protocol"))

        proto_row = QtWidgets.QHBoxLayout()
        self._proto_path_lbl = QtWidgets.QLabel("No protocol selected")
        self._proto_path_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._proto_path_lbl.setWordWrap(True)
        proto_browse_btn = QtWidgets.QPushButton("Browse…")
        proto_browse_btn.setFixedWidth(70)
        proto_browse_btn.clicked.connect(self._browse_protocol)
        proto_row.addWidget(self._proto_path_lbl, 1)
        proto_row.addWidget(proto_browse_btn)
        root.addLayout(proto_row)

        self._proto_summary = QtWidgets.QFrame()
        self._proto_summary.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._proto_summary.setStyleSheet(
            "QFrame { background:#222; border:1px solid #444; border-radius:4px; }")
        summary_layout = QtWidgets.QVBoxLayout(self._proto_summary)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        summary_layout.setSpacing(2)
        self._sum_session = QtWidgets.QLabel()
        self._sum_rewards = QtWidgets.QLabel()
        self._sum_trial   = QtWidgets.QLabel()
        for lbl in (self._sum_session, self._sum_rewards, self._sum_trial):
            lbl.setStyleSheet("color:#ccc; font-size:10px;")
            summary_layout.addWidget(lbl)
        self._proto_summary.setVisible(False)
        root.addWidget(self._proto_summary)

        root.addWidget(self._separator())

    def _build_recording_section(self, root):
        root.addWidget(self._section_label("Recording"))

        flag_row = QtWidgets.QHBoxLayout()
        self._cam_chk    = QtWidgets.QCheckBox("Camera")
        self._sensor_chk = QtWidgets.QCheckBox("Sensors")
        self._dlc_chk    = QtWidgets.QCheckBox("DeepLabCut")
        self._cam_chk.setChecked(True)
        self._sensor_chk.setChecked(True)
        for chk in (self._cam_chk, self._sensor_chk, self._dlc_chk):
            flag_row.addWidget(chk)
        flag_row.addStretch()
        root.addLayout(flag_row)

        btn_row = QtWidgets.QHBoxLayout()
        self._start_btn = QtWidgets.QPushButton("START RECORDING")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setStyleSheet(
            "QPushButton { background:#1a6b1a; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#247a24; }"
            "QPushButton:disabled { background:#333; color:#666; }")
        self._start_btn.clicked.connect(self._start_recording)
        self._stop_btn = QtWidgets.QPushButton("STOP RECORDING")
        self._stop_btn.setFixedHeight(36)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton { background:#8b0000; color:#fff; border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#b00000; }"
            "QPushButton:disabled { background:#333; color:#666; }")
        self._stop_btn.clicked.connect(self._stop_recording)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        root.addLayout(btn_row)

        self._rec_status = QtWidgets.QLabel("Idle")
        self._rec_status.setStyleSheet("color:#aaa; font-size:10px;")
        self._rec_status.setWordWrap(True)
        root.addWidget(self._rec_status)

        root.addWidget(self._separator())

    def _build_tags_section(self, root):
        root.addWidget(self._section_label("Tags"))

        auto_row = QtWidgets.QHBoxLayout()
        auto_row.addWidget(QtWidgets.QLabel("Automatic:"))
        self._tag_auto_lbl = QtWidgets.QLabel("—")
        self._tag_auto_lbl.setStyleSheet("color:#7ab7ff; font-size:10px; font-weight:bold;")
        auto_row.addWidget(self._tag_auto_lbl)
        auto_row.addStretch()
        root.addLayout(auto_row)

        opt_row = QtWidgets.QHBoxLayout()
        opt_row.addWidget(QtWidgets.QLabel("Also recorded with:"))
        self._tag_opt_chks = {}
        for tag in OPTIONAL_METHOD_TAGS:
            chk = QtWidgets.QCheckBox(tag)
            self._tag_opt_chks[tag] = chk
            opt_row.addWidget(chk)
        opt_row.addStretch()
        root.addLayout(opt_row)

        root.addWidget(self._separator())

    def _build_comments_panel(self):
        panel = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(panel)
        lay.setContentsMargins(10, 4, 10, 10)
        lay.setSpacing(4)
        lay.addWidget(self._section_label("Comments"))
        self._comment_view = QtWidgets.QListWidget()
        self._comment_view.setMaximumHeight(110)
        lay.addWidget(self._comment_view)
        self._comment_input = QtWidgets.QLineEdit()
        self._comment_input.setPlaceholderText("Type a comment and press Enter…")
        self._comment_input.returnPressed.connect(self._post_comment)
        lay.addWidget(self._comment_input)
        return panel

    # ── Background ────────────────────────────────────────────────

    def paintEvent(self, event):
        QtGui.QPainter(self).fillRect(event.rect(), QtGui.QColor("#2b2b2b"))

    # ── Layout helpers ────────────────────────────────────────────

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#aaa; font-size:11px; font-weight:bold;")
        return lbl

    @staticmethod
    def _separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#444;")
        return line

    @staticmethod
    def _make_id_combo() -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        return cb

    # ── Local scanning ────────────────────────────────────────────

    @staticmethod
    def _list_subdirs(path: str) -> list:
        """Return sorted list of immediate sub-directory names under path."""
        try:
            return sorted(d for d in os.listdir(path)
                          if os.path.isdir(os.path.join(path, d)))
        except OSError:
            return []

    def _data_path(self) -> str:
        return data_root()

    def _local_mice(self) -> list:
        return self._list_subdirs(self._data_path())

    def _local_sessions(self, mouse: str) -> list:
        if not mouse:
            return []
        return self._list_subdirs(os.path.join(self._data_path(), mouse))

    def _mouse_log_path(self, mouse: str) -> str:
        return os.path.join(self._data_path(), mouse, f"{mouse}.json")

    # ── Combo population ──────────────────────────────────────────

    def _fill_combo(self, combo, local, online):
        """Fill an editable combo with local ∪ online IDs, colour-coded by origin.

        The item *text* stays the raw ID — these combos are editable, so
        currentText() must never contain decoration. Origin is conveyed by the
        item's colour and tooltip instead.
        """
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        local, online = set(local), set(online)
        for name in sorted(local | online):
            if name in local and name in online:
                colour, tip = self._COL_BOTH, "local + RSpace"
            elif name in online:
                colour, tip = self._COL_ONLINE, "RSpace only"
            else:
                colour, tip = self._COL_LOCAL, "local only"
            combo.addItem(name)
            i = combo.count() - 1
            combo.setItemData(i, QtGui.QBrush(colour), QtCore.Qt.ForegroundRole)
            combo.setItemData(i, tip, QtCore.Qt.ToolTipRole)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def _refresh_mouse_combo(self):
        self._fill_combo(self._mouse_cb, self._local_mice(), self._mice_online)
        self._on_mouse_changed(self._mouse_cb.currentText())

    def _refresh_session_combo(self):
        mouse = self._mouse_cb.currentText().strip()
        self._fill_combo(self._session_cb, self._local_sessions(mouse),
                         self._sessions_online)

    def _on_mouse_changed(self, _mouse=None):
        self._tag_auto_lbl.setText(", ".join(self._auto_tags()))
        self._sessions_online = []
        self._refresh_session_combo()
        self._sess_debounce.start()

    # ── RSpace ────────────────────────────────────────────────────

    def _open_rspace_settings(self):
        dlg = RSpaceSettingsDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._refresh_rspace_status()

    def _set_rspace_status(self, ok, msg):
        self._rspace_ok = bool(ok)
        self._rs_dot.setStyleSheet(
            f"color:{'#1a9e1a' if ok else '#8b0000'}; font-size:14px;")
        # Uploads being off is easy to forget, so it is shown next to the status
        # rather than only inside the settings dialog.
        off = "" if rspace.load_upload_enabled() else "  —  uploads OFF (saved locally)"
        self._rs_lbl.setText(f"RSpace: {msg}{off}")

    def _refresh_rspace_status(self):
        if not rspace.has_credentials():
            self._set_rspace_status(False, "not configured — open RSpace settings")
            return
        self._rs_lbl.setText("RSpace: checking…")
        _run_task(self, rspace.check_connection, self._on_status)

    def _on_status(self, ok, result):
        if not ok:
            self._set_rspace_status(False, str(result))
            return
        connected, msg = result
        self._set_rspace_status(connected, msg)
        if connected:
            self._refresh_online_mice()

    def _refresh_online_mice(self):
        """List the documents in the project's "mice" folder (one per mouse).

        The documents are named "#<mouse>"; the hash is dropped so the ID used for
        the dropdown, the id_ tag and the local folder is the bare mouse ID.
        """
        folder_id, _ = rspace.load_project()
        if not (self._rspace_ok and folder_id):
            self._mice_online = []
            self._refresh_mouse_combo()
            return
        client = rspace.default_client()

        def work():
            mice_folder = client.find_child(folder_id, "mice")
            if not mice_folder:
                return []
            return [mouse_id_from_doc_name(n["name"])
                    for n in client.list_children(mice_folder["id"])
                    if n["type"] == "document"]

        _run_task(self, work, self._on_online_mice)

    def _on_online_mice(self, ok, result):
        if not ok:
            self._rs_lbl.setText(
                f"RSpace: could not list mice ({rspace.describe_error(result)})")
            result = []
        self._mice_online = result
        self._refresh_mouse_combo()

    def _refresh_online_sessions(self):
        """List sessions from notebook entries tagged id_<mouse>."""
        mouse = self._mouse_cb.currentText().strip()
        _, nb_id = rspace.load_project()
        if not (self._rspace_ok and nb_id and mouse):
            return
        client = rspace.default_client()
        tag = f"{rspace.ID_PREFIX}{mouse}"

        def work():
            names = []
            for doc in client.list_documents(nb_id):
                tags = [t.strip() for t in (doc.get("tags") or "").split(",") if t.strip()]
                if tag not in tags:
                    continue
                session = session_from_entry_name(doc.get("name", ""))
                if session:
                    names.append(session)
            return sorted(set(names))

        _run_task(self, work, self._on_online_sessions)

    def _on_online_sessions(self, ok, result):
        self._sessions_online = result if ok else []
        self._refresh_session_combo()

    # ── Tags ──────────────────────────────────────────────────────

    def _auto_tags(self) -> list:
        """Tags applied to every entry: the mouse ID and the behaviour method."""
        mouse = self._mouse_cb.currentText().strip()
        tags = [f"{rspace.ID_PREFIX}{mouse}"] if mouse else []
        tags.append(AUTO_METHOD_TAG)
        return tags

    def _selected_tags(self) -> list:
        """The automatic tags plus whichever optional method tags are ticked."""
        return self._auto_tags() + [tag for tag, chk in self._tag_opt_chks.items()
                                    if chk.isChecked()]

    # ── Comments ──────────────────────────────────────────────────

    def _post_comment(self):
        text = self._comment_input.text().strip()
        if not text:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self._comments.append({"time": stamp, "text": text})
        self._comment_view.addItem(f"[{stamp}]  {text}")
        self._comment_view.scrollToBottom()
        self._comment_input.clear()

    def _mark_comments_session_end(self, session):
        """Close off the comment log when a session ends.

        The notes stay on screen until the next recording starts, so without this
        marker they could be mistaken for the next session's. View-only — it is
        never added to self._comments, so it reaches neither mouse.json nor RSpace.
        """
        stamp = datetime.now().strftime("%H:%M:%S")
        item = QtWidgets.QListWidgetItem(
            f"── session “{session}” ended {stamp} — the comments above belong to it ──")
        item.setForeground(QtGui.QBrush(self._COL_LOCAL))
        self._comment_view.addItem(item)
        self._comment_view.scrollToBottom()

    # ── Protocol ──────────────────────────────────────────────────

    def _browse_protocol(self):
        """Open a file dialog to pick a protocol JSON; load and show a summary."""
        try:
            from shared_states import protocols_path
            start_dir = protocols_path
        except Exception:
            start_dir = ""

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Protocol", start_dir, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r") as fh:
                self._loaded_protocol = json.load(fh)
        except Exception as exc:
            self._proto_path_lbl.setText(f"Error loading: {exc}")
            self._proto_summary.setVisible(False)
            return

        self._loaded_protocol_path = path
        self._proto_path_lbl.setText(path)
        self._show_protocol_summary()

    def _show_protocol_summary(self):
        """Populate and reveal the summary box from self._loaded_protocol."""
        d = self._loaded_protocol
        if d is None:
            self._proto_summary.setVisible(False)
            return

        sess = d.get("session", {})
        s_type   = sess.get("type", "?")
        s_length = sess.get("length", "?")
        unit     = "s" if s_type == "time" else "trials"
        self._sum_session.setText(f"Session: {s_type},  {s_length} {unit}")

        rew  = d.get("rewards", {})
        cnt  = rew.get("count", "?")
        dist = rew.get("distribution", {}).get("type", "?")
        self._sum_rewards.setText(f"Rewards: {cnt}  ({dist} distribution)")

        trial  = d.get("trial", {})
        t_type = trial.get("end_type", "?")
        t_detail = (f"{trial.get('duration_s', '?')} s per trial" if t_type == "time"
                    else "ends when all rewards collected")
        self._sum_trial.setText(f"Trial: {t_detail}")

        self._proto_summary.setVisible(True)

    # ── Recording controls ────────────────────────────────────────

    def _set_id_widgets_enabled(self, enabled: bool):
        for w in (self._mouse_cb, self._session_cb,
                  self._cam_chk, self._sensor_chk, self._dlc_chk):
            w.setEnabled(enabled)

    def _start_recording(self):
        mouse   = self._mouse_cb.currentText().strip()
        session = self._session_cb.currentText().strip()
        if not mouse or not session:
            self._rec_status.setText("Fill in Mouse and Session IDs first.")
            return
        if self._loaded_protocol is None:
            self._rec_status.setText("Load a protocol first.")
            return

        # Comments and the lick counts belong to one session.
        self._comments = []
        self._comment_view.clear()
        self.recording_started.emit()

        # Attach session metadata so the SM can write the mouse log.
        protocol_to_send = dict(self._loaded_protocol)
        protocol_to_send["_meta"] = {
            "mouse_id":      mouse,
            "session_id":    session,
            "protocol_path": self._loaded_protocol_path or "",
        }

        # Drain any stale entry from a previous run, then push the current protocol.
        if self._protocol_queue is not None:
            while not self._protocol_queue.empty():
                try:
                    self._protocol_queue.get_nowait()
                except Exception:
                    pass
            self._protocol_queue.put_nowait(protocol_to_send)

        ds = self._data_sources
        self._saving_running = Value('b', True)
        self._cam_flag    = Value('b', self._cam_chk.isChecked())
        self._sensor_flag = Value('b', self._sensor_chk.isChecked())
        self._dlc_flag    = Value('b', self._dlc_chk.isChecked())

        self._saving_proc = Process(
            target=saving_process,
            args=(
                ds.get("dlc_queue"),      # full-res frames for camera saving
                ds.get("sensor_array"),
                ds.get("pose_queue"),     # DLC pose estimates for CSV
                ds.get("timestamp_value"),
                mouse, session,
                self._cam_flag, self._sensor_flag, self._dlc_flag,
                self._saving_running,
            ),
            daemon=True,
        )
        self._saving_proc.start()

        if self._sm_active is not None:
            self._sm_active.value = True

        self._sm_done_timer.start()

        self._set_id_widgets_enabled(False)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._rec_status.setText(f"Recording  {mouse} / {session}")

    def _stop_recording(self):
        # Stop polling and halt the state machine immediately
        self._sm_done_timer.stop()
        if self._sm_stop is not None:
            self._sm_stop.value = True
        if self._saving_running is not None:
            self._saving_running.value = False

        self._stop_btn.setEnabled(False)
        self._rec_status.setText("Stopping — waiting for the state machine…")
        # The SM clears sm_active once it has written the session entry (reward
        # ports + end time) to mouse.json; only then is it safe to read it back.
        self._wait_ticks = 0
        self._sm_wait_timer.start()

    def _wait_for_sm(self):
        idle = self._sm_active is None or not self._sm_active.value
        self._wait_ticks += 1
        if idle or self._wait_ticks > 50:      # ~5 s safety timeout
            self._sm_wait_timer.stop()
            self._finalize_stop()

    def _finalize_stop(self):
        # The saving process appends its last chunk of rows when it is told to
        # stop — running_flag=False (set in _stop_recording) makes the saving
        # thread flush, and its SIGTERM handler flushes too. Give it room to
        # finish instead of reaping it mid-write.
        if self._saving_proc and self._saving_proc.is_alive():
            self._saving_proc.terminate()
            self._saving_proc.join(timeout=3)
        self._saving_proc    = None
        self._saving_running = None
        self._cam_flag       = None
        self._sensor_flag    = None
        self._dlc_flag       = None

        mouse   = self._mouse_cb.currentText().strip()
        session = self._session_cb.currentText().strip()
        entry = self._update_mouse_log(mouse, session)
        self._mark_comments_session_end(session)

        self._set_id_widgets_enabled(True)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._refresh_session_combo()

        self._push_rspace_entry(mouse, session, entry)

    def _check_session_done(self):
        """Called every 500 ms while recording; auto-stop on natural session end."""
        if self._session_done is not None and self._session_done.value:
            self._sm_done_timer.stop()
            self._rec_status.setText("Session complete — stopping recording…")
            self._stop_recording()

    # ── Session write-up ──────────────────────────────────────────

    def _update_mouse_log(self, mouse, session) -> dict:
        """Add this session's comments to mouse.json; return the session entry.

        The state machine owns mouse.json (it writes the entry with reward_ports
        and end_time), so this only runs once sm_active has cleared. Returns the
        entry dict (including reward_ports) or {} if it could not be read.
        """
        path = self._mouse_log_path(mouse)
        try:
            with open(path, "r") as fh:
                log = json.load(fh)
        except Exception:
            return {}

        entry = next((e for e in reversed(log.get("sessions", []))
                      if e.get("session_id") == session), None)
        if entry is None:
            return {}

        entry["comments"] = list(self._comments)
        try:
            with open(path, "w") as fh:
                json.dump(log, fh, indent=2)
        except Exception as exc:
            print(f"[ExperimentPage] could not write mouse log: {exc}")
        return entry

    def _write_session_record(self, mouse, session, entry):
        """Write this session's RSpace write-up to its Data folder as JSON.

        Runs for every session, uploaded or not, so the entry can always be
        recreated later (see PendingUploadsDialog). Returns (path, record), or
        (None, record) if the file could not be written.
        """
        _, nb_id = rspace.load_project()
        meta = {
            "mouse_id":      mouse,
            "session_id":    session,
            "protocol_path": self._loaded_protocol_path or "",
            "date":          entry.get("date", ""),
            "start_time":    entry.get("start_time", ""),
            "end_time":      entry.get("end_time", ""),
        }
        content = build_entry_html(self._loaded_protocol, meta,
                                   entry.get("reward_ports", []), self._comments)
        record = build_entry_record(
            name=build_entry_name(session),
            tags=self._selected_tags(),
            content=content,
            meta=meta,
            protocol=self._loaded_protocol,
            reward_ports=entry.get("reward_ports", []),
            comments=self._comments,
            notebook_id=nb_id,
        )
        path = entry_record_path(self._data_path(), mouse, session, record["name"])
        try:
            write_entry_record(path, record)
        except Exception as exc:
            print(f"[ExperimentPage] could not write session record: {exc}")
            return None, record
        return path, record

    def _push_rspace_entry(self, mouse, session, entry):
        """Save the session write-up locally, then upload it if uploads are on.

        The JSON is written either way; the upload is skipped (and the record
        left pending) when uploads are switched off, RSpace is unreachable, or no
        notebook is configured.
        """
        path, record = self._write_session_record(mouse, session, entry)
        where = f" Write-up saved to {os.path.basename(path)}." if path else ""

        if not rspace.load_upload_enabled():
            self._rec_status.setText(
                "Idle — RSpace upload is switched off." + where +
                "  Use RSpace settings → Pending entries… to upload it later.")
            return
        if not (self._rspace_ok and record.get("notebook_id")):
            self._rec_status.setText(
                "Idle — RSpace entry skipped (not connected or no notebook selected)."
                + where)
            return

        nb_id  = record["notebook_id"]
        name   = record["name"]
        tags   = record["tags"]
        content = record["content"]
        client = rspace.default_client()

        self._rec_status.setText("Uploading session entry to RSpace…")
        _run_task(self,
                  lambda: client.create_document(nb_id, name, tags, content),
                  lambda ok, res: self._on_uploaded(ok, res, name, path))

    def _on_uploaded(self, ok, result, name, path=None):
        if ok:
            if path:
                try:
                    mark_record_uploaded(path, document_id(result))
                except Exception as exc:
                    print(f"[ExperimentPage] could not stamp session record: {exc}")
            self._rec_status.setText(f"Idle — RSpace entry “{name}” created.")
            self._sess_debounce.start()   # the new session now exists online
        else:
            # The record stays pending, so nothing is lost — it can be retried.
            self._rec_status.setText(
                f"Idle — RSpace upload failed: {result}. The write-up is saved "
                "locally; retry from RSpace settings → Pending entries…")
