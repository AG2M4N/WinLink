"""
Trackpad: a paired phone moves this computer's pointer, clicks and scrolls (like Samsung DeX's
touchpad). The phone sends small messages on the control link (already encrypted and authenticated):

    {"type": "pointer", "op": "move",   "dx": 3.5, "dy": -1}         relative, in phone points
    {"type": "pointer", "op": "button", "b": "left", "down": true}   press or release (drag)
    {"type": "pointer", "op": "click",  "b": "left"|"right", "n": 1|2}   n 2: the second click of a double
    {"type": "pointer", "op": "scroll", "dx": 0, "dy": 12}           how far two fingers moved (down/right
                                                                     positive); the content follows them

Only while "Accept keyboard and mouse from other computers" is on (Settings). The pointer is moved
with the system's own input functions: user32 on Windows, CoreGraphics on a Mac (needs WinLink under
Privacy & Security > Accessibility, as keyboard sharing does), XTest on Linux with X11 (not Wayland).
"""

import ctypes
import ctypes.util
import logging
import math
import os
import sys
import threading

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

SPEED = 1.6           # pointer pixels per phone point (the phone also accelerates fast swipes)
SCROLL_STEP = 1.0     # scroll: phone points per pixel (Mac) / per 1/120 of a wheel notch (Windows)
MAX_STEP = 4000       # ignore absurd values


def _num(v, limit=MAX_STEP):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0.0
    return max(-limit, min(limit, float(v)))


class _Windows:
    MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, HWHEEL = 0x1, 0x2, 0x4, 0x8, 0x10, 0x800, 0x1000

    def __init__(self):
        self.u = ctypes.windll.user32
        self.u.mouse_event.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_size_t]
        self.rx = self.ry = 0.0

    def move(self, dx, dy):
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)          # keep the fractions for next time: slow moves still move
        self.rx -= ix
        self.ry -= iy
        if ix or iy:
            self.u.mouse_event(self.MOVE, ix, iy, 0, 0)

    def button(self, b, down):
        flag = (self.LDOWN if down else self.LUP) if b == "left" else (self.RDOWN if down else self.RUP)
        self.u.mouse_event(flag, 0, 0, 0, 0)

    def scroll(self, dx, dy):
        # 120 = one wheel notch. Fingers down: content down = scroll up = positive. Fingers right:
        # content right = scroll left = negative horizontal wheel.
        if dy:
            self.u.mouse_event(self.WHEEL, 0, 0, int(dy * SCROLL_STEP * 4), 0)
        if dx:
            self.u.mouse_event(self.HWHEEL, 0, 0, int(-dx * SCROLL_STEP * 4), 0)

    def ok(self):
        return True


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGPoint)]


class _Mac:
    # CGEventType
    MOVED, LDOWN, LUP, RDOWN, RUP, LDRAG = 5, 1, 2, 3, 4, 6
    CLICK_STATE = 1           # kCGMouseEventClickState
    DELTA_X, DELTA_Y = 4, 5   # kCGMouseEventDeltaX / DeltaY (apps that read raw motion, e.g. games)

    def __init__(self):
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.cg, self.cf = cg, cf
        vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        cg.CGEventCreate.restype, cg.CGEventCreate.argtypes = vp, [vp]
        cg.CGEventGetLocation.restype, cg.CGEventGetLocation.argtypes = _CGPoint, [vp]
        cg.CGEventCreateMouseEvent.restype = vp
        cg.CGEventCreateMouseEvent.argtypes = [vp, u32, _CGPoint, u32]
        # The non-variadic version (macOS 10.13+): varargs aren't safe through ctypes on Apple Silicon
        cg.CGEventCreateScrollWheelEvent2.restype = vp
        cg.CGEventCreateScrollWheelEvent2.argtypes = [vp, u32, u32, i32, i32, i32]
        cg.CGEventSetIntegerValueField.argtypes = [vp, u32, ctypes.c_int64]
        cg.CGEventPost.argtypes = [u32, vp]
        cg.CGGetActiveDisplayList.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
        cg.CGDisplayBounds.restype, cg.CGDisplayBounds.argtypes = _CGRect, [u32]
        cf.CFRelease.argtypes = [vp]
        self.held = None                 # "left" while dragging
        self.last_click = (0.0, 0)
        self.ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self.ax.AXIsProcessTrusted.restype = ctypes.c_bool

    def ok(self):
        try:
            return bool(self.ax.AXIsProcessTrusted())
        except Exception:  # noqa: BLE001
            return True

    def _where(self):
        e = self.cg.CGEventCreate(None)
        try:
            return self.cg.CGEventGetLocation(e)
        finally:
            self.cf.CFRelease(e)

    def _screens(self):
        ids = (ctypes.c_uint32 * 16)()
        n = ctypes.c_uint32()
        self.cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n))
        return [self.cg.CGDisplayBounds(ids[i]) for i in range(n.value)]

    def _clamp(self, x, y):
        rects = self._screens()
        for r in rects:          # already on a screen: fine
            if r.origin.x <= x < r.origin.x + r.size.x and r.origin.y <= y < r.origin.y + r.size.y:
                return x, y
        best = None              # off every screen: the nearest point on the nearest screen
        for r in rects:
            cx = min(max(x, r.origin.x), r.origin.x + r.size.x - 1)
            cy = min(max(y, r.origin.y), r.origin.y + r.size.y - 1)
            d = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or d < best[0]:
                best = (d, cx, cy)
        return (best[1], best[2]) if best else (x, y)

    def _post(self, kind, at, button=0, clicks=None, delta=None):
        e = self.cg.CGEventCreateMouseEvent(None, kind, at, button)
        if not e:
            return
        try:
            if clicks:
                self.cg.CGEventSetIntegerValueField(e, self.CLICK_STATE, clicks)
            if delta:
                self.cg.CGEventSetIntegerValueField(e, self.DELTA_X, int(delta[0]))
                self.cg.CGEventSetIntegerValueField(e, self.DELTA_Y, int(delta[1]))
            self.cg.CGEventPost(0, e)           # kCGHIDEventTap
        finally:
            self.cf.CFRelease(e)

    def move(self, dx, dy):
        p = self._where()
        x, y = self._clamp(p.x + dx, p.y + dy)
        kind = self.LDRAG if self.held == "left" else self.MOVED
        self._post(kind, _CGPoint(x, y), delta=(dx, dy))

    def button(self, b, down, clicks=1):
        at = self._where()
        if b == "left":
            self.held = "left" if down else None
            self._post(self.LDOWN if down else self.LUP, at, 0, clicks)
        else:
            self._post(self.RDOWN if down else self.RUP, at, 1, clicks)

    def click(self, b, n):
        # macOS counts clicks itself only for real mice: say which click of a double this is
        self.button(b, True, n)
        self.button(b, False, n)

    def scroll(self, dx, dy):
        # 0 = kCGScrollEventUnitPixel; positive moves the content down / right: it follows the fingers
        e = self.cg.CGEventCreateScrollWheelEvent2(None, 0, 2, int(dy * SCROLL_STEP), int(dx * SCROLL_STEP), 0)
        if e:
            try:
                self.cg.CGEventPost(0, e)
            finally:
                self.cf.CFRelease(e)


