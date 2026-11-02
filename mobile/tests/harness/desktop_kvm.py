"""Keyboard and mouse sharing between two WinLink desktop nodes on this computer, with the real
Deskflow engine (needs Deskflow installed). A shares and places B on its right; WinLink must swap
fingerprints and get B's Deskflow client connected to A's server by itself."""
import importlib, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
T = sys.argv[1]
os.environ["LINK_NO_BEACON"] = "1"
import net, kvm
results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

def make(home, port, probe, name):
    os.environ.update(LINK_HOME=home, LINK_PORT=str(port), LINK_PROBE_PORT=str(probe), LINK_NAME=name)
    mod = importlib.reload(net)
    box = {}
    def emit(kind, *a):
        sh = box.get("kvm")
        if sh is None:
            return
        if kind == "kvm_msg":
            sh.on_message(*a)
        elif kind == "peers":
            sh.refresh()
    n = mod.Node(mod.Settings(), emit)
    box["kvm"] = kvm.Sharing(n, n.settings, os.path.join(home, "kvm"), lambda: None)
    n.start()
    time.sleep(0.8)        # let it bind its port before the next node reloads the module's settings
    return n, box["kvm"]

if not kvm.find_core():
    print("SKIP: Deskflow isn't installed"); sys.exit(0)
A, ka = make(T + "/A", 51990, 51991, "ZZ Desk A 7731")
B, kb = make(T + "/B", 51991, 51990, "ZZ Desk B 7731")
C, kc = make(T + "/C", 51992, 51990, "ZZ Desk C 7731")
time.sleep(0.5)

def pair(other, port):
    info = A._probe("127.0.0.1", port); A._seen(info, "127.0.0.1")
    p = A.peers[info["id"]]
    p.port = port          # in one process the nodes share net's module settings: use the real port
    A.connect(p, manual=True)
    for _ in range(100):
        q = other.peers.get(A.id)
        if q and q.incoming: other.answer(q, True); break
        time.sleep(0.05)
    for _ in range(100):
        if p.control: break
        time.sleep(0.05)
    return p
pB = pair(B, 51991)
time.sleep(0.5)
pC = pair(C, 51992)
check("paired with B and C", pB.control is not None and pC.control is not None)

ka.set_share(True)
ka.set_side(pB.id, "right")
for _ in range(150):
    if ka.clients.get(pB.id, {}).get("state") == "on": break
    time.sleep(0.1)
c = ka.clients.get(pB.id, {})
check("B answered with its fingerprint", len(c.get("fp", "")) == 64)
check("B follows A's keyboard and mouse", kb.role == "client" and kb.server_peer is not None, kb.status)
check("A's Deskflow server sees B connected", c.get("state") == "on", c.get("state"))
check("Deskflow's clipboard is off", "enableClipboard=false" in open(ka.files.settings).read())
check("layout: B on A's right", "right = " + c.get("screen", "?") in open(ka.files.server_conf).read())

# any arrangement: C to the right of B, not touching A (Deskflow links A-B and B-C)
ka.set_grid({pB.id: [1, 0], pC.id: [2, 0]})
for _ in range(150):
    if ka.clients.get(pC.id, {}).get("state") == "on" and ka.clients.get(pB.id, {}).get("state") == "on": break
    time.sleep(0.1)
conf = open(ka.files.server_conf).read()
cB, cC = ka.clients[pB.id]["screen"], ka.clients[pC.id]["screen"]
check("a row of three: both connected", ka.clients[pC.id].get("state") == "on" and ka.clients[pB.id].get("state") == "on",
      f'{ka.clients[pB.id].get("state")}/{ka.clients[pC.id].get("state")}')
check("B links to both A and C", f"left = {ka.me()}" in conf and f"right = {cC}" in conf)
ka.set_grid({pB.id: [1, 0], pC.id: [3, 3]})   # C touches nothing now: left out
time.sleep(3)
conf = open(ka.files.server_conf).read()
check("a screen touching nothing is left out", cC not in conf and cB in conf)
check("the window says so", any(c["id"] == pC.id and "Not touching" in c["text"] for c in ka.state([pB, pC])["computers"]))

ka.set_side(pB.id, "")                      # take B out: it must stop following
for _ in range(50):
    if kb.role is None: break
    time.sleep(0.1)
check("taking B out stops its client", kb.role is None, kb.status)

kb.set_allow(False)                         # B refuses from now on
ka.set_side(pB.id, "left")
for _ in range(50):
    if ka.clients.get(pB.id, {}).get("state") == "no": break
    time.sleep(0.1)
check("B can refuse", ka.clients.get(pB.id, {}).get("state") == "no", ka.clients.get(pB.id, {}).get("text"))
ka.set_share(False); ka.stop_all(); kb.stop_all(); kc.stop_all()
print(f"\n{sum(results)}/{len(results)} passed", flush=True)
os._exit(0 if all(results) else 1)
