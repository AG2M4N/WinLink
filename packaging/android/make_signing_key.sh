#!/bin/sh
# One time only: makes WinLink's Android signing key and gives it to GitHub, so the release
# workflow (.github/workflows/release.yml) can sign the Android apps (WinLink-Basic.apk, WinLink-Full.apk).
#
#   sh packaging/android/make_signing_key.sh            (needs keytool from a JDK, and gh signed in)
#
# Every future APK is signed with this key; Android only installs an update signed with the same
# key, so a backup copy is kept in ~/WinLink-android-signing. Keep that folder safe (a password
# manager or a backup drive). If it's lost, phones have to uninstall WinLink to get the next one.
set -e
REPO=${WINLINK_REPO:-AG2M4N/winlink}
OUT=${1:-$HOME/WinLink-android-signing}
for tool in keytool gh openssl base64; do
  command -v "$tool" >/dev/null || { echo "Needs $tool"; exit 1; }
done
KS="$OUT/winlink-release.jks"
if [ -e "$KS" ]; then
  echo "$KS already exists: WinLink already has a signing key. (Delete it only if you mean to start over.)"
  exit 1
fi
mkdir -p "$OUT" && chmod 700 "$OUT"
PASS=$(openssl rand -base64 30 | tr -d '/+=\n')
keytool -genkeypair -keystore "$KS" -storetype PKCS12 -alias winlink -keyalg RSA -keysize 4096 \
  -validity 10000 -storepass "$PASS" -keypass "$PASS" -dname "CN=WinLink" >/dev/null
printf '%s\n' "$PASS" > "$OUT/password.txt"
chmod 600 "$KS" "$OUT/password.txt"
base64 < "$KS" | tr -d '\n' | gh secret set ANDROID_KEYSTORE_BASE64 --repo "$REPO"
printf '%s' "$PASS" | gh secret set ANDROID_KEYSTORE_PASSWORD --repo "$REPO"
echo
echo "Done: the key is in GitHub's secrets for $REPO (ANDROID_KEYSTORE_BASE64, ANDROID_KEYSTORE_PASSWORD)."
echo "A backup is in $OUT; keep it somewhere safe."
