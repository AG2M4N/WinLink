"""
A phone as one of Deskflow's screens, so the computer's mouse and keyboard reach it off a screen
edge like any other computer's.

Phones can't run Deskflow. Instead, the sharing computer joins its own Deskflow server on the
phone's behalf: PhoneScreen speaks Deskflow's protocol (version 1.6) over TLS to 127.0.0.1, with
this computer's own certificate (trusted for the purpose), and passes what it's told on to the
phone as small JSON messages over WinLink's encrypted link:

  {"type": "remote", "e": "enter", "x", "y"}  /  {"e": "leave"}
  {"e": "move", "x", "y"}                      the pointer, in the screen size the phone gave
  {"e": "down" | "up", "b"}                    1 left, 2 middle, 3 right, 4 back, 5 forward
  {"e": "wheel", "dx", "dy"}                   120 per notch, up/right positive
  {"e": "key", "k": "down" | "up" | "repeat", "id", "mask", "n"}   Deskflow's key ids and modifiers

The phone (its accessibility service) draws the pointer and turns these into taps, swipes and typing.
"""

import hashlib
import logging
import select
import socket
import ssl
import struct
import threading
import time

log = logging.getLogger("winlink")

VERSION = (1, 6)           # Deskflow then uses its plain messages (no keyboard-language field)
SHIFT, CTRL, ALT, META, SUPER, ALTGR = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
MASK_OF = (0, SHIFT, CTRL, ALT, META, SUPER, ALTGR)          # by Deskflow's modifier id 0..6
MOD_KEY = {0xEFE1: (1, 0), 0xEFE2: (1, 1), 0xEFE3: (2, 0), 0xEFE4: (2, 1), 0xEFE9: (3, 0), 0xEFEA: (3, 1),
           0xEFE7: (4, 0), 0xEFE8: (4, 1), 0xEFEB: (5, 0), 0xEFEC: (5, 1), 0xEF7E: (6, 0)}
KEY_OF = {1: (0xEFE1, 0xEFE2), 2: (0xEFE3, 0xEFE4), 3: (0xEFE9, 0xEFEA), 4: (0xEFE7, 0xEFE8),
          5: (0xEFEB, 0xEFEC), 6: (0xEF7E, 0xEF7E)}
OPTION_MOD = {b"MMFS": 1, b"MMFC": 2, b"MMFA": 3, b"MMFG": 6, b"MMFM": 4, b"MMFR": 5}
MAX_MESSAGE = 4 * 2 ** 20


class Closed(Exception):
    pass


