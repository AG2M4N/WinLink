"""The phone trackpad on Linux: WinLink's virtual mouse (uinput). Run as root (or with access to
/dev/uinput and /dev/input), with the uinput module loaded. Creates the device through
pointer.Pointer, as the app does, and reads back what the kernel received from its event node."""

import os
import select
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
os.environ.pop("DISPLAY", None)                 # no X11 here: uinput or nothing
import pointer  # noqa: E402

EV = struct.Struct("@llHHi")
EV_KEY, EV_REL = 1, 2
fails = []


def check(what, ok):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        fails.append(what)


class Peer:
    id = "phone"

    class control:
        sent = []

        @staticmethod
        def send(m):
            Peer.control.sent.append(m)


p = pointer.Pointer(lambda: True)
peer = Peer()
p.handle(peer, {"type": "pointer", "op": "move", "dx": 0, "dy": 0})
check("virtual mouse created", isinstance(p.backend, pointer._Uinput))
if p.backend is None:
    print("error:", p.error)
    sys.exit(1)

# Find its event node.
node = None
for _ in range(50):
    with open("/proc/bus/input/devices") as f:
        for block in f.read().split("\n\n"):
            if 'Name="WinLink trackpad"' in block:
                for line in block.splitlines():
                    if line.startswith("H: Handlers="):
                        node = next((h for h in line.split("=", 1)[1].split() if h.startswith("event")), None)
    if node:
        break
    time.sleep(0.1)
check("listed by the kernel with an event node", node is not None)
if not node:
    sys.exit(1)
fd = os.open("/dev/input/" + node, os.O_RDONLY | os.O_NONBLOCK)
time.sleep(0.2)


def drain():
    out = []
    while select.select([fd], [], [], 0.3)[0]:
        try:
            data = os.read(fd, EV.size * 64)
        except BlockingIOError:
            break
        for i in range(0, len(data), EV.size):
            _, _, t, c, v = EV.unpack_from(data, i)
            if t in (EV_KEY, EV_REL):
                out.append((t, c, v))
    return out


drain()
p.handle(peer, {"type": "pointer", "op": "move", "dx": 10, "dy": -5})
ev = drain()
x = sum(v for t, c, v in ev if t == EV_REL and c == 0)
y = sum(v for t, c, v in ev if t == EV_REL and c == 1)
check(f"move 10,-5 -> {x},{y} pixels (speed {pointer.SPEED})", x == int(10 * pointer.SPEED) and y == int(-5 * pointer.SPEED))

p.handle(peer, {"type": "pointer", "op": "move", "dx": 0.3, "dy": 0})
p.handle(peer, {"type": "pointer", "op": "move", "dx": 0.3, "dy": 0})
p.handle(peer, {"type": "pointer", "op": "move", "dx": 0.3, "dy": 0})
ev = drain()
check("slow moves add up", sum(v for t, c, v in ev if t == EV_REL and c == 0) == 1)

p.handle(peer, {"type": "pointer", "op": "click", "b": "left"})
ev = drain()
check("left click: press then release", [(c, v) for t, c, v in ev if t == EV_KEY] == [(0x110, 1), (0x110, 0)])

p.handle(peer, {"type": "pointer", "op": "button", "b": "right", "down": True})
p.handle(peer, {"type": "pointer", "op": "button", "b": "right", "down": False})
ev = drain()
check("right button down and up", [(c, v) for t, c, v in ev if t == EV_KEY] == [(0x111, 1), (0x111, 0)])

p.handle(peer, {"type": "pointer", "op": "scroll", "dx": 0, "dy": 45})       # fingers down 45 points
ev = drain()
hi = sum(v for t, c, v in ev if t == EV_REL and c == 11)
lo = sum(v for t, c, v in ev if t == EV_REL and c == 8)
check(f"scroll: fingers down -> wheel up, smooth {hi}/180 and {lo}/1 notch", hi == 180 and lo == 1)
p.handle(peer, {"type": "pointer", "op": "scroll", "dx": 0, "dy": 15})
ev = drain()
check("the rest of the notch comes with the next scroll",
      sum(v for t, c, v in ev if t == EV_REL and c == 8) == 1)
p.handle(peer, {"type": "pointer", "op": "scroll", "dx": 30, "dy": 0})        # fingers right
ev = drain()
check("fingers right -> wheel left", sum(v for t, c, v in ev if t == EV_REL and c == 12) == -120
      and sum(v for t, c, v in ev if t == EV_REL and c == 6) == -1)

check("the phone was told nothing went wrong", not Peer.control.sent)
p.backend.close()
os.close(fd)
print("\n" + ("All passed." if not fails else f"{len(fails)} failed."))
sys.exit(1 if fails else 0)
