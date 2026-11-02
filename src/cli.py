"""
`winlink <command>`: control WinLink from a terminal, with or without a desktop (made for Linux
servers and SSH, works anywhere WinLink runs from a terminal). Commands go to the running WinLink
over its private local channel (sendto.py); if none is running, one is started in the background.
"""

import json
import os
import shutil
import subprocess
import sys
import time

import net
import sendto

HELP = """WinLink: clipboard, files and sound between your devices.

  winlink                          open WinLink (without a desktop it runs as 'winlink daemon')
  winlink daemon [--downloads DIR] [--verbose]
                                   run without a window (servers, SSH); Ctrl-C stops it
  winlink service install|remove   run the daemon in the background at every boot (systemd --user)

  winlink status                   this computer, devices, transfers, Tailscale
  winlink devices                  paired and nearby devices
  winlink pair <device>            pair with a device on the same network (compare the code shown)
  winlink add <address>[:port]     find a device by address (another subnet, a VPN, a server)
  winlink accept <device>          allow a device that asked to pair (check its code first)
  winlink decline <device>
  winlink connect|disconnect|forget <device>

  winlink send <device|all> <files or folders...> [--no-wait]
  winlink transfers                recent transfers
  winlink cancel <transfer|all>

  winlink clip                     print the last text copied on another device
  winlink clip <text>              put text on your devices' clipboards (or: echo hi | winlink clip -)

  winlink sound share <device>     play this computer's sound on a device
  winlink sound stop               stop sharing
  winlink sound stop-playing       stop the sound playing here

  winlink login                    connect from anywhere: sign in to Tailscale (prints a link)
  winlink logout
  winlink anywhere on|off|status

  winlink set clipboard on|off     also: set volume 0-100, set latency auto|lowest|low|balanced|smooth
  winlink log [-f]                 WinLink's log (-f: keep following it)
  winlink show | hide              open WinLink's window, or hide it in the tray / menu bar
  winlink quit                     stop the running WinLink
  winlink --check                  (Linux) what works on this computer and what to install

<device> is a name, or the start of one: "winlink send msi report.pdf"."""

COMMANDS = {"add", "show", "hide", "daemon", "service", "status", "devices", "pair", "accept", "decline", "connect", "disconnect",
            "forget", "send", "transfers", "cancel", "clip", "sound", "login", "logout", "anywhere", "set",
            "log", "quit", "help", "ping"}
OS_LABEL = {"mac": "Mac", "windows": "Windows PC", "linux": "Linux PC", "android": "Android", "ios": "iPhone"}


class Fail(Exception):
    pass


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# ---------------------------------------------------------------- talking to the running WinLink
_reached = False                 # once WinLink answered, never start another (it was quit meanwhile)


def call(cmd, *args, timeout=90, start=True):
    global _reached
    reply = sendto.hand_off(net.config_dir(), {"cmd": cmd, "args": list(args)}, timeout=timeout)
    if reply is None:
        if not start or _reached:
            raise Fail("WinLink isn't running." if not _reached else "WinLink stopped.")
        start_background()
        reply = sendto.hand_off(net.config_dir(), {"cmd": cmd, "args": list(args)}, timeout=timeout)
        if reply is None:
            raise Fail("WinLink didn't start. Try 'winlink daemon' to see why.")
    _reached = True
    ok, msg = reply
    if not ok:
        raise Fail(msg or f"{cmd} failed")
    return msg


def data(cmd, *args, **kw):
    return json.loads(call(cmd, *args, **kw))


def has_desktop():
    if os.environ.get("WINLINK_HEADLESS"):
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or net.IS_MAC or net.IS_WIN


def launch_words():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "link.py")]


