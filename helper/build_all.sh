#!/bin/sh
# Builds the Tailscale helper (linkts) for every computer WinLink runs on, into ../bin.
# Needs Go 1.23+. The phone's copy: build_android.sh.
set -e
cd "$(dirname "$0")"
mkdir -p ../bin
for t in darwin:arm64:linkts-mac-arm64 darwin:amd64:linkts-mac-amd64 \
         linux:amd64:linkts-linux-amd64 linux:arm64:linkts-linux-arm64 \
         windows:amd64:linkts-windows-amd64.exe windows:arm64:linkts-windows-arm64.exe; do
  os=${t%%:*}; rest=${t#*:}; arch=${rest%%:*}; out=${rest#*:}
  flags="-s -w"
  [ "$os" = windows ] && flags="$flags -H windowsgui"      # no console window
  CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags "$flags" -o "../bin/$out" .
  echo "built bin/$out"
done
