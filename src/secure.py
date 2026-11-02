"""
WinLink's security layer.

Pairing: the two devices do an X25519 key exchange. The 4-digit code both
screens show is derived from that exchange, so if someone sat in the middle the
codes wouldn't match. Each pair of devices keeps the resulting 32-byte key.

Every later connection: both sides prove they have that key (HMAC over fresh
random nonces, so a recording can't be replayed), then everything is encrypted
with ChaCha20-Poly1305 using keys derived for that connection only.

Guard: limits on connections, attempts and prompts per address, so a flood of
connections or junk can't take WinLink down.
"""

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FRAME = 64 * 1024              # plaintext bytes per encrypted frame
MAX_HANDSHAKE_LINE = 8 * 1024


class AuthError(ConnectionError):
    """The other side couldn't prove it's the paired device (or refused us)."""


def b64(b):
    return base64.b64encode(b).decode()


def unb64(s, size=None):
    raw = base64.b64decode(s, validate=True)
    if size is not None and len(raw) != size:
        raise ValueError("bad length")
    return raw


def nonce():
    return os.urandom(16)


def _hkdf(secret, salt, info, length=32):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


# ---------------------------------------------------------------- pairing
class Pairing:
    """One side of a pairing key exchange."""

    def __init__(self):
        self._priv = X25519PrivateKey.generate()
        self.public = self._priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def finish(self, their_public, id_a, id_b, nonce_c, nonce_s):
        """(pair key, 4-digit code). Same on both sides only if nobody tampered."""
        shared = self._priv.exchange(X25519PublicKey.from_public_bytes(their_public))
        salt = "|".join(sorted((id_a, id_b))).encode() + nonce_c + nonce_s
        key = _hkdf(shared, salt, b"winlink pair key")
        code = int.from_bytes(_hkdf(shared, salt, b"winlink pair code", 4), "big") % 10000
        return key, f"{code:04d}"


def proof(key, who, nonce_c, nonce_s, kind):
    return hmac.new(key, who + b"|" + nonce_c + nonce_s + kind.encode(), hashlib.sha256).digest()


def session_keys(key, nonce_c, nonce_s):
    """(client->server key, server->client key) for one connection."""
    okm = _hkdf(key, nonce_c + nonce_s, b"winlink session", 64)
    return okm[:32], okm[32:]


# ---------------------------------------------------------------- handshake lines (cleartext)
def send_json(sock, obj):
    sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())


def recv_json(sock, limit=MAX_HANDSHAKE_LINE):
    """One JSON line, read byte by byte so nothing meant for the encrypted part is swallowed."""
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("connection closed")
        if b == b"\n":
            break
        buf += b
        if len(buf) > limit:
            raise ValueError("handshake line too long")
    obj = json.loads(buf)
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    return obj


