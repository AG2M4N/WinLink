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
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
PORT = int(os.environ.get("LINK_KVM_PORT", 24810))   # not Deskflow's usual 24800: yours isn't disturbed
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


MIN_VERSION = (1, 27)   # the settings layout WinLink writes, and --new-instance


def bundled_core():
    """The engine that ships inside WinLink (built from Deskflow's source by WinLink's release
    workflow), or None (Linux, or a build from source without it)."""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    if IS_MAC:
        places = [os.path.join(exe_dir, "..", "Library", "Deskflow.app", "Contents", "MacOS", "deskflow-core"),
                  os.path.join(here, "..", "bin", "Deskflow.app", "Contents", "MacOS", "deskflow-core")]
    elif IS_WIN:
        places = [os.path.join(exe_dir, "deskflow", "deskflow-core.exe"),
                  os.path.join(here, "..", "bin", "deskflow-windows", "deskflow-core.exe")]
    else:
        places = []
    for p in places:
        if os.path.exists(p):
            return os.path.normpath(p)
    return None


def find_core():
    """deskflow-core: WinLink's own, else an installed Deskflow, or None."""
    env = os.environ.get("WINLINK_DESKFLOW")
    if env and os.path.exists(env):
        return env
    own = bundled_core()
    if own:
        return own
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


def core_version(core):
    """(major, minor, patch) of a deskflow-core, or None if it can't tell."""
    try:
        out = subprocess.run([core, "--version"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if IS_WIN else 0).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def screen_name(device_name, device_id):
    """A name Deskflow accepts (letters, digits, - _ .) that's still recognisable, and unique."""
    clean = re.sub(r"[^\w\-.]", "", re.sub(r"\s+", "_", device_name.strip()))
    clean = clean.strip("-_.")[:40] or "computer"
    return f"{clean}-{device_id[:4]}"


def _old_copies(folder):
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if ".old-" in name:
                yield os.path.join(root, name)


def _stop_strays(folder):
    """Windows: end deskflow-core processes running from WinLink's own copy that no WinLink owns any
    more (left behind when WinLink was closed abruptly, e.g. by an update). They hold Deskflow's port
    and lock the files. Only this folder's: a Deskflow the person runs themselves is left alone."""
    if not IS_WIN:
        return
    target = os.path.normcase(os.path.abspath(folder)) + os.sep
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Process deskflow-core -ErrorAction SilentlyContinue | ForEach-Object { \"$($_.Id)`t$($_.Path)\" }"],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        pid, _, path = line.partition("\t")
        if pid.strip().isdigit() and path and os.path.normcase(os.path.abspath(path)).startswith(target):
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid.strip()], capture_output=True, timeout=10,
                               creationflags=0x08000000)
                log.info("keyboard and mouse: ended a leftover deskflow-core (%s)", pid.strip())
            except (OSError, subprocess.SubprocessError):
                pass


def portable_core(core, folder):
    """Windows: Deskflow reads its trusted lists from C:\\ProgramData\\Deskflow\\tls whatever --settings
    says, unless it runs in portable mode (a settings\\Deskflow.conf next to the program). So WinLink runs
    its own portable copy: the installed Deskflow folder mirrored into `folder` with hard links (no
    space used; a real copy if links can't be made), refreshed when Deskflow is updated."""
    src = os.path.dirname(core)
    _stop_strays(folder)
    for old in _old_copies(folder):              # set aside last time because they were in use
        try:
            os.remove(old)
        except OSError:
            pass
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if rel.split(os.sep)[0] == "settings":
            continue
        dst_root = os.path.normpath(os.path.join(folder, rel))
        os.makedirs(dst_root, exist_ok=True)
        for name in files:
            a, b = os.path.join(root, name), os.path.join(dst_root, name)
            if os.path.exists(b):
                sa, sb = os.stat(a), os.stat(b)
                if os.path.samefile(a, b) or (sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)):
                    continue
                try:
                    os.remove(b)
                except PermissionError:
                    # In use (a deskflow-core still running): Windows won't delete it but does let it
                    # be renamed. Put it aside; it's deleted next time. Failing here left keyboard and
                    # mouse sharing stuck on "Starting" after an update.
                    os.replace(b, f"{b}.old-{os.getpid()}-{int(time.time())}")
            try:
                os.link(a, b)
            except OSError:
                shutil.copy2(a, b)
    return os.path.join(folder, os.path.basename(core))


