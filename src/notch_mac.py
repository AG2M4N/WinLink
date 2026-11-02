"""
Drop files on the notch (macOS).

A borderless panel sits exactly over the camera notch, black on black, so it's
invisible. Drag files onto the notch and it springs open into a dark tray with
one card per connected device (plus "All devices" when there are several):
drop on a card to send there. A card lights up only while the pointer is over
it, and a drop anywhere else does nothing. Clicking the
notch opens WinLink. On Macs without a notch it's a slim pill at the top-centre
of the menu bar.

The tray is drawn by hand on a 60 fps timer (a small spring for the shape, a
fade and slide for the contents), so the motion stays smooth while a drag is in
progress, when ordinary delayed calls don't run.
"""

import objc
from AppKit import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSMakeRect,
    NSPanel,
    NSPasteboardTypeFileURL,
    NSPasteboardURLReadingFileURLsOnlyKey,
    NSScreen,
    NSShadow,
    NSStatusBar,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSURL, NSDate, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

DRAG_NONE, DRAG_COPY = 0, 1
MENU_LEVEL = 24                     # NSMainMenuWindowLevel
MARGIN = 26                         # room around the tray for the spring's overshoot and the shadow
MIN_W, BODY_H = 340, 104            # tray width (at least) and height below the notch
CARD_H, CARD_GAP, CARD_PAD = 54, 10, 14
EAR = 9                             # the little outward curves where the tray meets the top edge
RADIUS = 24                         # tray's bottom corners
FPS = 60.0

# springs: (stiffness, damping). Opening overshoots a touch; closing settles without bounce.
OPEN_SPRING = (300.0, 25.0)
CLOSE_SPRING = (420.0, 42.0)


def rgb(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def mix(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


INK = (0, 0, 0)
CARD = (30, 30, 33)
CARD_ON = (58, 98, 148)             # WinLink's blue, lit: the device a drop will go to
LIT_EDGE = (150, 190, 238)
LIT_GLOW = (80, 135, 205)
EDGE = (255, 255, 255)
TEXT = (238, 238, 240)
TEXT_2 = (146, 146, 152)
GOOD = (128, 186, 150)
BAD = (214, 140, 120)


def col(c, a=1.0):
    return rgb(*c, a)


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def ease_out(t):
    return 1 - (1 - t) ** 3


def notch_geometry():
    """(screen, notch rect in screen coordinates, has_notch)."""
    for screen in NSScreen.screens():
        top = screen.safeAreaInsets().top if hasattr(screen, "safeAreaInsets") else 0
        if top > 0:
            f = screen.frame()
            left, right = screen.auxiliaryTopLeftArea(), screen.auxiliaryTopRightArea()
            x = f.origin.x + left.size.width
            w = f.size.width - left.size.width - right.size.width
            return screen, NSMakeRect(x, f.origin.y + f.size.height - top, w, top), True
    screen = NSScreen.mainScreen()
    f = screen.frame()
    h = NSStatusBar.systemStatusBar().thickness()
    w = 160
    return screen, NSMakeRect(f.origin.x + (f.size.width - w) / 2, f.origin.y + f.size.height - h, w, h), False


def text_obj(text, size, color, weight=0.0):
    font = NSFont.systemFontOfSize_weight_(size, weight)
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color})


def short(name, limit=20):
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


def os_label(os_name, devices=0):
    if os_name == "all":
        return f"Send to all {devices}"
    return {"mac": "Mac", "windows": "Windows PC", "linux": "Linux", "android": "Android phone",
            "ios": "iPhone"}.get(os_name, "Computer")


