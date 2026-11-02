#!/bin/sh
# Builds WinLink.app (needs Python 3.11+, and Go for the Tailscale helper).
#   sh build_mac.sh
# Keyboard and mouse: if bin/Deskflow.app exists (the release workflow builds it from Deskflow's
# source), it goes inside WinLink.app; otherwise WinLink uses an installed Deskflow.
# Signing: set WINLINK_SIGN_IDENTITY to a codesigning identity (security find-identity -v -p codesigning)
# so macOS keeps WinLink's permissions (sound capture) across rebuilds; otherwise it's signed ad hoc.
set -e
cd "$(dirname "$0")"
L=$(pwd)
ARCH=$(uname -m); [ "$ARCH" = x86_64 ] && ARCH=amd64
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade -r requirements-mac.txt pyinstaller
[ -f "bin/linkts-mac-$ARCH" ] || (cd helper && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o "../bin/linkts-mac-$ARCH" .)
# (PyInstaller reads relative paths from --specpath, so every path here is absolute)
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name WinLink --icon "$L/assets/WinLink.icns" \
  --osx-bundle-identifier local.akshat.winlink --paths "$L/src" --add-data "$L/src/webui:webui" --collect-all webview \
  --collect-submodules zeroconf --hidden-import ScreenCaptureKit --hidden-import CoreMedia --hidden-import dispatch \
  --add-data "$L/assets/WinLink.png:." --add-binary "$L/bin/linkts-mac-$ARCH:." \
  --distpath "$L/build/dist" --workpath "$L/build/work" --specpath "$L/build" "$L/src/link.py"
P=build/dist/WinLink.app/Contents/Info.plist
plutil -insert NSLocalNetworkUsageDescription -string "WinLink finds your other computers on this Wi-Fi to share the clipboard, files and sound." "$P"
plutil -insert NSBonjourServices -json '["_winlink._tcp"]' "$P"
if [ -d "$L/bin/Deskflow.app" ]; then
  mkdir -p build/dist/WinLink.app/Contents/Library
  ditto "$L/bin/Deskflow.app" build/dist/WinLink.app/Contents/Library/Deskflow.app
  cp "$L"/bin/Deskflow-*.txt build/dist/WinLink.app/Contents/Library/ 2>/dev/null || true   # its licence and source
fi
codesign --force --deep --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" build/dist/WinLink.app
rm -rf WinLink.app && ditto build/dist/WinLink.app WinLink.app
echo "Built $L/WinLink.app"
