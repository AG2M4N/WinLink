"""A stand-in for deskflow-core's server, for desktop_phone.py on a computer without Deskflow:
`fake_deskflow_core.py server --settings <file> --new-instance`. It reads WinLink's settings and
layout, listens with TLS on the port, accepts only the trusted client certificates and the screen
names in the layout, logs connections the way Deskflow does, and moves the pointer onto each client
that connects (enter, two moves, a click, leave)."""
import configparser, hashlib, os, socket, ssl, struct, sys, threading, time

if "--version" in sys.argv:
    print("deskflow-core v1.27.0 (WinLink test stand-in)"); sys.exit(0)
if sys.argv[1:2] != ["server"]:
    print("ERROR: only the server is imitated", flush=True); sys.exit(2)
settings = sys.argv[sys.argv.index("--settings") + 1]
ini = configparser.ConfigParser(strict=False)
ini.read(settings, encoding="utf-8")
port = int(ini["core"]["port"])
cert = ini["security"]["certificate"]
tls = os.path.join(os.path.dirname(settings), "tls")
trusted = {l.strip().split(":")[-1] for l in open(os.path.join(tls, "trusted-clients")) if l.strip()}
layout = open(ini["server"]["externalConfigFile"], encoding="utf-8").read()
names = {l.strip()[:-1] for l in layout.splitlines() if l.strip().endswith(":")}

def frame(b): return struct.pack(">I", len(b)) + b
def exact(s, n):
    out = b""
    while len(out) < n:
        p = s.recv(n - len(out))
        if not p: raise EOFError
        out += p
    return out
def recv(s): return exact(s, struct.unpack(">I", exact(s, 4))[0])

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert)
ctx.verify_mode = ssl.CERT_OPTIONAL
ctx.load_verify_locations(cert)          # WinLink's clients here all show this same certificate

def client(raw):
    name = "?"
    try:
        s = ctx.wrap_socket(raw, server_side=True)
        fp = hashlib.sha256(s.getpeercert(binary_form=True) or b"").hexdigest()
        if fp not in trusted:
            print("WARNING: fingerprint does not match trusted fingerprint", flush=True); s.close(); return
        s.sendall(frame(b"Barrier" + struct.pack(">hh", 1, 8)))
        hb = recv(s)
        name = hb[15:].decode()
        if name not in names:
            print(f'WARNING: unrecognised client name "{name}"', flush=True); s.sendall(frame(b"EUNK")); s.close(); return
        s.sendall(frame(b"QINF"))
        info = recv(s)
        w, h = struct.unpack(">hhhhhhh", info[4:18])[2:4]
        s.sendall(frame(b"CIAK"))
        print(f'NOTE: client "{name}" has connected', flush=True)
        time.sleep(0.5)
        s.sendall(frame(b"CINN" + struct.pack(">hhIH", 0, h // 2, 1, 0)))
        for i in (1, 2):
            s.sendall(frame(b"DMMV" + struct.pack(">hh", 20 * i, h // 2)))
            time.sleep(0.05)
        s.sendall(frame(b"DMDN" + bytes([1])) + frame(b"DMUP" + bytes([1])))
        s.sendall(frame(b"COUT"))
        s.settimeout(1.0)
        while True:
            s.sendall(frame(b"CALV"))
            try:
                msg = recv(s)
            except socket.timeout:
                continue
            if msg[:4] not in (b"CALV", b"DINF", b"CNOP"):
                print(f"WARNING: unexpected {msg[:4]!r}", flush=True)
            time.sleep(1.0)
    except (OSError, EOFError, ssl.SSLError):
        pass
    print(f'NOTE: client "{name}" has disconnected', flush=True)

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", port)); srv.listen(4)
print("NOTE: started server, waiting for clients", flush=True)
while True:
    raw, _ = srv.accept()
    threading.Thread(target=client, args=(raw,), daemon=True).start()
