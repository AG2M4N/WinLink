#!/usr/bin/env python3
"""WinLink: shared clipboard, files and sound between your Macs and PCs, on Wi-Fi or anywhere."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
if os.path.isdir(_VENDOR):                 # the Linux packages: pure-Python libraries shipped with WinLink
    sys.path.insert(1, os.path.normpath(_VENDOR))

import net  # noqa: E402
import sendto  # noqa: E402


def capture_test(seconds=4.0):
    """`--capture-test`: capture this computer's sound for a few seconds, write what arrived to
    capture-test.json in the settings folder, and quit (diagnosing "nothing plays on the other side")."""
    import json
    import time
    import audio
    out = {"backend": None, "chunks": 0, "bytes": 0, "peak": 0.0, "error": "", "detail": ""}
    backend = audio.capture_backend()
    out["backend"] = backend.__name__ if backend else None
    if backend is not None:
        import numpy as np
        cap = backend()

        def on_pcm(pcm):
            out["chunks"] += 1
            out["bytes"] += len(pcm)
            a = np.frombuffer(pcm[:len(pcm) - len(pcm) % 4], "<f4")
            if a.size:
                out["peak"] = max(out["peak"], float(np.abs(a).max()))
        try:
            cap.start(on_pcm)
            time.sleep(seconds)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"
        finally:
            out["detail"] = cap.describe() if hasattr(cap, "describe") else ""
            try:
                cap.stop()
            except Exception:  # noqa: BLE001
                pass
    with open(os.path.join(net.config_dir(), "capture-test.json"), "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(json.dumps(out))


def main():
    args = sys.argv[1:]
    pending = None
    if "--capture-test" in args:
        return capture_test()
    if "--check" in args and not net.IS_MAC and not net.IS_WIN:
        import check_linux
        sys.exit(check_linux.check())
    import cli
    if args and args[0] in cli.COMMANDS - {"daemon"}:     # winlink status / send / pair ...
        sys.exit(cli.main(args))
    if args[:1] == ["help"] or "--help" in args or "-h" in args:
        sys.exit(cli.main(["help"]))
    if args[:1] == ["daemon"] or (not args and not cli.has_desktop()):
        return daemon(args[1:])
    if args and not args[0].startswith("-"):
        sys.exit(f"winlink: unknown command '{args[0]}' (see: winlink help)")
    if "--send" in args:                     # from a "Send to" shortcut: --send <device id> <paths...>
        i = args.index("--send")
        target, paths = (args[i + 1] if i + 1 < len(args) else ""), args[i + 2:]
        paths = [os.path.abspath(p) for p in paths]   # the running WinLink has another current folder
        result = sendto.hand_off(net.config_dir(), {"send": target, "paths": paths})
        if result is not None:
            ok, msg = result
            if not ok:
                sendto.message_box(msg or "Couldn't send.")
            return
        pending = (target, paths)            # WinLink isn't running: become it, then send
    elif "--hidden" in args:                 # opened at login
        if sendto.hand_off(net.config_dir(), {"cmd": "ping", "args": []}, timeout=3) is not None:
            return                           # already running: leave it as it is
    elif sendto.hand_off(net.config_dir(), {"show": True}, timeout=5) is not None:
        return                               # already running: its window was brought to the front

    try:
        from app import App
        App(pending, hidden="--hidden" in args).run()
    except Exception as e:  # noqa: BLE001
        if net.IS_MAC or net.IS_WIN:
            raise
        net.log.exception("WinLink couldn't start")      # Linux: usually a missing system package
        sendto.message_box(f"WinLink couldn't start: {e}\n\nRun 'winlink --check' in a terminal to see what's missing.")
        sys.exit(1)


def daemon(args):
    """WinLink without a window, controlled with `winlink <command>`."""
    if "--downloads" in args:
        i = args.index("--downloads")
        if i + 1 >= len(args):
            sys.exit("winlink: --downloads needs a folder")
        os.environ["LINK_DOWNLOADS"] = os.path.abspath(os.path.expanduser(args[i + 1]))
    if sendto.hand_off(net.config_dir(), {"cmd": "ping", "args": []}, timeout=3) is not None:
        sys.exit("winlink: WinLink is already running (see: winlink status, or stop it with: winlink quit)")
    import logging
    net.setup_logging()
    if "--verbose" in args:
        net.log.setLevel(logging.DEBUG)
    try:                                   # `kill -USR1 <pid>` prints what every thread is doing
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (ImportError, AttributeError, ValueError):
        pass
    console = logging.StreamHandler(sys.stderr)          # also show the log in the terminal
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    net.log.addHandler(console)
    from app import App
    print("WinLink is running without a window. In another terminal: winlink help", file=sys.stderr)
    App(headless=True).run_headless()


if __name__ == "__main__":
    main()