def draw_device(kind, x, y, color, size=22, bg=None):
    """A small laptop (Mac) or monitor (PC) outline with its top-left at (x, y); flipped coordinates."""
    color.setStroke()
    s = size
    if kind == "all":                       # two screens, one behind the other
        for dx, dy in ((0.30, 0.10), (0.04, 0.32)):
            r = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x + s * dx, y + s * dy, s * 0.64, s * 0.44), 2, 2)
            r.setLineWidth_(1.5)
            if dy > 0.2:
                (bg or rgb(0, 0, 0)).setFill()   # front screen hides the one behind
                r.fill()
            r.stroke()
        return
    if kind in ("android", "ios"):                # a phone
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + s * 0.3, y + s * 0.06, s * 0.4, s * 0.86), 3, 3)
        body.setLineWidth_(1.5)
        body.stroke()
        return
    if kind == "mac":
        screen = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + s * 0.14, y + s * 0.2, s * 0.72, s * 0.48), 2, 2)
        screen.setLineWidth_(1.5)
        screen.stroke()
        base = NSBezierPath.bezierPath()
        base.moveToPoint_((x + s * 0.02, y + s * 0.78))
        base.lineToPoint_((x + s * 0.98, y + s * 0.78))
        base.setLineWidth_(1.6)
        base.setLineCapStyle_(1)
        base.stroke()
    else:
        screen = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + s * 0.06, y + s * 0.14, s * 0.88, s * 0.54), 2, 2)
        screen.setLineWidth_(1.5)
        screen.stroke()
        stand = NSBezierPath.bezierPath()
        stand.moveToPoint_((x + s * 0.5, y + s * 0.68))
        stand.lineToPoint_((x + s * 0.5, y + s * 0.84))
        stand.moveToPoint_((x + s * 0.3, y + s * 0.86))
        stand.lineToPoint_((x + s * 0.7, y + s * 0.86))
        stand.setLineWidth_(1.5)
        stand.setLineCapStyle_(1)
        stand.stroke()


def draw_check(cx, cy, r, color, ok):
    color.setStroke()
    ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r))
    ring.setLineWidth_(1.6)
    ring.stroke()
    mark = NSBezierPath.bezierPath()
    if ok:
        mark.moveToPoint_((cx - r * 0.42, cy + r * 0.02))
        mark.lineToPoint_((cx - r * 0.1, cy + r * 0.34))
        mark.lineToPoint_((cx + r * 0.44, cy - r * 0.3))
    else:
        mark.moveToPoint_((cx - r * 0.32, cy - r * 0.32))
        mark.lineToPoint_((cx + r * 0.32, cy + r * 0.32))
        mark.moveToPoint_((cx + r * 0.32, cy - r * 0.32))
        mark.lineToPoint_((cx - r * 0.32, cy + r * 0.32))
    mark.setLineWidth_(1.8)
    mark.setLineCapStyle_(1)
    mark.setLineJoinStyle_(1)
    mark.stroke()