# ---------------------------------------------------------------- encrypted channel
class Channel:
    """An authenticated, encrypted connection. Looks enough like a socket + file for the rest of
    WinLink: sendall, read1, read, readline, settimeout, shutdown, close, getpeername."""

    def __init__(self, sock, send_key, recv_key, route):
        self.sock = sock
        self.route = route
        self._tx, self._rx = ChaCha20Poly1305(send_key), ChaCha20Poly1305(recv_key)
        self._tx_n = self._rx_n = 0
        self._buf = bytearray()
        self._wlock = threading.Lock()
        self._eof = False

    # -- writing
    def sendall(self, data):
        data = memoryview(bytes(data))
        with self._wlock:
            for i in range(0, max(len(data), 1), FRAME):
                chunk = bytes(data[i:i + FRAME])
                if not chunk and i:
                    break
                ct = self._tx.encrypt(struct.pack(">4xQ", self._tx_n), chunk, None)
                self._tx_n += 1
                self.sock.sendall(struct.pack(">I", len(ct)) + ct)

    # -- reading
    def _recv_exact(self, n):
        out = bytearray()
        while len(out) < n:
            b = self.sock.recv(n - len(out))
            if not b:
                return None
            out += b
        return bytes(out)

    def _fill(self):
        if self._eof:
            return False
        head = self._recv_exact(4)
        if head is None:
            self._eof = True
            return False
        (size,) = struct.unpack(">I", head)
        if size < 16 or size > FRAME + 16:
            raise ConnectionError("bad frame")
        ct = self._recv_exact(size)
        if ct is None:
            raise ConnectionError("connection closed mid-frame")
        try:
            self._buf += self._rx.decrypt(struct.pack(">4xQ", self._rx_n), ct, None)
        except Exception as e:  # noqa: BLE001  (tampered or out-of-order data)
            raise ConnectionError("data failed integrity check") from e
        self._rx_n += 1
        return True

    def read1(self, n=FRAME):
        while not self._buf:
            if not self._fill():
                return b""
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def read(self, n):
        out = bytearray()
        while len(out) < n:
            chunk = self.read1(n - len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def readline(self, limit=-1):
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                end = i + 1
                break
            if 0 < limit <= len(self._buf):
                end = limit
                break
            if not self._fill():
                end = len(self._buf)
                break
        if 0 < limit < end:
            end = limit
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    def send_json(self, obj):
        self.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def recv_json(self, limit=64 * 1024):
        line = self.readline(limit)
        if not line.endswith(b"\n"):
            raise ConnectionError("connection closed")
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        return obj

    # -- socket-ish
    def settimeout(self, t):
        self.sock.settimeout(t)

    def getpeername(self):
        return self.sock.getpeername()

    def shutdown(self, how=socket.SHUT_RDWR):
        self.sock.shutdown(how)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


# ---------------------------------------------------------------- abuse limits
class Guard:
    """Keeps floods and guessing from hurting WinLink.

    - at most MAX_TOTAL connections being handled at once, MAX_PER_IP from one address
    - new connections per address: RATE per second (bursts up to BURST)
    - an address that fails to prove itself FAIL_LIMIT times in a minute is ignored for a while
    - at most MAX_PROMPTS pairing questions on screen; a declined device waits COOLDOWN to ask again
    """
    MAX_TOTAL, MAX_PER_IP = 128, 16
    RATE, BURST = 8.0, 24.0
    FAIL_LIMIT, BLOCK_FOR = 5, 60
    MAX_PROMPTS, COOLDOWN = 3, 60

    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}           # ip -> open connections
        self.total = 0
        self.buckets = {}          # ip -> (tokens, last time)
        self.fails = {}            # ip -> [times]
        self.blocked = {}          # ip -> until
        self.prompts = 0
        self.declined = {}         # device id -> until

    @staticmethod
    def key(ip):
        return ip.split("%", 1)[0].removeprefix("::ffff:")

    def admit(self, ip):
        ip = self.key(ip)
        now = time.time()
        with self.lock:
            if self.blocked.get(ip, 0) > now:
                return False
            if self.total >= self.MAX_TOTAL or self.active.get(ip, 0) >= self.MAX_PER_IP:
                return False
            tokens, last = self.buckets.get(ip, (self.BURST, now))
            tokens = min(self.BURST, tokens + (now - last) * self.RATE)
            if tokens < 1:
                self.buckets[ip] = (tokens, now)
                return False
            self.buckets[ip] = (tokens - 1, now)
            self.active[ip] = self.active.get(ip, 0) + 1
            self.total += 1
            if len(self.buckets) > 4096:          # don't let spoofed addresses grow memory
                self._prune(now)
            return True

    def release(self, ip):
        ip = self.key(ip)
        with self.lock:
            n = self.active.get(ip, 0) - 1
            if n > 0:
                self.active[ip] = n
            else:
                self.active.pop(ip, None)
            self.total = max(0, self.total - 1)

    def failed(self, ip):
        """Count a failed proof. Returns True if this address is now blocked."""
        ip = self.key(ip)
        now = time.time()
        with self.lock:
            times = [t for t in self.fails.get(ip, []) if now - t < 60] + [now]
            self.fails[ip] = times
            if len(times) >= self.FAIL_LIMIT:
                self.blocked[ip] = now + self.BLOCK_FOR
                self.fails.pop(ip, None)
                return True
            return False

    def why_refused(self, ip):
        """A short reason to tell a refused connection (cheap; no thread needed)."""
        ip = self.key(ip)
        left = self.blocked.get(ip, 0) - time.time()
        if left > 0:
            return f"too many failed attempts from this device; try again in {int(left) + 1} s"
        return "busy; try again in a moment"

    def is_blocked(self, ip):
        return self.blocked.get(self.key(ip), 0) > time.time()

    def take_prompt(self, device_id):
        with self.lock:
            if self.prompts >= self.MAX_PROMPTS or self.declined.get(device_id, 0) > time.time():
                return False
            self.prompts += 1
            return True

    def end_prompt(self, device_id, declined):
        with self.lock:
            self.prompts = max(0, self.prompts - 1)
            if declined:
                self.declined[device_id] = time.time() + self.COOLDOWN

    def _prune(self, now):
        for ip, (_tokens, last) in list(self.buckets.items()):
            if now - last > 60 and ip not in self.active:
                del self.buckets[ip]
        for ip, until in list(self.blocked.items()):
            if until < now:
                del self.blocked[ip]


class RateLimit:
    """Simple per-key rate limit (for UDP announcements)."""

    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            tokens, last = self.buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= 1
            self.buckets[key] = (tokens - 1 if ok else tokens, now)
            if len(self.buckets) > 4096:
                self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 30}
            return ok
