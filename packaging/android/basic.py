"""Make the Android build WinLink Basic: take the "use this phone with your computer's mouse and
keyboard" accessibility service out of the app's manifest (the part between full:start and full:end
in mobile/modules/winlink-native/android/src/main/AndroidManifest.xml). Without it the app never
asks for, or can be given, accessibility access, so Android and Play Protect don't warn about it.
Everything else is the same as WinLink Full. Run before building: python3 packaging/android/basic.py"""
import os
import re
import sys

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mobile", "modules", "winlink-native",
                    "android", "src", "main", "AndroidManifest.xml")
with open(path, encoding="utf-8") as f:
    s = f.read()
out, n = re.subn(r"[ \t]*<!-- full:start.*?<!-- full:end -->[ \t]*\n", "", s, flags=re.S)
if n != 1 or "RemoteInputService" in out or "BIND_ACCESSIBILITY_SERVICE" in out:
    sys.exit("basic.py: couldn't find exactly one full:start … full:end block")
with open(path, "w", encoding="utf-8") as f:
    f.write(out)
print("WinLink Basic: accessibility service left out")
