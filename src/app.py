"""
WinLink's window and the glue between it and the network.

The interface is webui/index.html shown in a native web view (WebKit on the
Mac, Edge WebView2 on Windows). Python sends it the whole state as JSON
(winlink.render) whenever something changes, plus new log lines
(winlink.logs); the page calls back through pywebview's js_api (Api below).
"""

import base64
import collections
import datetime
import functools
import hashlib
import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import webbrowser

try:
    import webview
    from webview.dom import DOMEventHandler
except ImportError:          # Linux servers: `winlink daemon` runs without a window
    webview = DOMEventHandler = None

import autostart
import kvm
import net
import sendto
from cleanup import Cleanup, move_to_trash

IS_MAC, IS_WIN = net.IS_MAC, net.IS_WIN
IS_LINUX = not IS_MAC and not IS_WIN
MAX_TEXT = 4 << 20
MAX_IMAGE = 40 << 20
SHOW_BATCHES = 12
CLIP_HISTORY = 50
CLIP_HISTORY_BYTES = 64 << 20       # images in history: memory cap
SEND_IDLE = "Play it on another computer."
RECV_IDLE = "Nothing playing here. Share sound from another device and choose this one to play it here."
QUALITY_LABELS = [("auto", "Auto"), ("lossless", "Lossless"), ("cd", "CD quality"), ("saver", "Data saver")]
LATENCY_LABELS = [("auto", "Auto"), ("lowest", "Lowest"), ("low", "Low"), ("balanced", "Balanced"), ("smooth", "Smooth")]


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def icon_path():
    """WinLink.png: next to the app in a build, in assets/ when run from the source folder."""
    for path in (resource("WinLink.png"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "WinLink.png")):
        if os.path.exists(path):
            return path
    return None


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def item_hash(item):
    if item["kind"] == "text":
        data = item["text"].replace("\r\n", "\n").encode("utf-8", "ignore")
    else:
        data = item.get("png") or item.get("bmp") or b""
    return hashlib.sha1(data).hexdigest()


