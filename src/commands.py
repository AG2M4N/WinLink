"""
What the running WinLink does for `winlink <command>` (cli.py is the other half). Each command
returns (ok, text); data comes back as JSON text for the command line to format.
"""

import json
import os
import threading
import time

import net


def run(app, cmd, args):
    fn = COMMANDS.get(cmd)
    if fn is None:
        return False, f"unknown command: {cmd}"
    try:
        return fn(app, [str(a) for a in args])
    except LookupError as e:
        return False, str(e.args[0] if e.args else e)
    except Exception as e:  # noqa: BLE001  (a bad request must never take WinLink down)
        net.log.exception("command %s", cmd)
        return False, f"{cmd} failed: {e}"


def find(app, query, connected=False, allow_all=False):
    """A device by id, name, or the start of either (case doesn't matter)."""
    if allow_all and query.lower() in ("all", "everyone", "*"):
        return None
    peers = app.node.connected() if connected else app.node.snapshot()
    q = query.lower()
    for test in (lambda p: p.id == query, lambda p: p.name.lower() == q,
                 lambda p: p.id.startswith(q) or p.name.lower().startswith(q), lambda p: q in p.name.lower()):
        hits = [p for p in peers if test(p)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise LookupError(f"'{query}' matches {', '.join(p.name for p in hits)}: be more specific")
    where = "connected" if connected else "known"
    raise LookupError(f"no {where} device called '{query}' (see: winlink devices)")


def _need(args, n, usage):
    if len(args) < n:
        raise LookupError(f"usage: winlink {usage}")


# ---------------------------------------------------------------- information
def status(app, _args):
    s = app.state()
    node = app.node
    out = {
        "me": s["me"]["name"], "os": net.OS_NAME, "downloads": node.downloads,
        "devices": [{k: d.get(k) for k in ("id", "name", "os", "state", "status", "route", "code", "trusted")}
                    for d in s["devices"]],
        "anywhere": {"enabled": s["anywhere"]["enabled"], "text": s["anywhere"]["text"],
                     "state": node.anywhere.get("state", "Off"), "ips": node.anywhere.get("ips") or []},
        "transfers": [_batch(b) for b in sorted(node.batches.values(), key=lambda b: -b.started)[:20]],
        "clipboard": {"enabled": s["clipboard"]["enabled"], "problem": s["clipboard"]["problem"]},
        "sound": {"can_share": app.sender is not None, "sharing": bool(app.settings["audio"]),
                  "share_text": app.send_text, "playing_from": app.receiving},
    }
    return True, json.dumps(out)


def _batch(b):
    return {"key": b.key, "dir": b.direction, "name": b.name, "peer": b.peer.name, "state": b.state,
            "done": b.done, "size": b.size, "files": b.files_total, "files_done": b.files_done,
            "error": b.error, "started": b.started}


def transfers(app, args):
    keys = set(args)
    out = [_batch(b) for b in sorted(app.node.batches.values(), key=lambda b: -b.started)
           if not keys or b.key in keys]
    return True, json.dumps(out)


# ---------------------------------------------------------------- devices and pairing
def connect(app, args):
    _need(args, 1, "pair <device>")
    peer = find(app, " ".join(args))
    if peer.control is not None:
        return True, json.dumps({"id": peer.id, "name": peer.name, "already": True})
    app.node.connect(peer, manual=True)
    # Answer once there's something to show: the pairing code to compare, or the connection.
    code, end = "", time.time() + 10
    while time.time() < end and peer.control is None:
        code = peer.waiting_code or ""
        if code or (not peer.connecting and time.time() > end - 9.5):
            break
        time.sleep(0.1)
    return True, json.dumps({"id": peer.id, "name": peer.name, "code": code, "on": peer.control is not None})


def answer(allow):
    def handler(app, args):
        _need(args, 1, f"{'accept' if allow else 'decline'} <device>")
        peer = find(app, " ".join(args))
        if not peer.incoming:
            raise LookupError(f"{peer.name} isn't asking to pair right now")
        app.node.answer(peer, allow)
        app.dirty.set()
        return True, f"{'Allowed' if allow else 'Declined'} {peer.name}."
    return handler


def add(app, args):
    """Find WinLink at an address (another subnet, a VPN, a server): `winlink add 10.0.0.5[:port]`."""
    _need(args, 1, "add <address>[:port]")
    host, port = args[0], None
    if host.count(":") == 1:                       # host:port (an IPv6 address has more colons)
        host, p = host.rsplit(":", 1)
        port = int(p)
    host = host.strip("[]")
    info = app.node._probe(host, port)
    if not info:
        raise LookupError(f"no WinLink answered at {args[0]}")
    app.node._seen(info, host)
    app.dirty.set()
    return True, f"Found {info['name']}. Pair with: winlink pair \"{info['name']}\""


def disconnect(app, args):
    _need(args, 1, "disconnect <device>")
    peer = find(app, " ".join(args))
    app.node.disconnect(peer)
    return True, f"Disconnected {peer.name}. It won't reconnect until you run: winlink connect {peer.name}"


def forget(app, args):
    _need(args, 1, "forget <device>")
    peer = find(app, " ".join(args))
    app.forget(peer)
    return True, f"Forgot {peer.name}. Pair again to use it."


# ---------------------------------------------------------------- files
def send(app, args):
    _need(args, 2, "send <device|all> <files or folders...>")
    target, paths = args[0], args[1:]
    peer = find(app, target, allow_all=True)
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise LookupError(f"not found: {', '.join(missing)}")
    before = set(app.node.batches)
    ok, msg = app._send_when_ready("all" if peer is None else peer.id, paths, wait=30)
    if not ok:
        return False, msg
    new = [k for k, b in app.node.batches.items() if k not in before and b.direction == "out"]
    return True, json.dumps({"batches": new})


def cancel(app, args):
    _need(args, 1, "cancel <transfer number|all>")
    active = [b for b in sorted(app.node.batches.values(), key=lambda b: -b.started) if b.state == "active"]
    if args[0] == "all":
        chosen = active
    else:
        chosen = [b for b in active if b.key == args[0] or b.key.startswith(args[0]) or b.name == args[0]]
    if not chosen:
        raise LookupError("no transfer in progress matches that (see: winlink transfers)")
    for b in chosen:
        app.node.cancel(b.key)
    return True, f"Cancelled {', '.join(b.name for b in chosen)}."


# ---------------------------------------------------------------- clipboard
def clip(app, args):
    if not args:                        # print the newest clipboard item received
        for e in reversed(app.clip_history):
            if e["dir"] == "in" and e["kind"] == "text" and e.get("item"):
                return True, json.dumps({"text": e["item"]["text"], "from": e["peer"], "at": e["at"]})
        raise LookupError("nothing received yet")
    text = " ".join(args) if args[0] != "--text" else args[1]
    if not app.node.connected():
        raise LookupError("no devices connected")
    item = {"kind": "text", "text": text}
    app.node.send_clip(item)
    app._remember_clip("out", item, ", ".join(p.name for p in app.node.connected()))
    return True, f"Sent to {', '.join(p.name for p in app.node.connected())}."


# ---------------------------------------------------------------- sound
def sound(app, args):
    _need(args, 1, "sound share <device> | sound stop | sound stop-playing")
    what = args[0]
    if what == "share":
        if app.sender is None:
            raise LookupError("this computer can't share its sound (winlink --check says why)")
        _need(args, 2, "sound share <device>")
        peer = find(app, " ".join(args[1:]), connected=True)
        app.settings["audio_target"] = peer.id
        app.toggle_audio(True)
        return True, f"Sharing this computer's sound with {peer.name}."
    if what == "stop":
        if app.sender is not None:
            app.toggle_audio(False)
        return True, "Stopped sharing sound."
    if what in ("stop-playing", "mute"):
        app.stop_playing()
        return True, "Stopped the sound playing here."
    raise LookupError("usage: winlink sound share <device> | sound stop | sound stop-playing")


# ---------------------------------------------------------------- Tailscale
def anywhere(app, args):
    what = args[0] if args else "status"
    node = app.node
    if what == "on":
        node.set_anywhere(True)
        return True, "Connect from anywhere is starting. Next: winlink login"
    if what == "off":
        node.set_anywhere(False)
        return True, "Connect from anywhere is off."
    if what == "login":
        if not app.settings["anywhere"]:
            node.set_anywhere(True)
            for _ in range(40):                 # let the helper start
                if node.anywhere.get("state") not in ("Off", "Starting", None):
                    break
                time.sleep(0.25)
        if node.anywhere.get("state") == "Running":
            return True, json.dumps({"running": True, "ips": node.anywhere.get("ips") or []})
        url = node.sign_in_url()
        if not url:
            raise LookupError("Tailscale didn't give a sign-in link. Check the internet connection and try again")
        return True, json.dumps({"url": url})
    if what == "logout":
        ok = node.sign_out()
        return ok, "Signed out of Tailscale." if ok else "Couldn't sign out. Try again in a moment."
    if what == "status":
        st = node.anywhere
        return True, json.dumps({"enabled": bool(app.settings["anywhere"]), "state": st.get("state", "Off"),
                                 "ips": st.get("ips") or [], "name": st.get("name", ""),
                                 "expiry": st.get("key_expiry", ""), "error": st.get("error", ""),
                                 "text": app._anywhere_state()["text"]})
    raise LookupError("usage: winlink anywhere on|off|login|logout|status")


# ---------------------------------------------------------------- settings and the app
def setting(app, args):
    _need(args, 2, "set clipboard on|off  (or: set volume 0-100, set latency auto|lowest|low|balanced|smooth)")
    key, value = args[0], args[1]
    if key == "clipboard":
        app.settings["clipboard"] = value in ("on", "true", "1", "yes")
        app.clip_reset.set()
        return True, f"Clipboard sharing {'on' if app.settings['clipboard'] else 'off'}."
    if key == "volume" and app.player is not None:
        app.player.volume = max(0.0, min(1.0, float(value) / 100))
        app.settings.data["volume"] = app.player.volume
        return True, f"Volume {round(app.player.volume * 100)}%."
    if key == "latency":
        app.set_latency(value)
        return True, f"Latency {app.settings['audio_latency']}."
    raise LookupError("can set: clipboard, volume, latency")


def show(app, _args):
    if app.window is None:
        raise LookupError("this WinLink runs without a window (winlink daemon)")
    app._show_window()
    return True, ""


def hide(app, _args):
    if app.window is None:
        raise LookupError("this WinLink runs without a window (winlink daemon)")
    if app.tray is None:
        raise LookupError("there's no tray / menu bar icon to open it from again")
    if _args[:1] == ["--minimize"]:                  # as if the minimise button was pressed (tests)
        app.window.minimize()
    else:
        app.hide_to_tray()
    return True, ""


def quit_(app, _args):
    threading.Timer(0.3, app._quit).start()          # answer first
    return True, "WinLink is quitting."


def ping(app, _args):
    out = {"pid": os.getpid(), "headless": app.headless, "name": app.node.name, "in_tray": app.in_tray}
    if app.window is not None and net.IS_MAC:      # for diagnosing hide / show
        try:
            from AppKit import NSApplication
            from webview.platforms.cocoa import BrowserView
            w = BrowserView.instances[app.window.uid].window
            out.update(visible=bool(w.isVisible()), miniaturized=bool(w.isMiniaturized()),
                       dock_icon=NSApplication.sharedApplication().activationPolicy() == 0)
        except Exception as e:  # noqa: BLE001
            out["window_error"] = str(e)
    return True, json.dumps(out)


COMMANDS = {
    "status": status, "devices": status, "transfers": transfers,
    "add": add, "pair": connect, "connect": connect, "accept": answer(True), "decline": answer(False),
    "disconnect": disconnect, "forget": forget,
    "send": send, "cancel": cancel, "clip": clip, "sound": sound,
    "anywhere": anywhere, "set": setting, "show": show, "hide": hide, "quit": quit_, "ping": ping,
}
