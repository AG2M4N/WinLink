#!/bin/sh
# Builds libopus (Xiph, BSD-3-Clause) from its release source into bin/, for build_mac.sh and the
# Windows build to put inside WinLink (sound over Tailscale: see src/opus.py). Needs cmake and a C
# compiler; on Windows run it from Git Bash. Linux packages use the distribution's libopus instead.
set -e
cd "$(dirname "$0")/.."
V=1.5.2
SHA256=65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1   # downloads.xiph.org/releases/opus/SHA256SUMS.txt
W=build/opus
mkdir -p "$W" bin
T="$W/opus-$V.tar.gz"
[ -f "$T" ] || curl -fsSL --retry 5 --retry-all-errors --retry-delay 3 -o "$T" "https://downloads.xiph.org/releases/opus/opus-$V.tar.gz"
if command -v sha256sum > /dev/null; then echo "$SHA256  $T" | sha256sum -c -; else echo "$SHA256  $T" | shasum -a 256 -c -; fi
rm -rf "$W/opus-$V" "$W/b"
tar -xzf "$T" -C "$W"
cmake -S "$W/opus-$V" -B "$W/b" -DCMAKE_BUILD_TYPE=Release -DOPUS_BUILD_SHARED_LIBRARY=ON \
  -DOPUS_STATIC_RUNTIME=ON -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 > /dev/null
cmake --build "$W/b" --config Release > /dev/null
case "$(uname -s)" in
  Darwin) cp -L "$W/b/libopus.0.dylib" bin/libopus.0.dylib ;;
  MINGW*|MSYS*|CYGWIN*) cp "$W/b/Release/opus.dll" bin/opus.dll ;;
  *) echo "Linux uses the distribution's libopus (libopus0 / opus)"; exit 0 ;;
esac
cp "$W/opus-$V/COPYING" bin/opus-COPYING.txt
echo "Built libopus $V into bin/"
