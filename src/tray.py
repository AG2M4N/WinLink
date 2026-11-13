"""
WinLink's icon in the notification area (Windows), the panel / top bar tray (Linux) and the menu
bar (Mac). The menu: open the window, who's connected, share this computer's sound, connect from
anywhere, quit. On Windows and Linux, minimising the window hides it here.

The app supplies `menu()` -> [(label, action, checked)] (checked None = a plain item, action None
= a line of text) and `open_window()`, `quit()`. Everything runs on the GUI's own thread.
"""

import logging
import os
import sys
import threading

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def create(app, icon_path):
    """A started tray for this system, or (None, why it isn't available)."""
    try:
        if IS_MAC:
            return MacMenuBar(app), ""
        if not IS_WIN:
            why = linux_tray_problem()
            if why:
                return None, why
        return PystrayIcon(app, icon_path), ""
    except Exception as e:  # noqa: BLE001  (missing library, no tray on this desktop...)
        log.warning("tray: %s", e)
        return None, str(e)


# ---------------------------------------------------------------- Linux: is there a tray at all?
def linux_tray_problem():
    """"" if this desktop shows tray icons, else why not (GNOME hides them without an extension)."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "no desktop session"
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        has = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except Exception:  # noqa: BLE001
        has = False
    if has and _appindicator():
        os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")
        return ""
    if has:
        return ("the AppIndicator library is missing (gir1.2-ayatanaappindicator3-0.1 / "
                "libayatana-appindicator-gtk3)")
    if os.environ.get("DISPLAY") and _xembed_tray():
        os.environ.setdefault("PYSTRAY_BACKEND", "gtk")        # Xfce, MATE, LXDE...: the classic tray
        return ""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "GNOME" in desktop.upper():
        return "GNOME shows tray icons only with the \"AppIndicator and KStatusNotifierItem Support\" extension"
    return "this desktop has no tray"


def _appindicator():
    try:
        import gi
        for name in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(name, "0.1")
                return True
            except ValueError:
                continue
    except ImportError:
        pass
    return False


def _xembed_tray():
    try:
        import ctypes
        import ctypes.util
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XGetSelectionOwner.restype = ctypes.c_ulong
        x11.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            return x11.XGetSelectionOwner(dpy, x11.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_S0", 0)) != 0
        finally:
            x11.XCloseDisplay(dpy)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- Windows and Linux
class PystrayIcon:
    def __init__(self, app, icon_path):
        import pystray
        from PIL import Image
        self.app = app
        if icon_path:
            image = Image.open(icon_path).convert("RGBA")
            image.thumbnail((64, 64))
        else:                                       # no icon file: a plain blue dot rather than no tray
            from PIL import ImageDraw
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=(37, 99, 235, 255))

        def entry(i):
            def text(_item):
                items = app.menu()
                return items[i][0] if i < len(items) else ""

            def visible(_item):
                return i < len(app.menu())

            def enabled(_item):
                items = app.menu()
                return i < len(items) and items[i][1] is not None

            def checked(_item):
                items = app.menu()
                return bool(i < len(items) and items[i][2])

            def run(_icon, _item):
                items = app.menu()
                if i < len(items) and items[i][1] is not None:
                    threading.Thread(target=items[i][1], daemon=True).start()
            return pystray.MenuItem(text, run, visible=visible, enabled=enabled, checked=checked,
                                    default=(i == 0))
        # A fixed set of slots whose text, tick and visibility follow app.menu() each time it opens.
        items = [entry(i) for i in range(12)]
        menu = pystray.Menu(*items[:1], pystray.Menu.SEPARATOR, *items[1:],
                            pystray.Menu.SEPARATOR,
                            pystray.MenuItem("Quit WinLink", lambda *_: app.quit()))
        self.icon = pystray.Icon("winlink", image, "WinLink", menu)
        if IS_WIN:
            threading.Thread(target=self.icon.run, daemon=True, name="tray").start()
        else:
            # GTK and AppIndicator share pywebview's GTK main loop: set up on that thread.
            from gi.repository import GLib
            GLib.idle_add(lambda: (self.icon.run_detached(), False)[1])

    def refresh(self):
        try:
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- Mac
_TARGET = None


def _mac_target_class():
    """Objective-C classes can only be defined once per process."""
    global _TARGET
    if _TARGET is None:
        from AppKit import NSObject

        class WinLinkMenuBarTarget(NSObject):
            owner = None

            def menuNeedsUpdate_(self, menu):           # rebuilt each time it opens: always current
                if self.owner is not None:
                    self.owner.fill(menu)

            def act_(self, sender):
                if self.owner is not None:
                    self.owner.act(int(sender.tag()))
        _TARGET = WinLinkMenuBarTarget
    return _TARGET


class MacMenuBar:
    def __init__(self, app):
        from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
        from PyObjCTools import AppHelper
        self.app = app

        self.target = _mac_target_class().alloc().init()
        self.target.owner = self
        self.NSMenuItem = NSMenuItem
        self.actions = []

        def build():
            from AppKit import NSImage
            self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
            image = None
            try:
                image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("link", "WinLink")
            except Exception:  # noqa: BLE001  (macOS before 11)
                pass
            button = self.item.button()
            if image is not None:
                image.setTemplate_(True)                   # follows light / dark menu bars
                button.setImage_(image)
            else:
                button.setTitle_("WinLink")
            button.setToolTip_("WinLink")
            self.menu = NSMenu.alloc().init()
            self.menu.setDelegate_(self.target)
            self.menu.setAutoenablesItems_(False)
            self.item.setMenu_(self.menu)
            self.fill(self.menu)
        AppHelper.callAfter(build)

    def fill(self, menu):
        menu.removeAllItems()
        self.actions = []
        entries = self.app.menu() + [None, ("Quit WinLink", self.app.quit, None)]
        for entry in entries:
            if entry is None:
                menu.addItem_(self.NSMenuItem.separatorItem())
                continue
            label, action, checked = entry
            item = self.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "act:" if action else None, "")
            if action:
                item.setTarget_(self.target)
                item.setTag_(len(self.actions))
                self.actions.append(action)
            else:
                item.setEnabled_(False)
            if checked is not None:
                item.setState_(1 if checked else 0)
            menu.addItem_(item)
            if len(self.actions) == 1 and action:          # a line under "Open WinLink"
                menu.addItem_(self.NSMenuItem.separatorItem())

    def act(self, tag):
        if 0 <= tag < len(self.actions):
            threading.Thread(target=self.actions[tag], daemon=True).start()

    def refresh(self):
        pass                                               # the menu is rebuilt whenever it opens

    def stop(self):
        try:
            from AppKit import NSStatusBar
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda: NSStatusBar.systemStatusBar().removeStatusItem_(self.item))
        except Exception:  # noqa: BLE001
            pass