class NotchView(NSView):
    def initWithOwner_(self, owner):
        self = objc.super(NotchView, self).init()
        if self is None:
            return None
        self.owner = owner
        self.p, self.v, self.goal = 0.0, 0.0, 0.0       # shape progress (0 = notch, 1 = tray), spring state
        self.spring = OPEN_SPRING
        self.fade = 0.0                                  # contents' opacity
        self.hover = None                                # device id under the cursor
        self.glow = {}                                   # device id -> hover highlight 0..1
        self.message = None                              # ("Sending …", ok) right after a drop
        self.drag_count = 0
        self.timer = None
        self.last_tick = 0.0
        self.registerForDraggedTypes_([NSPasteboardTypeFileURL])
        return self

    def isFlipped(self):
        return True                # lay out top-down

    def acceptsFirstMouse_(self, event):
        return True

    # ---- layout (in the expanded window's coordinates; independent of the animation)
    @objc.python_method
    def tray_size(self):
        n = self.owner.notch
        cards = self.card_specs()
        total = sum(c["w"] for c in cards) + CARD_GAP * max(0, len(cards) - 1)
        max_w = self.owner.screen.frame().size.width - 2 * MARGIN - 40
        w = min(max_w, max(MIN_W, n.size.width + 150, total + 48))
        return w, n.size.height + BODY_H

    @objc.python_method
    def card_specs(self):
        specs = []
        for t in self.owner.targets():
            tid, name = t[0], t[1]
            kind = t[2] if len(t) > 2 else "?"
            label = short(name)
            tw = max(text_obj(label, 13, col(TEXT), 0.23).size().width,
                     text_obj(os_label(kind, len(self.owner.targets()) - 1), 11, col(TEXT_2)).size().width)
            specs.append({"id": tid, "name": name, "label": label, "kind": kind,
                          "w": CARD_PAD + 22 + 11 + tw + CARD_PAD + 4})
        return specs

    @objc.python_method
    def card_rects(self):
        """[(rect, spec)] for the cards, centred under the notch."""
        W = self.bounds().size.width
        cards = self.card_specs()
        total = sum(c["w"] for c in cards) + CARD_GAP * max(0, len(cards) - 1)
        tw, _ = self.tray_size()
        avail = tw - 32
        scale = min(1.0, avail / total) if total else 1.0          # many devices: squeeze the cards
        x = (W - min(total, avail)) / 2
        y = self.owner.notch.size.height + 36
        out = []
        for c in cards:
            w = c["w"] * scale
            out.append((NSMakeRect(x, y, w, CARD_H), c))
            x += w + CARD_GAP * scale
        return out

    # ---- animation
    @objc.python_method
    def animate_to(self, goal):
        self.goal = goal
        self.spring = OPEN_SPRING if goal > 0 else CLOSE_SPRING
        self.start_timer()

    @objc.python_method
    def start_timer(self):
        if self.timer is None:
            self.last_tick = NSDate.timeIntervalSinceReferenceDate()
            self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / FPS, self, "tick:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)   # keeps running mid-drag

    def tick_(self, timer):
        now = NSDate.timeIntervalSinceReferenceDate()
        dt = clamp(now - self.last_tick, 0.001, 0.05)
        self.last_tick = now
        k, c = self.spring
        for _ in range(4):                     # a few small steps keep the spring stable
            h = dt / 4
            self.v += (k * (self.goal - self.p) - c * self.v) * h
            self.p += self.v * h
        if self.goal == 0:
            self.p = max(self.p, 0.0)
        # contents fade in once the tray is mostly open, and out quickly before it closes
        want = 1.0 if self.goal > 0 and self.p > 0.55 else 0.0
        rate = 9.0 if want else 16.0
        self.fade += (want - self.fade) * clamp(dt * rate)
        settled = abs(self.goal - self.p) < 0.002 and abs(self.v) < 0.02 and abs(want - self.fade) < 0.01
        for tid in set(self.glow) | ({self.hover} if self.hover else set()):
            g = self.glow.get(tid, 0.0)
            g += ((1.0 if tid == self.hover else 0.0) - g) * clamp(dt * 16)
            self.glow[tid] = g
            if 0.01 < g < 0.99:
                settled = False
        self.setNeedsDisplay_(True)
        if settled:
            self.p, self.v, self.fade = self.goal, 0.0, want
            timer.invalidate()
            self.timer = None
            if self.goal == 0:
                self.owner.did_collapse()

    # ---- drawing
    def drawRect_(self, rect):
        b = self.bounds()
        W = b.size.width
        n = self.owner.notch
        nw, nh = n.size.width, n.size.height
        tw, th = self.tray_size()
        p = self.p
        q = clamp(p)
        w = nw + (tw - nw) * p
        h = nh + (th - nh) * p
        r = min(8.0, nh / 2) + (RADIUS - min(8.0, nh / 2)) * q
        r = min(r, h / 2, w / 2)
        e = EAR * q
        x0, x1 = (W - w) / 2, (W + w) / 2

        path = NSBezierPath.bezierPath()
        path.moveToPoint_((x0 - e, 0))
        path.curveToPoint_controlPoint1_controlPoint2_((x0, e), (x0 - e * 0.35, 0), (x0, e * 0.35))
        path.lineToPoint_((x0, h - r))
        path.curveToPoint_controlPoint1_controlPoint2_((x0 + r, h), (x0, h - r * 0.45), (x0 + r * 0.45, h))
        path.lineToPoint_((x1 - r, h))
        path.curveToPoint_controlPoint1_controlPoint2_((x1, h - r), (x1 - r * 0.45, h), (x1, h - r * 0.45))
        path.lineToPoint_((x1, e))
        path.curveToPoint_controlPoint1_controlPoint2_((x1 + e, 0), (x1, e * 0.35), (x1 + e * 0.35, 0))
        path.closePath()

        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        if q > 0.01:                                     # soft shadow so the tray lifts off the desktop
            sh = NSShadow.alloc().init()
            sh.setShadowColor_(col(INK, 0.45 * q))
            sh.setShadowBlurRadius_(20 * q)
            sh.setShadowOffset_((0, -6 * q))
            sh.set()
        col(INK).setFill()
        path.fill()
        ctx.restoreGraphicsState()

        a = self.fade
        if a < 0.01:
            return
        ctx.saveGraphicsState()
        path.addClip()
        slide = (1 - ease_out(a)) * -8                   # contents drift down into place as they fade in
        self.draw_contents(W, nh, tw, a, slide)
        ctx.restoreGraphicsState()

    @objc.python_method
    def draw_contents(self, W, nh, tw, a, slide):
        left, right = (W - tw) / 2 + 22, (W + tw) / 2 - 22
        top = nh + 12 + slide
        if self.message:
            text, ok = self.message
            s = text_obj(text, 13, col(GOOD if ok else BAD, a), 0.23)
            sz = s.size()
            total = 22 + 10 + sz.width
            x = (W - total) / 2
            cy = nh + BODY_H / 2 + slide
            draw_check(x + 11, cy, 10, col(GOOD if ok else BAD, a), ok)
            s.drawAtPoint_((x + 32, cy - sz.height / 2))
            return
        cards = self.card_rects()
        if not cards:
            s = text_obj("No devices connected", 13, col(TEXT, a), 0.23)
            sub = text_obj("Open WinLink to connect one", 11.5, col(TEXT_2, a))
            cy = nh + BODY_H / 2 + slide
            s.drawAtPoint_(((W - s.size().width) / 2, cy - 17))
            sub.drawAtPoint_(((W - sub.size().width) / 2, cy + 3))
            return
        head = text_obj("Send to" if len(cards) > 1 else "Drop to send to", 11.5, col(TEXT_2, a), 0.3)
        head.drawAtPoint_((left, top))
        if self.drag_count:
            cnt = text_obj(f"{self.drag_count} item{'s' if self.drag_count != 1 else ''}", 11.5, col(TEXT_2, a))
            cnt.drawAtPoint_((right - cnt.size().width, top))
        devices = len(cards) - 1
        everyone = self.glow.get("all", 0.0)              # hovering "All devices" lights every card
        for rect, c in cards:
            g = self.glow.get(c["id"], 0.0)
            if c["id"] != "all":
                g = max(g, everyone)
            grow = 3 * g                                 # the lit card lifts slightly
            rr = NSMakeRect(rect.origin.x - grow, rect.origin.y + slide - grow,
                            rect.size.width + 2 * grow, rect.size.height + 2 * grow)
            box = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rr, 14, 14)
            ctx = NSGraphicsContext.currentContext()
            ctx.saveGraphicsState()
            if g > 0.01:                                 # soft blue glow around the lit card
                glow = NSShadow.alloc().init()
                glow.setShadowColor_(col(LIT_GLOW, 0.75 * g * a))
                glow.setShadowBlurRadius_(16 * g)
                glow.setShadowOffset_((0, 0))
                glow.set()
            col(mix(CARD, CARD_ON, g), a).setFill()
            box.fill()
            ctx.restoreGraphicsState()
            col(mix((60, 60, 66), LIT_EDGE, g), (0.5 + 0.5 * g) * a).setStroke()
            box.setLineWidth_(1.0 + 0.5 * g)
            box.stroke()
            ix = rr.origin.x + CARD_PAD
            cy = rr.origin.y + rr.size.height / 2
            draw_device(c["kind"], ix, cy - 11, col(mix(TEXT_2, (255, 255, 255), g), a),
                        bg=col(mix(CARD, CARD_ON, g)))
            tx = ix + 22 + 11
            name = text_obj(c["label"], 13, col(mix(TEXT, (255, 255, 255), g), a), 0.23 + 0.07 * g)
            sub = text_obj(os_label(c["kind"], devices), 11, col(mix(TEXT_2, (210, 226, 246), g), a))
            name.drawAtPoint_((tx, cy - 17))
            sub.drawAtPoint_((tx, cy + 1))

    # ---- drag and drop
    @objc.python_method
    def _target_at(self, info):
        """The card under the pointer (only when it's really over one), else None."""
        p = self.convertPoint_fromView_(info.draggingLocation(), None)
        for r, c in self.card_rects():
            if r.origin.x - 2 <= p.x <= r.origin.x + r.size.width + 2 and \
                    r.origin.y - 2 <= p.y <= r.origin.y + r.size.height + 2:
                return c["id"]
        return None

    def draggingEntered_(self, info):
        self.message = None
        items = info.draggingPasteboard().pasteboardItems()
        self.drag_count = len(items) if items else 0
        self.owner.expand()
        return self.draggingUpdated_(info)

    def draggingUpdated_(self, info):
        hover = self._target_at(info)
        if hover != self.hover:
            self.hover = hover
            self.start_timer()
        return DRAG_COPY if hover else DRAG_NONE

    def draggingExited_(self, info):
        self.hover = None
        self.owner.collapse_soon(0.18)

    def prepareForDragOperation_(self, info):
        return self.hover is not None

    def performDragOperation_(self, info):
        target = self.hover
        urls = info.draggingPasteboard().readObjectsForClasses_options_(
            [NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True}) or []
        paths = [str(u.path()) for u in urls]
        if not paths or target is None:
            return False
        targets = self.owner.targets()
        name = f"all {len(targets) - 1} devices" if target == "all" else \
            short(next((t[1] for t in targets if t[0] == target), ""), 24)
        ok = self.owner.on_drop(paths, target)
        count = f"{len(paths)} item{'s' if len(paths) != 1 else ''}"
        self.message = (f"Sending {count} to {name}" if ok else "Couldn't send", ok)
        self.hover = None
        self.fade = min(self.fade, 0.3)          # cross-fade from the cards to the message
        self.start_timer()
        self.owner.collapse_soon(1.5)
        return ok

    def mouseDown_(self, event):
        self.owner.on_click()

    def collapseNow_(self, timer):
        self.owner.collapse_timer = None
        self.hover = None
        self.animate_to(0.0)


