"""
"Send to" in Explorer's right-click menu (Windows), and handing work to the running WinLink.

Menu: WinLink keeps one shortcut per paired device ("WinLink - Msi", ...) plus
"WinLink - All devices" in your Send to folder. Each shortcut starts WinLink
with `--send <device id>`, and Explorer adds the selected files and folders.

Hand-off: the running WinLink listens on 127.0.0.1 (a random port, written to
handoff.json in its settings folder together with a random token). A second
copy started by a shortcut gives it the files and exits; if WinLink isn't
running, that copy becomes WinLink and sends once the device connects. Starting
WinLink again normally just brings the open window to the front.
"""

import base64
import hmac
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading

log = logging.getLogger("winlink")

IS_WIN = os.name == "nt"
PREFIX = "WinLink - "
MAX_REQUEST = 4 << 20            # a long list of paths, but not unbounded


def _handoff_path(folder):
    return os.path.join(folder, "handoff.json")


# ---------------------------------------------------------------- the running app's side
class HandoffServer:
    """handler(request dict) -> (ok, message). Runs each request on its own thread."""

    def __init__(self, folder, handler):
        self.path = _handoff_path(folder)
        self.handler = handler
        self.token = secrets.token_hex(16)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        port = self.sock.getsockname()[1]
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"port": port, "token": self.token, "pid": os.getpid()}, f)
        os.replace(tmp, self.path)
        threading.Thread(target=self._accept, daemon=True, name="handoff").start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.settimeout(10)
            buf = bytearray()
            while not buf.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_REQUEST:
                    return
            req = json.loads(buf)
            if not isinstance(req, dict) or not hmac.compare_digest(str(req.get("token", "")), self.token):
                return
            conn.settimeout(None)
            ok, msg = self.handler(req)
            conn.sendall((json.dumps({"ok": bool(ok), "msg": msg}) + "\n").encode())
        except Exception:  # noqa: BLE001  (a bad request just gets dropped)
            log.debug("hand-off request failed", exc_info=True)
        finally:
            conn.close()


# ---------------------------------------------------------------- the second copy's side
def hand_off(folder, request, timeout=60):
    """Give `request` to the running WinLink. None if it isn't running, else (ok, message)."""
    try:
        with open(_handoff_path(folder), encoding="utf-8") as f:
            info = json.load(f)
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=3) as s:
            s.sendall((json.dumps({**request, "token": info["token"]}) + "\n").encode())
            s.settimeout(timeout)
            buf = bytearray()
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf)
        if not isinstance(reply, dict) or "ok" not in reply:
            return None                  # something else took the port of a WinLink that has quit
        return bool(reply["ok"]), str(reply.get("msg", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def message_box(text, title="WinLink"):
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x40000)   # info icon, topmost
        return
    print(text, file=sys.stderr)
    if sys.platform != "darwin":         # Linux: started from a file manager, there's no terminal to see it
        import shutil
        for cmd in (["notify-send", "--app-name=WinLink", "--icon=winlink", title, text],
                    ["zenity", "--info", f"--title={title}", f"--text={text}"],
                    ["kdialog", "--title", title, "--msgbox", text]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, timeout=60)
                    return
                except (OSError, subprocess.SubprocessError):
                    continue


# ---------------------------------------------------------------- the Send to shortcuts
def sendto_folder():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "SendTo")


def safe_name(name):
    bad = '<>:"/\\|?*'
    name = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    return name[:60] or "device"


def launch_command():
    """(program, arguments before --send) that start WinLink."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "link.py")
    return (pythonw if os.path.exists(pythonw) else exe), f'"{script}" '


def _make_link_com(path, target, args, icon, description):
    """Create a .lnk with the Windows shell's own IShellLink, called through ctypes (no PowerShell)."""
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_void_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    ole32 = ctypes.OleDLL("ole32")

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(g))
        return g

    def method(obj, index, *argtypes):
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        fn = WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtable[index])   # raises OSError on a failed HRESULT
        return lambda *a: fn(obj, *a)

    try:
        ole32.CoInitializeEx(None, 2)            # apartment-threaded; harmless if this thread already did it
        uninit = True
    except OSError:
        uninit = False
    link = c_void_p()
    persist = c_void_p()
    try:
        ole32.CoCreateInstance(byref(guid("{00021401-0000-0000-C000-000000000046}")), None, 1,   # CLSID_ShellLink
                               byref(guid("{000214F9-0000-0000-C000-000000000046}")), byref(link))   # IShellLinkW
        method(link, 20, wintypes.LPCWSTR)(target)                     # SetPath
        method(link, 11, wintypes.LPCWSTR)(args)                       # SetArguments
        method(link, 17, wintypes.LPCWSTR, c_int)(icon, 0)             # SetIconLocation
        method(link, 7, wintypes.LPCWSTR)(description)                 # SetDescription
        method(link, 0, POINTER(GUID), POINTER(c_void_p))(            # QueryInterface(IPersistFile)
            byref(guid("{0000010B-0000-0000-C000-000000000046}")), byref(persist))
        method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(path, True)   # Save
    finally:
        for obj in (persist, link):
            if obj.value:
                method(obj, 2)()                                       # Release
        if uninit:
            ole32.CoUninitialize()


