"""
Windows: run Deskflow's engine with administrator rights, so keyboard and mouse sharing keeps working
over Task Manager, installers and other "Run as administrator" windows. (Windows doesn't let a normal
program send input to those: the pointer seemed to freeze while one was in front.)

How it stays safe:
  - It only happens when the person turns it on, and Windows asks them (the usual UAC prompt) each time
    WinLink starts it. Nothing elevated is left installed or running when WinLink isn't.
  - The elevated helper is WinLink itself (`WinLink.exe --kvm-helper <port> <token>`). It *connects
    out* to WinLink on 127.0.0.1 and proves itself with the one-time token; it never listens, so no
    other program can talk to it (Deskflow's own service had exactly that hole: CVE-2026-41477).
  - It only starts or stops deskflow-core, in "server" or "client" mode, with WinLink's own settings
    file. The program and its settings are worked out by the helper itself, never taken from messages.
  - When WinLink's side of the connection closes (WinLink quits or crashes), it stops Deskflow and exits.

Not covered: the UAC prompt itself and the sign-in / lock screens run on Windows' secure desktop, which
only a SYSTEM service can reach.

Messages are JSON lines. WinLink -> helper: {"op": "start", "mode": "server"|"client"} / {"op": "stop"}.
Helper -> WinLink: {"hello": token} first, then {"line": "<deskflow log line>"} / {"exit": code}.
"""

import ctypes
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading

log = logging.getLogger("winlink")
WAIT_FOR_YES = 120          # seconds the UAC prompt may stay up
NO_WINDOW = 0x08000000


def _send(sock, obj, lock):
    data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
    with lock:
        sock.sendall(data)


# ---------------------------------------------------------------- WinLink's side (not elevated)
class Declined(OSError):
    """The person said No to Windows' administrator prompt (or it couldn't be shown)."""


class Link:
    """WinLink's end: starts the helper (one UAC prompt), then drives it. on_line(text) gets Deskflow's
    log lines, and "__exit__ <code>" when it stops, like Engine's own reader."""

    def __init__(self, on_line):
        self.on_line = on_line
        self.sock = None
        self.lock = threading.Lock()
        self.running = False           # deskflow-core is running in the helper
        self.run = 0                   # which start the running/exit news belongs to
        self.opening = threading.Lock()   # one prompt at a time; a start waits for one under way

    @property
    def up(self):
        return self.sock is not None

    def open(self):
        """Start the helper and wait for it to connect back. Raises Declined or OSError."""
        with self.opening:
            if self.sock is None:
                self._open()

    def _open(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        token = secrets.token_hex(24)
        port = srv.getsockname()[1]
        try:
            _run_as_admin(port, token)
            srv.settimeout(WAIT_FOR_YES)
            while True:
                try:
                    sock, _addr = srv.accept()
                except socket.timeout:
                    raise Declined("Windows' administrator prompt wasn't answered") from None
                sock.settimeout(10)
                try:
                    hello = json.loads(_readline(sock) or b"{}")
                except (OSError, ValueError):
                    sock.close()
                    continue
                if isinstance(hello, dict) and secrets.compare_digest(str(hello.get("hello", "")), token):
                    sock.settimeout(None)
                    break
                sock.close()                          # not our helper: ignore it
        finally:
            srv.close()
        self.sock = sock
        threading.Thread(target=self._read, args=(sock,), daemon=True, name="kvm elevated").start()
        log.info("keyboard and mouse: running Deskflow with administrator rights")

    def _read(self, sock):
        buf = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "line" in msg:
                        self.on_line(str(msg["line"]))
                    elif "exit" in msg and msg.get("run") == self.run:
                        self.running = False
                        self.on_line(f"__exit__ {msg['exit']}")
        except OSError:
            pass
        finally:
            was = self.running
            self.running = False
            if self.sock is sock:
                self.sock = None
            if was:
                self.on_line("__exit__ helper-gone")

    def start(self, mode):
        self.open()
        self.run += 1
        self.running = True
        _send(self.sock, {"op": "start", "mode": mode, "run": self.run}, self.lock)

    def stop(self):
        self.run += 1                  # news of the run being stopped on purpose isn't "it stopped"
        if self.sock is not None and self.running:
            self.running = False
            try:
                _send(self.sock, {"op": "stop"}, self.lock)
            except OSError:
                pass

    def close(self):
        sock, self.sock = self.sock, None
        self.running = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _readline(sock, limit=4096):
    out = b""
    while not out.endswith(b"\n"):
        c = sock.recv(1)
        if not c:
            break
        out += c
        if len(out) > limit:
            raise ValueError("line too long")
    return out.strip()


def _helper_command():
    """How to start WinLink itself again: the program, and the arguments before ours."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    exe = sys.executable
    if exe.lower().endswith("python.exe"):                      # no console window for the helper
        quiet = exe[:-len("python.exe")] + "pythonw.exe"
        exe = quiet if os.path.exists(quiet) else exe
    return exe, [os.path.join(os.path.dirname(os.path.abspath(__file__)), "link.py")]


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]


def _run_as_admin(port, token):
    exe, pre = _helper_command()
    params = subprocess.list2cmdline(pre + ["--kvm-helper", str(port), token])
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000400 | 0x00000040     # SEE_MASK_FLAG_NO_UI | SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", exe, params, 0
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.GetLastError()
        if err == 1223:                          # ERROR_CANCELLED: the person said No
            raise Declined("you said No to Windows' administrator prompt")
        raise OSError(f"couldn't ask for administrator rights (error {err})")
    if info.hProcess:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))


# ---------------------------------------------------------------- the helper (elevated)
def helper_main(port, token):
    """`--kvm-helper <port> <token>`: run deskflow-core for WinLink until WinLink goes away."""
    import kvm
    import net
    files = kvm.Files(os.path.join(net.config_dir(), "kvm"))
    core = kvm.find_core()
    sock = socket.create_connection(("127.0.0.1", int(port)), timeout=10)
    sock.settimeout(None)
    lock = threading.Lock()
    _send(sock, {"hello": token}, lock)
    state = {"proc": None, "run": 0}

    def pump(proc, run):
        for line in proc.stdout:
            line = line.strip()
            if line:
                try:
                    _send(sock, {"line": line}, lock)
                except OSError:
                    break
        code = proc.wait()
        try:
            _send(sock, {"exit": code, "run": run}, lock)
        except OSError:
            pass

    def stop():
        proc, state["proc"] = state["proc"], None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()

    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("op") == "stop":
                    stop()
                elif msg.get("op") == "start" and msg.get("mode") in ("server", "client") and core:
                    stop()
                    run = msg.get("run") if isinstance(msg.get("run"), int) else 0
                    exe = kvm.portable_core(core, os.path.join(files.folder, "engine"))
                    proc = subprocess.Popen([exe, msg["mode"], "--settings", files.settings, "--new-instance"],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, text=True, errors="replace",
                                            creationflags=NO_WINDOW)
                    state["proc"], state["run"] = proc, run
                    threading.Thread(target=pump, args=(proc, run), daemon=True).start()
    except OSError:
        pass
    finally:
        stop()
        try:
            sock.close()
        except OSError:
            pass
