"""kvm_phone.PhoneScreen (a phone as one of Deskflow's screens) against a stand-in Deskflow server
that speaks Deskflow's protocol over TLS: the hello, the screen info, keepalives, the Ctrl/Command
swap, the pointer (coalesced), buttons, the wheel and keys, a resize, and goodbye."""
import os, socket, ssl, struct, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
import kvm, kvm_phone

results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

home = tempfile.mkdtemp()
files = kvm.Files(home)
fp = files.fingerprint()

srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
port = srv.getsockname()[1]
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(files.cert)
ctx.verify_mode = ssl.CERT_OPTIONAL
ctx.check_hostname = False
ctx.load_verify_locations(files.cert)        # the client must show this computer's own certificate
got = {}

def frame(b): return struct.pack(">I", len(b)) + b
def recv(s):
    n = struct.unpack(">I", exact(s, 4))[0]
    return exact(s, n)
def exact(s, n):
    out = b""
    while len(out) < n:
        p = s.recv(n - len(out))
        if not p: raise EOFError
        out += p
    return out

def server():
    raw, _ = srv.accept()
    s = ctx.wrap_socket(raw, server_side=True)
    got["client_cert"] = s.getpeercert(binary_form=True) is not None
    s.sendall(frame(b"Barrier" + struct.pack(">hh", 1, 8)))
    hb = recv(s)
    got["hello"] = hb
    s.sendall(frame(b"QINF"))
    got["dinf"] = recv(s)
    s.sendall(frame(b"CIAK"))
    s.sendall(frame(b"CROP"))
    # this screen: Command/Windows ("super") acts as Ctrl and Ctrl as super (modifierSuper=ctrl, modifierCtrl=super)
    opts = [struct.unpack(">I", b"MMFR")[0], 2, struct.unpack(">I", b"MMFC")[0], 5]
    s.sendall(frame(b"DSOP" + struct.pack(">I", len(opts)) + struct.pack(f">{len(opts)}I", *opts)))
    s.sendall(frame(b"CALV"))
    got["calv"] = recv(s)
    s.sendall(frame(b"CINN" + struct.pack(">hhIH", 0, 300, 1, 0)))
    burst = b"".join(frame(b"DMMV" + struct.pack(">hh", 10 + i, 300 + i)) for i in range(50))
    s.sendall(burst)                                    # 50 moves at once: the phone gets few, ending at the last
    time.sleep(0.1)
    s.sendall(frame(b"DMRM" + struct.pack(">hh", -5, 7)))
    s.sendall(frame(b"DMDN" + bytes([1])) + frame(b"DMUP" + bytes([1])))
    s.sendall(frame(b"DMWM" + struct.pack(">hh", 0, -240)))
    s.sendall(frame(b"DKDN" + struct.pack(">HHH", 0xEFEB, 0, 55)))        # Command down: becomes Ctrl
    s.sendall(frame(b"DKDN" + struct.pack(">HHH", ord("c"), 0x10, 8)))   # Command+C: becomes Ctrl+C
    s.sendall(frame(b"DKRP" + struct.pack(">HHHH", ord("x"), 0x1, 3, 7)))
    s.sendall(frame(b"DKUP" + struct.pack(">HHH", ord("c"), 0x10, 8)))
    s.sendall(frame(b"DCLP" + b"\x00\x00\x00\x01\x00" + struct.pack(">I", 0)))   # clipboard: ignored
    s.sendall(frame(b"LSYN" + struct.pack(">I", 2) + b"en"))                     # unknown here: ignored
    s.sendall(frame(b"COUT"))
    time.sleep(0.4)
    got["resized_dinf"] = recv(s)                        # after screen.resize()
    s.sendall(frame(b"CIAK"))
    time.sleep(0.2)
    s.sendall(frame(b"CBYE"))
    time.sleep(0.3)
    s.close()

t = threading.Thread(target=server, daemon=True); t.start()
sent, states = [], []
screen = kvm_phone.PhoneScreen("ZZ-Phone-7731", files.cert, fp, (600, 1300), sent.append, states.append, port)
screen.RETRY = 30                                        # one session is enough here
screen.start()
for _ in range(100):
    if any(m.get("e") == "leave" for m in sent): break
    time.sleep(0.05)
