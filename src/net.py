"""
Networking for WinLink: finding devices, pairing, the control channel (clipboard),
file transfers and audio connections.

Everything runs over one TCP port. Each new connection starts with a JSON
header line saying what it is:
  {"type": "control", ...}  long-lived: pairing, clipboard, keep-alive
  {"type": "file", ...}     one file, raw bytes follow, receiver replies "ok"
  {"type": "audio", ...}    sound stream, raw PCM or Opus (see audio.py)

Devices announce themselves every 1.5 s with UDP broadcast (IPv4) and link-local
multicast (IPv6, so IPv6-only Wi-Fi and phone hotspots work too). Devices on
other networks are found through Tailscale (see tailnet.py) with a "probe"
connection that just asks "are you WinLink, and who are you?".
"""

import base64
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import weakref

import secure
import tailnet
import tsembed

VERSION = "2026.11.8"
PORT = int(os.environ.get("LINK_PORT", 50555))
PROBE_PORT = int(os.environ.get("LINK_PROBE_PORT", 50555))   # WinLink's port on other devices
BEACON_PORT = 50556
GROUP6 = "ff02::4d:6163"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
OS_NAME = "windows" if IS_WIN else "mac" if IS_MAC else "linux"
OS_NAMES = ("mac", "windows", "linux", "android", "ios")
CHUNK = 1 << 20
MAX_LINE = 160 << 20          # clipboard images travel as one base64 line
SEEN_TIMEOUT = 8              # seconds without a beacon before a device counts as gone
TS_SEEN_TIMEOUT = 35          # same for devices found through Tailscale (checked every 10 s)
TS_POLL = 10
PROTO = 2                     # connection protocol version (2 = authenticated + encrypted;
                              # "plain" in the hello = skip the encryption inside the built-in Tailscale)
HANDSHAKE_TIMEOUT = 8         # seconds a new connection gets to introduce itself
PROMPT_WAIT = 90              # seconds a "wants to connect" question stays up
MAX_STRANGERS = 24            # unpaired devices shown at once (stops junk filling the list)
MAX_FILE = 1 << 40            # 1 TB
SPACE_MARGIN = 256 << 20      # keep this much disk free when receiving
FILE_ATTEMPTS, RETRY_DELAYS = 5, (2, 5, 15, 30)   # failed attempts in a row (without progress) per file
FILE_PATIENCE = 20 * 60       # keep resuming a file that's making progress for up to 20 minutes
CLIP_OFFERS = 8               # copied files the other computers can still ask for (newest)
CLIP_FILES_SHOWN = 20         # names listed in a "files copied" message
PARTIAL_DIR = ".winlink-partial"   # unfinished downloads, kept so a transfer can resume
PARTIAL_MAX_AGE = 2 * 86400

log = logging.getLogger("winlink")


# ---------------------------------------------------------------- places
def config_dir():
    if os.environ.get("LINK_HOME"):
        base = os.environ["LINK_HOME"]
    else:
        if IS_WIN:
            root = os.environ.get("APPDATA", os.path.expanduser("~"))
        elif IS_MAC:
            root = os.path.expanduser("~/Library/Application Support")
        else:
            root = os.path.expanduser("~/.config")
        base = os.path.join(root, "WinLink")
        old = os.path.join(root, "Link")             # before the rename: keep pairings and sign-in
        if not os.path.exists(base) and os.path.isdir(old) and \
                os.path.exists(os.path.join(old, "settings.json")):
            try:
                os.rename(old, base)
            except OSError:
                pass
    os.makedirs(base, exist_ok=True)
    return base


