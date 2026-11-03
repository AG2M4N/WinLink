"""Windows clipboard (clip_win): copied files are read as paths, and another computer's files are
fetched only when something pastes them (delayed rendering). Uses the real clipboard: for a CI
machine or a spare PC, not one you're working on."""
import os, subprocess, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
import clip_win

results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

T = tempfile.mkdtemp()
a = os.path.join(T, "report final.pdf"); open(a, "wb").write(b"x" * 100)
folder = os.path.join(T, "Board"); os.makedirs(folder)
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src")

# Explorer-style copy from another program: read() sees the paths
writer = f"import sys; sys.path.insert(0, {SRC!r}); import clip_win; " \
         f"clip_win.Clipboard().write({{'kind': 'files', 'paths': [{a!r}, {folder!r}]}})"
subprocess.run([sys.executable, "-c", writer], check=True)
asked, state = [], {}

def owner():                       # the clipboard thread, as in WinLink: create, read, pump
    c = clip_win.Clipboard()
    state["read"] = c.read()
    def render(oid):
        asked.append((oid, time.time()))
        time.sleep(1)              # "downloading"
        return [a, folder]
    c.on_render = render
    c.write({"kind": "files", "id": "ab" * 16, "names": ["report final.pdf", "Board"], "count": 1, "size": 100})
    state["written"] = time.time()
    state["own_read"] = c.read()
    end = time.time() + 20
    while time.time() < end and not state.get("stop"):
        c.pump()
        time.sleep(0.02)

t = threading.Thread(target=owner, daemon=True); t.start()
for _ in range(100):
    if "written" in state: break
    time.sleep(0.05)
check("copied files read as paths", state.get("read") == {"kind": "files", "paths": [a, folder]}, state.get("read"))
check("our own promise isn't read back", state.get("own_read") is None, state.get("own_read"))
time.sleep(1.5)
check("nothing fetched before the paste", not asked, asked)

# another program pastes: asks for CF_HDROP; that's when the files are fetched
# (what Explorer does: open the clipboard and ask for CF_HDROP; text comes through read())
paster = f"""import sys, json, ctypes; sys.path.insert(0, {SRC!r}); import clip_win as w
c = w.Clipboard()
if not w.user32.IsClipboardFormatAvailable(w.CF_HDROP):
    print(json.dumps(c.read())); sys.exit()
c._open(); h = w.user32.GetClipboardData(w.CF_HDROP); paths = []
for i in range(w.shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0) if h else 0):
    n = w.shell32.DragQueryFileW(h, i, None, 0); buf = ctypes.create_unicode_buffer(n + 1)
    w.shell32.DragQueryFileW(h, i, buf, n + 1); paths.append(buf.value)
w.user32.CloseClipboard(); print(json.dumps({{"paths": paths}}))
"""
t0 = time.time()
r = subprocess.run([sys.executable, "-c", paster], capture_output=True, text=True, timeout=30)
check("the paste fetches them", len(asked) == 1 and asked[0][0] == "ab" * 16 and asked[0][1] >= t0, asked)
check("and gets the paths", '"paths"' in r.stdout and "report final.pdf" in r.stdout and "Board" in r.stdout,
      r.stdout.strip() + r.stderr.strip()[-300:])
r2 = subprocess.run([sys.executable, "-c", paster], capture_output=True, text=True, timeout=30)
check("a second paste doesn't fetch again", len(asked) == 1 and "report final.pdf" in r2.stdout, asked)

# text still works
state["stop"] = True; t.join(5)
c = clip_win.Clipboard()
c.write({"kind": "text", "text": "hello\nthere"})
check("text still works", subprocess.run([sys.executable, "-c", paster], capture_output=True, text=True).stdout.strip()
      == '{"kind": "text", "text": "hello\\r\\nthere"}')

print(f"{sum(results)}/{len(results)} passed", flush=True)
sys.exit(0 if all(results) else 1)