class _X11:
    def __init__(self):
        if not os.environ.get("DISPLAY"):
            raise OSError("no X11 display (Wayland desktops don't let apps move the pointer)")
        x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
        xt = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x11.XOpenDisplay.restype, x11.XOpenDisplay.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        xt.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.d = x11.XOpenDisplay(None)
        if not self.d:
            raise OSError("can't open the X11 display")
        self.x11, self.xt = x11, xt
        self.rx = self.ry = 0.0
        self.sx = self.sy = 0.0

    def ok(self):
        return True

    def move(self, dx, dy):
        self.rx += dx
        self.ry += dy
        ix, iy = int(self.rx), int(self.ry)
        self.rx -= ix
        self.ry -= iy
        if ix or iy:
            self.xt.XTestFakeRelativeMotionEvent(self.d, ix, iy, 0)
            self.x11.XFlush(self.d)

    def button(self, b, down):
        self.xt.XTestFakeButtonEvent(self.d, 1 if b == "left" else 3, 1 if down else 0, 0)
        self.x11.XFlush(self.d)

    def scroll(self, dx, dy):
        # X11 scrolls in whole wheel clicks (buttons 4 up, 5 down, 6 left, 7 right): add up the
        # fingers' movement. Fingers down = content down = scroll up (4); right = scroll left (6).
        self.sx += dx
        self.sy += dy
        for attr, neg, pos in (("sy", 5, 4), ("sx", 7, 6)):
            v = getattr(self, attr)
            while abs(v) >= 30:
                btn = pos if v > 0 else neg
                self.xt.XTestFakeButtonEvent(self.d, btn, 1, 0)
                self.xt.XTestFakeButtonEvent(self.d, btn, 0, 0)
                v -= 30 if v > 0 else -30
            setattr(self, attr, v)
        self.x11.XFlush(self.d)


class Pointer:
    """Applies trackpad messages from phones. allowed: callable -> bool (the Settings switch)."""

    def __init__(self, allowed, backend=None):
        self.allowed = allowed
        self.lock = threading.Lock()
        self.backend = backend
        self.error = None
        self.told = set()               # phones already told why it can't work here

    def _get(self):
        if self.backend is None and self.error is None:
            try:
                self.backend = _Windows() if IS_WIN else _Mac() if IS_MAC else _X11()
            except Exception as e:  # noqa: BLE001
                self.error = str(e) or type(e).__name__
                log.info("trackpad: can't move the pointer here: %s", self.error)
        return self.backend

    def _refuse(self, peer, reason):
        if peer.id in self.told:
            return
        self.told.add(peer.id)
        try:
            peer.control.send({"type": "pointer", "op": "no", "reason": reason})
        except (OSError, ValueError, AttributeError):
            pass

    def handle(self, peer, msg):
        if not self.allowed():
            self._refuse(peer, "This computer doesn't accept keyboard and mouse from other devices (Settings).")
            return
        with self.lock:
            b = self._get()
            if b is None:
                self._refuse(peer, f"This computer can't be controlled from a phone: {self.error}.")
                return
            if not b.ok():
                self._refuse(peer, "Allow WinLink under Privacy & Security › Accessibility on the Mac.")
                return
            self.told.discard(peer.id)
            op = msg.get("op")
            try:
                if op == "move":
                    b.move(_num(msg.get("dx")) * SPEED, _num(msg.get("dy")) * SPEED)
                elif op == "scroll":
                    b.scroll(_num(msg.get("dx")), _num(msg.get("dy")))
                elif op == "button" and msg.get("b") in ("left", "right"):
                    b.button(msg["b"], msg.get("down") is True)
                elif op == "click" and msg.get("b") in ("left", "right"):
                    n = 2 if msg.get("n") == 2 else 1
                    if hasattr(b, "click"):
                        b.click(msg["b"], n)
                    else:                    # Windows and X11 spot a double click by its timing
                        b.button(msg["b"], True)
                        b.button(msg["b"], False)
            except Exception as e:  # noqa: BLE001  (one bad event shouldn't end the link)
                log.debug("trackpad: %s failed: %s", op, e)
