"""
WinLink's built-in Tailscale: starts the bundled linkts helper (see helper/main.go)
and talks to it over a private localhost port.
"""

import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading

IS_WIN = os.name == "nt"


def helper_path():
    if os.environ.get("LINKTS_HELPER"):
        return os.environ["LINKTS_HELPER"]
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    name = f"linkts-windows-{arch}.exe" if IS_WIN else f"linkts-mac-{arch}" if sys.platform == "darwin" \
        else f"linkts-linux-{arch}"
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, "_MEIPASS", None), os.path.join(here, "..", "bin"), here):
        if base and os.path.exists(os.path.join(base, name)):
            return os.path.join(base, name)
    return None


def hostname_for(device_name):
    slug = re.sub(r"[’'`]", "", device_name.lower())           # "Sam’s" -> "sams"
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return ("winlink-" + slug)[:60] or "winlink"


KEYRING_ATTRS = ["application", "winlink", "purpose", "tailscale-state"]


def linux_state_key(state_dir):
    """The key the helper encrypts its stored sign-in with (Linux). Kept in the desktop keyring
    (GNOME Keyring, KWallet...) through secret-tool; without one, in a file only you can read,
    outside the folder it protects."""
    import shutil
    tool = shutil.which("secret-tool")
    if tool:
        try:
            r = subprocess.run([tool, "lookup", *KEYRING_ATTRS], capture_output=True, text=True, timeout=10)
            key = r.stdout.strip()
            if re.fullmatch(r"[0-9a-f]{64}", key):
                return key
            key = secrets.token_hex(32)
            r = subprocess.run([tool, "store", "--label=WinLink Tailscale key", *KEYRING_ATTRS],
                               input=key, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return key
        except (OSError, subprocess.SubprocessError):
            pass
    path = os.path.join(os.path.dirname(os.path.abspath(state_dir)), "tailscale.key")
    try:
        with open(path, encoding="ascii") as f:
            key = f.read().strip()
        if re.fullmatch(r"[0-9a-f]{64}", key):
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key)
    return key


class Embedded:
    """One running helper. All methods are safe to call from worker threads."""

    def __init__(self, state_dir, hostname, forward_port, tailnet_port):
        self.args = ["-dir", state_dir, "-hostname", hostname,
                     "-forward", f"127.0.0.1:{forward_port}", "-port", str(tailnet_port)]
        self.token = secrets.token_hex(16)
        self.proc = None
        self.port = None
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None and self.port is not None

    def start(self):
        path = helper_path()
        if not path:
            raise FileNotFoundError("Tailscale helper not found in this build")
        os.makedirs(self.args[1], exist_ok=True)
        env = dict(os.environ, LINKTS_TOKEN=self.token)
        if sys.platform.startswith("linux"):
            env["LINKTS_KEY"] = linux_state_key(self.args[1])
        # Smaller tunnel packets (1180 + ~80 bytes of overhead < 1280, the size every IPv6 network
        # must carry). Mobile and IPv6-only links often drop bigger ones silently, which makes
        # small messages work but file transfers crawl.
        env.setdefault("TS_DEBUG_MTU", "1180")
        self.proc = subprocess.Popen([path, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True,
                                     creationflags=0x08000000 if IS_WIN else 0)
        line = self.proc.stdout.readline().split()   # "READY <port>" or "ERROR ..."
        if len(line) == 2 and line[0] == "READY":
            self.port = int(line[1])
        else:
            self.stop()
            raise OSError("Tailscale helper didn't start: " + " ".join(line))

    def stop(self):
        proc, self.proc, self.port = self.proc, None, None
        if proc is not None:
            try:
                proc.stdin.close()               # helper exits when this closes
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()

    def _request(self, command, timeout=10):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        sock.sendall(f"{self.token} {command}\n".encode())
        return sock

    def status(self):
        if not self.running:
            return {"state": "Stopped", "devices": []}
        try:
            with self._request("STATUS") as sock:
                return json.loads(sock.makefile("rb").readline())
        except (OSError, ValueError) as e:
            return {"state": "Error", "error": str(e), "devices": []}

    def login(self):
        with self._request("LOGIN") as sock:
            return sock.makefile("rb").readline().startswith(b"OK")

    def logout(self):
        """Sign out and erase this device's stored Tailscale identity."""
        with self._request("LOGOUT", timeout=20) as sock:
            return sock.makefile("rb").readline().startswith(b"OK")

    def dial(self, host, port, timeout=8):
        """A socket connected to host:port on the tailnet (through the helper)."""
        if not self.running:
            raise OSError("Tailscale is off")
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        sock = self._request(f"DIAL {target}", timeout=timeout)
        reply = b""
        while not reply.endswith(b"\n"):          # read the reply byte by byte: data follows it
            b = sock.recv(1)
            if not b:
                break
            reply += b
        if not reply.startswith(b"OK"):
            sock.close()
            raise OSError(reply.decode(errors="replace").strip() or "Tailscale dial failed")
        sock.settimeout(None)
        return sock

    def _held(self, command):
        """A command that lasts as long as its control connection: (socket, reply words)."""
        if not self.running:
            raise OSError("Tailscale is off")
        sock = self._request(command, timeout=10)
        reply = b""
        while not reply.endswith(b"\n"):
            b = sock.recv(1)
            if not b:
                break
            reply += b
        words = reply.decode(errors="replace").split()
        if not words or words[0] != "OK":
            sock.close()
            raise OSError(" ".join(words[1:]) or f"Tailscale helper refused {command.split()[0]}")
        sock.settimeout(None)
        return sock, words[1:]

    def tunnel(self, host, port):
        """127.0.0.1:<local port> leading to host:port on the tailnet, carried by the helper itself.
        Returns (handle, local port); closing the handle ends it."""
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        sock, words = self._held(f"TUNNEL {target}")
        return sock, int(words[0])

    def expose(self, port):
        """Tailnet connections to `port` on this device reach 127.0.0.1:port. Returns a handle;
        closing it ends it."""
        return self._held(f"EXPOSE {int(port)}")[0]
