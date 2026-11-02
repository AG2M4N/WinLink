"""
Auto-clean received files (optional).

WinLink keeps a list of the files it saved to Downloads. When cleanup is on,
files older than the chosen number of days are moved to the Trash (Recycle Bin
on Windows), never deleted outright. Only WinLink's own files are touched, and
a file you edited or moved since it arrived is left alone. Folders WinLink
created for received folders are removed once they're empty.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

IS_WIN = os.name == "nt"
DAY = 86400
log = logging.getLogger("winlink")


def move_to_trash(path):
    """True if the file is now in the Trash / Recycle Bin."""
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE = 3
        flags = 0x0040 | 0x0010 | 0x0004 | 0x0400     # allow undo (Recycle Bin), no confirm, silent, no error UI
        op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0\0", None, flags, False, None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path)
    if sys.platform == "darwin":
        from Foundation import NSFileManager, NSURL
        ok, _url, _err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(path), None, None)
        return bool(ok)
    return _linux_trash(path)


def _linux_trash(path):
    """The desktop's Trash: through GLib (what Files, Dolphin and the rest use), else `gio trash`,
    else the freedesktop.org Trash layout by hand (files in your home folder)."""
    try:
        from gi.repository import Gio
        if Gio.File.new_for_path(path).trash(None):
            return not os.path.exists(path)
    except Exception:  # noqa: BLE001  (no PyGObject, or a mount without a trash)
        pass
    try:
        if subprocess.run(["gio", "trash", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=10).returncode == 0:
            return not os.path.exists(path)
    except (OSError, subprocess.SubprocessError):
        pass
    home_trash = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")
    try:
        files, info = os.path.join(home_trash, "files"), os.path.join(home_trash, "info")
        os.makedirs(files, exist_ok=True)
        os.makedirs(info, exist_ok=True)
        base = name = os.path.basename(path)
        n = 2
        while os.path.exists(os.path.join(files, name)) or os.path.exists(os.path.join(info, name + ".trashinfo")):
            stem, ext = os.path.splitext(base)
            name, n = f"{stem}.{n}{ext}", n + 1
        with open(os.path.join(info, name + ".trashinfo"), "w", encoding="utf-8") as f:
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (
                urllib.parse.quote(os.path.abspath(path)), time.strftime("%Y-%m-%dT%H:%M:%S")))
        shutil.move(path, os.path.join(files, name))
        return True
    except OSError:
        return False


class Cleanup:
    def __init__(self, folder, settings, notice):
        self.path = os.path.join(folder, "received.json")
        self.settings, self.notice = settings, notice
        self.lock = threading.Lock()
        self.wake = threading.Event()
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.files = [e for e in data.get("files", []) if isinstance(e, dict) and "path" in e]
            self.dirs = [d for d in data.get("dirs", []) if isinstance(d, str)]
        except (OSError, ValueError, AttributeError):
            self.files, self.dirs = [], []
        settings.data.setdefault("cleanup", {"enabled": False, "days": 7})

    # ---- settings
    @property
    def enabled(self):
        return bool(self.settings["cleanup"].get("enabled"))

    @property
    def days(self):
        return max(1, min(365, int(self.settings["cleanup"].get("days", 7))))

    def configure(self, enabled=None, days=None):
        c = dict(self.settings["cleanup"])
        if enabled is not None:
            c["enabled"] = bool(enabled)
        if days is not None:
            c["days"] = max(1, min(365, int(days)))
        self.settings["cleanup"] = c
        self.wake.set()

    # ---- bookkeeping
    def record(self, path, created_dirs=(), sender=""):
        """A file WinLink just saved."""
        try:
            st = os.stat(path)
        except OSError:
            return
        with self.lock:
            self.files = [e for e in self.files if e["path"] != path]
            self.files.append({"path": path, "at": time.time(), "size": st.st_size, "mtime": st.st_mtime_ns,
                               "from": sender})
            self.dirs = sorted(set(self.dirs) | set(created_dirs))
            self._save()

    def entries(self):
        """Received files that still exist where WinLink saved them (oldest first)."""
        with self.lock:
            return [dict(e) for e in self.files if os.path.exists(e["path"])]

    def knows(self, path):
        with self.lock:
            return any(e["path"] == path for e in self.files)

    def forget(self, paths):
        paths = set(paths)
        with self.lock:
            self.files = [e for e in self.files if e["path"] not in paths]
            self._save()

    def remove_empty_dirs(self):
        with self.lock:
            for d in sorted(self.dirs, key=len, reverse=True):
                try:
                    if os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
                except OSError:
                    pass
            self.dirs = [d for d in self.dirs if os.path.isdir(d)]
            self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"files": self.files, "dirs": self.dirs}, f)
        os.replace(tmp, self.path)

    @staticmethod
    def _unchanged(e):
        try:
            st = os.stat(e["path"])
        except OSError:
            return None                          # moved or deleted by you
        return st.st_size == e["size"] and st.st_mtime_ns == e["mtime"]

    def summary(self):
        """(files that will be cleaned up, their total size, seconds until the next one)."""
        now, limit = time.time(), self.days * DAY
        with self.lock:
            files = list(self.files)
        due = [e for e in files if os.path.exists(e["path"])]
        soonest = min((e["at"] + limit - now for e in due), default=None)
        return len(due), sum(e["size"] for e in due), soonest

    # ---- the cleaning
    def run_once(self):
        if not self.enabled:
            return 0
        now, limit = time.time(), self.days * DAY
        moved, keep = [], []
        with self.lock:
            files = list(self.files)
        for e in files:
            state = self._unchanged(e)
            if state is None or state is False:
                continue                         # gone, or you changed it: not ours to clean any more
            if now - e["at"] < limit:
                keep.append(e)
                continue
            try:
                if move_to_trash(e["path"]):
                    moved.append(e)
                else:
                    keep.append(e)
            except Exception:  # noqa: BLE001
                log.exception("couldn't move %s to the Trash", e["path"])
                keep.append(e)
        with self.lock:
            done = {e["path"] for e in files}
            self.files = keep + [e for e in self.files if e["path"] not in done]   # files received meanwhile
            for d in sorted(self.dirs, key=len, reverse=True):                   # deepest first
                try:
                    if os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
                except OSError:
                    pass
            self.dirs = [d for d in self.dirs if os.path.isdir(d)]
            self._save()
        if moved:
            n = sum(e["size"] for e in moved)
            size = f"{n / (1 << 30):.1f} GB" if n >= 1 << 30 else f"{n / (1 << 20):.1f} MB" if n >= 1 << 20 \
                else f"{max(1, n >> 10)} KB"
            what = "file" if len(moved) == 1 else "files"
            self.notice(f"Moved {len(moved)} received {what} ({size}) older than {self.days} "
                        f"day{'s' if self.days != 1 else ''} to the {'Recycle Bin' if IS_WIN else 'Trash'}.")
            log.info("cleanup moved %d files to the trash", len(moved))
        return len(moved)

    def loop(self):
        """Check at startup, every 30 minutes, and whenever the setting changes."""
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("cleanup")
            self.wake.wait(1800)
            self.wake.clear()
