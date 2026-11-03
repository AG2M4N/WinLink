"""Copied files between two WinLink desktop nodes on this computer: a copy sends only their names;
the files travel when they're pasted (fetched into the paste's folder), or saved into Downloads.
Also checks the Ctrl/Command swap written into Deskflow's settings. Touches no real clipboard."""
import importlib, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
T = sys.argv[1]
os.environ["LINK_NO_BEACON"] = "1"
import net, kvm
results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

def make(home, port, probe, name):
    os.environ.update(LINK_HOME=home, LINK_PORT=str(port), LINK_PROBE_PORT=str(probe), LINK_NAME=name,
                      LINK_DOWNLOADS=os.path.join(home, "Downloads"))
    mod = importlib.reload(net)
    events = []
    n = mod.Node(mod.Settings(), lambda kind, *a: events.append((kind, a)))
    n.start()
    time.sleep(0.8)
    return n, events

A, ea = make(T + "/A", 51993, 51994, "ZZ Clip A 7731")
B, eb = make(T + "/B", 51994, 51993, "ZZ Clip B 7731")
time.sleep(0.5)
info = A._probe("127.0.0.1", 51994); A._seen(info, "127.0.0.1")
pB = A.peers[info["id"]]; pB.port = 51994
A.connect(pB, manual=True)
for _ in range(100):
    q = B.peers.get(A.id)
    if q and q.incoming: B.answer(q, True); break
    time.sleep(0.05)
for _ in range(100):
    if pB.control and B.peers[A.id].control: break
    time.sleep(0.05)
pA = B.peers[A.id]; pA.port = 51993
check("paired", pB.control is not None and pA.control is not None)

# what A copies: a folder (with a subfolder) and a file
src = os.path.join(T, "copied"); os.makedirs(os.path.join(src, "Board", "gerbers"))
open(os.path.join(src, "Board", "readme.txt"), "w").write("hello")
open(os.path.join(src, "Board", "gerbers", "top.gbr"), "wb").write(os.urandom(300_000))
open(os.path.join(src, "notes.md"), "w").write("# notes")
paths = [os.path.join(src, "Board"), os.path.join(src, "notes.md")]

item = A.offer_files(paths)
A.send_clip(item)
got = None
for _ in range(60):
    got = next((a[1] for k, a in eb if k == "clip_in"), None)
    if got: break
    time.sleep(0.05)
check("the copy sends only names", got is not None and got["kind"] == "files" and got["names"] == ["Board", "notes.md"]
      and got["count"] == 3 and got["size"] == 300_000 + 5 + 7 and "paths" not in got, got)
time.sleep(0.5)
check("no file moved before the paste", not A.batches and not B.batches, (len(A.batches), len(B.batches)))

# B pastes into a folder
dest = os.path.join(T, "pasted"); os.makedirs(dest)
fetch = B.fetch_files(pA, got["id"], dest)
try:
    out = fetch.wait(timeout=30)
except ConnectionError as e:
    out = str(e)
check("paste brings the files", sorted(os.path.basename(p) for p in out) == ["Board", "notes.md"]
      if isinstance(out, list) else False, out)
check("into the paste's folder, folders kept",
      open(os.path.join(dest, "Board", "gerbers", "top.gbr"), "rb").read() == open(os.path.join(src, "Board", "gerbers", "top.gbr"), "rb").read()
      and open(os.path.join(dest, "notes.md")).read() == "# notes")
check("a pasted file isn't a download", not any(k == "received" for k, _ in eb) and
      not [n for n in os.listdir(os.path.join(T, "B", "Downloads")) if not n.startswith(".")],
      os.listdir(os.path.join(T, "B", "Downloads")))
B.clip_wanted.pop(got["id"], None)

# pasting again in the same folder: new names, nothing overwritten
out2 = B.fetch_files(pA, got["id"], dest).wait(timeout=30)
B.clip_wanted.pop(got["id"], None)
check("pasting twice keeps both", sorted(os.path.basename(p) for p in out2) == ["Board (1)", "notes (1).md"], out2)

# Save on the Clipboard page: into Downloads, as usual
B.fetch_files(pA, got["id"], None, save=True)
saved = os.path.join(T, "B", "Downloads", "notes.md")
for _ in range(100):
    if os.path.exists(saved): break
    time.sleep(0.1)
check("Save puts them in Downloads", os.path.exists(saved) and
      os.path.exists(os.path.join(T, "B", "Downloads", "Board", "gerbers", "top.gbr")))

# an offer that's gone (copied long ago): the paste says so
try:
    B.fetch_files(pA, "0" * 32, dest).wait(timeout=10)
    check("an old copy fails clearly", False)
except ConnectionError as e:
    check("an old copy fails clearly", "clipboard" in str(e), str(e))
B.clip_wanted.pop("0" * 32, None)

# files nobody here is pasting are refused
pB_files = A.send_paths(pB, [os.path.join(src, "notes.md")], clip="f" * 32)
time.sleep(2)
check("unasked-for paste files refused", not os.path.exists(os.path.join(dest, "notes (2).md")) and
      not os.path.exists(os.path.join(T, "B", "Downloads", "notes (1).md")))

# a huge folder: the copy doesn't wait to count it
real = net._walk
net._walk = lambda p, budget=None: (time.sleep(0.01), ([], False))[1] if budget else real(p)
big = A.offer_files([os.path.join(src, "Board")])
net._walk = real
check("an uncounted folder still goes", big["count"] == -1 and big["size"] == -1 and big["names"] == ["Board"])

# Ctrl/Command swap in Deskflow's settings
f = kvm.Files(os.path.join(T, "kvmfiles"))
f.write_settings("ZZ-Mac", screens=["ZZ-Mac", "ZZ-PC"], swapped=["ZZ-PC"])
conf = open(f.settings).read()
pc = conf.split("[computer_ZZ-PC]")[1]
check("swap: the PC maps Command to Ctrl", "modifierSuper=ctrl" in pc and "modifierCtrl=super" in pc)
check("swap: the Mac's own keys stay", "modifier" not in conf.split("[computer_ZZ-Mac]")[1].split("[computer_ZZ-PC]")[0])

class P: pass
mac, pc_, lin = P(), P(), P(); mac.os, pc_.os, lin.os = "mac", "windows", "linux"
sh = kvm.Sharing.__new__(kvm.Sharing); sh.settings = net.Settings(); sh.settings.data["kvm"] = {}
me_mac = kvm.IS_MAC
check("swap between Mac and PC only", sh.swaps(pc_) == me_mac and sh.swaps(lin) == me_mac and sh.swaps(mac) != me_mac)
sh.settings.data["kvm"]["swap_keys"] = False
check("swap off: nothing swapped", not sh.swaps(pc_) and not sh.swaps(mac))

print(f"{sum(results)}/{len(results)} passed", flush=True)
os._exit(0 if all(results) else 1)
