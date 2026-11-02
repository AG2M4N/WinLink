#!/bin/sh
# A drag-to-Applications disk image:  sh packaging/mac/make_dmg.sh WinLink.app WinLink.dmg
set -e
APP=$1
OUT=$2
mkdir -p "$(dirname "$OUT")"
T=$(mktemp -d)
ditto "$APP" "$T/WinLink.app"
ln -s /Applications "$T/Applications"
# hdiutil now and then fails with "Resource busy" on build machines: try a few times
for i in 1 2 3 4 5; do
  hdiutil create -volname WinLink -srcfolder "$T" -fs HFS+ -format UDZO -ov "$OUT" && break
  [ "$i" = 5 ] && exit 1
  sleep 3
done
rm -rf "$T"
echo "Made $OUT"
