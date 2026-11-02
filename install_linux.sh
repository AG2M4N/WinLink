#!/bin/sh
# Installs WinLink for your user on Linux: Debian, Ubuntu, Mint, Pop!_OS, Fedora, Arch, Manjaro,
# openSUSE. Only the system packages need sudo (you're asked first).
#
#   sh install_linux.sh               install or update
#   sh install_linux.sh --yes         don't ask questions (installs packages, starts at login)
#   sh install_linux.sh --uninstall   remove WinLink (your pairings and settings are kept)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
APP=$DATA/winlink
BIN=$HOME/.local/bin
AUTOSTART=${XDG_CONFIG_HOME:-$HOME/.config}/autostart/winlink.desktop
YES=0
[ "${1:-}" = "--yes" ] && YES=1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ask() {   # ask "question" -> 0 for yes
  [ "$YES" = 1 ] && return 0
  printf '%s [Y/n] ' "$1"
  read -r a || a=y
  case "$a" in [nN]*) return 1 ;; *) return 0 ;; esac
}

if [ "${1:-}" = "--uninstall" ]; then
  if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/winlink.service" ]; then
    systemctl --user disable --now winlink.service 2>/dev/null || true
    rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/winlink.service"
  fi
  pkill -f "$APP/app/src/link.py" 2>/dev/null || true
  rm -rf "$APP" "$BIN/winlink" "$DATA/applications/winlink.desktop" "$AUTOSTART" \
         "$DATA/icons/hicolor/256x256/apps/winlink.png" "$DATA/nautilus/scripts/WinLink" \
         "$DATA/kio/servicemenus/winlink.desktop"
  rm -f "$DATA"/nemo/actions/winlink-*.nemo_action
  echo "WinLink is removed. Your settings and pairings are still in ~/.config/WinLink (delete it to forget them)."
  exit 0
fi

# ---------------------------------------------------------------- system packages
say "System packages"
if command -v apt-get >/dev/null 2>&1; then
  WEBKIT=gir1.2-webkit2-4.1
  apt-cache show "$WEBKIT" >/dev/null 2>&1 || WEBKIT=gir1.2-webkit2-4.0
  PKGS="python3 python3-venv python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0 $WEBKIT libportaudio2 \
pulseaudio-utils xclip wl-clipboard libsecret-tools xdg-utils libnotify-bin libxfixes3"
  apt-cache show gir1.2-ayatanaappindicator3-0.1 >/dev/null 2>&1 && PKGS="$PKGS gir1.2-ayatanaappindicator3-0.1"
  INSTALL="sudo apt-get install -y"
elif command -v dnf >/dev/null 2>&1; then
  PKGS="python3 python3-gobject gtk3 webkit2gtk4.1 portaudio pulseaudio-utils xclip wl-clipboard libsecret \
xdg-utils libnotify libXfixes libayatana-appindicator-gtk3"
  INSTALL="sudo dnf install -y"
elif command -v pacman >/dev/null 2>&1; then
  PKGS="python python-gobject gtk3 webkit2gtk-4.1 portaudio libpulse xclip wl-clipboard libsecret xdg-utils \
libnotify libxfixes libayatana-appindicator"
  INSTALL="sudo pacman -S --needed --noconfirm"
elif command -v zypper >/dev/null 2>&1; then
  PKGS="python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1 portaudio \
pulseaudio-utils xclip wl-clipboard libsecret-tools xdg-utils libnotify-tools libXfixes3 \
typelib-1_0-AyatanaAppIndicator3-0_1"
  INSTALL="sudo zypper install -y"
else
  PKGS=""
  INSTALL=""
  echo "Couldn't tell which package manager this is. Install these yourself, then run this again:"
  echo "  Python 3.10+ with venv, PyGObject, GTK 3, WebKitGTK (webkit2 4.1 or 4.0), PortAudio,"
  echo "  parec (PulseAudio utils), xclip, wl-clipboard, secret-tool, xdg-utils, libXfixes."
fi
if [ -n "$INSTALL" ]; then
  echo "WinLink needs: $PKGS"
  if ask "Install them now with sudo (it asks for your password)?"; then
    # shellcheck disable=SC2086
    $INSTALL $PKGS || echo "Some packages didn't install; 'winlink --check' at the end shows what's missing."
  else
    echo "Skipped. 'winlink --check' at the end shows what's missing."
  fi
fi

