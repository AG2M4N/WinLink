"""`winlink --check` on Linux: what works on this computer and what to install for the rest."""

import os
import shutil
import socket
import subprocess
import sys
import time

OK, WARN, BAD = "✓", "!", "✗"
rows = []


def row(mark, what, detail):
    rows.append((mark, what, detail))
    print(f" {mark} {what:<22} {detail}", flush=True)


def check():
    print(f"WinLink on {platform_text()}\n")
    session = os.environ.get("XDG_SESSION_TYPE") or ("wayland" if os.environ.get("WAYLAND_DISPLAY") else
                                                     "x11" if os.environ.get("DISPLAY") else "none")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    row(OK if session != "none" else BAD, "Desktop", f"{desktop} on {session}")

    # window
    try:
        import gi
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2  # noqa: F401
        import webview  # noqa: F401
        row(OK, "Window", "GTK + WebKitGTK")
    except Exception as e:  # noqa: BLE001
        row(BAD, "Window", f"WebKitGTK missing ({e}). Install gir1.2-webkit2-4.1 / webkit2gtk4.1 / webkit2gtk-4.1")

    # clipboard
    try:
        import clip_linux
        clip = clip_linux.Clipboard()
        kind = "Wayland (wl-clipboard)" if isinstance(clip, clip_linux.WaylandClipboard) else "X11 (xclip + XFixes)"
        item = clip.read()
        row(OK, "Clipboard", f"{kind}; now holds {item['kind'] if item else 'nothing readable'}")
        try:
            import PIL  # noqa: F401
        except ImportError:
            row(WARN, "Clipboard images", "Pillow missing: images go out as PNG only (fine for Macs; some Windows apps want BMP)")
    except Exception as e:  # noqa: BLE001
        row(BAD, "Clipboard", str(e))

    # sound
    try:
        import sounddevice as sd
        out = sd.query_devices(kind="output")
        row(OK, "Playing sound", f"through {out['name']}")
    except Exception as e:  # noqa: BLE001
        row(BAD, "Playing sound", f"{e}. Install libportaudio2 / portaudio")
    if shutil.which("parec"):
        try:
            p = subprocess.Popen(["parec", "--device=@DEFAULT_MONITOR@", "--format=float32le", "--rate=48000",
                                  "--channels=2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.6)
            p.terminate()
            data, err = p.communicate(timeout=3)
            if data:
                row(OK, "Sharing sound", f"capturing what this PC plays ({len(data) // 1024} KB in 0.6 s)")
            else:
                row(BAD, "Sharing sound", f"parec captured nothing: {err.decode(errors='replace').strip()[:120]}")
        except Exception as e:  # noqa: BLE001
            row(BAD, "Sharing sound", str(e))
    else:
        row(BAD, "Sharing sound", "parec missing. Install pulseaudio-utils (Debian, Ubuntu, Fedora) or libpulse (Arch)")

    # sound over the internet
    try:
        import opus
        row(OK if opus.available() else WARN, "Sound over the internet",
            "Opus (compressed, keeps up on slow connections)" if opus.available()
            else "libopus missing: sound away from home is sent uncompressed. Install libopus0 / opus")
    except Exception as e:  # noqa: BLE001
        row(WARN, "Sound over the internet", str(e))

    # phone trackpad
    try:
        import pointer
        b = pointer._linux()
        kind = "virtual mouse (uinput: Wayland and X11)" if isinstance(b, pointer._Uinput) else "XTest (X11 only)"
        if hasattr(b, "close"):
            b.close()
        row(OK, "Phone trackpad", kind)
    except Exception as e:  # noqa: BLE001
        row(WARN, "Phone trackpad", f"{e}. The packages set this up; from source, run install_linux.sh again")

    # network
    try:
        import zeroconf  # noqa: F401
        row(OK, "Finding devices", "Bonjour + broadcast")
    except ImportError:
        row(WARN, "Finding devices", "zeroconf missing: phones won't find this PC by themselves")
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", 50555))
        row(OK, "Port 50555", "free")
    except OSError:
        row(WARN, "Port 50555", "in use (WinLink is probably already running: fine)")
    finally:
        s.close()
    if shutil.which("ufw") or shutil.which("firewall-cmd"):
        row(WARN, "Firewall", "if devices can't connect, allow TCP 50555 and UDP 50556 (see the README)")

    # Tailscale
    import tsembed
    helper = tsembed.helper_path()
    row(OK if helper and os.access(helper, os.X_OK) else BAD, "Built-in Tailscale",
        helper or "the helper isn't in this install")
    row(OK if shutil.which("secret-tool") else WARN, "Keyring",
        "secret-tool: Tailscale's key goes in your keyring" if shutil.which("secret-tool")
        else "secret-tool missing: Tailscale's key is kept in a private file instead (libsecret-tools)")

    # tray
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        row(OK, "Tray icon", "no desktop here: not needed ('winlink daemon' runs without one)")
    else:
        try:
            import tray
            why = tray.linux_tray_problem()
            import pystray  # noqa: F401
            row(OK if not why else WARN, "Tray icon", "shown in the panel; minimising hides WinLink there" if not why
                else f"{why}. WinLink works without it (minimising works as usual)")
        except ImportError:
            row(WARN, "Tray icon", "pystray missing (pip): no tray icon")
        except Exception as e:  # noqa: BLE001  (pystray's X11 backend can't reach the display)
            row(WARN, "Tray icon", f"can't show one ({e}). WinLink works without it")

    # right-click menu
    found = [n for n in ("nautilus", "dolphin", "nemo") if shutil.which(n)]
    row(OK if found else WARN, "Right-click menu", ", ".join(found) if found else
        "no supported file manager (Files, Dolphin, Nemo); drag files onto the window instead")
    row(OK if shutil.which("xdg-open") else WARN, "Opening files", "xdg-open" if shutil.which("xdg-open")
        else "xdg-open missing (xdg-utils)")

    bad = sum(1 for r in rows if r[0] == BAD)
    print("\nAll set." if not bad else f"\n{bad} thing(s) to fix above. Re-run: winlink --check")
    return 1 if bad else 0


def platform_text():
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            info = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
        name = info.get("PRETTY_NAME", "Linux").strip('"')
    except OSError:
        name = "Linux"
    return f"{name}, Python {sys.version.split()[0]}"
