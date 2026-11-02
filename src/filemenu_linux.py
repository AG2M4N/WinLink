"""
"Send with WinLink" in Linux file managers' right-click menus (the Linux side of Windows' Send to).

One entry per paired device, plus "All devices", in:
  - GNOME Files (Nautilus):  right-click › Scripts › WinLink › <device>
  - KDE Dolphin:             right-click › Send with WinLink › <device>
  - Cinnamon Nemo:           right-click › Send to <device> with WinLink
Each entry runs WinLink with `--send <device id> <files>`; the running WinLink sends them (see
sendto.py for the hand-off). Only files WinLink made are touched.
"""

import logging
import os
import shlex
import shutil
import sys
import threading

from sendto import safe_name

log = logging.getLogger("winlink")
MARK = "# Made by WinLink: it keeps this up to date. Turn it off in WinLink › Settings."


def data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def command():
    """The words that start this WinLink."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    launcher = os.path.expanduser("~/.local/bin/winlink")      # made by install_linux.sh
    if os.access(launcher, os.X_OK):
        return [launcher]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "link.py")]


def _desktop_exec(words):
    """Quote for a .desktop Exec= line (freedesktop rules: double quotes, escape \\ " ` $)."""
    out = []
    for w in words:
        if any(c in w for c in ' \t\n"\'\\><~|&;$*?#()`'):
            w = '"' + w.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
        out.append(w)
    return " ".join(out).replace("%", "%%")


def _write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class FileManagerMenu:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = None
        self.status = ""

    @staticmethod
    def entries(devices):
        """[(id, name)] -> [(label, id)], "All devices" last when there are several."""
        out, seen = [], set()
        for pid, name in sorted(devices, key=lambda d: d[1].lower()):
            label, n = safe_name(name), 2
            while label.lower() in seen:
                label, n = f"{safe_name(name)} ({n})", n + 1
            seen.add(label.lower())
            out.append((label, pid))
        if len(devices) > 1:
            out.append(("All devices", "all"))
        return out

    def _paths(self):
        base = data_home()
        return (os.path.join(base, "nautilus", "scripts", "WinLink"),
                os.path.join(base, "kio", "servicemenus", "winlink.desktop"),
                os.path.join(base, "nemo", "actions"))

    def _clear(self):
        nautilus, dolphin, nemo = self._paths()
        if os.path.isdir(nautilus):
            for f in os.listdir(nautilus):
                p = os.path.join(nautilus, f)
                try:
                    with open(p, encoding="utf-8") as fh:
                        if MARK in fh.read(4096):
                            os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(nautilus)
            except OSError:
                pass
        try:
            os.remove(dolphin)
        except OSError:
            pass
        if os.path.isdir(nemo):
            for f in os.listdir(nemo):
                if f.startswith("winlink-") and f.endswith(".nemo_action"):
                    try:
                        os.remove(os.path.join(nemo, f))
                    except OSError:
                        pass

    def sync(self, devices, enabled=True):
        """Bring the menus in line. True if anything changed (so the window can update)."""
        want = self.entries(devices) if enabled else []
        cmd = command()
        key = (tuple(want), tuple(cmd))
        nautilus, dolphin, nemo = self._paths()
        with self.lock:
            if key == self.current and (not want or os.path.exists(dolphin)):
                return False
            try:
                self._clear()
                if not want:
                    self.current = key
                    self.status = "Off." if not enabled else "Pair a device and it appears here."
                    return True
                for label, pid in want:
                    # Files: selected paths arrive one per line in NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
                    # (relative names in "$@" otherwise, with the folder as the current directory)
                    _write(os.path.join(nautilus, label), "\n".join([
                        "#!/bin/sh", MARK,
                        'if [ -n "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" ]; then',
                        "  set -f", "  IFS='\n'", "  set -- $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS", "fi",
                        f"exec {shlex.join(cmd)} --send {pid} \"$@\"", ""]))
                ids = [f"winlink_{i}" for i in range(len(want))]
                lines = ["[Desktop Entry]", MARK, "Type=Service", "MimeType=all/all;",
                         f"Actions={';'.join(ids)};", "X-KDE-Submenu=Send with WinLink", "X-KDE-Priority=TopLevel", ""]
                for act, (label, pid) in zip(ids, want):
                    lines += [f"[Desktop Action {act}]", f"Name={label}", "Icon=winlink",
                              f"Exec={_desktop_exec(cmd + ['--send', pid])} %F", ""]
                _write(dolphin, "\n".join(lines))
                for i, (label, pid) in enumerate(want):
                    _write(os.path.join(nemo, f"winlink-{i}.nemo_action"), "\n".join([
                        "[Nemo Action]", MARK,
                        f"Name=Send to {label.replace('_', '__')} with WinLink",
                        f"Comment=Send the selected items to {label} with WinLink",
                        f"Exec={_desktop_exec(cmd + ['--send', pid])} %F",
                        "Icon-Name=winlink", "Selection=notnone", "Extensions=any;", ""]), 0o644)
            except OSError as e:
                self.current = None
                self.status = f"Couldn't add the menu entries: {e}"
                log.error("file manager menu: %s", self.status)
                return True
            self.current = key
            found = [n for n, exe in (("Files", "nautilus"), ("Dolphin", "dolphin"), ("Nemo", "nemo"))
                     if shutil.which(exe)]
            names = ", ".join(label for label, _ in want)
            self.status = f"In the menu{' of ' + ', '.join(found) if found else ''}: {names}."
            log.info("file manager menu: %s", self.status)
            return True
