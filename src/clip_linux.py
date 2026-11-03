"""Linux clipboard: text, images and copied files (their paths: they're sent only when pasted).

Wayland desktops that let apps watch the clipboard (KDE, Sway, Hyprland...) use wl-clipboard.
Everything else, including GNOME on Wayland (it mirrors the clipboard to X11 apps), uses X11:
xclip reads and writes, and the XFixes extension says when the clipboard changes, so nothing
is polled. Images go out as PNG plus BMP (Windows apps want a bitmap) when Pillow is installed.
"""

import ctypes
import ctypes.util
import io
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.parse

log = logging.getLogger("winlink")
FILE_TYPES = ("text/uri-list", "x-special/gnome-copied-files", "application/x-kde-cutselection")
TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT")
TIMEOUT = 3


class Unavailable(OSError):
    """No way to reach the clipboard here; the message says what to install."""


def Clipboard(_window=None):  # noqa: N802  (same name as the Mac and Windows classes)
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    problems = []
    if wayland and shutil.which("wl-paste") and shutil.which("wl-copy"):
        try:
            return WaylandClipboard()
        except OSError as e:          # GNOME: no clipboard watching for apps; X11 below still works
            problems.append(str(e))
    if x11:
        if not shutil.which("xclip"):
            problems.append("xclip isn't installed")
        else:
            try:
                return X11Clipboard()
            except OSError as e:
                problems.append(str(e))
    if wayland and not shutil.which("wl-paste"):
        problems.append("wl-clipboard isn't installed")
    raise Unavailable("; ".join(problems) or "no X11 or Wayland display")


def _png_to_bmp(png):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            out = io.BytesIO()
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(out, "BMP")
            return out.getvalue()
    except Exception:  # noqa: BLE001  (no Pillow, or an odd image: PNG alone still works for most apps)
        return None


def _bmp_to_png(bmp):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(bmp)) as im:
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _file_paths(raw):
    """Local paths from a text/uri-list (or GNOME's list, which starts with "copy"/"cut")."""
    paths = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("file://"):
            path = urllib.parse.unquote(urllib.parse.urlparse(line).path)
            if os.path.exists(path):
                paths.append(path)
    return paths


def _run(cmd, data=None, timeout=TIMEOUT):
    """stdout of a clipboard tool, or None if it failed (nothing there, or that type isn't offered)."""
    try:
        r = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _put(cmd, data):
    """Hand data to xclip / wl-copy. They stay in the background to serve it (until something else
    is copied), so their output must not be a pipe we wait on."""
    try:
        subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=TIMEOUT, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("clipboard write: %s", e)


class _Base:
    def __init__(self):
        self.count = 0
        self.lock = threading.Lock()

    def _changed(self):
        with self.lock:
            self.count += 1

    def seq(self):
        return self.count

    def _item(self, types, get):
        if any(t in types for t in FILE_TYPES):        # files copied in a file manager
            for t in FILE_TYPES[:2]:
                if t in types:
                    paths = _file_paths(get(t) or b"")
                    if paths:
                        return {"kind": "files", "paths": paths}
            return None
        if "image/png" in types:
            png = get("image/png")
            if png:
                return {"kind": "image", "png": png, "bmp": _png_to_bmp(png)}
        if "image/bmp" in types:
            bmp = get("image/bmp")
            if bmp and bmp[:2] == b"BM":
                return {"kind": "image", "png": _bmp_to_png(bmp), "bmp": bmp}
        for t in TEXT_TYPES:
            if t in types:
                raw = get(t)
                if raw:
                    return {"kind": "text", "text": raw.decode("utf-8", "replace")}
        return None

    @staticmethod
    def _image(item):
        png = item.get("png")
        if not png and item.get("bmp"):
            png = _bmp_to_png(item["bmp"])
        return png


class X11Clipboard(_Base):
    def __init__(self):
        super().__init__()
        self._watch()

    def _watch(self):
        """A thread with its own X connection, woken by XFixes whenever the clipboard owner changes."""
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        name = ctypes.util.find_library("Xfixes") or "libXfixes.so.3"
        try:
            xf = ctypes.CDLL(name)
        except OSError as e:
            raise OSError("the XFixes library (libxfixes) is missing") from e
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        xf.XFixesQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        xf.XFixesSelectSelectionInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        x11.XInitThreads()            # GTK uses Xlib from another thread in this process; harmless if repeated
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            raise OSError(f"can't open the X display {os.environ.get('DISPLAY')}")
        base, err = ctypes.c_int(), ctypes.c_int()
        if not xf.XFixesQueryExtension(dpy, ctypes.byref(base), ctypes.byref(err)):
            raise OSError("the X server has no XFixes extension")
        clipboard = x11.XInternAtom(dpy, b"CLIPBOARD", 0)
        xf.XFixesSelectSelectionInput(dpy, x11.XDefaultRootWindow(dpy), clipboard, 1)   # SetSelectionOwnerNotify
        notify = base.value                                 # XFixesSelectionNotify = event base + 0
        event = ctypes.create_string_buffer(192)            # sizeof(XEvent) on 64-bit

        def loop():
            while True:
                x11.XNextEvent(dpy, event)                  # blocks; this display is used only here
                if int.from_bytes(event.raw[:4], "little") == notify:
                    self._changed()
        threading.Thread(target=loop, daemon=True, name="x11 clipboard").start()

    def _get(self, t):
        return _run(["xclip", "-selection", "clipboard", "-o", "-t", t])

    def read(self):
        raw = self._get("TARGETS")
        if not raw:
            return None
        return self._item(set(raw.decode("utf-8", "replace").split()), self._get)

    def write(self, item):
        if item["kind"] == "files":          # files from another computer: Linux can't wait for the paste
            return self.seq()                # (WinLink's Clipboard page saves them instead)
        if item["kind"] == "text":
            _put(["xclip", "-selection", "clipboard", "-i", "-t", "UTF8_STRING"], item["text"].encode())
        else:
            png = self._image(item)
            if png:
                _put(["xclip", "-selection", "clipboard", "-i", "-t", "image/png"], png)
        time.sleep(0.05)              # let the owner-change event arrive, so it isn't mistaken for a new copy
        return self.seq()


class WaylandClipboard(_Base):
    def __init__(self):
        super().__init__()
        # Prints a line every time the clipboard changes. Fails at once on compositors that don't
        # let apps watch the clipboard (GNOME): then X11 is used instead.
        self.proc = subprocess.Popen(["wl-paste", "--watch", "echo", "x"], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
        time.sleep(0.4)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            raise OSError(f"this desktop doesn't let wl-paste watch the clipboard ({err[:120] or 'exited'})")

        def loop():
            for _line in self.proc.stdout:
                self._changed()
            log.warning("clipboard: wl-paste --watch stopped")
        threading.Thread(target=loop, daemon=True, name="wayland clipboard").start()

    def _get(self, t):
        return _run(["wl-paste", "--no-newline", "--type", t])

    def read(self):
        raw = _run(["wl-paste", "--list-types"])
        if not raw:
            return None
        return self._item(set(raw.decode("utf-8", "replace").split("\n")), self._get)

    def write(self, item):
        if item["kind"] == "files":          # files from another computer: Linux can't wait for the paste
            return self.seq()                # (WinLink's Clipboard page saves them instead)
        if item["kind"] == "text":
            _put(["wl-copy", "--type", "text/plain;charset=utf-8"], item["text"].encode())
        else:
            png = self._image(item)
            if png:
                _put(["wl-copy", "--type", "image/png"], png)
        time.sleep(0.05)
        return self.seq()