class Files:
    """Deskflow's files for WinLink, in one private folder."""

    def __init__(self, folder):
        self.folder = folder
        # Deskflow looks for tls/ next to its settings file; on Windows that file must be the portable
        # one (see portable_core)
        base = os.path.join(folder, "engine", "settings") if IS_WIN else folder
        self.tls = os.path.join(base, "tls")
        self.cert = os.path.join(self.tls, "winlink.pem")
        self.settings = os.path.join(base, "Deskflow.conf" if IS_WIN else "deskflow.conf")
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

    def write_settings(self, name, remote_host="", screens=(), port=PORT, swapped=()):
        """screens: every computer in the arrangement (the server's view). Deskflow 1.27+ lists them
        here as [computer_<name>] groups; the layout file only links them. swapped: the screens where
        Ctrl and Command/Windows trade places (a Mac and a PC), so Ctrl+C on one is Command+C on the other."""
        lines = [
            "[core]", f"computerName={name}", f"port={port}", "preventSleep=false", "",
            "[client]", f"remoteHost={remote_host}", "",
            "[security]", "tlsEnabled=true", "checkPeerFingerprints=true", f"certificate={_ini(self.cert)}", "",
            "[server]", "externalConfig=true", f"externalConfigFile={_ini(self.server_conf)}",
            "enableClipboard=false", "",
            "[log]", "level=DEBUG", "toFile=false", "",          # connection news is only logged at DEBUG
        ]
        for n in screens:
            lines += [f"[computer_{n}]", f"name={n}"]
            if n in swapped:                     # Deskflow calls Command (and the Windows key) "super"
                lines += ["modifierCtrl=super", "modifierSuper=ctrl"]
            lines.append("")
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def write_layout(self, positions):
        """positions: {screen name: (x, y)} on a grid; touching screens are linked both ways."""
        # Deskflow applies the [server] settings (enableClipboard=false among them) only when the layout
        # has an options section, even an empty one: without it, its clipboard sharing stays on.
        out = ["section: options", "end", "", "section: links"]
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
        self.elevated = False          # Windows: run it with administrator rights (kvm_elevated.py)
        self.link = None
        self.problem = ""              # why administrator rights aren't in use, if they were wanted

    @property
    def running(self):
        if self.link is not None and self.link.up and self.link.running:
            return True
        return self.proc is not None and self.proc.poll() is None

    def set_elevated(self, on):
        """Turn administrator rights on or off; takes effect at the next start."""
        self.elevated = bool(on) and IS_WIN
        if not self.elevated and self.link is not None:
            self.link.close()
            self.link = None
        self.problem = ""
        if self.elevated:              # ask now (Windows' prompt), so a start later needn't wait for it
            import kvm_elevated
            if self.link is None:
                self.link = kvm_elevated.Link(self.on_event)

            def ask(link=self.link):
                try:
                    link.open()
                except kvm_elevated.Declined as e:     # said No: don't ask again until switched on again
                    self.elevated = False
                    self.problem = f"Not over admin windows: {e}."
                    log.info("keyboard and mouse: %s", e)
                except OSError as e:   # the next start says why and carries on normally
                    log.info("keyboard and mouse: administrator rights: %s", e)
            t = threading.Thread(target=ask, daemon=True, name="kvm elevate")
            t.start()
            return t
        return None

    def _start_elevated(self, mode):
        """True if it started with administrator rights; False to start it normally instead."""
        import kvm_elevated
        if self.link is None:
            self.link = kvm_elevated.Link(self.on_event)
        try:
            self.link.start(mode)
        except kvm_elevated.Declined as e:
            self.problem = f"Not over admin windows: {e}."
            self.elevated = False              # don't ask again until it's switched on again
            log.info("keyboard and mouse: %s", e)
            return False
        except OSError as e:
            self.problem = f"Couldn't get administrator rights: {e}."
            log.warning("keyboard and mouse: administrator rights: %s", e)
            return False
        self.mode = mode
        self.problem = ""
        log.info("keyboard and mouse: deskflow-core %s started (as administrator)", mode)
        return True

    def start(self, mode):
        self.stop()
        if self.elevated and self._start_elevated(mode):
            return
        with self.lock:
            self.mode = mode
            flags = 0x08000000 if IS_WIN else 0                 # no console window
            core = self.core
            if IS_WIN:
                core = portable_core(core, os.path.join(self.files.folder, "engine"))
            self.proc = subprocess.Popen([core, mode, "--settings", self.files.settings, "--new-instance"],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         text=True, errors="replace", creationflags=flags)
            proc = self.proc
        threading.Thread(target=self._read, args=(proc,), daemon=True, name=f"deskflow {mode}").start()
        log.info("keyboard and mouse: deskflow-core %s started", mode)

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line:
                important = summarise(line) or re.search(r"\b(ERROR|WARNING|FATAL|CRIT)\b", line, re.I)
                log.log(logging.INFO if important else logging.DEBUG, "deskflow: %s", line)
                self.on_event(line)
        code = proc.wait()
        if proc is self.proc:
            self.on_event(f"__exit__ {code}")

    def stop(self):
        if self.link is not None:
            self.link.stop()
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


