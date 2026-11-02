#!/bin/sh
# Builds the Tailscale helper for the Android app. It ships as a "native library" so Android
# installs it somewhere it may run from (see LinkTs.kt); it's a normal program, not a .so.
set -e
cd "$(dirname "$0")"
out=../mobile/modules/winlink-native/android/src/main/jniLibs
# Phones: a real Android build. Emulators (x86_64): Go can only build that for Android with the
# NDK, so it gets a static Linux build, which Android runs too (Tailscale's androidbin covers it).
for target in android:arm64:arm64-v8a linux:amd64:x86_64; do
  goos=${target%%:*}; rest=${target#*:}; arch=${rest%%:*}; abi=${rest#*:}
  mkdir -p "$out/$abi"
  CGO_ENABLED=0 GOOS=$goos GOARCH=$arch go build -trimpath -ldflags="-s -w" -o "$out/$abi/liblinkts.so" .
  echo "built $out/$abi/liblinkts.so"
done