def setup_logging():
    """Problems go to WinLink.log in the settings folder (1 MB, 3 old copies kept)."""
    if log.level == logging.NOTSET:
        log.setLevel(logging.INFO)              # always log INFO and up, whatever else is attached
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        return
    try:
        handler = logging.handlers.RotatingFileHandler(os.path.join(config_dir(), "winlink.log"),
                                                       maxBytes=1 << 20, backupCount=3, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    threading.excepthook = lambda a: log.error("unexpected error in %s", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def downloads_dir():
    path = os.environ.get("LINK_DOWNLOADS")
    if not path and IS_WIN:
        path = _windows_downloads()
    path = path or os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(path, exist_ok=True)
    return path


def _windows_downloads():
    """The real Downloads folder, even if the user moved it."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                        ("d", ctypes.c_ubyte * 8)]

        folder = GUID(0x374DE290, 0x123F, 0x4565,
                      (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None,
                                                      ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:  # noqa: BLE001
        pass
    return None


def device_name():
    if os.environ.get("LINK_NAME"):
        return os.environ["LINK_NAME"]
    if IS_MAC:
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    if IS_WIN and os.environ.get("COMPUTERNAME"):
        return os.environ["COMPUTERNAME"].title()
    if not IS_MAC and not IS_WIN:            # Linux: the "pretty" name set in Settings › About, if any
        try:
            name = subprocess.run(["hostnamectl", "--pretty"], capture_output=True, text=True,
                                  timeout=2).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname().removesuffix(".local")


def safe_relpath(rel):
    """Keep a sender-supplied path inside Downloads and valid on Windows and macOS."""
    parts = []
    for part in rel.replace("\\", "/").split("/"):
        part = "".join(c for c in part if c not in '<>:"|?*' and ord(c) >= 32).strip().rstrip(".")
        if part and part not in (".", ".."):
            parts.append(part[:200])
    return os.path.join(*parts) if parts else "file"


def unique_path(path):
    if not os.path.exists(path) and not os.path.exists(path + ".part"):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{stem} ({n}){ext}") or os.path.exists(f"{stem} ({n}){ext}.part"):
        n += 1
    return f"{stem} ({n}){ext}"


def addr_rank(addr):
    """Lower is better: real IPv4, then global IPv6, then link-local, then odd ones."""
    if ":" not in addr:
        return 3 if addr.startswith(("192.0.0.", "169.254.", "127.")) else 0
    return 2 if addr.lower().startswith("fe80") else 1


# Interfaces that never lead to another computer on your network (VPN tunnels, AirDrop,
# loopback...). Announcing on them only teaches other devices addresses that don't work.
VIRTUAL_IFACES = ("lo", "utun", "awdl", "llw", "gif", "stf", "anpi", "ipsec", "ppp", "bridge",
                  "loopback", "tunnel")


def lan_interfaces():
    return [(i, n) for i, n in socket.if_nameindex() if not n.lower().startswith(VIRTUAL_IFACES)]


def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # sends nothing; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------- settings
class Settings:
    def __init__(self):
        self.path = os.path.join(config_dir(), "settings.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("id", uuid.uuid4().hex)
        self.data.setdefault("trusted", {})
        self.data.setdefault("clipboard", True)
        self.data.setdefault("audio", False)
        self.data.setdefault("anywhere", False)
        self.data.setdefault("keys", {})
        # Pairings from before keys existed can't be verified: they need pairing again.
        self.data["trusted"] = {k: v for k, v in self.data["trusted"].items() if k in self.data["keys"]}
        self.lock = threading.Lock()
        self.save()

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds pairing keys
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)


# ---------------------------------------------------------------- model
class Peer:
    def __init__(self, pid, name, os_name, port):
        self.id, self.name, self.os, self.port = pid, name, os_name, port
        self.addrs = {}            # address -> last time we heard a beacon from it
        self.last_seen = 0.0
        self.control = None        # live Control connection
        self.incoming = None       # pending pairing request from them: {"code", "event", "allow"}
        self.waiting_code = None   # we asked them; waiting for their approval
        self.paused = False        # we pressed Disconnect: ask before reconnecting
        self.remote_paused = False # they pressed Disconnect: don't auto-dial them
        self.connecting = False
        self.idle_since = time.time()   # when it was last connected (or first seen)
        self.was_nearby = None
        self.upgrade_tried = 0.0
        self.fails = 0                  # failed connection attempts in a row
        self.retry_at = 0.0             # don't auto-connect before this time
        self.lan_failed_at = 0.0        # last time "same Wi-Fi again" turned out unreachable

    def _fresh(self, addr, now):
        limit = TS_SEEN_TIMEOUT if tailnet.is_tailscale(addr) else SEEN_TIMEOUT
        return now - self.addrs[addr] < limit

    @property
    def nearby(self):
        now = time.time()
        return any(self._fresh(a, now) for a in list(self.addrs))

    def has_fresh_lan(self):
        now = time.time()
        return any(self._fresh(a, now) and not tailnet.is_tailscale(a) for a in list(self.addrs))

    def addresses(self):
        """Recently seen first; same Wi-Fi before Tailscale; then the usual preferences."""
        now = time.time()
        return sorted(list(self.addrs), key=lambda a: (not self._fresh(a, now), tailnet.is_tailscale(a),
                                                       addr_rank(a), -self.addrs[a]))


class Transfer:
    _next = 0

    def __init__(self, direction, name, size, peer_name, batch=None):
        Transfer._next += 1
        self.tid = Transfer._next
        self.direction, self.name, self.size, self.peer_name = direction, name, size, peer_name
        self.batch = batch
        self.done = 0
        self.state = "active"      # active | done | failed | cancelled
        self.path = None
        self.error = None
        self._last_emit = 0.0


class Batch:
    """Everything sent in one go (one drop, one folder): shown as one row, cancelled as one."""

    def __init__(self, direction, bid, name, peer, files, size):
        self.direction, self.id, self.name, self.peer = direction, bid, name, peer
        self.files_total, self.size = files, size
        self.transfers = []
        self.files_done = 0
        self.state = "active"      # active | done | failed | cancelled
        self.error = None
        self.cancelled = False
        self.chan = None           # the connection carrying the current file (closed on cancel)
        self.paths = []            # incoming: files that arrived (deleted if the batch is cancelled)
        self.created_dirs = []
        self.partials = set()
        self.started = self.updated = time.time()
        self.lock = threading.Lock()
        self._last_emit = 0.0

    @property
    def key(self):
        return self.id if self.direction == "out" else f"{self.peer.id}:{self.id}"

    @property
    def done(self):
        return sum(t.done for t in (self.transfers if self.direction == "out" else self.files.values()))

    files = None                   # incoming: relative path -> latest Transfer for it
    clip = None                    # outgoing: the clipboard offer being pasted (its files go to the paste)


class Control:
    """The long-lived, encrypted JSON-lines connection to a paired device."""

    def __init__(self, chan, route):
        self.chan = chan
        self.last_ping = 0.0
        self.started = time.time()
        self.route = route

    def send(self, obj):
        self.chan.send_json(obj)

    def close(self):
        self.chan.close()


# ---------------------------------------------------------------- node
class Node:
    """This device on the network.

    emit(kind, *args) is called from worker threads; the UI queues it:
      ("peers",)                         device list changed
      ("transfer", Transfer)             a transfer started / progressed / finished
      ("clip_in", Peer, dict)            clipboard item received
      ("audio_in", Peer, dict | None)    audio stream started (info) / stopped (None)
      ("notice", str)                    short message for the user
    """

    def __init__(self, settings, emit):
        self.settings = settings
        self.emit = emit
        self.id = settings["id"]
        self.name = device_name()
        self.peers = {}
        self.lock = threading.RLock()
        self.on_audio = None   # callable(peer, header, chan) on devices that can play sound
        self.on_kvm = None     # callable(peer, chan): Deskflow's connection carried over our link (kvm.py)
        self.downloads = downloads_dir()
        self.tailscale = "missing"   # the Tailscale app: missing | stopped | on
        # Built-in Tailscale ("Connect from anywhere"):
        self.embedded = None
        self.anywhere = {"state": "Off"}
        self._shared_ts = []
        self.ts_socks = weakref.WeakSet()    # sockets that go through it
        self.ts_lock = threading.Lock()
        self.guard = secure.Guard()
        self.batches = {}                      # key -> Batch (recent ones, both directions)
        self.cancelled_ids = {}                # batch id -> when (refuse late files of a cancelled batch)
        # Copied files travel only when they're pasted: the copy sends their names (an offer), the
        # paste asks for them ("clipwant") and they come like any other files, into the paste's folder.
        self.clip_offers = {}                  # offer id -> {"paths", "at"} (files copied here)
        self.clip_wanted = {}                  # offer id -> Fetch (files being pasted here)
        self.beacon_limit = secure.RateLimit(4, 8)     # announcements per second per address
        setup_logging()
        self._ts_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ts_listener.bind(("127.0.0.1", 0))
        self._ts_listener.listen(16)

    # ---- lifecycle
    def start(self):
        threading.Thread(target=self.prune_partials, daemon=True).start()
        for target in (self._serve, self._serve_tailnet, self._maintain, self._tailscale):
            threading.Thread(target=target, daemon=True).start()
        if not os.environ.get("LINK_NO_BEACON"):      # tests use Tailscale discovery only
            threading.Thread(target=self._beacon, daemon=True).start()
            threading.Thread(target=self._bonjour, daemon=True, name="bonjour").start()
            for family in (socket.AF_INET, socket.AF_INET6):
                threading.Thread(target=self._listen, args=(family,), daemon=True).start()

    def is_trusted(self, pid):
        return pid in self.settings["keys"] and pid in self.settings["trusted"]

    def key_for(self, pid):
        k = self.settings["keys"].get(pid)
        return base64.b64decode(k) if k and pid in self.settings["trusted"] else None

    def save_key(self, peer, key):
        keys, trusted = dict(self.settings["keys"]), dict(self.settings["trusted"])
        keys[peer.id], trusted[peer.id] = secure.b64(key), peer.name
        self.settings.data["keys"] = keys
        self.settings["trusted"] = trusted

    def forget(self, peer):
        keys, trusted = dict(self.settings["keys"]), dict(self.settings["trusted"])
        keys.pop(peer.id, None)
        trusted.pop(peer.id, None)
        self.settings.data["keys"] = keys
        self.settings["trusted"] = trusted
        self.disconnect(peer)

    def snapshot(self):
        with self.lock:
            peers = list(self.peers.values())
        return sorted(peers, key=lambda p: (p.control is None, not p.nearby, p.name.lower()))

    def connected(self):
        with self.lock:
            return [p for p in self.peers.values() if p.control is not None]

    # ---- discovery
    def _beacon(self):
        msg = json.dumps({"app": "link", "id": self.id, "name": self.name, "os": OS_NAME,
                          "port": PORT}).encode()
        s4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s4.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        try:
            s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, 1)
        except OSError:
            pass
        while True:
            targets = {"255.255.255.255"}
            ip = local_ipv4()
            if ip and addr_rank(ip) == 0:
                targets.add(".".join(ip.split(".")[:3] + ["255"]))
            for addr in targets:
                try:
                    s4.sendto(msg, (addr, BEACON_PORT))
                except OSError:
                    pass
            for idx, _name in lan_interfaces():
                try:
                    s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, idx)
                    s6.sendto(msg, (GROUP6, BEACON_PORT, 0, idx))
                except OSError:
                    pass
            time.sleep(1.5)

    def _bonjour(self):
        """Announce WinLink with Bonjour (_winlink._tcp) too. iPhones can't hear the broadcast
        announcements (iOS keeps apps from receiving them), but they can browse Bonjour."""
        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
        except ImportError:
            return
        zc = info = None
        last = None
        while True:
            ips = sorted({ip for ip in [local_ipv4()] if ip and addr_rank(ip) == 0})
            if ips != last:
                try:
                    if zc is None:
                        zc = Zeroconf(ip_version=IPVersion.V4Only)
                    if info is not None:
                        zc.unregister_service(info)
                    info = None
                    if ips:
                        label = re.sub(r"[^\w \-]", "", self.name)[:40] or "WinLink"
                        info = ServiceInfo("_winlink._tcp.local.", f"{label} {self.id[:6]}._winlink._tcp.local.",
                                           addresses=[socket.inet_aton(ip) for ip in ips], port=PORT,
                                           properties={"id": self.id, "name": self.name[:60], "os": OS_NAME,
                                                       "port": str(PORT)})
                        zc.register_service(info, allow_name_change=True)
                    last = ips
                except Exception as e:  # noqa: BLE001  (mDNS port busy, no network...)
                    log.debug("bonjour: %s", e)
            time.sleep(30)

    def my_tailnet_addrs(self):
        """This computer's Tailscale addresses (built-in and the Tailscale app), for phones to keep."""
        out = [ip for ip in (self.anywhere.get("ips") or []) if isinstance(ip, str)]
        out += [ip for ip in tailnet.self_ips if ip not in out]
        return out[:6]

    def _share_tailnet_addrs(self):
        """Tell connected phones when this computer's Tailscale addresses change."""
        addrs = self.my_tailnet_addrs()
        if addrs == self._shared_ts:
            return
        self._shared_ts = addrs
        for peer in self.connected():
            if peer.os in ("android", "ios"):
                try:
                    peer.control.send({"type": "info", "ts_addrs": addrs})
                except (OSError, ValueError, AttributeError):
                    pass

    def _listen(self, family):
        s = socket.socket(family, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                s.bind(("::", BEACON_PORT))
                group = socket.inet_pton(socket.AF_INET6, GROUP6)
                for idx, _name in [(0, "")] + socket.if_nameindex():
                    try:
                        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP,
                                     group + struct.pack("@I", idx))
                    except OSError:
                        pass
            else:
                s.bind(("", BEACON_PORT))
        except OSError:
            return
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except OSError:
                time.sleep(0.05)
                continue
            if not self.beacon_limit.allow(addr[0]):       # someone flooding announcements
                continue
            try:
                info = clean_beacon(json.loads(data))
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if info is None or info["id"] == self.id:
                continue
            host = addr[0]
            if family == socket.AF_INET6 and addr[3] and "%" not in host:
                host = f"{host}%{addr[3]}"   # link-local addresses need their interface
            self._seen(info, host)

    def _seen(self, info, host):
        changed = False
        with self.lock:
            peer = self.peers.get(info["id"])
            if peer is None:
                if not self.is_trusted(info["id"]) and self._strangers() >= MAX_STRANGERS:
                    return
                peer = self.peers[info["id"]] = Peer(info["id"], info["name"], info["os"], info["port"])
                changed = True
            if not peer.nearby:
                changed = True
            if not peer.nearby or host not in peer.addrs:
                peer.fails, peer.retry_at = 0, 0.0    # back (or somewhere new): don't sit out a backoff
            peer.name = info["name"]
            peer.port = info["port"]
            peer.addrs[host] = peer.last_seen = time.time()
            _trim_addrs(peer)
        if changed:
            self.emit("peers")

    # ---- Tailscale
    def _tailscale(self):
        """Find WinLink on your Tailscale devices: through the Tailscale app every 10 s, and
        through the built-in connection every 10 s (every 2 s while it's signing in)."""
        cli = tailnet.find_cli()
        next_app = next_builtin = 0.0
        while True:
            now = time.time()
            if now >= next_app:
                next_app = now + TS_POLL
                state, devices = tailnet.status(cli)
                if state != self.tailscale:
                    self.tailscale = state
                    self.emit("tailscale")
                for _name, ips in devices:
                    self._probe_any(ips)
                self._share_tailnet_addrs()
                if cli is None:
                    cli = tailnet.find_cli()      # maybe installed since
            if now >= next_builtin:
                running = self._poll_builtin()
                next_builtin = now + (TS_POLL if running else 2)
            time.sleep(1)

    def _poll_builtin(self):
        emb = self.embedded
        if emb is None:
            return False
        if not emb.running:                       # helper crashed: restart it
            try:
                emb.start()
            except OSError as e:
                self._set_anywhere({"state": "Error", "error": str(e)})
                return False
        st = emb.status()
        self._set_anywhere(st)
        if st.get("state") != "Running":
            return False
        for d in st.get("devices") or []:
            if d.get("online") and d.get("name", "").startswith(("winlink-", "link-")):
                self._probe_any(["ts:" + ip for ip in d.get("ips") or []])
        return True

    def _set_anywhere(self, st):
        key = lambda s: (s.get("state"), s.get("auth_url"), s.get("name"), s.get("error"),
                         s.get("key_expiry"))
        changed = key(st) != key(self.anywhere)
        self.anywhere = st
        if changed:
            self.emit("anywhere")

    def set_anywhere(self, on):
        """Turn the built-in Tailscale connection on or off (slow; runs in a thread)."""
        self.settings["anywhere"] = on

        def work():
            with self.ts_lock:
                if on and self.embedded is None:
                    self._set_anywhere({"state": "Starting"})
                    emb = tsembed.Embedded(os.path.join(config_dir(), "tailscale"),
                                           tsembed.hostname_for(self.name),
                                           self._ts_listener.getsockname()[1], PROBE_PORT)
                    try:
                        emb.start()
                    except OSError as e:
                        self._set_anywhere({"state": "Error", "error": str(e)})
                        return
                    self.embedded = emb
                    self._poll_builtin()
                elif not on and self.embedded is not None:
                    emb, self.embedded = self.embedded, None
                    emb.stop()
                    self._set_anywhere({"state": "Off"})
        threading.Thread(target=work, daemon=True).start()

    def sign_in_url(self, wait=8.0):
        """The Tailscale sign-in link (asks for a fresh one if needed)."""
        emb = self.embedded
        if emb is None:
            return None
        url = self.anywhere.get("auth_url")
        if url:
            return url
        try:
            emb.login()
        except OSError:
            return None
        end = time.time() + wait
        while time.time() < end:
            st = emb.status()
            self._set_anywhere(st)
            if st.get("auth_url"):
                return st["auth_url"]
            time.sleep(0.5)
        return None

    def sign_out(self):
        emb = self.embedded
        if emb is None:
            return False
        try:
            ok = emb.logout()
        except OSError:
            return False
        self._set_anywhere(emb.status())
        return ok

    def _probe_any(self, addrs):
        for addr in addrs:
            info = self._probe(addr)
            if info:
                self._seen(info, addr)
                return

    def _probe(self, addr, port=None):
        try:
            with self._open(addr, port or PROBE_PORT, timeout=8 if addr.startswith("ts:") else 2.5) as sock:
                sock.settimeout(HANDSHAKE_TIMEOUT)
                secure.send_json(sock, {"type": "probe"})
                info = clean_beacon(secure.recv_json(sock, 2048))
        except (OSError, ValueError, ConnectionError):
            return None
        if info and info["id"] != self.id:
            return info
        return None

    def in_tunnel(self, sock):
        """This connection runs through the built-in Tailscale (inside WireGuard all the way to the
        other device), so WinLink's own encryption would only repeat it. Not true for a 100.x
        address alone: that could be an ordinary network using the same addresses."""
        return sock in self.ts_socks

    def route_of(self, sock):
        if isinstance(sock, secure.Channel):
            return sock.route
        if sock in self.ts_socks:
            return "tailscale"
        try:
            return "tailscale" if tailnet.is_tailscale(sock.getpeername()[0]) else "wifi"
        except OSError:
            return "wifi"

    def _maintain(self):
        """Auto-reconnect to paired devices, keep connections alive, forget stale strangers."""
        while True:
            time.sleep(1.0)
            now = time.time()
            changed = False
            with self.lock:
                peers = list(self.peers.values())
            for p in peers:
                if p.control is not None:
                    p.idle_since = now
                    # Back on the same Wi-Fi? Move from Tailscale to the faster direct route.
                    if (p.control.route == "tailscale" and p.has_fresh_lan() and self.id < p.id
                            and now - max(p.control.started, p.upgrade_tried) > 10
                            and now - p.lan_failed_at > 60 and not p.connecting):
                        p.upgrade_tried = now
                        self.connect(p, force=True)
                    if now - p.control.last_ping > 4:
                        p.control.last_ping = now
                        try:
                            p.control.send({"type": "ping"})
                        except (OSError, ValueError):
                            p.control.close()
                # Paired and nearby: the device with the smaller id dials; the other
                # only steps in if nothing has happened for a few seconds.
                elif (p.nearby and not p.paused and not p.remote_paused and not p.connecting
                      and self.is_trusted(p.id) and now >= p.retry_at
                      and p.incoming is None and (self.id < p.id or now - p.idle_since > 6)):
                    self.connect(p)
                if not p.nearby and not self.is_trusted(p.id) and p.control is None \
                        and p.incoming is None and now - p.last_seen > 30:
                    with self.lock:
                        self.peers.pop(p.id, None)
                    changed = True
                if p.nearby != p.was_nearby:
                    p.was_nearby = p.nearby
                    changed = True
            for batch in list(self.batches.values()):
                if batch.direction == "in" and batch.state == "active" and batch.chan is None \
                        and now - batch.updated > 15 * 60:
                    batch.state, batch.error = "failed", f"{batch.peer.name} stopped sending"
                    self.emit("batch", batch)
            for bid, when in list(self.cancelled_ids.items()):
                if now - when > 3600:
                    del self.cancelled_ids[bid]
            if changed:
                self.emit("peers")

    # ---- connecting
    def _open(self, addr, port, timeout=4):
        if addr.startswith("ts:"):
            emb = self.embedded
            if emb is None:
                raise OSError("Connect from anywhere is off")
            sock = emb.dial(addr[3:], PROBE_PORT, timeout=max(timeout, 8))
            self.ts_socks.add(sock)
            return sock
        sock = socket.create_connection((addr, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    def dial(self, peer, kind, manual=False, allow_pair=False, timeout=4, lan_only=False):
        """An encrypted Channel to a peer. Tries all its local addresses at once (the first
        that also passes the security handshake wins), then Tailscale.
        Raises Declined / Paused / secure.AuthError / OSError."""
        paired = self.key_for(peer.id) is not None

        def finish(sock):
            if not paired:
                return sock                       # pairing needs the user; do it after the race
            try:
                return self._secure_client(sock, peer, kind, manual)
            except BaseException:
                sock.close()
                raise

        addrs = peer.addresses()
        local = [a for a in addrs if not tailnet.is_tailscale(a)]
        last = OSError("no address")
        result = None
        if local:
            try:
                result = self._race(local, peer.port, timeout, finish)
            except (OSError, ValueError, ConnectionError) as e:
                last = e
        if result is None and not lan_only:
            for addr in addrs:
                if tailnet.is_tailscale(addr):
                    try:
                        result = finish(self._open(addr, peer.port, timeout))
                        break
                    except (OSError, ValueError, ConnectionError) as e:
                        last = e
            if result is not None and local and peer.has_fresh_lan():
                peer.lan_failed_at = time.time()      # "on this Wi-Fi" but unreachable: don't keep trying
        if result is None:
            raise last
        if not paired:
            if not allow_pair:
                result.close()
                raise secure.AuthError("not paired")
            try:
                return self._secure_client(result, peer, kind, manual, allow_pair=True)
            except BaseException:
                result.close()
                raise
        return result

    def _race(self, addrs, port, timeout, finish):
        if len(addrs) == 1:
            return finish(self._open(addrs[0], port, timeout))
        results = queue.Queue()
        won = threading.Event()

        def attempt(addr, delay):
            if won.wait(delay):                  # someone already connected
                return
            try:
                results.put((finish(self._open(addr, port, timeout)), None))
            except (OSError, ValueError, ConnectionError) as e:
                results.put((None, e))

        for i, addr in enumerate(addrs):         # best address first, the rest 250 ms apart
            threading.Thread(target=attempt, args=(addr, i * 0.25), daemon=True).start()
        last = OSError("no address")
        for _ in addrs:
            try:
                conn, err = results.get(timeout=timeout + 12 + len(addrs) * 0.25)
            except queue.Empty:
                break
            if conn is not None:
                won.set()
                threading.Thread(target=self._close_losers, args=(results, len(addrs)), daemon=True).start()
                return conn
            last = err
        raise last

    @staticmethod
    def _close_losers(results, n):
        for _ in range(n):
            try:
                conn, _err = results.get(timeout=20)
            except queue.Empty:
                return
            if conn is not None:
                conn.close()

    def _secure_client(self, sock, peer, kind, manual=False, allow_pair=False):
        """Security handshake as the caller; returns an encrypted Channel."""
        route = self.route_of(sock)
        sock.settimeout(HANDSHAKE_TIMEOUT)
        key = self.key_for(peer.id)
        nc = secure.nonce()
        hello = {"v": PROTO, "type": kind, "id": self.id, "name": self.name, "os": OS_NAME,
                 "port": PORT, "nonce": secure.b64(nc), "manual": bool(manual)}
        if key is None:
            hello["pair"] = True
        offer_plain = key is not None and self.in_tunnel(sock)
        if offer_plain:
            hello["plain"] = True                  # skip our encryption if they're in the tunnel too
        plain = False
        secure.send_json(sock, hello)
        try:
            reply = secure.recv_json(sock)
        except ConnectionError as e:
            raise ConnectionError(f"{peer.name} closed the connection before the security check") from e
        mode = reply.get("mode")
        if mode == "paused":
            raise Paused()
        if mode not in ("auth", "pair"):
            raise Declined() if reply.get("reason") == "declined" else secure.AuthError(reply.get("reason") or "refused")
        ns = secure.unb64(str(reply.get("nonce", "")), 16)
        if mode == "auth":
            if key is None:
                raise secure.AuthError("not paired")
            secure.send_json(sock, {"proof": secure.b64(secure.proof(key, b"client", nc, ns, kind))})
            ok = secure.recv_json(sock)
            theirs = secure.unb64(str(ok.get("proof", "")), 32) if ok.get("ok") else b""
            if not hmac.compare_digest(theirs, secure.proof(key, b"server", nc, ns, kind)):
                raise secure.AuthError(f"{peer.name} couldn't prove it's the device you paired")
            plain = offer_plain and ok.get("plain") is True
        else:
            if not allow_pair or kind != "control":
                raise secure.AuthError("not paired")
            pairing = secure.Pairing()
            secure.send_json(sock, {"pub": secure.b64(pairing.public)})
            key, code = pairing.finish(secure.unb64(str(reply.get("pub", "")), 32), self.id, peer.id, nc, ns)
            peer.waiting_code = code
            self.emit("peers")
            try:
                sock.settimeout(PROMPT_WAIT + 5)
                answer = secure.recv_json(sock)
            finally:
                peer.waiting_code = None
            if answer.get("type") != "welcome":
                raise Declined()
            self.save_key(peer, key)
        c2s, s2c = secure.session_keys(key, nc, ns)
        return secure.Channel(sock, c2s, s2c, route, plain=plain)

    def connect(self, peer, manual=False, force=False):
        """Open the control connection (pairs first if needed)."""
        if (peer.control is not None and not force) or peer.connecting:
            return
        if manual:
            peer.paused = peer.remote_paused = False
            peer.fails, peer.retry_at = 0, 0.0
        peer.connecting = True
        threading.Thread(target=self._connect, args=(peer, manual, force), daemon=True).start()

    def _connect(self, peer, manual, upgrade=False):
        try:
            try:
                chan = self.dial(peer, "control", manual=manual, lan_only=upgrade,
                                 allow_pair=manual or self.key_for(peer.id) is not None)
            except (OSError, ValueError, ConnectionError) as e:
                if not upgrade:
                    raise
                # Moving a working Tailscale link to Wi-Fi didn't work: keep it, try again later.
                peer.lan_failed_at = time.time()
                log.info("%s: staying on Tailscale (Wi-Fi didn't answer: %s)", peer.name, _why(e))
                return
            if upgrade:
                log.info("%s: moved from Tailscale to Wi-Fi", peer.name)
            peer.connecting = False
            peer.remote_paused = False
            peer.fails, peer.retry_at = 0, 0.0
            self._run_control(peer, chan)
        except Declined:
            if manual:
                self.emit("notice", f"{peer.name} didn't accept the connection.")
            peer.remote_paused = True          # wait for a manual Connect
        except Paused:
            peer.remote_paused = True
        except secure.AuthError as e:
            log.warning("secure connection to %s failed: %s", peer.name, e)
            self._backoff(peer)
            if manual:
                self.emit("notice", f"Couldn't connect securely to {peer.name}. Try Forget, then pair again.")
        except (OSError, ValueError, ConnectionError) as e:
            log.info("connect to %s failed: %s", peer.name, e)
            self._backoff(peer)
            if manual:
                self.emit("notice", f"Couldn't reach {peer.name}. Is WinLink open on it?")
        except Exception:  # noqa: BLE001
            log.exception("connect to %s", peer.name)
            self._backoff(peer)
        finally:
            peer.connecting = False
            peer.waiting_code = None
            self.emit("peers")

    @staticmethod
    def _backoff(peer):
        """Wait longer after each failed attempt (2 s, 4 s, ... up to a minute)."""
        peer.fails += 1
        peer.retry_at = time.time() + min(60, 2 ** min(peer.fails, 6))

    def answer(self, peer, allow):
        """User's answer to an incoming pairing request."""
        req = peer.incoming
        if req is not None:
            req["allow"] = allow
            req["event"].set()

    def disconnect(self, peer):
        peer.paused = True
        ctrl, peer.control = peer.control, None     # disconnected now, not when the reader notices
        if ctrl is not None:
            try:
                ctrl.send({"type": "bye"})
            except (OSError, ValueError):
                pass
            # If the other side closes first, macOS can't wake a read blocked on this socket; the
            # reader then ends at its timeout (15 s), harmlessly, since it's no longer peer.control.
            ctrl.close()
        self.emit("peers")

    # ---- server side
    def _serve(self):
        try:
            srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("::", PORT))
        except OSError:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # no IPv6 on this machine
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("0.0.0.0", PORT))
            except OSError as e:
                self.emit("notice", f"Can't use port {PORT}: {e}. Is WinLink already open?")
                return
        srv.listen(64)
        while True:
            try:
                sock, addr = srv.accept()
            except OSError:
                time.sleep(0.1)                    # e.g. out of file handles: don't spin
                continue
            ip = addr[0]
            if not self.guard.admit(ip):          # flood / blocked: refuse without spending a thread
                _refuse(sock, self.guard.why_refused(ip))
                continue
            threading.Thread(target=self._handle, args=(sock, addr, ip), daemon=True).start()

    def _serve_tailnet(self):
        """Connections that reach us through the built-in Tailscale, forwarded by the helper."""
        while True:
            try:
                sock, _addr = self._ts_listener.accept()
            except OSError:
                time.sleep(0.1)
                continue
            threading.Thread(target=self._handle_tailnet, args=(sock,), daemon=True).start()

    def _handle_tailnet(self, sock):
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            first = bytearray()
            while not first.endswith(b"\n") and len(first) < 128:
                b = sock.recv(1)
                if not b:
                    raise ConnectionError("closed")
                first += b
            parts = first.decode(errors="replace").split()
            if len(parts) != 2 or parts[0] != "FROM":
                raise ValueError("bad forward line")
        except (OSError, ValueError, ConnectionError):
            sock.close()
            return
        ip = "ts:" + parts[1]
        if not self.guard.admit(ip):
            _refuse(sock, self.guard.why_refused(ip))
            return
        self.ts_socks.add(sock)
        self._handle(sock, (ip, 0), ip)

    def _handle(self, sock, addr, ip):
        """One incoming connection: probe, or security handshake then control / file / audio."""
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            hello = secure.recv_json(sock)
            kind = hello.get("type")
            if kind == "probe":
                secure.send_json(sock, {"app": "link", "id": self.id, "name": self.name,
                                        "os": OS_NAME, "port": PORT})
                return
            if kind not in ("control", "file", "audio", "kvm"):
                raise ValueError("unknown request")
            hello = clean_hello(hello)
            peer = self._peer_for(hello, addr)
            if peer is None:
                return
            chan = self._secure_server(sock, peer, hello, ip)
            if chan is None:
                return
            if kind == "control":
                self._run_control(peer, chan)
            elif kind == "file":
                self._receive_file(peer, chan)
            elif kind == "audio" and self.on_audio is not None:
                chan.settimeout(10)
                params = clean_audio(chan.recv_json())
                chan.settimeout(30)                  # senders send a keep-alive every 5 s of silence
                self.on_audio(peer, params, chan)
            elif kind == "kvm" and self.on_kvm is not None:
                self.on_kvm(peer, chan)
        except (OSError, ValueError, ConnectionError, KeyError, TypeError) as e:
            log.debug("connection from %s ended: %s", ip, e)
        except Exception:  # noqa: BLE001
            log.exception("connection from %s", ip)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self.guard.release(ip)

    def _secure_server(self, sock, peer, hello, ip):
        """Security handshake as the one being called. Returns a Channel, or None (refused)."""
        kind = hello["type"]
        nc = hello["nonce"]
        ns = secure.nonce()
        key = self.key_for(peer.id)
        if key is not None and not hello["pair"]:
            if kind == "control" and peer.paused and not hello["manual"]:
                secure.send_json(sock, {"mode": "paused"})
                return None
            secure.send_json(sock, {"mode": "auth", "nonce": secure.b64(ns)})
            msg = secure.recv_json(sock)
            theirs = secure.unb64(str(msg.get("proof", "")), 32)
            if not hmac.compare_digest(theirs, secure.proof(key, b"client", nc, ns, kind)):
                blocked = self.guard.failed(ip)
                log.warning("%s at %s failed to prove it's paired%s", peer.name, ip,
                            "; ignoring that address for a minute" if blocked else "")
                return None
            # Both ends inside the built-in Tailscale's tunnel: no second layer of encryption
            plain = hello["plain"] and self.in_tunnel(sock)
            secure.send_json(sock, {"ok": True, "proof": secure.b64(secure.proof(key, b"server", nc, ns, kind)),
                                    **({"plain": True} if plain else {})})
            c2s, s2c = secure.session_keys(key, nc, ns)
            chan = secure.Channel(sock, s2c, c2s, self.route_of(sock), plain=plain)
            if kind == "control" and peer.paused:     # they asked to reconnect after we disconnected
                if not self._ask(peer, ""):
                    return None
                peer.paused = False
            return chan
        if kind != "control":                          # files / sound only from paired devices
            log.info("refused %s from %s (%s): not paired", kind, peer.name, ip)
            secure.send_json(sock, {"mode": "denied", "reason": f"not paired with {self.name}; pair again"})
            return None
        if peer.incoming is not None or not self.guard.take_prompt(peer.id):
            secure.send_json(sock, {"mode": "denied", "reason": "busy"})
            return None
        allow = False
        key_before = key
        try:
            pairing = secure.Pairing()
            secure.send_json(sock, {"mode": "pair", "nonce": secure.b64(ns), "pub": secure.b64(pairing.public)})
            msg = secure.recv_json(sock)
            key, code = pairing.finish(secure.unb64(str(msg.get("pub", "")), 32), peer.id, self.id, nc, ns)
            allow = self._ask(peer, code, repair=key_before is not None)
            if not allow:
                secure.send_json(sock, {"type": "denied"})
                return None
            self.save_key(peer, key)
            peer.paused = peer.remote_paused = False
            secure.send_json(sock, {"type": "welcome"})
        finally:
            self.guard.end_prompt(peer.id, declined=not allow)
        c2s, s2c = secure.session_keys(key, nc, ns)
        return secure.Channel(sock, s2c, c2s, self.route_of(sock))

    def _ask(self, peer, code, repair=False):
        """Show "wants to connect" and wait for the user (up to PROMPT_WAIT seconds).
        repair: it was paired before but couldn't prove it (reinstalled, or an impostor)."""
        req = {"code": code, "event": threading.Event(), "allow": False, "repair": repair}
        peer.incoming = req
        self.emit("peers")
        try:
            req["event"].wait(PROMPT_WAIT)
        finally:
            peer.incoming = None
            self.emit("peers")
        return req["allow"]

    def _peer_for(self, hello, addr):
        with self.lock:
            peer = self.peers.get(hello["id"])
            if peer is None:
                if not self.is_trusted(hello["id"]) and self._strangers() >= MAX_STRANGERS:
                    return None                    # don't let junk fill the device list
                name = hello["name"] or self.settings["trusted"].get(hello["id"], addr[0])
                peer = self.peers[hello["id"]] = Peer(hello["id"], name, hello["os"], hello["port"])
            peer.name = hello["name"] or peer.name
            host = addr[0].removeprefix("::ffff:")
            peer.addrs.setdefault(host, 0.0)
            _trim_addrs(peer)
            return peer

    def _strangers(self):
        return sum(1 for p in self.peers.values() if not self.is_trusted(p.id))

    def _run_control(self, peer, chan):
        chan.settimeout(15)
        ctrl = Control(chan, chan.route)
        old, peer.control = peer.control, ctrl
        if old is not None:
            old.close()
        self.emit("peers")
        try:     # so phones can reach this computer from other networks later (computers ignore it)
            chan.send_json({"type": "info", "ts_addrs": self.my_tailnet_addrs()})
        except (OSError, ValueError):
            pass
        try:
            while True:
                line = chan.readline(MAX_LINE)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise ValueError("message too long")
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "clip":
                    clip = clean_clip(msg)
                    if clip:
                        self.emit("clip_in", peer, clip)
                elif kind == "cancel" and isinstance(msg.get("batch"), str) \
                        and re.fullmatch(r"[0-9a-f]{32}", msg["batch"]):
                    self._on_cancel_msg(peer, msg["batch"])
                elif kind == "clipwant" and _is_id(msg.get("id")):
                    threading.Thread(target=self._clip_want, args=(peer, msg["id"], bool(msg.get("save"))),
                                     daemon=True, name="clip want").start()
                elif kind == "clipfail" and _is_id(msg.get("id")):
                    fetch = self.clip_wanted.get(msg["id"])
                    error = str(msg.get("error") or "it couldn't send them")[:200]
                    if fetch is not None and fetch.peer is peer:
                        fetch.fail(error)
                    else:                           # (a Save, not a paste)
                        self.emit("notice", f"Couldn't get the files from {peer.name}: {error}.")
                elif kind == "kvm":                 # keyboard and mouse sharing (kvm.py) sets itself up
                    self.emit("kvm_msg", peer, msg)
                elif kind == "bye":
                    peer.remote_paused = True
                    break
        except (OSError, ValueError, ConnectionError) as e:
            log.debug("control with %s ended: %s", peer.name, e)
        finally:
            ctrl.close()
            if peer.control is ctrl:
                peer.control = None
            self.emit("peers")

    # ---- clipboard
    def send_clip(self, item, exclude=None):
        """Send a clipboard item to every connected device (except `exclude`, the one it came from)."""
        msg = {"type": "clip", "kind": item["kind"]}
        if item["kind"] == "text":
            msg["text"] = item["text"]
        elif item["kind"] == "files":               # only the names: the files come when pasted
            msg.update({k: item[k] for k in ("id", "count", "size")})
            msg["names"] = item["names"][:CLIP_FILES_SHOWN]
        else:
            for key in ("png", "bmp"):
                if item.get(key):
                    msg[key] = base64.b64encode(item[key]).decode()

        def work():
            for peer in self.connected():
                if exclude is not None and peer.id == exclude:
                    continue
                try:
                    peer.control.send(msg)
                except (OSError, ValueError, AttributeError):
                    pass
        threading.Thread(target=work, daemon=True).start()

    def offer_files(self, paths):
        """Files copied here: remember them under a new id. Returns the clipboard item to announce."""
        items, complete = _walk(paths, budget=1.5)       # a huge folder: don't hold up the clipboard
        oid = uuid.uuid4().hex
        self.clip_offers[oid] = {"paths": list(paths), "at": time.time()}
        while len(self.clip_offers) > CLIP_OFFERS:
            self.clip_offers.pop(next(iter(self.clip_offers)))
        names = [os.path.basename(os.path.abspath(p).rstrip(os.sep)) for p in paths]
        return {"kind": "files", "id": oid, "names": names, "count": len(items) if complete else -1,
                "size": sum(i[2] for i in items) if complete else -1, "paths": list(paths)}

    def _clip_want(self, peer, oid, save=False):
        """The other computer pasted files copied here (or pressed Save): send them now."""
        offer = self.clip_offers.get(oid)
        sent = offer is not None and self.send_paths(peer, offer["paths"], clip=None if save else oid)
        if not sent:
            try:
                peer.control.send({"type": "clipfail", "id": oid,
                                   "error": "they aren't on its clipboard any more" if offer is None
                                   else "the files were moved or deleted"})
            except (OSError, ValueError, AttributeError):
                pass

    def fetch_files(self, peer, oid, folder, save=False):
        """Ask for the files of an offer. They arrive in `folder` (save: in Downloads, as usual).
        Returns a Fetch to wait on."""
        fetch = Fetch(peer, oid, folder)
        if not save:
            self.clip_wanted[oid] = fetch
        try:
            peer.control.send({"type": "clipwant", "id": oid, "save": save})
        except (OSError, ValueError, AttributeError):
            fetch.fail(f"{peer.name} isn't connected")
        return fetch

    # ---- files
    def send_paths(self, peer, paths, clip=None):
        items, _ = _walk(paths)
        if not items:
            return 0
        tops = [os.path.basename(os.path.abspath(p).rstrip(os.sep)) for p in paths]
        name = tops[0] if len(tops) == 1 else f"{len(tops)} items"
        batch = Batch("out", uuid.uuid4().hex, name, peer, len(items), sum(i[2] for i in items))
        batch.clip = clip
        for full, rel, size in items:
            t = Transfer("out", rel.replace(os.sep, "/"), size, peer.name, batch)
            t.full = full
            batch.transfers.append(t)
        self._track(batch)
        threading.Thread(target=self._send_files, args=(peer, batch), daemon=True,
                         name=f"send {name}").start()
        return len(items)

    def _track(self, batch):
        self.batches[batch.key] = batch
        if len(self.batches) > 40:                       # keep the recent ones
            for key in sorted(self.batches, key=lambda k: self.batches[k].updated)[:len(self.batches) - 40]:
                if self.batches[key].state != "active":
                    del self.batches[key]
        self.emit("batch", batch)

    def cancel(self, key):
        """Cancel a transfer from either side; the other side is told and cleans up too."""
        batch = self.batches.get(key)
        if batch is None or batch.state != "active":
            return False
        self._cancel_local(batch)
        if batch.peer.control is not None:
            try:
                batch.peer.control.send({"type": "cancel", "batch": batch.id})
            except (OSError, ValueError):
                pass
        return True

    def _cancel_local(self, batch, by_other=False):
        batch.cancelled = True
        self.cancelled_ids[batch.id] = time.time()
        chan = batch.chan
        if chan is not None:
            chan.close()                                  # stops a file mid-way
        if batch.direction == "in":
            self._discard_incoming(batch)
        batch.state = "cancelled"
        batch.error = f"cancelled on {batch.peer.name}" if by_other else "cancelled"
        batch.updated = time.time()
        for t in batch.transfers:
            if t.state == "active":
                t.state = "cancelled"
        log.info("%s %s %s %s: cancelled%s", "sending" if batch.direction == "out" else "receiving",
                 batch.name, "to" if batch.direction == "out" else "from", batch.peer.name,
                 f" by {batch.peer.name}" if by_other else "")
        self.emit("batch", batch)

    def _discard_incoming(self, batch):
        """Delete everything a cancelled incoming batch left behind."""
        with batch.lock:
            for path in list(batch.partials) + list(batch.paths):
                try:
                    os.remove(path)
                except OSError:
                    pass
            for folder in sorted(batch.created_dirs, key=len, reverse=True):
                try:
                    if os.path.isdir(folder) and not os.listdir(folder):
                        os.rmdir(folder)
                except OSError:
                    pass
            removed = list(batch.paths)
            batch.partials.clear()
            try:
                os.rmdir(os.path.join(self.downloads, PARTIAL_DIR))     # only if nothing else is in it
            except OSError:
                pass
        if removed:
            self.emit("files_removed", removed)

    def _on_cancel_msg(self, peer, bid):
        for batch in list(self.batches.values()):
            if batch.id == bid and batch.peer is peer and batch.state == "active":
                self._cancel_local(batch, by_other=True)
                return
        self.cancelled_ids[bid] = time.time()             # not started here yet: refuse it later

    def _send_files(self, peer, batch):
        for t in batch.transfers:
            if batch.cancelled:
                break
            full = t.full
            began, attempt, retries = time.time(), 0, 0
            while True:
                before = t.done
                try:
                    started = time.time()
                    route = self._send_one(peer, t, full)
                    t.state = "done"
                    batch.files_done += 1
                    log.info("sent %s (%d bytes) to %s via %s in %.1fs%s", t.name, t.size, peer.name, route,
                             time.time() - started, f" after {retries} retries" if retries else "")
                    break
                except Cancelled:
                    break
                except MoveToWifi:
                    t.moving = True                   # straight back in, over Wi-Fi this time
                    continue
                except Refused as e:                  # the other side said no: don't retry
                    if batch.cancelled or "cancelled" in str(e):
                        if not batch.cancelled:
                            self._cancel_local(batch, by_other=True)
                        break
                    t.state, t.error = "failed", str(e)
                    break
                except FileNotFoundError:
                    t.state, t.error = "failed", "the file was moved or deleted"
                    break
                except (OSError, ValueError, ConnectionError) as e:
                    if batch.cancelled:
                        break
                    progressed = t.done > before or getattr(t, "resumed_at", 0) > before
                    attempt = 0 if progressed else attempt + 1     # a flaky link that still moves data: keep going
                    retries += 1
                    log.warning("sending %s to %s stopped at %d%% (%s); %s", t.name, peer.name,
                                100 * t.done // max(t.size, 1), e,
                                "resuming" if progressed else f"try {attempt} of {FILE_ATTEMPTS}")
                    t.error = str(e)
                    if attempt >= FILE_ATTEMPTS or time.time() - began > FILE_PATIENCE:
                        t.state = "failed"
                        break
                    t.retries = retries
                    batch.error = f"retrying: {_why(e)}"
                    self._emit_batch(batch, force=True)
                    for _ in range(int((1 if progressed else RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]) * 10)):
                        if batch.cancelled:
                            break
                        time.sleep(0.1)
                finally:
                    batch.chan = None
            batch.error = None if t.state == "done" else batch.error
            self._emit_batch(batch, force=True)
        if batch.state == "active":
            failed = [t for t in batch.transfers if t.state == "failed"]
            batch.state = "failed" if failed else "done"
            batch.error = failed[0].error if failed else None
            batch.updated = time.time()
            self.emit("batch", batch)
        if batch.clip and batch.state != "done":          # a paste is waiting for these: say they won't come
            try:
                peer.control.send({"type": "clipfail", "id": batch.clip, "error": batch.error or "cancelled"})
            except (OSError, ValueError, AttributeError):
                pass

    def _emit_batch(self, batch, force=False):
        now = time.time()
        batch.updated = now
        if force or now - batch._last_emit > 0.1:
            batch._last_emit = now
            self.emit("batch", batch)

    def _send_one(self, peer, t, full):
        """Send one file; returns the route used. Errors say which step failed."""
        batch = t.batch
        stage = "connecting"
        chan = None
        try:
            chan = self.dial(peer, "file")
            if batch.cancelled:
                raise Cancelled()
            if getattr(t, "moving", False):
                t.moving = False
                if chan.route == "tailscale":           # "same Wi-Fi" but it didn't answer: not for a while
                    peer.lan_failed_at = time.time()
                else:
                    log.info("%s to %s moved from Tailscale to Wi-Fi at %d%%", t.name, peer.name,
                             100 * t.done // max(t.size, 1))
            batch.chan = chan
            route = f"{chan.route} ({_addr_of(chan)})"
            stage = f"waiting for {peer.name} to accept the file"
            chan.settimeout(30)
            chan.send_json({"rel": t.name, "size": t.size, "resume": _resume_key(self.id, full, t),
                            "batch": batch.id, "batch_name": batch.name, "batch_files": batch.files_total,
                            "batch_size": batch.size, **({"clip": batch.clip} if batch.clip else {})})
            reply = chan.recv_json()
            if not reply.get("ok"):
                raise Refused(str(reply.get("error") or "refused")[:200])
            offset = reply.get("offset", 0)
            if not isinstance(offset, int) or not 0 <= offset <= t.size:
                offset = 0
            stage = "sending"
            with open(full, "rb") as f:
                f.seek(offset)
                t.done = t.resumed_at = offset          # the other side already has this much
                next_check = time.time() + 3
                while chunk := f.read(CHUNK):
                    if batch.cancelled:
                        raise Cancelled()
                    if chan.route == "tailscale" and time.time() >= next_check:
                        next_check = time.time() + 3
                        # Both on the same Wi-Fi again: finish over the faster route (it resumes here).
                        if peer.has_fresh_lan() and time.time() - peer.lan_failed_at > 60:
                            raise MoveToWifi()
                    chan.sendall(chunk)
                    t.done += len(chunk)
                    self._emit_batch(batch)
            if t.done != t.size:
                raise Refused("the file changed while it was being sent")
            stage = f"waiting for {peer.name} to save it"
            chan.settimeout(120)
            reply = chan.recv_json()
            if not reply.get("saved"):
                raise ConnectionError(str(reply.get("error") or "not saved on the other side")[:200])
            return route
        except (Refused, FileNotFoundError, Cancelled, MoveToWifi):
            raise
        except (OSError, ValueError, ConnectionError) as e:
            if batch.cancelled:
                raise Cancelled() from e
            raise ConnectionError(f"{stage}: {_why(e)}") from e
        finally:
            if chan is not None:
                chan.close()

    def _incoming_batch(self, peer, meta):
        bid = meta.get("batch")
        if not isinstance(bid, str) or not re.fullmatch(r"[0-9a-f]{32}", bid):
            bid = uuid.uuid4().hex                       # older sender: every file is its own batch
        key = f"{peer.id}:{bid}"
        batch = self.batches.get(key)
        if batch is None:
            files, size = meta.get("batch_files"), meta.get("batch_size")
            batch = Batch("in", bid, clean_name(meta.get("batch_name") or meta.get("rel") or "file"), peer,
                          files if isinstance(files, int) and 0 < files < 10 ** 6 else 1,
                          size if isinstance(size, int) and 0 <= size <= MAX_FILE else meta.get("size", 0))
            batch.files = {}
            self._track(batch)
        return batch

    def _receive_file(self, peer, chan):
        chan.settimeout(30)
        meta = chan.recv_json()
        rel, size = meta.get("rel"), meta.get("size")
        if not isinstance(rel, str) or not rel or len(rel) > 1024 or not isinstance(size, int) \
                or not 0 <= size <= MAX_FILE:
            chan.send_json({"error": "invalid file details"})
            return
        if meta.get("batch") in self.cancelled_ids:
            chan.send_json({"error": "cancelled"})
            return
        fetch = self.clip_wanted.get(meta.get("clip")) if isinstance(meta.get("clip"), str) else None
        if fetch is not None and fetch.peer is not peer:
            fetch = None
        if meta.get("clip") and fetch is None:          # nobody here is pasting that
            chan.send_json({"error": "not wanted any more"})
            return
        root = fetch.folder if fetch is not None else self.downloads
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            free = size + SPACE_MARGIN
        if size + SPACE_MARGIN > free:
            chan.send_json({"error": f"not enough space on {self.name}"})
            self.emit("notice", f"{peer.name} tried to send a {size >> 20} MB file, but this computer is low on space.")
            return
        batch = self._incoming_batch(peer, meta)
        if batch.cancelled:
            chan.send_json({"error": "cancelled"})
            return
        if fetch is not None:
            fetch.batch = batch
        dest = fetch.place(rel) if fetch is not None else unique_path(os.path.join(root, safe_relpath(rel)))
        created = []                             # folders WinLink makes (cleanup may remove them later)
        folder = os.path.dirname(dest)
        for _ in range(64):
            if not folder.startswith(root) or folder == root or os.path.isdir(folder):
                break
            created.append(folder)
            folder = os.path.dirname(folder)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except OSError as e:
            log.warning("can't create folder for %s from %s: %s", rel, peer.name, e)
            chan.send_json({"error": f"{self.name} couldn't create the folder: {_why(e)}"})
            return
        # Unfinished data is kept under a key from the sender, so a stalled transfer can resume.
        resume = meta.get("resume")
        if isinstance(resume, str) and re.fullmatch(r"[0-9a-f]{40}", resume):
            partial_dir = os.path.join(root, PARTIAL_DIR) if fetch is None else os.path.join(self.downloads, PARTIAL_DIR)
            os.makedirs(partial_dir, exist_ok=True)
            part = os.path.join(partial_dir, hashlib.sha1((peer.id + resume).encode()).hexdigest() + ".part")
        else:
            part, resume = dest + ".part", None
        offset = 0
        if resume and os.path.exists(part):
            offset = os.path.getsize(part)
            if offset > size:
                offset = 0
        t = Transfer("in", os.path.basename(dest), size, peer.name, batch)
        t.path = dest
        t.created_dirs = created
        t.done = offset
        try:
            f = open(part, "r+b" if offset else "wb")
            f.seek(offset)
            f.truncate()
        except OSError as e:
            log.warning("can't write %s from %s: %s", part, peer.name, e)
            chan.send_json({"error": f"{self.name} couldn't save the file: {_why(e)}"})
            return
        with batch.lock:
            batch.files[rel] = t
            batch.partials.add(part)
            batch.created_dirs.extend(d for d in created if d not in batch.created_dirs)
            batch.chan = chan
        chan.send_json({"ok": True, "offset": offset})
        if offset:
            log.info("resuming %s from %s at %d of %d bytes", rel, peer.name, offset, size)
        self._emit_batch(batch, force=True)
        stage = "receiving"
        try:
            with f:
                while t.done < size:
                    chunk = chan.read1(min(CHUNK, size - t.done))
                    if not chunk:
                        raise ConnectionError("the sender stopped")
                    f.write(chunk)
                    t.done += len(chunk)
                    self._emit_batch(batch)
            if batch.cancelled:
                raise ConnectionError("cancelled")
            stage = "saving"
            t.path = dest = _finish_file(part, dest)
            chan.send_json({"saved": True})
            t.state = "done"
            with batch.lock:
                batch.partials.discard(part)
                batch.paths.append(dest)
                batch.files_done += 1
                batch.error = None
                if batch.files_done >= batch.files_total and batch.state == "active":
                    batch.state = "done"
            log.info("received %s (%d bytes) from %s via %s", os.path.basename(dest), size, peer.name, chan.route)
            if fetch is not None:                    # pasted: it's where the user put it, not a download
                if batch.files_done >= batch.files_total:
                    fetch.finish()
            else:
                self.emit("received", dest, created, peer.name)
            if batch.cancelled:                      # cancelled just as it finished: still clean up
                self._discard_incoming(batch)
        except (OSError, ValueError, ConnectionError) as e:
            t.state, t.error = "failed", f"{stage}: {_why(e)}"
            if batch.cancelled:
                try:
                    os.remove(part)                  # (on Windows only possible once it's closed)
                except OSError:
                    pass
                batch.partials.discard(part)
            else:
                log.warning("receiving %s from %s failed while %s: %s", rel, peer.name, stage, e)
                batch.error = f"waiting for {peer.name} to continue"
                try:
                    chan.send_json({"saved": False, "error": f"{self.name} {stage} failed: {_why(e)}"})
                except (OSError, ValueError, ConnectionError):
                    pass
                if not resume or stage == "saving":     # keep resumable data; drop the rest
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                    batch.partials.discard(part)
        finally:
            batch.chan = None
            self._emit_batch(batch, force=True)

    def prune_partials(self):
        """Forget unfinished downloads nobody resumed for two days."""
        folder = os.path.join(self.downloads, PARTIAL_DIR)
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if time.time() - os.path.getmtime(path) > PARTIAL_MAX_AGE:
                    os.remove(path)
            if not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass

class Fetch:
    """Copied files on their way here because they were pasted. wait() returns their paths
    (the top-level files and folders, as they were copied) or raises ConnectionError."""

    def __init__(self, peer, oid, folder):
        self.peer, self.id, self.folder = peer, oid, folder
        self.batch = None
        self.error = None
        self.tops = {}                   # first part of each relative path -> where it went
        self.done = threading.Event()

    def place(self, rel):
        """Where one arriving file goes: under folder, its top-level name made unique once."""
        rel = safe_relpath(rel)
        top, _, rest = rel.partition(os.sep)
        if top not in self.tops:
            self.tops[top] = unique_path(os.path.join(self.folder, top))
        return os.path.join(self.tops[top], rest) if rest else self.tops[top]

    def finish(self):
        self.done.set()

    def fail(self, error):
        if not self.done.is_set():
            self.error = error
            self.done.set()

    def wait(self, timeout=None, alive=None):
        """alive(): still worth waiting? (checked every second)"""
        end = None if timeout is None else time.time() + timeout
        while not self.done.wait(1):
            if (alive is not None and not alive()) or (end is not None and time.time() > end):
                self.fail("it took too long")
        if self.error:
            raise ConnectionError(self.error)
        return list(self.tops.values())


def _walk(paths, budget=None):
    """(full path, relative path to send, size) for every file under paths (folders included), and
    whether that's all of them (budget: give up after that many seconds)."""
    end = None if budget is None else time.time() + budget
    items = []
    for path in paths:
        try:
            path = os.path.abspath(path)
            if os.path.isdir(path):
                root = os.path.dirname(path.rstrip(os.sep))
                for folder, _dirs, files in os.walk(path):
                    if end is not None and time.time() > end:
                        return items, False
                    for name in sorted(files):
                        if name.startswith("."):
                            continue
                        full = os.path.join(folder, name)
                        items.append((full, os.path.relpath(full, root), os.path.getsize(full)))
            elif os.path.isfile(path):
                items.append((path, os.path.basename(path), os.path.getsize(path)))
        except OSError as e:
            log.info("skipping %s: %s", path, e)
    return items, True


def _is_id(v):
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v) is not None


# ---------------------------------------------------------------- helpers
def _finish_file(part, dest, patience=30):
    """Rename file.part to its real name. Antivirus or indexing can lock a brand-new file for a
    moment (common on Windows), so keep trying for a while instead of failing."""
    delay, waited = 0.25, 0.0
    while True:
        try:
            if os.path.exists(dest):                  # something else took the name meanwhile
                dest = unique_path(dest)
            os.replace(part, dest)
            return dest
        except PermissionError:
            if waited >= patience:
                raise
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 4)