class Modifiers:
    """Deskflow's per-screen modifier swaps (modifierCtrl=super...), which its own client applies."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.table = list(range(7))

    def options(self, values):
        for i in range(0, len(values) - 1, 2):
            code = struct.pack(">I", values[i])
            if code in OPTION_MOD and 0 <= values[i + 1] < 7:
                self.table[OPTION_MOD[code]] = values[i + 1]

    def key(self, kid):
        if kid not in MOD_KEY:
            return kid
        mod, side = MOD_KEY[kid]
        to = self.table[mod]
        return KEY_OF[to][side] if to in KEY_OF else kid

    def mask(self, mask):
        out = mask & ~(SHIFT | CTRL | ALT | META | SUPER | ALTGR)
        for mod in range(1, 7):
            if mask & MASK_OF[mod]:
                out |= MASK_OF[self.table[mod]]
        return out


class PhoneScreen:
    """One phone's place in the arrangement, while it's there. send(dict) goes to the phone;
    on_state(on) says whether Deskflow has it as a screen right now."""

    MOVE_GAP = 0.012           # at most ~80 pointer updates a second to the phone
    RETRY = 1.5

    def __init__(self, name, cert, fingerprint, size, send, on_state, port):
        self.name, self.cert, self.fp, self.port = name, cert, fingerprint, port
        self.w, self.h = size
        self.send_phone, self.on_state = send, on_state
        self.stopped = threading.Event()
        self.sock = None
        self.lock = threading.Lock()          # writes to Deskflow
        self.on = False
        self.mods = Modifiers()
        self.x, self.y = self.w // 2, self.h // 2
        self.pending = None                   # a pointer position not sent to the phone yet
        self.last_sent = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"kvm phone {name}")

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        s = self.sock
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def resize(self, w, h):
        """The phone turned (or its screen changed): tell Deskflow its new size."""
        if (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        self.x, self.y = min(self.x, w - 1), min(self.y, h - 1)
        if self.sock is not None and self.on:
            try:
                self._info()
            except (OSError, Closed):
                pass

    # ---- the connection to Deskflow
    def _run(self):
        while not self.stopped.is_set():
            try:
                self._session()
            except (OSError, Closed, ssl.SSLError, ValueError, struct.error) as e:
                if not self.stopped.is_set():
                    log.debug("keyboard and mouse: %s's screen: %s", self.name, e)
            finally:
                self._set_on(False)
                s, self.sock = self.sock, None
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass
            self.stopped.wait(self.RETRY)

    def _session(self):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                  # checked below: it must be our own server
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert)
        s = ctx.wrap_socket(raw)
        self.sock = s
        der = s.getpeercert(binary_form=True) or b""
        if hashlib.sha256(der).hexdigest() != self.fp:
            raise Closed("that isn't this computer's Deskflow")
        s.settimeout(15)
        hello = self._recv()
        proto = hello[:7]
        if proto not in (b"Barrier", b"Synergy") or len(hello) < 11:
            raise Closed(f"unexpected hello {hello[:16]!r}")
        name = self.name.encode()
        self._send(proto + struct.pack(">hh", *VERSION) + struct.pack(">I", len(name)) + name)
        self.mods.reset()
        while not self.stopped.is_set():
            wait = None
            if self.pending is not None:
                wait = max(0.0, self.MOVE_GAP - (time.monotonic() - self.last_sent))
            if s.pending() == 0:
                ready, _, _ = select.select([s], [], [], 15 if wait is None else wait)
                if not ready:
                    if self.pending is not None:
                        self._flush_move()
                        continue
                    raise Closed("Deskflow stopped answering")
            self._handle(self._recv())
            if self.pending is not None and s.pending() == 0 \
                    and time.monotonic() - self.last_sent >= self.MOVE_GAP:
                self._flush_move()

    def _recv(self):
        n = struct.unpack(">I", self._exact(4))[0]
        if n > MAX_MESSAGE:
            raise Closed("message too long")
        return self._exact(n)

    def _exact(self, n):
        out = b""
        while len(out) < n:
            part = self.sock.recv(n - len(out))
            if not part:
                raise Closed("Deskflow closed the connection")
            out += part
        return out

    def _send(self, payload):
        with self.lock:
            self.sock.sendall(struct.pack(">I", len(payload)) + payload)

    def _info(self):
        self._send(b"DINF" + struct.pack(">hhhhhhh", 0, 0, self.w, self.h, 0, self.x, self.y))

    def _set_on(self, on):
        if on != self.on:
            self.on = on
            if not on:
                self.pending = None
            try:
                self.on_state(on)
            except Exception:  # noqa: BLE001
                log.exception("keyboard and mouse: phone state")

    # ---- what Deskflow says
    def _handle(self, msg):
        code, body = msg[:4], msg[4:]
        if code == b"QINF":
            self._info()
        elif code == b"CIAK":
            self._set_on(True)
        elif code == b"CALV":
            self._send(b"CALV")
        elif code == b"CROP":
            self.mods.reset()
        elif code == b"DSOP":
            n = struct.unpack(">I", body[:4])[0]
            self.mods.options(list(struct.unpack(f">{n}I", body[4:4 + 4 * n])))
        elif code == b"CINN":
            x, y, _seq, _mask = struct.unpack(">hhIH", body[:10])
            self.x, self.y = x, y
            self.pending = None
            self._phone({"e": "enter", "x": x, "y": y})
        elif code == b"COUT":
            self._flush_move()
            self._phone({"e": "leave"})
        elif code == b"DMMV":
            self.x, self.y = struct.unpack(">hh", body[:4])
            self.pending = (self.x, self.y)
        elif code == b"DMRM":
            dx, dy = struct.unpack(">hh", body[:4])
            self.x = max(0, min(self.w - 1, self.x + dx))
            self.y = max(0, min(self.h - 1, self.y + dy))
            self.pending = (self.x, self.y)
        elif code in (b"DMDN", b"DMUP"):
            self._flush_move()
            self._phone({"e": "down" if code == b"DMDN" else "up", "b": body[0] if body else 1})
        elif code == b"DMWM":
            self._flush_move()
            dx, dy = struct.unpack(">hh", body[:4]) if len(body) >= 4 else (0, struct.unpack(">h", body[:2])[0])
            self._phone({"e": "wheel", "dx": dx, "dy": dy})
        elif code in (b"DKDN", b"DKUP"):
            kid, mask = struct.unpack(">HH", body[:4])
            self._key("down" if code == b"DKDN" else "up", kid, mask, 1)
        elif code == b"DKRP":
            kid, mask, count = struct.unpack(">HHH", body[:6])
            self._key("repeat", kid, mask, count)
        elif code == b"CBYE":
            raise Closed("Deskflow said goodbye")
        elif code in (b"EICV", b"EBSY", b"EUNK", b"EBAD"):
            raise Closed({b"EICV": "Deskflow says the versions don't match", b"EBSY": "Deskflow says the name is in use",
                          b"EUNK": "Deskflow doesn't know this screen", b"EBAD": "Deskflow refused a message"}[code])
        # CNOP, clipboard (off), screen saver, language, drag and drop: nothing to do

    def _key(self, kind, kid, mask, count):
        self._flush_move()
        self._phone({"e": "key", "k": kind, "id": self.mods.key(kid), "mask": self.mods.mask(mask), "n": count})

    def _flush_move(self):
        if self.pending is None:
            return
        x, y = self.pending
        self.pending = None
        self.last_sent = time.monotonic()
        self._phone({"e": "move", "x": x, "y": y})

    def _phone(self, body):
        try:
            self.send_phone(body)
        except (OSError, ValueError, AttributeError, ConnectionError):
            pass
