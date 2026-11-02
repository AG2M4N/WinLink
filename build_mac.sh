#!/bin/sh
# Builds WinLink.app (needs Python 3.11+, and Go for the Tailscale helper).
#   sh build_mac.sh
# Signing: set WINLINK_SIGN_IDENTITY to a codesigning identity (security find-identity -v -p codesigning)
# so macOS keeps WinLink's permissions (sound capture) across rebuilds; otherwise it's signed ad hoc.
set -e
cd "$(dirname "$0")"
L=$(pwd)
ARCH=$(uname -m); [ "$ARCH" = x86_64 ] && ARCH=amd64
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade -r requirements-mac.txt pyinstaller
[ -f "bin/linkts-mac-$ARCH" ] || (cd helper && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o "../bin/linkts-mac-$ARCH" .)
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name WinLink --icon assets/WinLink.icns \
  --osx-bundle-identifier local.akshat.winlink --paths src --add-data "src/webui:webui" --collect-all webview \
  --collect-submodules zeroconf --hidden-import ScreenCaptureKit --hidden-import CoreMedia --hidden-import dispatch \
  --add-data "assets/WinLink.png:." --add-binary "bin/linkts-mac-$ARCH:." \
  --distpath build/dist --workpath build/work --specpath build src/link.py
P=build/dist/WinLink.app/Contents/Info.plist
plutil -insert NSLocalNetworkUsageDescription -string "WinLink finds your other computers on this Wi-Fi to share the clipboard, files and sound." "$P"
plutil -insert NSBonjourServices -json '["_winlink._tcp"]' "$P"
codesign --force --deep --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" build/dist/WinLink.app
rm -rf WinLink.app && ditto build/dist/WinLink.app WinLink.app
echo "Built $L/WinLink.app"