def _resume_key(my_id, path, t):
    """Same file, same size, same modification time -> same key, so the receiver can resume."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = 0
    return hashlib.sha1(f"{my_id}|{t.name}|{t.size}|{mtime}".encode()).hexdigest()


def _refuse(sock, reason):
    """Tell a connection we won't serve it, then close (never blocks)."""
    try:
        sock.setblocking(False)
        sock.send((json.dumps({"mode": "denied", "reason": reason}) + "\n").encode())
    except OSError:
        pass
    finally:
        sock.close()


def _addr_of(chan):
    try:
        return str(chan.getpeername()[0])
    except (OSError, IndexError):
        return "?"


def _why(e):
    """A short, human reason for a network or file error."""
    if isinstance(e, socket.timeout) or "timed out" in str(e):
        return "no response in time"
    if isinstance(e, PermissionError):
        return "permission denied (the file may be in use, or blocked by antivirus)"
    text = str(e) or type(e).__name__
    return text[:160]


# ---------------------------------------------------------------- input checks
class Declined(secure.AuthError):
    """The other person pressed Decline."""


class Paused(secure.AuthError):
    """The other side disconnected us and doesn't want automatic reconnects."""


class Cancelled(Exception):
    """The user cancelled this transfer."""


class MoveToWifi(Exception):
    """Mid-transfer over Tailscale, both devices are on the same Wi-Fi again: reconnect there and
    carry on from the same byte (much faster)."""