def image_size(item):
    png, bmp = item.get("png"), item.get("bmp")
    if png and png[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", png[16:24])
    if bmp and bmp[:2] == b"BM":
        w, h = struct.unpack("<ii", bmp[18:26])
        return w, abs(h)
    return None


def reveal(path):
    if IS_MAC:
        subprocess.Popen(["open", "-R", path])
    elif IS_WIN:
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        _linux_reveal(path)


def open_path(path):
    if IS_MAC:
        subprocess.Popen(["open", path])
    elif IS_WIN:
        os.startfile(path)  # noqa: S606
    else:
        _linux_open(path)


def _linux_open(path):
    """xdg-open, without blocking or leaving zombies (it exits once the app has started)."""
    try:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        net.log.warning("can't open %s: xdg-open isn't installed", path)


def _linux_reveal(path):
    """Ask the file manager to show the file selected (Files, Dolphin, Nemo, Thunar... all answer this
    D-Bus call); otherwise just open its folder."""
    import urllib.parse
    uri = "file://" + urllib.parse.quote(os.path.abspath(path))
    for cmd in (["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                 "--object-path", "/org/freedesktop/FileManager1",
                 "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                 "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                 f"array:string:{uri}", "string:"]):
        try:
            if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            continue
    _linux_open(os.path.dirname(path) or ".")


def expiry_text(stamp):
    """("stays signed in" | "signed in until 30 Apr", days left or None)."""
    if not stamp:
        return "stays signed in", None
    try:
        when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "signed in", None
    days = (when - datetime.datetime.now(datetime.timezone.utc)).days
    local = when.astimezone()
    return f"signed in until {local.day} {local:%b}", days


# ---------------------------------------------------------------- live log for the Activity page
class LogFeed(logging.Handler):
    """Keeps the last 1000 log lines in memory so the window can show them as they happen."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = collections.deque(maxlen=1000)
        self.seq = 0
        self.lines_lock = threading.Lock()   # (Handler already has .lock; reusing it deadlocks)

    def emit(self, record):
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += " | " + logging.Formatter().formatException(record.exc_info).splitlines()[-1]
        except Exception:  # noqa: BLE001
            return
        with self.lines_lock:
            self.seq += 1
            self.lines.append({"n": self.seq, "t": record.created, "level": record.levelname.lower(),
                               "where": record.threadName, "msg": msg[:2000]})

    def since(self, n):
        with self.lines_lock:
            return [line for line in self.lines if line["n"] > n]


def safe(method):
    """A button press should never be able to break the window: log it and say so."""
    @functools.wraps(method)
    def wrapper(self, *args):
        try:
            return method(self, *args)
        except Exception:  # noqa: BLE001
            net.log.exception("UI action %s", method.__name__)
            self._app.toast("Something went wrong. Details are on the Activity page.")
            return None
    return wrapper


class _SafeMethods(type):
    def __new__(mcs, name, bases, ns):
        for key, value in list(ns.items()):
            if callable(value) and not key.startswith("_"):
                ns[key] = safe(value)
        return super().__new__(mcs, name, bases, ns)


class Api(metaclass=_SafeMethods):
    """Everything the page can ask for. (pywebview calls these on worker threads.)"""

    def __init__(self, app):
        self._app = app

    def ready(self):
        self._app.log_sent = 0
        self._app.dirty.set()

    # devices
    def connect(self, pid):
        self._app.with_peer(pid, lambda p: self._app.node.connect(p, manual=True))

    def answer(self, pid, allow):
        self._app.with_peer(pid, lambda p: self._app.node.answer(p, bool(allow)))

    def disconnect(self, pid):
        self._app.with_peer(pid, self._app.node.disconnect)

    def forget(self, pid):
        self._app.with_peer(pid, self._app.forget)

    def set_anywhere(self, on):
        self._app.node.set_anywhere(bool(on))
        self._app.dirty.set()

    def sign_in(self):
        self._app.sign_in()

    def sign_out(self):
        self._app.toast("Signed out of Tailscale. The saved sign-in on this computer was erased."
                        if self._app.node.sign_out() else "Couldn't sign out. Try again in a moment.")

    # clipboard
    def set_clipboard(self, on):
        self._app.settings["clipboard"] = bool(on)
        self._app.clip_reset.set()
        self._app.dirty.set()

    def clip_copy(self, cid):
        self._app.clip_copy(int(cid))

    def clip_delete(self, cid):
        self._app.clip_delete(int(cid))

    def clip_clear(self):
        self._app.clip_history.clear()
        self._app.dirty.set()

    # files
    def choose_files(self):
        paths = self._app.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        if paths:
            self._app.send(list(paths))

    def choose_folder(self):
        paths = self._app.window.create_file_dialog(webview.FileDialog.FOLDER)
        if paths:
            self._app.send(list(paths))

    def open_downloads(self):
        open_path(self._app.node.downloads)

    def set_file_target(self, target):
        self._app.file_target = target
        self._app.dirty.set()

    def cancel(self, key):
        if not self._app.node.cancel(key):
            self._app.toast("That transfer has already finished.")
        self._app.dirty.set()

    def dismiss(self, key):
        batch = self._app.node.batches.get(key)
        if batch is not None and batch.state != "active":
            del self._app.node.batches[key]
        self._app.dirty.set()

    def file_open(self, path):
        if self._app.cleanup.knows(path) and os.path.exists(path):
            open_path(path)

    def file_reveal(self, path):
        if (self._app.cleanup.knows(path) or self._app.batch_path_ok(path)) and os.path.exists(path):
            reveal(path)

    def file_delete(self, path):
        self._app.delete_received(path)

    def set_cleanup(self, on):
        self._app.cleanup.configure(enabled=bool(on))
        self._app.dirty.set()

    def set_cleanup_days(self, days):
        self._app.cleanup.configure(days=int(float(days)))
        self._app.dirty.set()

    # sound
    def set_audio(self, on):
        self._app.toggle_audio(bool(on))

    def set_audio_target(self, pid):
        self._app.pick_audio_target(pid)

    def set_quality(self, value):
        self._app.set_quality(value)

    def set_latency(self, value):
        self._app.set_latency(value)

    def set_volume(self, value):
        if self._app.player is not None:
            self._app.player.volume = max(0.0, min(1.0, float(value)))
            self._app.settings.data["volume"] = self._app.player.volume

    # settings
    def set_notch(self, on):
        self._app.set_notch(bool(on))

    def stop_playing(self):
        self._app.stop_playing()

    def open_privacy(self):
        self._app.open_privacy()

    def set_sendto(self, on):
        self._app.set_sendto(bool(on))

    def set_autostart(self, on):
        self._app.set_autostart(bool(on))

    # keyboard and mouse (Deskflow)
    def kvm_share(self, on):
        self._app.kvm.set_share(bool(on))

    def kvm_side(self, pid, side):
        self._app.kvm.set_side(str(pid), str(side))

    def kvm_grid(self, grid_json):
        grid = json.loads(grid_json) if isinstance(grid_json, str) else grid_json
        if isinstance(grid, dict):
            self._app.kvm.set_grid(grid)

    def kvm_allow(self, on):
        self._app.kvm.set_allow(bool(on))

    def kvm_get(self):
        webbrowser.open(kvm.DOWNLOAD_URL)
        self._app.toast("Install Deskflow, then come back: WinLink finds it by itself.", 8)

    def kvm_recheck(self):
        self._app.kvm.recheck()
        if not self._app.kvm.core:
            self._app.toast("Deskflow isn't installed yet.")

    def kvm_accessibility(self):
        if IS_MAC:
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

    def set_tray(self, on):
        self._app.set_tray(bool(on))

    def set_debug_log(self, on):
        self._app.settings["debug_log"] = bool(on)
        net.log.setLevel(logging.DEBUG if on else logging.INFO)
        self._app.dirty.set()

    def open_log_folder(self):
        open_path(net.config_dir())

    def copy_logs(self):
        self._app.clip_put_text("\n".join(
            f"{datetime.datetime.fromtimestamp(line['t']):%H:%M:%S} {line['level'].upper():7} {line['msg']}"
            for line in self._app.log_feed.since(0)))
        self._app.toast("Copied the log to the clipboard.")


class App:
    def __init__(self, pending=None, hidden=False, headless=False):
        self.pending = pending                   # (device id, paths) from "Send to", when WinLink wasn't running
        self.hidden = hidden                     # opened at login: start in the tray, no window
        self.headless = headless                 # Linux servers: no window at all (see cli.py)
        self.settings = net.Settings()
        for key, value in (("notch", False), ("audio_quality", "auto"), ("audio_latency", "auto"),
                           ("debug_log", False), ("sendto_menu", True), ("tray", True)):
            self.settings.data.setdefault(key, value)
        net.setup_logging()                      # log file first, then the live feed for the window
        self.log_feed = LogFeed()
        net.log.addHandler(self.log_feed)
        self.events = queue.Queue()
        self.node = net.Node(self.settings, lambda *a: self.events.put(a))
        if self.settings["debug_log"]:
            net.log.setLevel(logging.DEBUG)
        self.cleanup = Cleanup(net.config_dir(), self.settings, lambda text: self.toast(text, 8))
        self.dirty = threading.Event()
        self.clip_reset = threading.Event()
        self.clip_jobs = queue.Queue()              # work for the clipboard thread
        self.clip_history = collections.deque(maxlen=CLIP_HISTORY)
        self.clip_next = 0
        self.file_target = None
        self.sign_in_busy = False
        self.window = None
        self.notch = None
        if IS_LINUX:
            from filemenu_linux import FileManagerMenu
            self.sendto_menu = FileManagerMenu()
        else:
            self.sendto_menu = sendto.SendToMenu()
        self.log_sent = 0
        self.told = set()
        self.tray = None
        self.in_tray = False           # hidden in the tray / menu bar (window and Dock icon gone)
        self.tray_note = ""            # why there's no tray icon on this desktop
        self.login_error = ""

        self.player = self.sender = None
        self.receiving = None          # name of the device whose sound plays here
        self.recv_info = None
        self.send_text, self.send_tone = SEND_IDLE, "muted"
        try:
            from audio import Player
            self.player = Player()
            self.player.volume = float(self.settings.data.get("volume", 1.0))
            self._apply_latency()
            self.node.on_audio = self._audio_in
        except Exception:  # noqa: BLE001  (no audio output library: just can't receive sound)
            pass
        try:
            import audio
            backend = audio.capture_backend()          # Windows: WASAPI; Mac: ScreenCaptureKit
        except Exception:  # noqa: BLE001
            backend = None
        if backend is not None:
            self.sender = audio.SoundSender(self.node, backend,
                                            lambda text, st: self.events.put(("audio_out", text, st)),
                                            quality=lambda: self.settings["audio_quality"])
        self.clip_problem = ""         # Linux: why the clipboard can't be shared (a tool is missing)
        self.kvm = kvm.Sharing(self.node, self.settings, os.path.join(net.config_dir(), "kvm"), self.dirty.set)
        self.send_fix = None           # "privacy" when macOS needs the person to allow capturing sound

    # ------------------------------------------------------------ run
    def run(self):
        # Pass the page itself, not a URL: the web view can't serve a stale cached copy.
        with open(resource("webui", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.window = webview.create_window(
            "WinLink", html=page, js_api=Api(self), width=940, height=660, min_size=(780, 540),
            background_color="#F4F4F2", text_select=False, hidden=self.hidden)
        self.window.events.loaded += self._on_loaded
        self.window.events.minimized += self._minimized
        # Quit as soon as the window is asked to close. Waiting for the web view to tear itself down
        # took 2-3 s on Windows; nothing needs it: settings are saved as they change, the Tailscale
        # helper exits when our end of its pipe closes, and other computers see the connection drop.
        self.window.events.closing += self._quit
        self.window.events.closed += lambda: os._exit(0)
        icon = icon_path()
        if IS_LINUX and icon:                        # the window's icon in the dock and Alt-Tab (GTK)
            webview.start(self._started, private_mode=False, icon=icon)
        else:
            webview.start(self._started, private_mode=False)

    def run_headless(self):
        """No window: for servers and SSH sessions. Controlled with `winlink <command>` (cli.py)."""
        import signal
        signal.signal(signal.SIGTERM, lambda *_: self._quit())
        signal.signal(signal.SIGINT, lambda *_: self._quit())
        self._started()
        net.log.info("running without a window; control it with 'winlink help'")
        while True:
            time.sleep(3600)

    def _started(self):
        net.log.info("WinLink started on %s (%s)", self.node.name, net.OS_NAME)
        self.node.start()
        if self.settings["anywhere"]:
            self.node.set_anywhere(True)
        loops = [self._event_loop, self._clipboard_loop, self.cleanup.loop, self._awake_loop]
        if not self.headless:
            loops.append(self._push_loop)
        for target in loops:
            threading.Thread(target=target, daemon=True, name=target.__name__.strip("_")).start()
        if not self.headless:
            autostart.refresh()                  # WinLink moved since the entry was made: follow it
            if self.settings["tray"]:
                self._start_tray()
            if self.hidden:                      # opened at login
                if self.tray is not None:
                    self.in_tray = True
                    if IS_MAC:
                        self._mac_dock(False)
                else:                            # nowhere to open it from: show the window after all
                    self._show_window()
        if IS_MAC and self.settings.data.get("notch"):
            self.set_notch(True)
        try:
            sendto.HandoffServer(net.config_dir(), self._handoff)
        except OSError:
            net.log.exception("hand-off listener")
        self._sync_sendto()
        if self.pending:
            target, paths = self.pending
            self.pending = None
            threading.Thread(target=self._send_when_ready, args=(target, paths, 45), daemon=True).start()

    def _awake_loop(self):
        """Hidden in the tray, WinLink must not be slowed down (App Nap, efficiency mode); and the
        computer shouldn't fall asleep in the middle of sound or a transfer."""
        import keepawake
        try:
            awake = keepawake.KeepAwake()
        except Exception:  # noqa: BLE001
            net.log.exception("keep awake")
            return
        while True:
            sending = self.sender is not None and self.sender.running
            moving = [b for b in list(self.node.batches.values()) if b.state == "active"]
            why = ("sharing this computer's sound" if sending else
                   f"playing sound from {self.receiving}" if self.receiving else
                   f"transferring {moving[0].name}" if moving else "")
            awake.set_busy(bool(why), why)
            time.sleep(3)

    def _quit(self):
        net.log.info("WinLink quitting")
        try:
            self.kvm.stop_all()                  # Deskflow's engine is our child: don't leave it running
        except Exception:  # noqa: BLE001
            pass
        logging.shutdown()                       # flush the log file
        os._exit(0)

    def _on_loaded(self):
        try:
            self.window.dom.document.events.drop += DOMEventHandler(self._dropped, True, True)
        except Exception:  # noqa: BLE001
            pass
        self.dirty.set()

    def _dropped(self, event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            self.send(paths)

    def js(self, code):
        if self.window is None:
            return
        try:
            self.window.evaluate_js(code)
        except Exception:  # noqa: BLE001  (window closing)
            pass

    def toast(self, text, seconds=5):
        if self.headless:
            net.log.info("%s", text)                 # shows in `winlink log`
            return
        self.js(f"winlink.toast({json.dumps(text)}, {seconds})")

    # ------------------------------------------------------------ start at login, tray / menu bar
    def set_autostart(self, on):
        self.login_error = autostart.set_enabled(on)
        if self.login_error:
            self.toast(f"Couldn't change that: {self.login_error}")
        self.dirty.set()

    def set_tray(self, on):
        self.settings["tray"] = on
        if on and self.tray is None:
            self._start_tray()
        elif not on and self.tray is not None:
            self.tray.stop()
            self.tray = None
            if self.in_tray:                     # the icon was the only way back: show the window
                self._show_window()
        self.dirty.set()

    def _start_tray(self):
        import tray
        self.tray, self.tray_note = tray.create(self, icon_path())
        if self.tray is None:
            net.log.info("no tray icon: %s", self.tray_note)

    def _minimized(self):
        """Minimising hides WinLink completely: no taskbar button (Windows), no dock or taskbar entry
        (Linux), no Dock icon (Mac). The tray / menu bar icon brings it back. Without that icon there'd
        be no way back, so then it minimises as usual."""
        if self.tray is not None:
            self.hide_to_tray()

    def hide_to_tray(self):
        self.in_tray = True
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001
            pass
        if IS_MAC:
            self._mac_dock(False)

    def _mac_dock(self, show):
        """Mac: the Dock icon comes and goes with the window (an "accessory" app has none)."""
        try:
            from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                                NSApplicationActivationPolicyRegular)
            from PyObjCTools import AppHelper
        except ImportError:
            return

        def apply():
            nsapp = NSApplication.sharedApplication()
            nsapp.setActivationPolicy_(NSApplicationActivationPolicyRegular if show
                                       else NSApplicationActivationPolicyAccessory)
            if show:
                nsapp.activateIgnoringOtherApps_(True)       # come to the front, above other apps
        AppHelper.callAfter(apply)

    def menu(self):
        """The tray / menu bar menu: [(label, action or None, checked or None)]."""
        on = self.node.connected()
        items = [("Open WinLink", self._show_window, None),
                 (f"Connected: {', '.join(p.name for p in on)}" if on else "No devices connected", None, None)]
        if self.receiving:
            items.append((f"Playing sound from {self.receiving}", None, None))
        if self.sender is not None:
            items.append((f"Share this {self._kind()}'s sound", lambda: self.toggle_audio(not self.settings["audio"]),
                          bool(self.settings["audio"])))
        items.append(("Connect from anywhere", lambda: (self.node.set_anywhere(not self.settings["anywhere"]),
                                                        self.dirty.set()), bool(self.settings["anywhere"])))
        return items

    def quit(self):
        self._quit()

    def with_peer(self, pid, fn):
        peer = self.node.peers.get(pid)
        if peer is not None:
            fn(peer)
            self.dirty.set()

    def forget(self, peer):
        self.node.forget(peer)
        with self.node.lock:
            self.node.peers.pop(peer.id, None)

    # ------------------------------------------------------------ events from the network
    def _event_loop(self):
        while True:
            ev = self.events.get()
            handler = getattr(self, "_on_" + ev[0], None)
            if handler:
                try:
                    handler(*ev[1:])
                except Exception:  # noqa: BLE001  (one bad event shouldn't stop the app)
                    net.log.exception("handling %s", ev[0])
            self.dirty.set()

    def _on_kvm_msg(self, peer, msg):
        self.kvm.on_message(peer, msg)

    def _on_peers(self):
        self._maybe_start_audio()
        self.kvm.refresh()
        self._sync_sendto()
        if self.notch is not None:
            self.notch.refresh()

    def _on_batch(self, batch):
        if batch.state == "active" or batch.key in self.told:
            return
        self.told.add(batch.key)
        what = batch.name if batch.files_total == 1 else f"{batch.name} ({batch.files_total} files)"
        if batch.state == "done":
            self.toast(f"{'Sent' if batch.direction == 'out' else 'Received'} {what}.")
        elif batch.state == "failed":
            why = f": {batch.error}" if batch.error and len(batch.error) < 90 else "."
            self.toast(f"Couldn't transfer {what}{why}", 8)
        elif batch.state == "cancelled" and "on " in (batch.error or ""):
            self.toast(f"{batch.peer.name} cancelled {what}.")

    def _on_received(self, path, created, peer_name):
        self.cleanup.record(path, created, peer_name)

    def _on_files_removed(self, paths):
        self.cleanup.forget(paths)

    def _on_notice(self, text):
        self.toast(text)

    def _on_clip_in(self, peer, msg):
        if self.settings["clipboard"]:
            self.clip_jobs.put(("in", peer, msg))

    def _on_audio_in(self, peer, info):
        self.recv_info = info
        self.receiving = peer.name if info else None
        if not info:
            self._maybe_start_audio()

    def _on_audio_out(self, text, st):
        """The sender's state: playing | retry (network trouble: try again) | off (the other side said
        no or stop, or no permission: wait for the person) | idle (stopped here)."""
        self.send_fix = None
        if st == "playing":
            self.send_text, self.send_tone = text, "good"
        elif st == "retry" and self.settings["audio"]:
            self.send_text, self.send_tone = text or "Reconnecting…", "muted"
            if not text.startswith("Connecting"):
                threading.Timer(3, self._maybe_start_audio).start()
        elif st == "off":
            self.settings["audio"] = False             # don't fight: the person decides what's next
            self.send_text, self.send_tone = text, "bad"
            if "allowed WinLink" in text:
                self.send_fix = "privacy"
                self.send_text = ("macOS hasn't allowed WinLink to capture sound. Turn WinLink on under "
                                  "Screen & System Audio Recording, then turn sharing on again.")
            net.log.info("sound sharing stopped: %s", text)
            self.toast(self.send_text, 8)
        else:
            self.send_text, self.send_tone = SEND_IDLE, "muted"

    def _on_anywhere(self):
        self.sign_in_busy = False

    # ------------------------------------------------------------ pushing state to the page
    def _push_loop(self):
        last = None
        while True:
            self.dirty.wait(1.0)          # at least once a second, for timers and expiry text
            self.dirty.clear()
            try:
                state = json.dumps(self.state())
            except Exception:  # noqa: BLE001
                net.log.exception("building window state")
                time.sleep(1)
                continue
            if state != last:
                last = state
                self.js(f"winlink.render({state})")
                if self.tray is not None:
                    self.tray.refresh()              # the tray menu shows who's connected
            new = self.log_feed.since(self.log_sent)
            if new:
                self.log_sent = new[-1]["n"]
                self.js(f"winlink.logs({json.dumps(new)})")
            if self.player is not None and self.player.stream:
                self.js(f"winlink.level({min(1.0, self.player.peak * 1.4):.3f})")
                time.sleep(0.1)
                self.dirty.set()
            else:
                time.sleep(0.05)

    def state(self):
        node = self.node
        peers = node.snapshot()
        connected = node.connected()
        devices = []
        for p in peers:
            trusted = node.is_trusted(p.id)
            d = {"id": p.id, "name": p.name, "os": p.os, "trusted": trusted, "code": "", "route": None,
                 "status": ""}
            if p.incoming:
                d.update(state="ask", code=p.incoming["code"], repair=p.incoming.get("repair", False))
            elif p.waiting_code:
                d.update(state="wait", code=p.waiting_code)
            elif p.control is not None:
                d.update(state="on", route=p.control.route, status="Connected")
            elif p.connecting:
                d.update(state="busy", status="Connecting…")
            elif not p.nearby:
                d.update(state="away", status="Offline")
            elif trusted and not p.paused and not p.remote_paused:
                d.update(state="busy", status="Reconnecting…")
            else:
                d.update(state="free", status="Disconnected" if trusted else "Available")
            devices.append(d)

        targets = [{"id": p.id, "name": p.name} for p in connected]
        if len(targets) > 1:
            targets.append({"id": "all", "name": "All devices"})
        if self.file_target not in {t["id"] for t in targets}:
            self.file_target = targets[0]["id"] if targets else None

        sound = {"can_send": self.sender is not None, "can_receive": self.player is not None,
                 "send_hint": "" if self.sender is not None or IS_MAC or IS_WIN else
                 "To share it, install parec (pulseaudio-utils on Debian, Ubuntu and Fedora; libpulse on Arch) "
                 "and restart WinLink.",
                 "qualities": QUALITY_LABELS, "latencies": LATENCY_LABELS,
                 "quality": self.settings["audio_quality"], "latency": self.settings["audio_latency"]}
        if self.sender is not None:
            target = self._audio_target()
            sound["send"] = {"enabled": bool(self.settings["audio"]), "text": self.send_text,
                             "tone": self.send_tone, "targets": [{"id": p.id, "name": p.name} for p in connected],
                             "target": target.id if target else None, "fix": self.send_fix}
        if self.player is not None:
            info = self.recv_info
            if info and self.receiving:
                quality = f"{info['bits']}{' mono' if info.get('channels') == 1 else ''}"
                text = (f"Playing from {self.receiving} · {info['rate'] / 1000:g} kHz {quality} · "
                        f"{self.player.delay_ms} ms behind{' · via Tailscale' if info.get('remote') else ''}")
            else:
                text = RECV_IDLE
            sound["recv"] = {"playing": bool(info), "text": text, "volume": self.player.volume,
                             "from": self.receiving if info else None}

        extra = {"on": " · Tailscale app on"}.get(node.tailscale, "")
        return {
            "me": {"name": node.name, "kind": "mac" if IS_MAC else "pc", "os": net.OS_NAME, "extra": extra},
            "devices": devices,
            "anywhere": self._anywhere_state(),
            "clipboard": {"enabled": bool(self.settings["clipboard"]), "problem": self.clip_problem, "history": [
                {k: v for k, v in e.items() if k in ("id", "dir", "kind", "preview", "peer", "at")}
                for e in reversed(self.clip_history)]},
            "files": {"targets": targets, "target": self.file_target, "batches": self._batches_state(),
                      "received": self._received_state(), "downloads": node.downloads},
            "cleanup": self._cleanup_state(),
            "sound": sound,
            "notch": {"available": IS_MAC, "enabled": bool(self.settings.data.get("notch"))},
            "sendto": {"available": IS_WIN or IS_LINUX, "enabled": bool(self.settings["sendto_menu"]),
                       "status": self.sendto_menu.status},
            "debug_log": bool(self.settings["debug_log"]),
            "kvm": self.kvm.state(node.connected()),
            "about": {"version": net.VERSION, "id": node.id, "port": net.PORT,
                      "paired": sorted(self.settings["trusted"].values(), key=str.lower)},
            "startup": {"login": autostart.enabled(), "login_error": self.login_error,
                        "tray": bool(self.settings["tray"]), "tray_ok": self.tray is not None,
                        "tray_kind": "menubar" if IS_MAC else "tray",
                        "tray_note": f"No tray icon: {self.tray_note}." if self.settings["tray"] and self.tray_note else ""},
        }

    def _batches_state(self):
        out = []
        for b in sorted(self.node.batches.values(), key=lambda b: (b.state != "active", -b.started))[:SHOW_BATCHES]:
            done, size = b.done, max(b.size, 1)
            frac = min(1.0, done / size)
            if b.state == "active":
                status = f"{int(frac * 100)}%"
                if b.error:
                    status += f" · {b.error}"
            elif b.state == "done":
                status = f"{'Sent' if b.direction == 'out' else 'Saved'} · {human(b.size)}"
            elif b.state == "cancelled":
                status = "Cancelled" + (f" on {b.peer.name}" if "on " in (b.error or "") else "")
                if b.direction == "in":
                    status += " · removed"
            else:
                status = f"Failed · {b.error}" if b.error else "Failed"
            path = None
            if b.direction == "in" and b.state == "done" and b.paths:
                try:
                    path = b.paths[0] if len(b.paths) == 1 else os.path.commonpath(b.paths)
                except ValueError:
                    path = b.paths[0]
            out.append({"key": b.key, "dir": b.direction, "name": b.name, "peer": b.peer.name,
                        "state": b.state, "frac": frac, "status": status, "path": path,
                        "files": f"{b.files_done} of {b.files_total} files" if b.files_total > 1 else "",
                        "size": human(b.size)})
        return out

    def batch_path_ok(self, path):
        return any(path in b.paths or (len(b.paths) > 1 and os.path.commonpath(b.paths) == path)
                   for b in self.node.batches.values() if b.direction == "in" and b.paths)

    def _received_state(self):
        out = []
        for e in reversed(self.cleanup.entries()[-100:]):
            out.append({"path": e["path"], "name": os.path.basename(e["path"]), "size": human(e["size"]),
                        "at": e["at"], "from": e.get("from", ""),
                        "folder": os.path.relpath(os.path.dirname(e["path"]), self.node.downloads)
                        if e["path"].startswith(self.node.downloads) else ""})
        return out

    def delete_received(self, path):
        if not self.cleanup.knows(path):
            return
        if not os.path.exists(path):
            self.cleanup.forget([path])
        elif move_to_trash(path):
            self.cleanup.forget([path])
            self.cleanup.remove_empty_dirs()
            self.toast(f"Moved {os.path.basename(path)} to the {'Recycle Bin' if IS_WIN else 'Trash'}.")
        else:
            self.toast(f"Couldn't delete {os.path.basename(path)}. Is it open somewhere?")
        self.dirty.set()

    def _cleanup_state(self):
        c = self.cleanup
        bin_name = "Recycle Bin" if IS_WIN else "Trash"
        if not c.enabled:
            text = f"Move files WinLink received to the {bin_name} after a number of days you choose."
        else:
            count, size, soonest = c.summary()
            if not count:
                text = "On · nothing to clean up yet. Files you edit or move are always kept."
            else:
                when = ("any moment" if soonest <= 60 else f"{int(soonest // 60)} min" if soonest < 3600
                        else f"{int(soonest // 3600)} h" if soonest < 86400 else f"{int(soonest // 86400)} days")
                what = "file" if count == 1 else "files"
                text = f"On · {count} received {what} ({human(size)}) · next goes to the {bin_name} in {when}"
        return {"enabled": c.enabled, "days": c.days, "text": text}

    def _anywhere_state(self):
        st = self.node.anywhere
        state = st.get("state", "Off")
        out = {"enabled": bool(self.settings["anywhere"]), "sign_in": False, "sign_out": False,
               "busy": self.sign_in_busy, "tone": "", "text": ""}
        if not self.settings["anywhere"] or state == "Off":
            out.update(text="Reach your computers on any network, using Tailscale.")
        elif state == "NeedsLogin":
            out.update(text="Sign in to Tailscale to finish. Use the same account everywhere.",
                       tone="strong", sign_in=True)
        elif state == "NeedsMachineAuth":
            out.update(text="Waiting for approval in your Tailscale admin console.", tone="strong")
        elif state == "Running":
            until, days = expiry_text(st.get("key_expiry"))
            out.update(text=f"On · {until}", tone="good", sign_out=True)
            if days is not None and days < 14:
                out.update(text=f"On · sign-in expires in {max(days, 0)} days. Turn off key expiry "
                                f"in Tailscale's admin page to stay signed in.", tone="warn")
        elif state == "Error":
            out.update(text=f"Couldn't start Tailscale: {st.get('error', 'unknown error')}", tone="bad")
        else:
            out.update(text="Connecting to Tailscale…")
        return out

    # ------------------------------------------------------------ Tailscale sign-in
    def sign_in(self):
        self.sign_in_busy = True
        self.dirty.set()
        url = self.node.sign_in_url()
        self.sign_in_busy = False
        self.dirty.set()
        if url:
            webbrowser.open(url)
            self.toast("Finish signing in to Tailscale in your browser.", 10)
        else:
            self.toast("Couldn't get a sign-in link from Tailscale. Try again in a moment.")

    # ------------------------------------------------------------ clipboard
    def _clipboard_loop(self):
        """All clipboard work happens on this one thread."""
        if os.environ.get("LINK_NO_CLIPBOARD"):        # leave this computer's clipboard alone
            self._clipboard_history_only()
            return
        try:
            if IS_MAC:
                from clip_mac import Clipboard
            elif IS_WIN:
                from clip_win import Clipboard
            else:
                from clip_linux import Clipboard
            clip = Clipboard(None)
        except Exception as e:  # noqa: BLE001
            if self.headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                net.log.info("no desktop: received clipboard items are kept for 'winlink clip' only")
            else:
                net.log.exception("clipboard unavailable")
                if not IS_MAC and not IS_WIN:
                    self.clip_problem = (f"Clipboard sharing is off: {e}. Install xclip (X11) or wl-clipboard "
                                         "(Wayland), then restart WinLink.")
                    self.toast(self.clip_problem, 12)
                    self.dirty.set()
            self._clipboard_history_only()
            return
        last_seq, last_hash = clip.seq(), None
        while True:
            try:
                job = self.clip_jobs.get(timeout=0.35)
            except queue.Empty:
                job = None
            try:
                if job and job[0] == "in":                 # from another computer
                    _, peer, msg = job
                    item = {"kind": msg.get("kind", "text")}
                    if item["kind"] == "text":
                        item["text"] = msg.get("text", "")
                    else:
                        for key in ("png", "bmp"):
                            if msg.get(key):
                                item[key] = base64.b64decode(msg[key])
                    h = item_hash(item)
                    if h == last_hash:                    # already have it (it came round another way)
                        continue
                    last_hash = h
                    last_seq = clip.write(item)
                    self._remember_clip("in", item, peer.name)
                    # pass it on, so every connected computer has the same clipboard, not just this one
                    if self.settings["clipboard"] and any(p.id != peer.id for p in self.node.connected()):
                        self.node.send_clip(item, exclude=peer.id)
                    continue
                if job and job[0] == "put":               # copied again from history (or the log)
                    _, item, share = job
                    last_hash = item_hash(item)
                    last_seq = clip.write(item)
                    if share and self.settings["clipboard"] and self.node.connected():
                        self.node.send_clip(item)
                    continue
                if self.clip_reset.is_set():            # just switched on: don't send old stuff
                    self.clip_reset.clear()
                    last_seq = clip.seq()
                seq = clip.seq()
                if seq == last_seq:
                    continue
                last_seq = seq
                if not self.settings["clipboard"] or not self.node.connected():
                    continue
                item = clip.read()
                if not item:
                    continue
                size = len(item.get("text", "")) if item["kind"] == "text" else \
                    len(item.get("png") or b"") + len(item.get("bmp") or b"")
                if size > (MAX_TEXT if item["kind"] == "text" else MAX_IMAGE):
                    self.toast("That's too big for the clipboard. Send it as a file instead.")
                    continue
                h = item_hash(item)
                if h != last_hash:
                    last_hash = h
                    self.node.send_clip(item)
                    self._remember_clip("out", item, ", ".join(p.name for p in self.node.connected()))
            except Exception:  # noqa: BLE001  (a busy clipboard is retried next time)
                net.log.debug("clipboard: %s", sys.exc_info()[1])

    def _clipboard_history_only(self):
        """No clipboard here (a server): keep what arrives, so `winlink clip` can print it."""
        while True:
            job = self.clip_jobs.get()
            try:
                if job[0] == "in":
                    _, peer, msg = job
                    item = {"kind": msg.get("kind", "text")}
                    if item["kind"] == "text":
                        item["text"] = msg.get("text", "")
                    else:
                        for key in ("png", "bmp"):
                            if msg.get(key):
                                item[key] = base64.b64decode(msg[key])
                    self._remember_clip("in", item, peer.name)
                elif job[0] == "put" and job[2] and self.node.connected():
                    self.node.send_clip(job[1])
            except Exception:  # noqa: BLE001
                net.log.debug("clipboard: %s", sys.exc_info()[1])

    def _remember_clip(self, direction, item, peer):
        if item["kind"] == "text":
            preview = " ".join(item["text"].split())[:200]
        else:
            dims = image_size(item)
            preview = f"Image · {dims[0]} × {dims[1]}" if dims else "Image"
        self.clip_next += 1
        self.clip_history.append({"id": self.clip_next, "dir": direction, "kind": item["kind"], "preview": preview,
                                  "peer": peer, "at": time.time(), "item": item})
        # keep images from eating memory: drop the oldest image data beyond the cap
        total = 0
        for e in reversed(self.clip_history):
            if e["kind"] == "image":
                total += len(e["item"].get("png") or b"") + len(e["item"].get("bmp") or b"")
                if total > CLIP_HISTORY_BYTES:
                    e["item"] = None
        self.dirty.set()

    def clip_copy(self, cid):
        entry = next((e for e in self.clip_history if e["id"] == cid), None)
        if entry is None or entry["item"] is None:
            self.toast("That item isn't available any more.")
            return
        self.clip_jobs.put(("put", entry["item"], True))
        self.toast("Copied. It's on the clipboard here" + (" and on your other computers." if
                   self.settings["clipboard"] and self.node.connected() else "."))

    def clip_put_text(self, text):
        self.clip_jobs.put(("put", {"kind": "text", "text": text}, False))

    def clip_delete(self, cid):
        for e in list(self.clip_history):
            if e["id"] == cid:
                self.clip_history.remove(e)
        self.dirty.set()

    # ------------------------------------------------------------ files
    def send(self, paths, target=None):
        peers = self.node.connected()
        if not peers:
            self.toast("Connect to another computer first.")
            return False
        target = target or self.file_target
        if target != "all":
            peers = [p for p in peers if p.id == target] if target else peers[:1]
            if not peers:
                self.toast("That device isn't connected any more.")
                return False
        count = 0
        for peer in peers:
            count = self.node.send_paths(peer, paths)
        if count == 0:
            self.toast("Nothing to send there.")
        return count > 0

    # ------------------------------------------------------------ sound
    def _apply_latency(self):
        import audio
        if self.player is not None:
            self.player.set_latency(audio.LATENCY.get(self.settings["audio_latency"]))

    def set_latency(self, value):
        if value not in dict(LATENCY_LABELS):
            return
        self.settings["audio_latency"] = value
        self._apply_latency()
        net.log.info("sound latency set to %s", value)
        self.dirty.set()

    def set_quality(self, value):
        if value not in dict(QUALITY_LABELS):
            return
        self.settings["audio_quality"] = value
        net.log.info("sound quality set to %s", value)
        if self.sender is not None and self.sender.running:
            self.sender.stop()
            threading.Timer(0.8, self._maybe_start_audio).start()    # restart with the new quality
        self.dirty.set()

    def _audio_in(self, peer, hdr, sock):        # network thread
        refuse = None
        if self.sender is not None and self.sender.running:
            # Never send and receive at once: that's how sound loops (A -> B -> A) start.
            refuse = f"{self.node.name} is sharing its own sound"
            self.toast(f"{peer.name} tried to play sound here, but this {self._kind()} is sharing its own.")
        self.player.receive(hdr, sock, peer.name, lambda info: self.events.put(("audio_in", peer, info)),
                            refuse=refuse, peer_id=peer.id)

    def _kind(self):
        return "Mac" if IS_MAC else "PC"

    def stop_playing(self):
        """Stop the sound playing here; its sender stops sharing (it's told why)."""
        if self.player is not None and self.receiving:
            self.player.stop_current(f"stopped on {self.node.name}")

    def open_privacy(self):
        if IS_MAC:
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])

    def _audio_target(self):
        peers = self.node.connected()
        saved = self.settings.data.get("audio_target")
        if saved:
            return next((p for p in peers if p.id == saved), None)
        return peers[0] if peers else None

    def pick_audio_target(self, pid):
        if pid == self.settings.data.get("audio_target"):
            return
        self.settings["audio_target"] = pid
        if self.sender is not None and self.sender.running:
            self.sender.stop()
            threading.Timer(0.8, self._maybe_start_audio).start()   # let the old stream close
        self.dirty.set()

    def toggle_audio(self, on):
        if self.sender is None:
            return
        self.settings["audio"] = on
        if on and self.receiving:
            # Sharing wins: stop what's playing here (its sender is told), then start.
            self.player.stop_current(f"{self.node.name} started sharing its own sound")
            threading.Timer(0.8, self._maybe_start_audio).start()
            self.dirty.set()
            return
        if on:
            self._maybe_start_audio()
        else:
            self.sender.stop()
            self.send_text, self.send_tone = SEND_IDLE, "muted"
        self.dirty.set()

    def _maybe_start_audio(self):
        if self.sender is None or not self.settings["audio"] or self.sender.running:
            return
        if self.receiving:
            self.send_text, self.send_tone = f"Paused while {self.receiving}'s sound plays here.", "muted"
            return
        target = self._audio_target()
        if target is None:
            saved = self.settings["trusted"].get(self.settings.data.get("audio_target", ""), "")
            self.send_text = f"Waiting for {saved or 'another computer'} to connect…"
            self.send_tone = "muted"
            return
        self.sender.start(target)

    # ------------------------------------------------------------ "Send to" menu (Windows) and hand-off
    def _sync_sendto(self):
        if not IS_WIN and not IS_LINUX:
            return
        # paired devices, under the name they use now (a renamed PC gets a renamed entry)
        devices = [(pid, getattr(self.node.peers.get(pid), "name", None) or name)
                   for pid, name in self.settings["trusted"].items()]
        enabled = bool(self.settings["sendto_menu"])

        def run():
            if self.sendto_menu.sync(devices, enabled):
                self.dirty.set()
        threading.Thread(target=run, daemon=True, name="sendto").start()

    def set_sendto(self, on):
        self.settings["sendto_menu"] = bool(on)
        self._sync_sendto()
        self.dirty.set()

    def _handoff(self, req):
        """Another copy of WinLink was started: by a Send to shortcut, `winlink <command>` (cli.py),
        or just opened again."""
        if isinstance(req.get("cmd"), str):
            import commands
            return commands.run(self, req["cmd"], req.get("args") or [])
        if req.get("send") is not None:
            paths = req.get("paths")
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                return False, "Nothing to send."
            return self._send_when_ready(str(req["send"]), paths)
        self._show_window()
        return True, ""

    def _send_when_ready(self, target, paths, wait=25):
        """Send once the device is connected, connecting to it if needed. (ok, message)"""
        paths = [p for p in paths[:10000] if os.path.exists(p)]
        if not paths:
            return False, "Those files couldn't be found."
        trusted = self.settings["trusted"]
        if target != "all" and target not in trusted:
            return False, "That device isn't paired with WinLink any more."
        name = "your devices" if target == "all" else trusted[target]
        start, asked = time.time(), set()
        while True:
            connected = {p.id for p in self.node.connected()}
            waited = time.time() - start
            if target == "all":
                ready = connected and (set(trusted) <= connected or waited > 6)
            else:
                ready = target in connected
            if ready:
                break
            if waited > wait:
                return False, f"Couldn't reach {name}. Check that WinLink is open on it."
            for pid in (trusted if target == "all" else [target]):
                peer = self.node.peers.get(pid)
                if peer is not None and pid not in connected and pid not in asked and not peer.connecting:
                    asked.add(pid)
                    self.node.connect(peer, manual=True)
            time.sleep(0.4)
        if not self.send(paths, target):
            return False, f"Couldn't send to {name}."
        count = f"{len(paths)} item{'s' if len(paths) != 1 else ''}"
        self.toast(f"Sending {count} to {name}.")
        return True, ""

    # ------------------------------------------------------------ notch (Mac)
    def set_notch(self, on):
        if not IS_MAC:
            return
        self.settings["notch"] = on
        from PyObjCTools import AppHelper

        def apply():
            if on and self.notch is None:
                import notch_mac
                self.notch = notch_mac.NotchDrop(self._notch_targets, self._notch_drop, self._show_window)
            elif not on and self.notch is not None:
                self.notch.close()
                self.notch = None
        AppHelper.callAfter(apply)
        self.dirty.set()

    def _notch_targets(self):
        peers = self.node.connected()
        # one card per device (a drop goes only to the device it lands on), plus "All devices"
        targets = [(p.id, p.name, p.os) for p in peers]
        if len(peers) > 1:
            targets.append(("all", "All devices", "all"))
        return targets

    def _notch_drop(self, paths, target):
        return self.send(paths, target)

    def _show_window(self):
        if self.window is None:
            return
        self.in_tray = False
        if IS_MAC:
            self._mac_dock(True)
        try:
            self.window.show()
            self.window.restore()
        except Exception:  # noqa: BLE001
            pass
