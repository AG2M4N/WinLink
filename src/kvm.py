"""
Keyboard and mouse sharing, by running Deskflow's engine (deskflow-core, GPL-2.0, a separate program:
https://github.com/deskflow/deskflow) with everything set up by WinLink.

The computer whose keyboard and mouse you use is the *server*; the others are *clients*. WinLink:
  - finds an installed Deskflow (it isn't bundled),
  - gives each computer a TLS certificate and swaps fingerprints with its paired computers over
    WinLink's own encrypted link, so Deskflow trusts exactly those and nobody asks "trust this?",
  - writes Deskflow's settings, the screen arrangement and trusted lists into a private folder
    (WinLink's settings folder/kvm; Deskflow's own settings are left alone),
  - runs deskflow-core and reads its log for status.
Deskflow's clipboard sharing is off: WinLink already shares the clipboard (with images and history).
"""

import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
PORT = 24810            # not Deskflow's usual 24800, so a Deskflow you run yourself isn't disturbed
SIDES = ("left", "right", "up", "down")
STEP = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
GRID_LIMIT = 6          # positions -6..6 on each axis: plenty of desk


def links(positions):
    """positions: {screen name: (x, y)} on a grid (y grows downwards). Every two screens that touch get
    a pair of links, so the pointer can go between any neighbours, not only to the main computer."""
    at = {tuple(xy): n for n, xy in positions.items()}
    out = {n: [] for n in positions}
    for n, (x, y) in positions.items():
        for side, (dx, dy) in STEP.items():
            other = at.get((x + dx, y + dy))
            if other is not None:
                out[n].append((side, other))
    return out


def reachable(positions, start):
    """Screens the pointer can get to from `start` by crossing touching edges."""
    at = {tuple(xy): n for n, xy in positions.items()}
    seen, todo = {start}, [start]
    while todo:
        x, y = positions[todo.pop()]
        for dx, dy in STEP.values():
            n = at.get((x + dx, y + dy))
            if n is not None and n not in seen:
                seen.add(n)
                todo.append(n)
    return seen
DOWNLOAD_URL = "https://github.com/deskflow/deskflow/releases/latest"


def find_core():
    """deskflow-core from an installed Deskflow, or None."""
    env = os.environ.get("WINLINK_DESKFLOW")
    if env and os.path.exists(env):
        return env
    if IS_MAC:
        for app in ("/Applications/Deskflow.app", os.path.expanduser("~/Applications/Deskflow.app")):
            p = os.path.join(app, "Contents", "MacOS", "deskflow-core")
            if os.path.exists(p):
                return p
    elif IS_WIN:
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            p = os.path.join(root, "Deskflow", "deskflow-core.exe")
            if root and os.path.exists(p):
                return p
    else:
        p = shutil.which("deskflow-core")
        if p:
            return p
    return None


def screen_name(device_name, device_id):
    """A name Deskflow accepts (letters, digits, - _ .) that's still recognisable, and unique."""
    clean = re.sub(r"[^\w\-.]", "", re.sub(r"\s+", "_", device_name.strip()))
    clean = clean.strip("-_.")[:40] or "computer"
    return f"{clean}-{device_id[:4]}"