PY=$(command -v python3 || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "WinLink needs Python 3.10 or newer (found: $($PY --version 2>&1 || echo none))."
  exit 1
fi

# ---------------------------------------------------------------- the app
say "Installing WinLink in $APP"
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) ARCH="" ; echo "No built-in Tailscale for $(uname -m); everything else works." ;;
esac
pkill -f "$APP/app/src/link.py" 2>/dev/null || true          # updating: stop the old one
rm -rf "$APP/app"
mkdir -p "$APP/app/bin" "$BIN" "$DATA/applications" "$DATA/icons/hicolor/256x256/apps"
cp -R "$HERE/src" "$HERE/assets" "$APP/app/"
rm -f "$APP/app/src/clip_mac.py" "$APP/app/src/clip_win.py" "$APP/app/src/notch_mac.py"
if [ -n "$ARCH" ] && [ -f "$HERE/bin/linkts-linux-$ARCH" ]; then
  cp "$HERE/bin/linkts-linux-$ARCH" "$APP/app/bin/"
  chmod 755 "$APP/app/bin/linkts-linux-$ARCH"
fi

# A private Python environment. It sees the system's PyGObject (GTK and WebKit come from the
# distribution: pip can't install those).
if [ ! -x "$APP/venv/bin/python" ]; then
  "$PY" -m venv --system-site-packages "$APP/venv"
fi
"$APP/venv/bin/python" -m pip install --quiet --upgrade pip
"$APP/venv/bin/python" -m pip install --quiet -r "$HERE/requirements-linux.txt"

cat > "$BIN/winlink" <<EOF
#!/bin/sh
exec "$APP/venv/bin/python" "$APP/app/src/link.py" "\$@"
EOF
chmod 755 "$BIN/winlink"

cp "$HERE/assets/WinLink.png" "$DATA/icons/hicolor/256x256/apps/winlink.png"
DESKTOP="[Desktop Entry]
Type=Application
Name=WinLink
GenericName=Clipboard, files and sound between your devices
Comment=Share the clipboard, send files and play sound between your Macs, PCs and phones
Exec=$BIN/winlink
Icon=winlink
Terminal=false
Categories=Network;Utility;
Keywords=clipboard;files;sound;share;tailscale;"
printf '%s\n' "$DESKTOP" > "$DATA/applications/winlink.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true

if ask "Start WinLink when you log in (in the tray)?"; then
  mkdir -p "$(dirname "$AUTOSTART")"
  printf '%s\nX-GNOME-Autostart-enabled=true\n' "$DESKTOP" | sed "s|^Exec=.*|Exec=$BIN/winlink --hidden|" > "$AUTOSTART"
else
  rm -f "$AUTOSTART"
fi

# ---------------------------------------------------------------- firewall
# Devices connect on TCP 50555; they find each other on UDP 50556 and Bonjour (UDP 5353).
if command -v ufw >/dev/null 2>&1 && sudo -n true 2>/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  if ask "The firewall (ufw) is on. Let WinLink's connections through?"; then
    sudo ufw allow 50555/tcp && sudo ufw allow 50556/udp && sudo ufw allow 5353/udp
  fi
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  if ask "The firewall (firewalld) is on. Let WinLink's connections through?"; then
    sudo firewall-cmd --permanent --add-port=50555/tcp --add-port=50556/udp --add-service=mdns && sudo firewall-cmd --reload
  fi
elif command -v ufw >/dev/null 2>&1; then
  echo "If you use ufw and devices can't connect:  sudo ufw allow 50555/tcp && sudo ufw allow 50556/udp && sudo ufw allow 5353/udp"
fi

# The background service (winlink service install) was stopped for the update: start the new one.
if systemctl --user is-enabled winlink.service >/dev/null 2>&1; then
  systemctl --user restart winlink.service && echo "Restarted the WinLink service."
fi

# ---------------------------------------------------------------- check
say "Checking"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "(Add $BIN to your PATH to run 'winlink' from a terminal.)" ;; esac
"$BIN/winlink" --check || true
say "Done"
echo "Open WinLink from your apps menu, or run: $BIN/winlink"
echo "No desktop (a server)? 'winlink daemon' runs it without a window, 'winlink service install' at every boot;"
echo "'winlink help' lists the commands (pair, send, clip, login...)."
if ask "Open WinLink now?"; then
  nohup "$BIN/winlink" >/dev/null 2>&1 &
fi
