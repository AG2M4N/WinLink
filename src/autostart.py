"""
Open WinLink when you log in (it starts hidden, in the tray or menu bar).

  Mac:     a LaunchAgent in ~/Library/LaunchAgents
  Windows: the Run key in the registry (HKEY_CURRENT_USER, no admin needed)
  Linux:   ~/.config/autostart/winlink.desktop (the freedesktop.org way, every desktop reads it)

What's on disk is the only record, so turning it off in System Settings / Task Manager / the
desktop's startup apps shows up here too. If WinLink moves, the entry is rewritten next launch.
"""

import logging
import os
import sys

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
LABEL = "local.akshat.winlink"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command(hidden=True):
    """The words that start this WinLink."""
    extra = ["--hidden"] if hidden else []
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    launcher = os.path.expanduser("~/.local/bin/winlink")          # Linux, from install_linux.sh
    if not IS_MAC and not IS_WIN and os.access(launcher, os.X_OK):
        return [launcher, *extra]
    exe = sys.executable
    if IS_WIN:
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")    # no console window
        exe = pythonw if os.path.exists(pythonw) else exe
    return [exe, os.path.join(os.path.dirname(os.path.abspath(__file__)), "link.py"), *extra]


def _mac_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def _linux_path():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "autostart", "winlink.desktop")


def enabled():
    try:
        if IS_MAC:
            return os.path.exists(_mac_path())
        if IS_WIN:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "WinLink")
            return True
        path = _linux_path()
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
    except OSError:
        return False


def set_enabled(on, quiet=False):
    """Returns an error message, or "" if it worked."""
    try:
        if IS_MAC:
            _mac(on)
        elif IS_WIN:
            _windows(on)
        else:
            _linux(on)
        if not quiet:
            log.info("open at login: %s", "on" if on else "off")
        return ""
    except OSError as e:
        log.warning("open at login: %s", e)
        return str(e)


def refresh():
    """WinLink moved or was rebuilt somewhere else: point the entry at this copy."""
    if enabled():
        set_enabled(True, quiet=True)


def _mac(on):
    import plistlib
    path = _mac_path()
    if not on:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plist = {"Label": LABEL, "ProgramArguments": command(), "RunAtLoad": True,
             "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua"}
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        plistlib.dump(plist, f)
    os.replace(tmp, path)


def _windows(on):
    import subprocess
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, "WinLink", 0, winreg.REG_SZ, subprocess.list2cmdline(command()))
        else:
            try:
                winreg.DeleteValue(k, "WinLink")
            except FileNotFoundError:
                pass


def _linux(on):
    from filemenu_linux import _desktop_exec
    path = _linux_path()
    if not on:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = "\n".join(["[Desktop Entry]", "Type=Application", "Name=WinLink",
                      "Comment=Share the clipboard, send files and play sound between your devices",
                      f"Exec={_desktop_exec(command())}", "Icon=winlink", "Terminal=false",
                      "X-GNOME-Autostart-enabled=true", ""])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