def start_background():
    """No WinLink running: start one (in the tray on a desktop, as a daemon otherwise)."""
    mode = ["--hidden"] if has_desktop() else ["daemon"]
    print(f"Starting WinLink{' in the background' if mode == ['daemon'] else ''}…", file=sys.stderr)
    subprocess.Popen(launch_words() + mode, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        time.sleep(0.25)
        if sendto.hand_off(net.config_dir(), {"cmd": "ping", "args": []}, timeout=3) is not None:
            time.sleep(1.5)                    # give it a moment to find devices
            return


# ---------------------------------------------------------------- printing
def device_line(d):
    mark = {"on": "●", "ask": "?", "wait": "…", "busy": "…"}.get(d["state"], "○")
    status = {"on": f"connected ({'Tailscale' if d.get('route') == 'tailscale' else 'Wi-Fi'})",
              "ask": f"wants to pair, code {d.get('code')}  → winlink accept \"{d['name']}\"",
              "wait": f"pairing, code {d.get('code')}: check {d['name']} shows it and press Allow there"}.get(
        d["state"], (d.get("status") or "").lower() + ("" if d.get("trusted") else ", not paired"))
    return f" {mark} {d['name'][:24]:<24} {OS_LABEL.get(d['os'], 'Computer'):<11} {status}"


def batch_line(b):
    frac = b["done"] / b["size"] if b["size"] else (1 if b["state"] == "done" else 0)
    arrow = "→" if b["dir"] == "out" else "←"
    files = f" · {b['files_done']}/{b['files']} files" if b["files"] > 1 else ""
    state = {"active": f"{int(frac * 100)}%", "done": "done", "cancelled": "cancelled",
             "failed": "failed"}.get(b["state"], b["state"])
    err = f" ({b['error']})" if b.get("error") and b["state"] != "done" else ""
    return f" {b['key'][:6]}  {arrow} {b['peer'][:16]:<16} {b['name'][:36]:<36} {human(b['size']):>9}  {state}{files}{err}"


def print_status(s, full=True):
    print(f"{s['me']} ({OS_LABEL.get(s['os'], s['os'])}) · files arrive in {s['downloads']}")
    print("\nDevices:" if s["devices"] else "\nNo devices yet. Open WinLink on another device on this network.")
    for d in s["devices"]:
        print(device_line(d))
    if not full:
        return
    a = s["anywhere"]
    print(f"\nConnect from anywhere: {a['text']}" + (f" ({', '.join(a['ips'])})" if a["ips"] else ""))
    snd = s["sound"]
    if snd["playing_from"]:
        print(f"Sound: playing from {snd['playing_from']} here")
    if snd["sharing"]:
        print(f"Sound: {snd['share_text']}")
    if s["clipboard"]["problem"]:
        print(f"Clipboard: {s['clipboard']['problem']}")
    if s["transfers"]:
        print("\nTransfers:")
        for b in s["transfers"][:8]:
            print(batch_line(b))


# ---------------------------------------------------------------- commands
def cmd_pair(args):
    if not args:
        raise Fail("usage: winlink pair <device>")
    who = data("pair", *args)
    if who.get("already"):
        print(f"Already connected to {who['name']}.")
        return 0
    print(f"Connecting to {who['name']}…")
    shown, end = False, time.time() + 110
    if who.get("code"):
        print(f"Pairing code: {who['code']}\nCheck that {who['name']} shows the same code, and press Allow there.")
        shown = True
    while time.time() < end:
        s = data("status")
        d = next((x for x in s["devices"] if x["id"] == who["id"]), None)
        if d is None:
            raise Fail(f"{who['name']} went away.")
        if d["state"] == "on":
            print(f"Connected to {d['name']}.")
            return 0
        if d["state"] == "wait" and d.get("code") and not shown:
            print(f"Pairing code: {d['code']}\nCheck that {d['name']} shows the same code, and press Allow there.")
            shown = True
        if d["state"] in ("free", "away") and time.time() > end - 105:
            raise Fail(f"Couldn't connect to {d['name']}: {d.get('status') or 'it said no'}. "
                       "Is WinLink open there? ('winlink log' has details)")
        time.sleep(0.5)
    raise Fail("No answer in time.")


def cmd_send(args):
    wait = "--no-wait" not in args
    args = [a for a in args if a != "--no-wait"]
    if len(args) < 2:
        raise Fail("usage: winlink send <device|all> <files or folders...>")
    target, paths = args[0], [os.path.abspath(p) for p in args[1:]]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise Fail(f"Not found: {', '.join(missing)}")
    keys = data("send", target, *paths)["batches"]
    if not keys:
        raise Fail("Nothing to send there.")
    if not wait:
        print(f"Sending. Follow it with: winlink transfers   (cancel: winlink cancel {keys[0][:6]})")
        return 0
    last, t0, done0 = "", time.time(), None
    tty = sys.stdout.isatty()
    try:
        while True:
            bs = json.loads(call("transfers", *keys))
            done, size = sum(b["done"] for b in bs), sum(b["size"] for b in bs) or 1
            done0 = done if done0 is None else done0
            rate = (done - done0) / max(time.time() - t0, 0.1)
            active = [b for b in bs if b["state"] == "active"]
            retry = next((b["error"] for b in active if b.get("error")), "")
            line = (f"{', '.join(b['name'] for b in bs)[:40]} → {', '.join(sorted({b['peer'] for b in bs}))[:24]}  "
                    f"{int(100 * done / size):3d}%  {human(done)} of {human(size)}  {human(rate)}/s"
                    + (f"  ({retry})" if retry else ""))
            if tty:
                print("\r" + line[:150].ljust(150), end="", flush=True)
            elif line != last and int(time.time()) % 5 == 0:
                print(line, flush=True)
            last = line
            if not active:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\nStill sending in the background. Cancel with: winlink cancel {keys[0][:6]}")
        return 130
    if tty:
        print()
    failed = [b for b in bs if b["state"] != "done"]
    for b in bs:
        print(batch_line(b))
    return 1 if failed else 0


def cmd_clip(args):
    if not args:
        got = data("clip")
        sys.stdout.write(got["text"])
        if sys.stdout.isatty():
            print(f"\n(from {got['from']})", file=sys.stderr)
        return 0
    text = sys.stdin.read() if args == ["-"] else " ".join(args)
    print(call("clip", "--text", text))
    return 0


def cmd_login(_args):
    got = data("anywhere", "login", timeout=60)
    if got.get("running"):
        print(f"Already signed in. This computer is {', '.join(got['ips'])} on your tailnet.")
        return 0
    print("Open this link on any device and sign in with the same Tailscale account as your other devices:\n")
    print(f"  {got['url']}\n")
    print("Waiting for you to finish…", flush=True)
    end = time.time() + 600
    while time.time() < end:
        st = data("anywhere", "status")
        if st["state"] == "Running":
            print(f"Signed in. This computer is {', '.join(st['ips'])}; your devices find it from any network.")
            return 0
        if st["state"] == "NeedsMachineAuth":
            print("Signed in; waiting for approval in the Tailscale admin console.")
        time.sleep(2)
    raise Fail("Not signed in after 10 minutes. Run 'winlink login' again for a fresh link.")


def cmd_log(args):
    path = os.path.join(net.config_dir(), "winlink.log")
    if not os.path.exists(path):
        raise Fail("No log yet.")
    if "-f" in args and shutil.which("tail"):
        os.execvp("tail", ["tail", "-n", "40", "-F", path])
    with open(path, encoding="utf-8", errors="replace") as f:
        sys.stdout.writelines(f.readlines()[-60:])
    return 0


def cmd_service(args):
    if net.IS_MAC or net.IS_WIN or not shutil.which("systemctl"):
        raise Fail("'winlink service' is for Linux with systemd. On a desktop, use Settings › Open at login.")
    what = args[0] if args else "status"
    unit_dir = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "systemd", "user")
    unit = os.path.join(unit_dir, "winlink.service")
    if what == "install":
        launcher = os.path.expanduser("~/.local/bin/winlink")
        words = [launcher] if os.access(launcher, os.X_OK) else launch_words()
        extra = args[1:]                     # e.g. --downloads /srv/incoming
        os.makedirs(unit_dir, exist_ok=True)
        with open(unit, "w", encoding="utf-8") as f:
            f.write("\n".join([
                "[Unit]", "Description=WinLink: clipboard, files and sound between your devices",
                "Wants=network-online.target", "After=network-online.target", "",
                "[Service]", f"ExecStart={' '.join(json.dumps(w) for w in words + ['daemon'] + extra)}",
                "Restart=on-failure", "RestartSec=5", "",
                "[Install]", "WantedBy=default.target", ""]))
        if sendto.hand_off(net.config_dir(), {"cmd": "ping", "args": []}, 3) is not None:
            call("quit", start=False)            # the service takes over from the WinLink running now
            time.sleep(1)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        r = subprocess.run(["systemctl", "--user", "enable", "--now", "winlink.service"], check=False)
        if r.returncode != 0:
            raise Fail("systemctl couldn't start it; see: systemctl --user status winlink")
        print("WinLink now runs in the background and starts with your user session.")
        print(f"To keep it running when you're logged out (a server): sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")
        return 0
    if what == "remove":
        subprocess.run(["systemctl", "--user", "disable", "--now", "winlink.service"], check=False)
        if os.path.exists(unit):
            os.remove(unit)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print("Removed the WinLink service.")
        return 0
    return subprocess.run(["systemctl", "--user", "status", "--no-pager", "winlink.service"]).returncode


