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
# Opus for sound over Tailscale (built from Xiph's source if cmake is here); without it sound goes as PCM
if [ ! -f bin/libopus.0.dylib ] && command -v cmake > /dev/null; then sh packaging/build_opus.sh; fi
set --
if [ -f bin/libopus.0.dylib ]; then
  set -- --add-binary "$L/bin/libopus.0.dylib:." --add-data "$L/bin/opus-COPYING.txt:."
else
  echo "No libopus (brew install cmake, then build again): sound over Tailscale will go as PCM"
fi
# (PyInstaller reads relative paths from --specpath, so every path here is absolute)
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name WinLink --icon "$L/assets/WinLink.icns" \
  --osx-bundle-identifier local.akshat.winlink --paths "$L/src" --add-data "$L/src/webui:webui" --collect-all webview \
  --collect-submodules zeroconf --hidden-import ScreenCaptureKit --hidden-import CoreMedia --hidden-import dispatch \
  --add-data "$L/assets/WinLink.png:." --add-binary "$L/bin/linkts-mac-$ARCH:." "$@" \
  --distpath "$L/build/dist" --workpath "$L/build/work" --specpath "$L/build" "$L/src/link.py"
P=build/dist/WinLink.app/Contents/Info.plist
plutil -insert NSLocalNetworkUsageDescription -string "WinLink finds your other computers on this Wi-Fi to share the clipboard, files and sound." "$P"
plutil -insert NSBonjourServices -json '["_winlink._tcp"]' "$P"
if [ -d "$L/bin/Deskflow.app" ]; then
  mkdir -p build/dist/WinLink.app/Contents/Library
  ditto "$L/bin/Deskflow.app" build/dist/WinLink.app/Contents/Library/Deskflow.app
  cp "$L"/bin/Deskflow-*.txt build/dist/WinLink.app/Contents/Library/ 2>/dev/null || true   # its licence and source
  # Re-sign Deskflow with WinLink's identity, inside out and without the hardened runtime: macOS won't
  # load its Qt frameworks into deskflow-core if the two end up signed by different teams ("different
  # Team IDs"), and the --deep below doesn't reach into Contents/Library.
  D=build/dist/WinLink.app/Contents/Library/Deskflow.app
  find "$D/Contents/Frameworks" "$D/Contents/PlugIns" -name '*.dylib' -type f -exec \
    codesign --force --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" {} +
  for f in "$D"/Contents/Frameworks/*.framework "$D"/Contents/MacOS/*; do
    [ "$f" = "$D/Contents/MacOS/Deskflow" ] || codesign --force --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" "$f"
  done
  codesign --force --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" "$D"
  "$D/Contents/MacOS/deskflow-core" --version > /dev/null   # stop here if it still can't start
fi
codesign --force --deep --timestamp=none -s "${WINLINK_SIGN_IDENTITY:--}" build/dist/WinLink.app
rm -rf WinLink.app && ditto build/dist/WinLink.app WinLink.app
echo "Built $L/WinLink.app"