class NotchDrop:
    """Create on the main thread. targets() -> [(id, name, os)], on_drop(paths, id) -> bool."""

    def __init__(self, targets, on_drop, on_click):
        self.targets, self.on_drop, self.on_click = targets, on_drop, on_click
        self.screen, self.notch, self.has_notch = notch_geometry()
        self.collapse_timer = None
        self.is_open = False
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self.notch, style, NSBackingStoreBuffered, False)
        self.panel.setLevel_(MENU_LEVEL + 3)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setMovable_(False)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorIgnoresCycle)
        self.view = NotchView.alloc().initWithOwner_(self)
        self.panel.setContentView_(self.view)
        self.panel.setFrame_display_(self.notch, True)
        self.panel.orderFrontRegardless()

    def open_frame(self):
        """The window while the tray is open: big enough for the tray, its overshoot and its shadow."""
        n = self.notch
        tw, th = self.view.tray_size()
        w, h = tw + 2 * MARGIN, th + MARGIN
        return NSMakeRect(n.origin.x + (n.size.width - w) / 2, n.origin.y + n.size.height - h, w, h)

    def _cancel_collapse(self):
        if self.collapse_timer is not None:
            self.collapse_timer.invalidate()
            self.collapse_timer = None

    def expand(self):
        self._cancel_collapse()
        if not self.is_open:
            # grow the (transparent) window at once; the view animates the black shape inside it
            self.is_open = True
            self.panel.setFrame_display_(self.open_frame(), False)
        self.view.animate_to(1.0)

    def collapse_soon(self, delay=0.18):
        self._cancel_collapse()
        self.collapse_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self.view, "collapseNow:", None, False)
        NSRunLoop.mainRunLoop().addTimer_forMode_(self.collapse_timer, NSRunLoopCommonModes)

    def did_collapse(self):
        """The shape is back to the notch: shrink the window to match."""
        self.is_open = False
        self.view.message = None
        self.view.glow.clear()
        self.panel.setFrame_display_(self.notch, True)

    def refresh(self):
        """Devices changed (any thread)."""
        def apply():
            if self.is_open:
                self.panel.setFrame_display_(self.open_frame(), True)
            self.view.setNeedsDisplay_(True)
        AppHelper.callAfter(apply)

    def close(self):
        self._cancel_collapse()
        if self.view.timer is not None:
            self.view.timer.invalidate()
            self.view.timer = None
        self.panel.orderOut_(None)
        self.panel.close()