class Refused(ConnectionError):
    """The other side won't take this file (no space, bad details...)."""


_ID = re.compile(r"^[0-9a-f]{16,64}$")


def clean_name(name):
    name = "".join(c for c in str(name or "") if c.isprintable()).strip()
    return name[:48] or "Unknown device"


def clean_hello(h):
    """Validate a connection's opening message (raises ValueError)."""
    if not isinstance(h.get("id"), str) or not _ID.match(h["id"]):
        raise ValueError("bad id")
    port = h.get("port", PORT)
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("bad port")
    return {"type": h["type"], "id": h["id"], "name": clean_name(h.get("name")),
            "os": h.get("os") if h.get("os") in OS_NAMES else "?",
            "port": port, "nonce": secure.unb64(str(h.get("nonce", "")), 16),
            "pair": bool(h.get("pair")), "manual": bool(h.get("manual")), "plain": h.get("plain") is True}


def clean_beacon(info):
    """A validated announcement, or None."""
    if not isinstance(info, dict) or info.get("app") != "link":
        return None
    try:
        h = clean_hello({"type": "beacon", "nonce": secure.b64(b"\0" * 16), **{k: info.get(k) for k in ("id", "name", "os", "port")}})
    except (ValueError, TypeError):
        return None
    return {"id": h["id"], "name": h["name"], "os": h["os"], "port": h["port"]}


