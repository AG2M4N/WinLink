"""
Tailscale support. Tailscale doesn't carry broadcasts, so WinLink asks
the Tailscale app for your devices and checks which of them are running WinLink.
"""

import ipaddress
import json
import os
import shutil
import subprocess

IS_WIN = os.name == "nt"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TS6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
known_ips = set()          # every address Tailscale reported for your devices
self_ips = []              # this computer's own Tailscale addresses (from the Tailscale app)


def find_cli():
    if os.environ.get("LINK_TAILSCALE_CLI"):
        return os.environ["LINK_TAILSCALE_CLI"]
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                               "Tailscale", "tailscale.exe")] if IS_WIN else \
        ["/Applications/Tailscale.app/Contents/MacOS/Tailscale",
         "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale"]
    return next((c for c in candidates if os.path.exists(c)), None)


def is_tailscale(addr):
    if addr.startswith("ts:"):          # reached through WinLink's built-in Tailscale
        return True
    host = addr.split("%", 1)[0].removeprefix("::ffff:")
    if host in known_ips:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip in _CGNAT if ip.version == 4 else ip in _TS6


def status(cli):
    """("missing" | "stopped" | "on", [(device name, [addresses])] of online devices)."""
    global self_ips
    if not cli:
        self_ips = []
        return "missing", []
    try:
        out = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=6,
                             stdin=subprocess.DEVNULL,
                             creationflags=0x08000000 if IS_WIN else 0)   # no console flash
        st = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        self_ips = []
        return "missing", []
    if st.get("BackendState") != "Running":
        self_ips = []
        return "stopped", []
    me = (st.get("Self") or {}).get("TailscaleIPs") or []
    self_ips = sorted((ip for ip in me if isinstance(ip, str)), key=lambda a: ":" in a)
    devices = []
    for peer in (st.get("Peer") or {}).values():
        ips = peer.get("TailscaleIPs") or []
        known_ips.update(ips)
        if peer.get("Online") and ips:
            ips = sorted(ips, key=lambda a: ":" in a)          # IPv4 first
            devices.append((peer.get("HostName") or ips[0], ips))
    return "on", devices
