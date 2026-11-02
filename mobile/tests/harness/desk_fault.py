import threading, sys, json as _j
def _hook(a):
    print(_j.dumps({"ev": "thread_crash", "thread": a.thread.name if a.thread else "?", "error": repr(a.exc_value)}), flush=True)
threading.excepthook = _hook
# Desktop node for the phone integration test. Prints JSON events; commands on stdin (JSON lines).
import os, sys, json, threading, time, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
import net, audio
def out(**k): print(json.dumps(k), flush=True)
node = None
def emit(kind, *a):
    if kind == "peers":
        for p in list(node.peers.values()):
            if p.incoming and p.incoming.get("code"):
                out(ev="pair_code", code=p.incoming["code"], name=p.name)
                node.answer(p, True)
            if p.control is not None and not getattr(p, "_said", False):
                p._said = True; out(ev="connected", id=p.id, name=p.name, os=p.os)
    elif kind == "clip_in":
        out(ev="clip_in", clip=a[1])
    elif kind == "received":
        out(ev="received", path=a[0], size=os.path.getsize(a[0]))
    elif kind == "batch":
        b = a[0]; out(ev="batch", dir=b.direction, state=b.state, name=b.name, error=b.error, key=b.key)
    elif kind == "files_removed":
        out(ev="removed", paths=a[0])
node = net.Node(net.Settings(), emit)
class Silent(audio.Player):
    def start(self, rate, target_ms=120, floor_ms=20): self.rate, self.target_ms = rate, target_ms
    def stop(self): pass
    def feed(self, pcm): self.bytes = getattr(self, "bytes", 0) + len(pcm)
player = Silent()
def on_audio(peer, hdr, chan):
    out(ev="sound_in", frm=peer.name, hdr=hdr)
    player.receive(hdr, chan, peer.name, lambda info: None)
    out(ev="sound_end", bytes=getattr(player, "bytes", 0))
node.on_audio = on_audio
class Sine:
    def __init__(self): self.ev = threading.Event()
    def start(self, on_pcm):
        import numpy as np
        def run():
            t = 0
            while not self.ev.is_set():
                x = (0.2*np.sin(2*math.pi*440*(np.arange(480)+t)/48000)).astype("<f4"); on_pcm(np.repeat(x[:,None],2,axis=1).tobytes()); t += 480; time.sleep(0.01)
        threading.Thread(target=run, daemon=True).start(); return 48000, 2
    def stop(self): self.ev.set()
sender = audio.SoundSender(node, Sine, lambda text, st: out(ev="sound_out", state=st, text=text), quality=lambda: "cd")
node.start()
out(ev="ready", id=node.id)
for line in sys.stdin:
    cmd = json.loads(line)
    peer = node.peers.get(cmd.get("peer", ""))
    c = cmd["cmd"]
    if c == "clip": node.send_clip({"kind": "text", "text": cmd["text"]})
    elif c == "send": out(ev="sending", n=node.send_paths(peer, cmd["paths"]))
    elif c == "sound": sender.start(peer)
    elif c == "sound_stop": sender.stop()
    elif c == "cancel_all":
        for b in list(node.batches.values()):
            if b.state == "active": node.cancel(b.key)