def clean_clip(msg):
    kind = msg.get("kind")
    if kind == "text" and isinstance(msg.get("text"), str):
        return {"kind": "text", "text": msg["text"][:4 << 20]}
    if kind == "files" and _is_id(msg.get("id")) and isinstance(msg.get("names"), list):
        names = [n for n in ("".join(c for c in n if c.isprintable()).strip()[:200]
                             for n in msg["names"][:CLIP_FILES_SHOWN] if isinstance(n, str)) if n]
        count, size = msg.get("count"), msg.get("size")
        if names and isinstance(count, int) and isinstance(size, int):
            return {"kind": "files", "id": msg["id"], "names": names, "count": max(count, -1),
                    "size": min(max(size, -1), MAX_FILE)}      # -1: not counted (a huge folder)
        return None
    if kind == "image" and all(isinstance(msg.get(k), str) for k in ("png", "bmp") if k in msg) \
            and (msg.get("png") or msg.get("bmp")):
        return {"kind": "image", **{k: msg[k] for k in ("png", "bmp") if msg.get(k)}}
    return None


def clean_audio(p):
    rate, ch = p.get("rate"), p.get("channels")
    if not isinstance(rate, int) or not 8000 <= rate <= 192000 or not isinstance(ch, int) or not 1 <= ch <= 8:
        raise ValueError("bad audio format")
    buffer_ms = p.get("buffer_ms", 120)
    if not isinstance(buffer_ms, int) or not 20 <= buffer_ms <= 2000:
        buffer_ms = 120
    return {"rate": rate, "channels": ch, "format": "f32" if p.get("format") == "f32" else "s16",
            "remote": bool(p.get("remote")), "buffer_ms": buffer_ms, "opus": p.get("opus") is True}


def _trim_addrs(peer, keep=12):
    if len(peer.addrs) > keep:
        for addr in sorted(peer.addrs, key=peer.addrs.get)[:len(peer.addrs) - keep]:
            del peer.addrs[addr]
