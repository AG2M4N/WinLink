**The tray icon on Linux.**

**Fixed**
- **Linux: the tray icon is back.** WinLink couldn't find its own icon in the .deb and .rpm packages, so it showed no tray icon (the log said *'NoneType' object has no attribute 'read'*) and the window had no icon. If the icon file is ever missing, the tray now draws a plain one instead of giving up.
- **Android setup:** the README explains two more ADB messages: *more than one device/emulator*, and *GRANT_RUNTIME_PERMISSIONS* (Xiaomi, Redmi, POCO: turn on *USB debugging (Security settings)*; OPPO, Realme, OnePlus: *Disable permission monitoring*).

Nothing changed for Windows, Mac or Android since [2026.11.14](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.14) (your computer's mouse and keyboard on Android, photos on the clipboard, and the two Android apps, **`WinLink-Basic.apk`** and **`WinLink-Full.apk`**); updating them is optional.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | `WinLink-Basic.apk` or `WinLink-Full.apk` | Open it on the phone (see above for which). |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0, and libopus (BSD-3-Clause).