def _make_links_powershell(links):
    """Fallback: the same shortcuts through PowerShell's WScript.Shell."""
    def ps(s):
        return "'" + s.replace("'", "''") + "'"
    lines = ["$ErrorActionPreference = 'Stop'", "$sh = New-Object -ComObject WScript.Shell"]
    for path, target, args, icon, description in links:
        lines += [f"$l = $sh.CreateShortcut({ps(path)})", f"$l.TargetPath = {ps(target)}", f"$l.Arguments = {ps(args)}",
                  f"$l.IconLocation = {ps(icon + ',0')}", f"$l.Description = {ps(description)}", "$l.Save()"]
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", base64.b64encode("\n".join(lines).encode("utf-16-le")).decode()],
        capture_output=True, timeout=30, creationflags=0x08000000)   # no console window
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or b"").decode(errors="replace").strip()[:300] or f"exit {r.returncode}")


class SendToMenu:
    """Keeps the Send to shortcuts in line with the paired devices. Windows only; does nothing elsewhere."""

    def __init__(self):
        self.lock = threading.Lock()
        self.current = None              # {file name: device id} last written
        self.status = ""                 # what happened last time, for Settings

    def wanted(self, devices):
        """devices: [(id, name)] -> {file name: id}."""
        out, seen = {}, set()
        for pid, name in sorted(devices, key=lambda d: d[1].lower()):
            base = safe_name(name)
            fname = f"{PREFIX}{base}.lnk"
            n = 2
            while fname.lower() in seen:                 # two devices with the same name
                fname = f"{PREFIX}{base} ({n}).lnk"
                n += 1
            seen.add(fname.lower())
            out[fname] = pid
        if len(devices) > 1:
            out[f"{PREFIX}All devices.lnk"] = "all"
        return out

    def sync(self, devices, enabled=True):
        """Bring the folder in line. Returns True if anything changed (so the window can update)."""
        if not IS_WIN:
            return False
        want = self.wanted(devices) if enabled else {}
        with self.lock:
            folder = sendto_folder()
            try:
                os.makedirs(folder, exist_ok=True)
                have = {f for f in os.listdir(folder) if f.startswith(PREFIX) and f.lower().endswith(".lnk")}
            except OSError as e:
                self.status = f"Couldn't open the Send to folder ({folder}): {e}"
                log.warning("Send to menu: %s", self.status)
                return True
            program, pre = launch_command()
            key = (want, program)                         # WinLink moved (or rebuilt elsewhere): rewrite them
            if key == self.current and set(want) <= have:
                return False                              # nothing to do (and nobody deleted them)
            for f in have - set(want):
                try:
                    os.remove(os.path.join(folder, f))
                except OSError:
                    pass
            if not want:
                self.current = key
                self.status = "Off." if not enabled else "Pair a device and it appears here."
                log.info("Send to menu: %s", "removed" if not enabled else "no paired devices yet")
                return True
            links = [(os.path.join(folder, fname), program, f"{pre}--send {pid}", program, "Send with WinLink")
                     for fname, pid in want.items()]
            try:
                for link in links:
                    _make_link_com(*link)
                how = "shell"
            except Exception as e:  # noqa: BLE001
                log.warning("Send to menu: the shell couldn't make the shortcuts (%s); trying PowerShell", e)
                try:
                    _make_links_powershell(links)
                    how = "PowerShell"
                except Exception as e2:  # noqa: BLE001
                    self.current = None
                    self.status = f"Couldn't add the entries: {e2}"
                    log.error("Send to menu: %s", self.status)
                    return True
            missing = [f for f in want if not os.path.exists(os.path.join(folder, f))]
            if missing:
                self.current = None
                self.status = f"Windows didn't save {', '.join(missing)} in {folder}."
                log.error("Send to menu: %s", self.status)
                return True
            self.current = key
            names = [f[len(PREFIX):-4] for f in sorted(want)]
            self.status = f"In the menu: {', '.join(names)}."
            log.info("Send to menu: added %s (via %s) in %s", ", ".join(names), how, folder)
            return True
