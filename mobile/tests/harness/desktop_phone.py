"""A phone placed next to this computer, with the real Deskflow engine (WINLINK_DESKFLOW or an installed
one). The phone is a stand-in: it says it's ready, and records what the computer sends it. Checks that
WinLink starts Deskflow's server with the phone's screen, joins it for the phone (kvm_phone.py), tells
the phone, and drops it again. On Windows it also pushes the real mouse off the right edge of the
screen and expects the phone to get the pointer."""
import os, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
os.environ["LINK_KVM_PORT"] = "24879"         # not the real WinLink's
import kvm

results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

if not kvm.find_core():
    print("SKIP: Deskflow isn't installed"); sys.exit(0)


class Ctl:
    route = "wifi"
    def __init__(self): self.sent, self.lock = [], threading.Lock()
    def send(self, obj):
        with self.lock: self.sent.append(obj)


class Peer:
    def __init__(self, pid, name):
        self.id, self.name, self.os, self.control = pid, name, "android", Ctl()
    def addresses(self): return []


class Node:
    name, id, embedded, anywhere = "ZZ Desk 7731", "d" * 32, None, {}
    def __init__(self, peers): self.peers = {p.id: p for p in peers}


class Settings:
    def __init__(self): self.data = {}
    def __setitem__(self, k, v): self.data[k] = v


home = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(home, exist_ok=True)
phone = Peer("p" * 32, "ZZ Phone 7731")
node = Node([phone])
sh = kvm.Sharing(node, Settings(), os.path.join(home, "kvm"), lambda: None)
lines = []
_on_line = sh._on_line
sh.engine.on_event = lambda line: (lines.append(line), _on_line(line))
sent = phone.control.sent

def wait(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond(): return True
        time.sleep(0.1)
    return False

sh.set_share(True)
sh.set_grid({phone.id: [1, 0]})
check("not placed until the phone says it's ready", sh.role is None and not sh.phone_screens)
sh.on_message(phone, {"op": "phone", "ready": True, "w": 620, "h": 1340, "why": ""})
check("Deskflow's server starts with the phone's screen", wait(lambda: sh.role == "server" and sh.engine.running, 15))
with open(sh.files.server_conf, encoding="utf-8") as f:
    layout = f.read()
check("the layout links the phone", kvm.screen_name(phone.name, phone.id) in layout, layout)
on = wait(lambda: any(m.get("type") == "kvm" and m.get("op") == "screen" and m.get("on") for m in sent), 30)
check("joined for the phone, and the phone is told", on, "\n".join(lines[-15:]))
st = sh.state([phone])
c = next((x for x in st["computers"] if x["id"] == phone.id), {})
check("the window shows it connected", c.get("on") is True and c.get("text", "").startswith("Connected"), str(c))

if os.environ.get("WINLINK_FAKE_CORE") and on:     # fake_deskflow_core.py moves the pointer by itself
    left = wait(lambda: "leave" in [m.get("e") for m in sent], 5)
    es = [m.get("e") for m in sent if m.get("type") == "remote"]
    check("the stand-in's pointer reaches the phone", left and es[:1] == ["enter"] and "move" in es and "up" in es, str(es))
elif os.name == "nt" and on:
    import ctypes
    u = ctypes.windll.user32
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    u.SetCursorPos(w // 2, h // 2)
    time.sleep(0.3)
    for _ in range(60):                       # a real (injected) mouse move to the right
        u.mouse_event(0x0001, 40, 0, 0, 0)
        time.sleep(0.02)
    got = wait(lambda: any(m.get("type") == "remote" and m.get("e") == "enter" for m in sent), 5)
    for _ in range(10):
        u.mouse_event(0x0001, 15, 5, 0, 0)
        time.sleep(0.03)
    moved = wait(lambda: any(m.get("type") == "remote" and m.get("e") == "move" for m in sent), 3)
    u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0)       # a click on the phone
    clicked = wait(lambda: any(m.get("type") == "remote" and m.get("e") == "up" for m in sent), 3)
    remote = [m for m in sent if m.get("type") == "remote"]
    print("INFO the phone got:", remote[:3], "…", remote[-3:])
    check("the mouse crosses onto the phone", got)
    check("the pointer moves on the phone", moved)
    check("a click reaches the phone", clicked)
    for _ in range(80):                       # and back
        u.mouse_event(0x0001, -40, 0, 0, 0)
        time.sleep(0.02)
    check("and leaves it again", wait(lambda: any(m.get("type") == "remote" and m.get("e") == "leave" for m in sent), 5))

sh.on_message(phone, {"op": "phone", "ready": False, "w": 620, "h": 1340, "why": "turned off"})
check("turned off on the phone: dropped", wait(lambda: not sh.phone_screens, 5))
check("and the phone is told", any(m.get("op") == "screen" and m.get("on") is False for m in sent))
st = sh.state([phone])
c = next((x for x in st["computers"] if x["id"] == phone.id), {})
check("the window says why", c.get("text") == "turned off", str(c))
sh.stop_all()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
