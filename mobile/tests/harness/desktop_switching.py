"""Two desktop nodes: Tailscale (simulated: [::1] through a 4 MB/s proxy) -> Wi-Fi (127.0.0.1) moves."""
import hashlib, importlib, math, os, socket, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
T = sys.argv[1]
os.environ["LINK_NO_BEACON"] = "1"
import tailnet
tailnet.known_ips.add("::1")                  # in this test, [::1] is "the Tailscale address"
import net, audio
results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

def make(home, port, probe, name):
    os.environ.update(LINK_HOME=home, LINK_PORT=str(port), LINK_PROBE_PORT=str(probe), LINK_NAME=name,
                      LINK_DOWNLOADS=home + "/dl")
    mod = importlib.reload(net)
    ev = []
    n = mod.Node(mod.Settings(), lambda *a: ev.append((time.time(),) + a))
    n.start()
    return n, ev

def proxy(listen_port, target_port, rate=4 << 20):
    """[::1]:listen_port -> 127.0.0.1:target_port, at `rate` bytes/s each way (a slower remote link)."""
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("::1", listen_port)); srv.listen(16)
    def pump(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d: break
                b.sendall(d); time.sleep(len(d) / rate)
        except OSError: pass
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    def loop():
        while True:
            c, _ = srv.accept()
            u = socket.create_connection(("127.0.0.1", target_port))
            threading.Thread(target=pump, args=(c, u), daemon=True).start()
            threading.Thread(target=pump, args=(u, c), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()

# A has the smaller id, so A moves the control link; B only ever sees the proxy's 127.0.0.1
import json as _json
for d, i in (("A", "0" * 31 + "1"), ("B", "f" * 32)):
    os.makedirs(f"{T}/{d}", exist_ok=True)
    _json.dump({"id": i}, open(f"{T}/{d}/settings.json", "w"))
A, evA = make(T + "/A", 51790, 51791, "ZZ A 7731")
B, evB = make(T + "/B", 51791, 51790, "ZZ B 7731")
proxy(51791, 51791); proxy(51790, 51790)
time.sleep(0.5)
infoB = A._probe("::1", 51791); A._seen(infoB, "::1")
pB = A.peers[infoB["id"]]
A.connect(pB, manual=True)
for _ in range(100):
    pA = next(iter(B.peers.values()), None)
    if pA and pA.incoming: B.answer(pA, True); break
    time.sleep(0.05)
for _ in range(100):
    if pB.control: break
    time.sleep(0.05)
pA = B.peers[A.id]
check("paired over Tailscale", pB.control is not None and pB.control.route == "tailscale", pB.control and pB.control.route)

# ---- 1. a file moves to Wi-Fi mid-way
data = os.urandom(40 << 20); src = T + "/big.bin"; open(src, "wb").write(data)
t0 = time.time()
A.send_paths(pB, [src])
time.sleep(2.5)
infoA = B._probe("::1", 51790)
home = {"on": True}
def beacons():                    # on the same Wi-Fi, devices hear each other every couple of seconds
    while True:
        if home["on"]:
            A._seen(infoB, "127.0.0.1"); B._seen(infoA, "127.0.0.1")
        time.sleep(2)
threading.Thread(target=beacons, daemon=True).start()
got = None
for _ in range(400):
    got = next((e for e in evB if e[1] == "received"), None)
    if got: break
    time.sleep(0.05)
took = time.time() - t0
same = got is not None and hashlib.sha256(open(got[2], "rb").read()).digest() == hashlib.sha256(data).digest()
check("file finished intact after moving to Wi-Fi", same, f"{took:.1f} s (all over Tailscale would take ~10 s)")
check("file was faster than staying on Tailscale", took < 8, f"{took:.1f} s")

# ---- 2. the control link moves too
for _ in range(300):
    if pB.control and pB.control.route == "wifi" and pA.control and pA.control.route == "wifi": break
    time.sleep(0.05)
print("  ids: A<B" if A.id < pB.id else "  ids: A>B", "| A sees B fresh LAN:", pB.has_fresh_lan(), "| B sees A fresh LAN:", pA.has_fresh_lan(),
      "| routes:", pB.control and pB.control.route, pA.control and pA.control.route, "| failed_at:", pB.lan_failed_at, pA.lan_failed_at,
      "| started ago:", pB.control and round(time.time() - pB.control.started), pA.control and round(time.time() - pA.control.started))
check("control link moved to Wi-Fi on both", pB.control is not None and pB.control.route == "wifi"
      and pA.control is not None and pA.control.route == "wifi")

# ---- 3. sound moves without stopping
home["on"] = False
for p in (pB, pA):
    p.addrs.pop("127.0.0.1", None)                                # "left home" again
starts, fed, states = [], [0], []
class Silent(audio.Player):
    def start(self, rate, target_ms=120): starts.append(time.time()); self.rate, self.target_ms = rate, target_ms; self.stream = True
    def stop(self): self.stream = None
    def feed(self, pcm): fed[0] += len(pcm)
player = Silent()
B.on_audio = lambda peer, hdr, chan: player.receive(hdr, chan, peer.name, lambda info: None, peer_id=peer.id)
class Sine:
    def __init__(self): self.ev = threading.Event()
    def start(self, on_pcm):
        import numpy as np
        def run():
            t = 0
            while not self.ev.is_set():
                x = (0.2 * np.sin(2 * math.pi * 440 * (np.arange(480) + t) / 48000)).astype("<f4")
                on_pcm(np.repeat(x[:, None], 2, axis=1).tobytes()); t += 480; time.sleep(0.01)
        threading.Thread(target=run, daemon=True).start(); return 48000, 2
    def stop(self): self.ev.set()
sender = audio.SoundSender(A, Sine, lambda text, st: states.append((time.time(), st, text)), quality=lambda: "auto")
sender.start(pB)
time.sleep(2)
check("sound starts over Tailscale", any(s == "playing" and "Tailscale" in t for _, s, t in states))
before = fed[0]
home["on"] = True
time.sleep(6)
moved = [t for _, s, t in states if s == "playing" and "Tailscale" not in t]
check("sound moved to Wi-Fi", bool(moved), moved[:1])
check("the speakers weren't restarted", len(starts) == 1, f"{len(starts)} start(s)")
check("sound kept flowing", fed[0] - before > 6 * 48000 * 8 * 0.5, f"{(fed[0]-before)/1e6:.1f} MB in 6 s")
check("no stop or retry", not any(s in ("off", "retry") and not t.startswith("Connecting") for _, s, t in states),
      [x[1:] for x in states])
sender.stop(); time.sleep(1)

# ---- 4. Wi-Fi seen but unreachable: stay on Tailscale, don't keep trying
home["on"] = False
for p in (pB, pA):
    p.addrs.pop("127.0.0.1", None); p.lan_failed_at = 0
pB.control.close(); time.sleep(3)
for _ in range(100):
    if pB.control: break
    time.sleep(0.1)
A._seen(infoB, "127.0.0.2")                                       # an address nothing answers on
data2 = os.urandom(12 << 20); src2 = T + "/b2.bin"; open(src2, "wb").write(data2)
n_before = len([e for e in evB if e[1] == "received"])
A.send_paths(pB, [src2])
for _ in range(400):
    if len([e for e in evB if e[1] == "received"]) > n_before: break
    time.sleep(0.05)
check("unreachable Wi-Fi: the file still finishes", len([e for e in evB if e[1] == "received"]) > n_before)
check("unreachable Wi-Fi: noted, not retried for a minute", time.time() - pB.lan_failed_at < 30)
print(f"\n{sum(results)}/{len(results)} passed")
os._exit(0 if all(results) else 1)