def pipe(sock, chan):
    """Copy both ways between a local socket and a WinLink Channel until either side closes."""
    def up():
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chan.sendall(data)
        except (OSError, ConnectionError):
            pass
        finally:
            for c in (chan, sock):
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except (OSError, ConnectionError):
                    pass
    t = threading.Thread(target=up, daemon=True, name="kvm tunnel")
    t.start()
    try:
        while True:
            data = chan.read1()
            if not data:
                break
            sock.sendall(data)
    except (OSError, ConnectionError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        t.join(5)
        sock.close()
        chan.close()


class Relay:
    """Deskflow's connection always travels over WinLink's own encrypted link, so it works wherever
    WinLink does: same Wi-Fi, the Tailscale app, WinLink's built-in Tailscale (which Deskflow can't
    use itself), and networks that only let one of the two computers connect to the other.

    On the computer that follows, Deskflow's client connects to 127.0.0.1:<port>. For each of its
    connections WinLink both dials the sharing computer (a "kvm" connection, then {"go": tid}) and
    asks it over the control link to dial back ({"op": "dialback", "tid"}; it opens a "kvm"
    connection with {"tid"} and waits for {"go"}). Whichever arrives first carries Deskflow; the
    sharing computer hands it to its Deskflow server. Deskflow's TLS and fingerprint check run end
    to end inside it."""

    WAIT = 15        # seconds to get through either way before giving the connection up

    def __init__(self, node, peer, ask_dialback):
        self.node, self.peer, self.ask = node, peer, ask_dialback
        self.pending = {}            # tid -> Deskflow's local socket, until one way gets through
        self.lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept, daemon=True, name="kvm relay").start()

    def _accept(self):
        while not self.closed:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            tid = secrets.token_hex(8)
            with self.lock:
                self.pending[tid] = sock
            threading.Thread(target=self._direct, args=(tid,), daemon=True, name="kvm relay").start()
            self.ask(tid)
            threading.Timer(self.WAIT, self._expire, args=(tid,)).start()

    def _claim(self, tid):
        with self.lock:
            return self.pending.pop(tid, None)

    def _expire(self, tid):
        sock = self._claim(tid)
        if sock is not None:
            log.info("keyboard and mouse: couldn't get through to %s either way", self.peer.name)
            sock.close()

    def _direct(self, tid):
        try:
            chan = self.node.dial(self.peer, "kvm", timeout=6)
        except Exception as e:  # noqa: BLE001  (offline, one-way network...: the dial-back may work)
            log.debug("keyboard and mouse: dialing %s failed: %s", self.peer.name, e)
            return
        self._use(tid, chan, "this computer dialed")

    def dialed_back(self, tid, chan):
        self._use(tid, chan, f"{self.peer.name} dialed back")

    def _use(self, tid, chan, how):
        sock = self._claim(tid)
        if sock is None:                 # the other way won, or it's too late
            chan.close()
            return
        try:
            chan.send_json({"go": tid})
        except (OSError, ConnectionError):
            chan.close()
            sock.close()
            return
        log.info("keyboard and mouse: connected to %s (%s, %s)", self.peer.name, how, getattr(chan, "route", "?"))
        chan.settimeout(None)
        pipe(sock, chan)

    def close(self):
        self.closed = True
        try:
            self.listener.close()
        except OSError:
            pass
        with self.lock:
            socks, self.pending = list(self.pending.values()), {}
        for sock in socks:
            sock.close()


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
        self.files = Files(folder)
        self.core, self.engine, self.outdated = None, None, None
        self._find()
        self.role = None             # "server" | "client" | None
        self.server_peer = None      # client: whose keyboard and mouse we follow
        self.relay = None            # client: Relay carrying Deskflow's connection (the fallback)
        self.tunnel = None           # client: the Tailscale helper's tunnel handle
        self.exposed = None          # server: the Tailscale helper's EXPOSE handle
        self.joining = None          # client: {"peer", "fp", "screen", "paths", "i", "joined"}
        self.path = None             # client: how Deskflow reaches the server ("direct", "tunnel", "relay")
        self.bad_paths = {}          # (peer id, path) -> when it last failed to connect
        self.rtt = {}                # peer id -> round trip in ms (measured over WinLink's link)
        self.clients = {}            # server: peer id -> {"fp", "screen", "state", "text", "path"}
        self.status, self.problem = "", ""
        self.applied = None
        self.lock = threading.RLock()
        self.retries = 0
        self.reswaps = []            # when certificates were last swapped again after a mismatch
        threading.Thread(target=self._measure, daemon=True, name="kvm delay").start()

    # ---- settings
    @property
    def conf(self):
        return self.settings.data["kvm"]

    def _save(self):
        self.settings["kvm"] = self.conf

    def me(self):
        return screen_name(self.node.name, self.node.id)

    def _find(self):
        core = find_core()
        self.outdated = None
        if core and core != bundled_core():               # ours is the right version already
            v = core_version(core)
            if v is not None and v[:2] < MIN_VERSION:
                self.outdated = ".".join(map(str, v))
                log.info("keyboard and mouse: Deskflow %s is too old (needs %d.%d+)", self.outdated, *MIN_VERSION)
                core = None
        self.core = core
        self.engine = Engine(core, self.files, self._on_line) if core else None
        if self.engine is not None and self.conf.get("elevated") and IS_WIN:
            self.engine.elevated = True        # asked for (Windows' prompt) the first time it starts

    def recheck(self):
        """Deskflow installed or updated since? (the Get Deskflow button, or a new launch)."""
        if self.core is None:
            self._find()
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

    def swaps(self, peer):
        """Ctrl and Command trade places between this computer and peer (one's a Mac, the other isn't)?"""
        return bool(self.conf.get("swap_keys", True)) and peer is not None and (peer.os == "mac") != IS_MAC

    def set_elevated(self, on):
        """Windows: run Deskflow with administrator rights (works over Task Manager and other admin
        windows). Asks with Windows' prompt now, then restarts Deskflow with them."""
        with self.lock:
            self.conf["elevated"] = bool(on) and IS_WIN
            self._save()
            asking = self.engine.set_elevated(self.conf["elevated"]) if self.engine is not None else None
        self.on_change()

        def then():                    # once the prompt's answered (not holding the lock meanwhile)
            if asking is not None:
                asking.join()
            self._restart()
            self.on_change()
        if self.engine is not None:
            threading.Thread(target=then, daemon=True, name="kvm elevate restart").start()

    def set_swap_keys(self, on):
        with self.lock:
            self.conf["swap_keys"] = bool(on)
            self._save()
            if self.role == "server":
                self._apply_server()                    # restarts Deskflow with the new keys
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
            self._expose(False)
            self.applied = None
            self.status = ("Place a computer next to this one to start." if not grid else
                           "Waiting for the other computers to answer.")
            return
        swapped = sorted(c["screen"] for pid, c in ready if self.swaps(self.node.peers.get(pid)))
        key = tuple(sorted((n, xy) for n, xy in positions.items())) + tuple(sorted(c["fp"] for _, c in ready)) \
            + ("swap",) + tuple(swapped)
        if key == self.applied and self.engine.running:
            return
        self.files.trust("clients", [c["fp"] for _, c in ready])
        self.files.write_settings(me, screens=list(positions), swapped=swapped)
        self.files.write_layout(positions)
        self.applied = key
        self.role = "server"
        self.engine.start("server")
        self._expose(True)
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
            self._expose(False)
            self.status = ""

    def _expose(self, on):
        """Server: let computers on WinLink's built-in Tailscale reach our Deskflow server through
        the helper (see Sharing._paths). Deskflow still only accepts the fingerprints we trust."""
        if self.exposed is not None:
            try:
                self.exposed.close()
            except OSError:
                pass
            self.exposed = None
        emb = self.node.embedded
        if on and emb is not None and emb.running:
            try:
                self.exposed = emb.expose(PORT)
            except OSError as e:
                log.info("keyboard and mouse: can't offer it over the built-in Tailscale: %s", e)

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
            elif op == "dialback" and peer.id in self.clients and re.fullmatch(r"[0-9a-f]{16}", str(msg.get("tid", ""))):
                # Only while our Deskflow server is up to take it, and not over and over: a follower
                # whose own Deskflow keeps failing asks every few seconds, forever.
                c = self.clients[peer.id]
                now = time.time()
                c["dialbacks"] = [t for t in c.get("dialbacks", []) if now - t < 60] + [now]
                if self.role == "server" and self.engine is not None and self.engine.running \
                        and len(c["dialbacks"]) <= 6:
                    threading.Thread(target=self._dial_back, args=(peer, msg["tid"]), daemon=True,
                                     name="kvm dial back").start()
            elif op == "path" and peer.id in self.clients and msg.get("path") in ("direct", "tunnel", "relay"):
                self.clients[peer.id]["path"] = msg["path"]
            elif op == "ping":
                self._send(peer, {"op": "pong", "t": msg.get("t")})
            elif op == "pong" and isinstance(msg.get("t"), (int, float)):
                self.rtt[peer.id] = max(0.0, (time.monotonic() - msg["t"]) * 1000)
            elif op == "rejoin" and peer.id in self.clients and self.conf.get("share"):
                # the computer following us got a certificate it didn't expect: swap them again
                self.clients[peer.id].update(fp="", asked=False)
                self.refresh()
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
        if why:
            self._send(peer, {"op": "no", "reason": why})
            return
        self.files.trust("servers", [fp])
        self.server_peer, self.role = peer, "client"
        self.status = f"Following {peer.name}'s keyboard and mouse."
        self.joining = {"peer": peer, "screen": screen, "paths": self._paths(peer), "i": -1, "joined": False}
        self._next_path()
        self._send(peer, {"op": "hello", "fp": self.files.fingerprint()})

    PATH_WAIT = 12   # seconds for Deskflow to connect along a path before trying the next one

    def _paths(self, peer):
        """How Deskflow could reach the sharing computer, quickest first:
          direct  straight to the address WinLink's own link uses (same Wi-Fi, or the Tailscale app);
          tunnel  WinLink's built-in Tailscale, carried by its helper (no Python in between);
          relay   over WinLink's link, dialing either way (works on one-way networks, a bit slower).
        A path that recently failed with this computer is skipped, except the relay."""
        ctrl, ip, route = peer.control, "", "wifi"
        if ctrl is not None:
            route = ctrl.route
            try:
                ip = ctrl.chan.getpeername()[0]          # Control wraps the encrypted Channel
            except (OSError, AttributeError, IndexError, TypeError):
                ip = ""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        # over the built-in Tailscale, WinLink's link comes from the helper on this computer
        builtin = route == "tailscale" and (ip.startswith("127.") or ip == "::1")
        out = []
        if ip and not builtin and "%" not in ip:
            out.append(("direct", ip))
        ts = [a[3:] for a in peer.addresses() if a.startswith("ts:")]
        emb = self.node.embedded
        if builtin and ts and emb is not None and emb.running:
            out.append(("tunnel", ts[0]))
        now = time.time()
        out = [p for p in out if now - self.bad_paths.get((peer.id, p[0]), 0) > 600]
        return out + [("relay", None)]

    def _next_path(self):
        """Client: (re)start Deskflow along the next path."""
        j = self.joining
        if j is None or self.role != "client":
            return
        j["i"] += 1
        if j["i"] >= len(j["paths"]):
            j["i"] = len(j["paths"]) - 1            # the relay: keep trying it (Deskflow retries)
        kind, host = j["paths"][j["i"]]
        peer = j["peer"]
        self._close_relay()
        port = PORT
        try:
            if kind == "tunnel":
                self.tunnel, port = self.node.embedded.tunnel(host, PORT)
                host = "127.0.0.1"
            elif kind == "relay":
                self.relay = Relay(self.node, peer, lambda tid: self._send(peer, {"op": "dialback", "tid": tid}))
                host, port = "127.0.0.1", self.relay.port
        except (OSError, AttributeError) as e:
            log.info("keyboard and mouse: %s path unavailable: %s", kind, e)
            self.bad_paths[(peer.id, kind)] = time.time()
            return self._next_path()
        self.path = kind
        j["joined"] = False
        self.files.write_settings(j["screen"], remote_host=host, port=port)
        self.engine.start("client")
        log.info("keyboard and mouse: reaching %s %s", peer.name,
                 {"direct": f"directly at {host}", "tunnel": "through the built-in Tailscale",
                  "relay": "through WinLink's link"}[kind])
        self._send(peer, {"op": "path", "path": kind})
        if kind != "relay":
            attempt = j["i"]
            threading.Timer(self.PATH_WAIT, self._path_check, args=(j, attempt)).start()

    def _path_check(self, j, attempt):
        with self.lock:
            if self.joining is not j or j["i"] != attempt or j["joined"] or self.role != "client":
                return
            kind = j["paths"][attempt][0]
            log.info("keyboard and mouse: %s didn't connect %s; trying the next way",
                     j["peer"].name, {"direct": "directly", "tunnel": "through the built-in Tailscale"}.get(kind, kind))
            self.bad_paths[(j["peer"].id, kind)] = time.time()
            self._next_path()
        self.on_change()

    def _measure(self):
        """Every few seconds, the round trip to the computers we share with or follow."""
        while True:
            time.sleep(3)
            with self.lock:
                if self.role == "server":
                    peers = [self.node.peers.get(pid) for pid, c in self.clients.items() if c.get("state") == "on"]
                elif self.role == "client":
                    peers = [self.server_peer]
                else:
                    peers = []
            for peer in peers:
                if peer is not None and peer.control is not None:
                    self._send(peer, {"op": "ping", "t": time.monotonic()})

    def stop_client(self, why=""):
        with self.lock:
            if self.role == "client" and self.engine is not None:
                self.engine.stop()
                log.info("keyboard and mouse: client stopped%s", f" ({why})" if why else "")
            if self.role == "client":
                self.role, self.server_peer, self.status = None, None, why
            self.joining, self.path = None, None
            self._close_relay()
        self.on_change()

    def _close_relay(self):
        if self.relay is not None:
            self.relay.close()
            self.relay = None
        if self.tunnel is not None:
            try:
                self.tunnel.close()
            except OSError:
                pass
            self.tunnel = None

    def tunnel_in(self, peer, chan):
        """A "kvm" connection (see Relay). Runs on the connection's thread until it closes."""
        try:
            chan.settimeout(Relay.WAIT)
            msg = chan.recv_json()
        except (OSError, ValueError, ConnectionError):
            chan.close()
            return
        tid = str(msg.get("tid") or msg.get("go") or "")
        relay = self.relay
        if "tid" in msg and relay is not None and self.server_peer is peer:
            relay.dialed_back(tid, chan)                 # we follow; the sharing computer dialed back
        elif "go" in msg:
            self._to_engine(peer, chan)                  # we share; the follower dialed us
        else:
            chan.close()

    def _dial_back(self, peer, tid):
        try:
            chan = self.node.dial(peer, "kvm", timeout=6)
            chan.settimeout(Relay.WAIT)
            chan.send_json({"tid": tid})
            msg = chan.recv_json()
        except Exception as e:  # noqa: BLE001  (the follower's own dial may have worked)
            log.debug("keyboard and mouse: dialing back %s failed: %s", peer.name, e)
            return
        if msg.get("go") == tid:
            self._to_engine(peer, chan)
        else:
            chan.close()

    def _to_engine(self, peer, chan):
        """Hand a follower's connection to our Deskflow server."""
        with self.lock:
            ok = (self.role == "server" and self.engine is not None and self.engine.running
                  and self.clients.get(peer.id, {}).get("fp"))
        if not ok:
            chan.close()
            return
        try:
            sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            log.debug("keyboard and mouse: can't reach our Deskflow server: %s", e)
            chan.close()
            return
        chan.settimeout(None)
        pipe(sock, chan)

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
                if self.joining is not None:
                    self.joining["joined"] = True
                self.status = f"Following {self.server_peer.name if self.server_peer else 'the other computer'}'s keyboard and mouse."
                self.problem = ""
            elif kind == "permission":
                self.problem = "Allow WinLink under Privacy & Security › Accessibility, then turn this off and on."
            elif kind == "port":
                self.problem = f"Port {PORT} is in use (is another Deskflow running?)."
            elif kind == "untrusted":
                self._reswap()
        self.on_change()

    RESWAP_LIMIT, RESWAP_WINDOW, RESWAP_GAP = 3, 300, 15

    def _reswap(self):
        """Deskflow refused the other side's certificate: the fingerprints we swapped are out of date
        (a computer made a new certificate, e.g. after a reinstall). Swap them again by ourselves, as
        turning sharing off and on does; only after a few failed tries ask the person. Under self.lock."""
        now = time.time()
        self.reswaps = [t for t in self.reswaps if now - t < self.RESWAP_WINDOW]
        if self.reswaps and now - self.reswaps[-1] < self.RESWAP_GAP:
            return                                   # one mismatch is logged several times: one swap
        if len(self.reswaps) >= self.RESWAP_LIMIT:
            self.problem = "The other computer's certificate didn't match. Turn sharing off and on to swap them again."
            return
        self.reswaps.append(now)
        log.info("keyboard and mouse: certificate didn't match, swapping them again")
        self.problem = ""
        if self.role == "server":
            for c in self.clients.values():
                c.update(fp="", asked=False)
            self.applied = None
            self.refresh()
        elif self.role == "client" and self.server_peer is not None:
            self._send(self.server_peer, {"op": "rejoin"})

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
            if self.engine.link is not None:           # the elevated helper stops Deskflow and exits
                self.engine.link.close()
        self._expose(False)
        self._close_relay()

    # ---- for the window
    def describe(self, peer, path):
        """"18 ms · Wi-Fi" / "62 ms · Tailscale, relayed (fra)": the pointer's delay and how it travels."""
        if peer is None:
            return ""
        ctrl = peer.control
        over = "Tailscale" if (path == "tunnel" or (ctrl is not None and ctrl.route == "tailscale")) else "Wi-Fi"
        if over == "Tailscale":
            ts = {a[3:] for a in peer.addresses() if a.startswith("ts:")}
            for d in self.node.anywhere.get("devices") or []:
                if ts & set(d.get("ips") or []):
                    if d.get("direct"):
                        over += ", direct"
                    elif d.get("relay"):
                        over += f", relayed ({d['relay']})"     # see README: Keyboard and mouse
                    break
        if path == "relay":
            over += " through WinLink"
        rtt = self.rtt.get(peer.id)
        return " · ".join(x for x in (f"{rtt:.0f} ms" if rtt is not None else "", over) if x)

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
            if c.get("state") == "on":
                text = " · ".join(x for x in ("Connected", self.describe(p, c.get("path"))) if x)
            pos = grid.get(p.id)
            if pos is not None and p.id not in near:
                text = "Not touching any screen: the pointer can't get there"
            computers.append({"id": p.id, "name": p.name, "os": p.os, "pos": pos, "text": text,
                              "on": c.get("state") == "on", "reachable": pos is None or p.id in near})
        return {
            "available": self.core is not None,
            "outdated": self.outdated,                   # an installed Deskflow too old to use
            "needs": ".".join(map(str, MIN_VERSION)),
            "download": DOWNLOAD_URL,
            "share": bool(self.conf.get("share")),
            "allow": bool(self.conf.get("allow", True)),
            "swap_keys": bool(self.conf.get("swap_keys", True)),
            "can_elevate": IS_WIN and self.engine is not None,
            "elevated": bool(self.conf.get("elevated")) and IS_WIN,
            "elevated_problem": self.engine.problem if self.engine is not None else "",
            "role": self.role,
            "me": {"name": self.node.name, "os": "mac" if IS_MAC else "windows" if IS_WIN else "linux"},
            "following": self.server_peer.name if self.role == "client" and self.server_peer else None,
            "path": self.describe(self.server_peer, self.path) if self.role == "client" and self.server_peer else "",
            "computers": computers,
            "status": self.status,
            "problem": self.problem,
            "needs_accessibility": IS_MAC and ax is False,
        }