def main(args):
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "help":
            print(HELP)
            return 0
        if cmd in ("status", "devices"):
            print_status(data("status"), full=(cmd == "status"))
            return 0
        if cmd == "transfers":
            bs = json.loads(call("transfers"))
            print("\n".join(batch_line(b) for b in bs) if bs else "No transfers yet.")
            return 0
        if cmd == "pair":
            return cmd_pair(rest)
        if cmd == "send":
            return cmd_send(rest)
        if cmd == "clip":
            return cmd_clip(rest)
        if cmd == "login":
            return cmd_login(rest)
        if cmd == "logout":
            print(call("anywhere", "logout"))
            return 0
        if cmd == "anywhere":
            if rest[:1] == ["login"]:
                return cmd_login(rest[1:])
            out = call("anywhere", *(rest or ["status"]))
            if out.startswith("{"):
                st = json.loads(out)
                out = f"{st['text']}" + (f" ({', '.join(st['ips'])})" if st["ips"] else "")
            print(out)
            return 0
        if cmd == "log":
            return cmd_log(rest)
        if cmd == "service":
            return cmd_service(rest)
        if cmd == "quit":
            print(call("quit", start=False))
            return 0
        if cmd == "ping":
            print(call("ping", start=False))
            return 0
        if cmd == "connect":
            who = data("connect", *rest)
            print(f"Connecting to {who['name']}…" if not who.get("already") else f"Already connected to {who['name']}.")
            return 0
        print(call(cmd, *rest))
        return 0
    except Fail as e:
        print(f"winlink: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
