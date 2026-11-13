**Copies sent automatically again on Android 15 and 16.**

**Fixed**
- **Android: *Send copies automatically* works on Android 15 and 16.** WinLink noticed each copy, but Android refused to let it open its invisible clipboard screen from the background (*"Background activity launch blocked"*), so nothing was sent. Seen on OnePlus with Android 16; it affects any phone on Android 15+. WinLink now reads the clipboard from a 1-pixel invisible window shown for a moment instead, which the *display over other apps* permission you already gave allows. Nothing to set up again.

Update the Android app (**`WinLink-Basic.apk`** or **`WinLink-Full.apk`**, either installs over the one you have). Nothing changed for Windows, Mac or Linux since [2026.11.15](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.15), which fixed the tray icon on Linux.

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