screen.resize(1300, 600)
t.join(5)
time.sleep(0.3)
screen.stop()

check("the client showed a certificate", got.get("client_cert"))
hb = got.get("hello", b"")
check("hello back: same protocol, version 1.6, its name", hb[:7] == b"Barrier" and struct.unpack(">hh", hb[7:11]) == (1, 6)
      and hb[15:] == b"ZZ-Phone-7731", repr(hb))
d = got.get("dinf", b"")
check("screen info is the phone's size", d[:4] == b"DINF" and struct.unpack(">hhhhhhh", d[4:18])[2:4] == (600, 1300), repr(d))
check("keepalive answered", got.get("calv") == b"CALV")
check("on once Deskflow acknowledged", states[:1] == [True], str(states))
es = [m["e"] for m in sent]
check("enter first", es[:1] == ["enter"] and sent[0]["x"] == 0 and sent[0]["y"] == 300, str(sent[:1]))
moves = [m for m in sent if m["e"] == "move"]
check("the burst of 50 moves reaches the phone as a few", 1 <= len(moves) <= 6, f"{len(moves)} moves")
check("ending where the pointer is", moves and (moves[0]["x"], moves[0]["y"]) == (59, 349), str(moves[:2]))
check("relative move adds up", len(moves) >= 2 and (moves[-1]["x"], moves[-1]["y"]) == (54, 356), str(moves[-1:]))
i_down = es.index("down") if "down" in es else -1
check("buttons after the last move", i_down > 0 and es[i_down - 1] == "move" and es[i_down + 1] == "up"
      and sent[i_down]["b"] == 1, str(es))
w = [m for m in sent if m["e"] == "wheel"]
check("wheel", w == [{"e": "wheel", "dx": 0, "dy": -240}], str(w))
keys = [m for m in sent if m["e"] == "key"]
check("Command became Ctrl (key)", keys and keys[0]["id"] == 0xEFE3 and keys[0]["k"] == "down", str(keys[:1]))
check("Command+C became Ctrl+C", len(keys) > 1 and keys[1]["id"] == ord("c") and keys[1]["mask"] == 0x2, str(keys[1:2]))
check("repeat with its count", len(keys) > 2 and keys[2] == {"e": "key", "k": "repeat", "id": ord("x"), "mask": 1, "n": 3})
check("key up", len(keys) > 3 and keys[3]["k"] == "up" and keys[3]["mask"] == 0x2)
check("leave last (clipboard and language ignored)", es[-1:] == ["leave"], str(es[-3:]))
r = got.get("resized_dinf", b"")
check("turned: new size sent", r[:4] == b"DINF" and struct.unpack(">hhhhhhh", r[4:18])[2:4] == (1300, 600), repr(r))
check("off after goodbye", states[-1:] == [False], str(states))

# The wrong server (not this computer's certificate) is refused before anything is sent
other = kvm.Files(tempfile.mkdtemp()); other.fingerprint()
srv2 = socket.socket(); srv2.bind(("127.0.0.1", 0)); srv2.listen(1)
ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx2.load_cert_chain(other.cert)
said = {}
def server2():
    raw, _ = srv2.accept()
    try:            # the client may hang up before (or right after) the hello: that's the refusal
        s = ctx2.wrap_socket(raw, server_side=True)
        s.sendall(frame(b"Barrier" + struct.pack(">hh", 1, 8)))
        s.settimeout(2)
        said["reply"] = s.recv(100)
    except (OSError, ssl.SSLError):
        said["reply"] = b""
t2 = threading.Thread(target=server2, daemon=True); t2.start()
sent2 = []
s2 = kvm_phone.PhoneScreen("ZZ-Phone-7731", files.cert, fp, (600, 1300), sent2.append, lambda on: None,
                           srv2.getsockname()[1])
s2.RETRY = 30
s2.start(); t2.join(5); s2.stop()
check("another computer's Deskflow is refused", said.get("reply") == b"" and not sent2, repr(said))

# Modifiers with no swap leave everything alone
m = kvm_phone.Modifiers()
check("no swap: keys unchanged", m.key(0xEFEB) == 0xEFEB and m.mask(0x12) == 0x12)

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