class Files:
    """Deskflow's files for WinLink, in one private folder."""

    def __init__(self, folder):
        self.folder = folder
        self.tls = os.path.join(folder, "tls")             # Deskflow looks next to its settings file
        self.cert = os.path.join(self.tls, "winlink.pem")
        self.settings = os.path.join(folder, "deskflow.conf")
        self.server_conf = os.path.join(folder, "server.conf")
        os.makedirs(self.tls, exist_ok=True)

    def fingerprint(self):
        """This computer's certificate fingerprint (sha256, hex), making the certificate if needed."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        if not os.path.exists(self.cert):
            self._make_cert()
        with open(self.cert, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        return cert.fingerprint(hashes.SHA256()).hex()

    def _make_cert(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WinLink keyboard and mouse")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        data = (cert.public_bytes(serialization.Encoding.PEM)
                + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
        fd = os.open(self.cert, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def trust(self, kind, fingerprints):
        """kind: "servers" (we're a client) or "clients" (we're the server). Only these are accepted."""
        path = os.path.join(self.tls, f"trusted-{kind}")
        with open(path, "w", encoding="ascii") as f:
            for fp in fingerprints:
                if re.fullmatch(r"[0-9a-f]{64}", fp or ""):
                    f.write(f"v2:sha256:{fp}\n")

    def write_settings(self, name, remote_host="", screens=()):
        """screens: every computer in the arrangement (the server's view). Deskflow 1.27+ lists them
        here as [computer_<name>] groups; the layout file only links them."""
        lines = [
            "[core]", f"computerName={name}", f"port={PORT}", "preventSleep=false", "",
            "[client]", f"remoteHost={remote_host}", "",
            "[security]", "tlsEnabled=true", "checkPeerFingerprints=true", f"certificate={_ini(self.cert)}", "",
            "[server]", "externalConfig=true", f"externalConfigFile={_ini(self.server_conf)}",
            "enableClipboard=false", "",
            "[log]", "level=DEBUG", "toFile=false", "",          # connection news is only logged at DEBUG
        ]
        for n in screens:
            lines += [f"[computer_{n}]", f"name={n}", ""]
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def write_layout(self, positions):
        """positions: {screen name: (x, y)} on a grid; touching screens are linked both ways."""
        out = ["section: links"]
        for n, near in links(positions).items():
            out.append(f"\t{n}:")
            out += [f"\t\t{side} = {other}" for side, other in near]
        out += ["end", ""]
        with open(self.server_conf, "w", encoding="utf-8") as f:
            f.write("\n".join(out))


def _ini(path):
    return path.replace("\\", "/")          # Qt reads forward slashes on every system


class Engine:
    """One running deskflow-core (server or client). on_line(text) gets its log lines."""

    def __init__(self, core, files, on_event):
        self.core, self.files, self.on_event = core, files, on_event
        self.proc = None
        self.mode = None
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode):
        self.stop()
        with self.lock:
            self.mode = mode
            flags = 0x08000000 if IS_WIN else 0                 # no console window
            self.proc = subprocess.Popen([self.core, mode, "--settings", self.files.settings, "--new-instance"],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         text=True, errors="replace", creationflags=flags)
            proc = self.proc
        threading.Thread(target=self._read, args=(proc,), daemon=True, name=f"deskflow {mode}").start()
        log.info("keyboard and mouse: deskflow-core %s started", mode)

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line:
                log.debug("deskflow: %s", line)
                self.on_event(line)
        code = proc.wait()
        if proc is self.proc:
            self.on_event(f"__exit__ {code}")

    def stop(self):
        with self.lock:
            proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()


def summarise(line):
    """A Deskflow log line -> a short status for the window, or None if it isn't interesting."""
    low = line.lower()
    m = re.search(r'client "([^"]+)" has connected', line)
    if m:
        return ("connected", m.group(1))
    m = re.search(r'client "([^"]+)" has disconnected', line)
    if m:
        return ("disconnected", m.group(1))
    if "connected to server" in low:
        return ("joined", "")
    if "started server" in low or "server status: started" in low:
        return ("listening", "")
    if "fingerprint" in low and ("does not match" in low or "failed to verify" in low):
        return ("untrusted", "")
    if "accessibility" in low or "assistive" in low or ("not trusted" in low and "process" in low):
        return ("permission", "")
    if "connection refused" in low or "failed to connect" in low or "connection timed out" in low:
        return ("unreachable", "")
    if "address already in use" in low or "cannot listen" in low:
        return ("port", "")
    return None


def wait_listening(port=PORT, timeout=8.0):
    import socket
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------- WinLink's side
def mac_accessibility_ok():
    """Deskflow needs Accessibility on a Mac (to see and send keystrokes). None = can't tell."""
    if not IS_MAC:
        return True
    try:
        import ctypes
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return None


def _route_addr(peer):
    """An address Deskflow can reach the computer on: same Wi-Fi first, IPv4 first. WinLink's
    built-in Tailscale (ts:...) runs inside WinLink, so Deskflow can't use it; the Tailscale app's
    100.x addresses work."""
    addrs = [a for a in peer.addresses() if not a.startswith("ts:") and "%" not in a]
    v4 = [a for a in addrs if ":" not in a]
    return (v4 or addrs or [None])[0]


class Sharing:
    """Keyboard and mouse sharing between paired computers.

    On the computer you're at ("share" on): every computer you placed next to it is sent a "join"
    with this computer's fingerprint and its screen name; it answers with its own fingerprint and
    starts Deskflow's client. When the set of computers changes, Deskflow's server is restarted with
    the new arrangement. Messages travel as {"type": "kvm", "op": ...} over the control link."""

    def __init__(self, node, settings, folder, on_change):
        self.node, self.settings, self.on_change = node, settings, on_change
        conf = settings.data.setdefault("kvm", {"share": False, "allow": True})
        grid = conf.setdefault("grid", {})
        for pid, side in (conf.pop("layout", None) or {}).items():   # older: left/right/up/down of here
            if side in STEP and pid not in grid:
                grid[pid] = list(STEP[side])
        self.core = find_core()
        self.files = Files(folder)
        self.engine = Engine(self.core, self.files, self._on_line) if self.core else None
        self.role = None             # "server" | "client" | None
        self.server_peer = None      # client: whose keyboard and mouse we follow
        self.clients = {}            # server: peer id -> {"fp", "screen", "state", "text"}
        self.status, self.problem = "", ""
        self.applied = None
        self.lock = threading.RLock()
        self.retries = 0

    # ---- settings
    @property
    def conf(self):
        return self.settings.data["kvm"]

    def _save(self):
        self.settings["kvm"] = self.conf

    def me(self):
        return screen_name(self.node.name, self.node.id)

    def recheck(self):
        """Deskflow installed since? (the Get Deskflow button, or a new launch)."""
        if self.core is None:
            self.core = find_core()
            if self.core:
                self.engine = Engine(self.core, self.files, self._on_line)
        self.on_change()

    # ---- the computer you're at
    def set_share(self, on):
        with self.lock:
            self.conf["share"] = bool(on)
            self._save()
            if on:
                self.stop_client("this computer is now the one sharing its keyboard and mouse")
                self.refresh()
            else:
                self._stop_server()
        self.on_change()

    def set_side(self, pid, side):
        """Place a computer right next to this one (left/right/up/down), or take it out ("")."""
        grid = dict(self.conf.get("grid", {}))
        if side in STEP:
            grid[pid] = list(STEP[side])
        else:
            grid.pop(pid, None)
        self.set_grid(grid)

    def set_grid(self, grid):
        """The whole arrangement from the window: {device id: [x, y]}, this computer at [0, 0]."""
        clean = {}
        for pid, xy in (grid or {}).items():
            try:
                x, y = int(xy[0]), int(xy[1])
            except (TypeError, ValueError, IndexError):
                continue
            if (x, y) != (0, 0) and abs(x) <= GRID_LIMIT and abs(y) <= GRID_LIMIT and (x, y) not in clean.values() \
                    and isinstance(pid, str) and len(pid) < 80:
                clean[pid] = (x, y)
        with self.lock:
            old = self.conf.get("grid", {})
            for pid in set(old) - set(clean):            # taken out: stop following
                peer = self.node.peers.get(pid)
                if peer is not None and peer.control is not None:
                    self._send(peer, {"op": "leave"})
                self.clients.pop(pid, None)
            self.conf["grid"] = {pid: list(xy) for pid, xy in clean.items()}
            self._save()
            self.refresh()
        self.on_change()

    def refresh(self):
        """Called when devices connect or disconnect: invite computers placed next to this one."""
        with self.lock:
            if not self.conf.get("share") or self.engine is None:
                return
            for pid in self.conf.get("grid", {}):
                peer = self.node.peers.get(pid)
                if peer is None or peer.os not in ("mac", "windows", "linux"):
                    continue
                c = self.clients.setdefault(pid, {"fp": "", "screen": screen_name(peer.name, pid),
                                                  "state": "waiting", "text": ""})
                if peer.control is not None and not c.get("asked"):
                    c["asked"] = True
                    self._send(peer, {"op": "join", "fp": self.files.fingerprint(), "screen": c["screen"],
                                      "server": self.me(), "port": PORT})
                elif peer.control is None:
                    c["asked"] = False                  # ask again when it's back
            self._apply_server()

    def _apply_server(self):
        grid = self.conf.get("grid", {})
        me = self.me()
        answered = {pid: c for pid, c in self.clients.items() if c.get("fp") and pid in grid}
        positions = {me: (0, 0), **{c["screen"]: tuple(grid[pid]) for pid, c in answered.items()}}
        near = reachable(positions, me)                  # a screen nobody touches can't be reached
        ready = [(pid, c) for pid, c in answered.items() if c["screen"] in near]
        positions = {n: xy for n, xy in positions.items() if n in near}
        if not ready:                       # nobody to share with yet: just don't run the engine
            if self.role == "server" and self.engine is not None:
                self.engine.stop()
                self.role = None
            self.applied = None
            self.status = ("Place a computer next to this one to start." if not grid else
                           "Waiting for the other computers to answer.")
            return
        key = tuple(sorted((n, xy) for n, xy in positions.items())) + tuple(sorted(c["fp"] for _, c in ready))
        if key == self.applied and self.engine.running:
            return
        self.files.trust("clients", [c["fp"] for _, c in ready])
        self.files.write_settings(me, screens=list(positions))
        self.files.write_layout(positions)
        self.applied = key
        self.role = "server"
        self.engine.start("server")
        self.status = "Move the pointer off this screen's edge to reach the computers placed below."

    def _stop_server(self):
        with self.lock:
            for pid in list(self.clients):
                peer = self.node.peers.get(pid)
                if peer is not None and peer.control is not None:
                    self._send(peer, {"op": "leave"})
            self.clients.clear()
            self.applied = None
            if self.role == "server" and self.engine is not None:
                self.engine.stop()
                self.role = None
            self.status = ""

    # ---- messages from other computers
    def on_message(self, peer, msg):
        op = msg.get("op")
        with self.lock:
            if op == "join":
                self._join(peer, msg)
            elif op == "leave" and self.server_peer is peer:
                self.stop_client(f"{peer.name} stopped sharing its keyboard and mouse")
            elif op == "hello" and peer.id in self.clients:
                fp = str(msg.get("fp", ""))
                if re.fullmatch(r"[0-9a-f]{64}", fp):
                    c = self.clients[peer.id]
                    c.update(fp=fp, state="starting", text="")
                    self._apply_server()
            elif op == "no" and peer.id in self.clients:
                self.clients[peer.id].update(state="no", text=str(msg.get("reason", ""))[:160])
        self.on_change()

    def _join(self, peer, msg):
        fp, screen = str(msg.get("fp", "")), str(msg.get("screen", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", fp) or not re.fullmatch(r"[\w.\-]{1,60}", screen):
            return
        why = None
        if not self.conf.get("allow", True):
            why = f"{self.node.name} doesn't accept keyboard and mouse from other computers (Settings)"
        elif self.engine is None:
            why = f"Deskflow isn't installed on {self.node.name}"
        elif self.conf.get("share"):
            why = f"{self.node.name} is sharing its own keyboard and mouse"
        addr = _route_addr(peer)
        if why is None and addr is None:
            why = f"{self.node.name} only reaches {peer.name} through WinLink's built-in Tailscale; use the same Wi-Fi"
        if why:
            self._send(peer, {"op": "no", "reason": why})
            return
        self.files.trust("servers", [fp])
        self.files.write_settings(screen, remote_host=addr)
        self.server_peer, self.role = peer, "client"
        self.engine.start("client")
        self.status = f"Following {peer.name}'s keyboard and mouse."
        self._send(peer, {"op": "hello", "fp": self.files.fingerprint()})

    def stop_client(self, why=""):
        with self.lock:
            if self.role == "client" and self.engine is not None:
                self.engine.stop()
                log.info("keyboard and mouse: client stopped%s", f" ({why})" if why else "")
            if self.role == "client":
                self.role, self.server_peer, self.status = None, None, why
        self.on_change()

    def set_allow(self, on):
        self.conf["allow"] = bool(on)
        self._save()
        if not on and self.role == "client":
            peer = self.server_peer
            self.stop_client("turned off here")
            if peer is not None and peer.control is not None:
                self._send(peer, {"op": "no", "reason": f"turned off on {self.node.name}"})
        self.on_change()

    def _send(self, peer, body):
        try:
            peer.control.send({"type": "kvm", **body})
        except (OSError, ValueError, AttributeError):
            pass

    # ---- what Deskflow says
    def _on_line(self, line):
        if line.startswith("__exit__"):
            code = line.split()[-1]
            with self.lock:
                wanted = self.role
            if wanted and code not in ("0", "-15", "15"):
                self.problem = f"Deskflow stopped unexpectedly (code {code}). See Activity."
                log.warning("keyboard and mouse: deskflow-core exited with %s", code)
                if self.retries < 3:
                    self.retries += 1
                    threading.Timer(3, self._restart).start()
            self.on_change()
            return
        s = summarise(line)
        if not s:
            return
        kind, who = s
        with self.lock:
            if kind in ("connected", "disconnected") and self.role == "server":
                for c in self.clients.values():
                    if c["screen"] == who:
                        c["state"] = "on" if kind == "connected" else "waiting"
                        if kind == "connected":
                            self.retries = 0
                            self.problem = ""
            elif kind == "joined" and self.role == "client":
                self.status = f"Following {self.server_peer.name if self.server_peer else 'the other computer'}'s keyboard and mouse."
                self.problem = ""
            elif kind == "permission":
                self.problem = "Allow WinLink under Privacy & Security › Accessibility, then turn this off and on."
            elif kind == "port":
                self.problem = f"Port {PORT} is in use (is another Deskflow running?)."
            elif kind == "untrusted":
                self.problem = "The other computer's certificate didn't match. Turn sharing off and on to swap them again."
        self.on_change()

    def _restart(self):
        with self.lock:
            if self.role == "server":
                self.applied = None
                self._apply_server()
            elif self.role == "client" and self.engine is not None:
                self.engine.start("client")

    def stop_all(self):
        if self.engine is not None:
            self.engine.stop()

    # ---- for the window
    def state(self, connected):
        ax = mac_accessibility_ok()
        grid = self.conf.get("grid", {})
        positions = {"__me__": (0, 0), **{pid: tuple(xy) for pid, xy in grid.items()}}
        near = reachable(positions, "__me__")
        computers = []
        for p in connected:
            if p.os not in ("mac", "windows", "linux"):
                continue
            c = self.clients.get(p.id, {})
            text = {"on": "Connected", "starting": "Starting…",
                    "waiting": "Waiting for it to answer", "no": c.get("text", "")}.get(c.get("state"), "")
            pos = grid.get(p.id)
            if pos is not None and p.id not in near:
                text = "Not touching any screen: the pointer can't get there"
            computers.append({"id": p.id, "name": p.name, "os": p.os, "pos": pos, "text": text,
                              "on": c.get("state") == "on", "reachable": pos is None or p.id in near})
        return {
            "available": self.core is not None,
            "download": DOWNLOAD_URL,
            "share": bool(self.conf.get("share")),
            "allow": bool(self.conf.get("allow", True)),
            "role": self.role,
            "me": {"name": self.node.name, "os": "mac" if IS_MAC else "windows" if IS_WIN else "linux"},
            "following": self.server_peer.name if self.role == "client" and self.server_peer else None,
            "computers": computers,
            "status": self.status,
            "problem": self.problem,
            "needs_accessibility": IS_MAC and ax is False,
        }
