**Android: your computers can always find the phone.**

**Fixed**
- **The Android app no longer goes to sleep when no computer is connected.** Before, WinLink stopped its background service 30 seconds after the last computer disconnected, so Android froze the app and it stayed unreachable until you opened it again. Now it keeps running whenever *Stay connected* is on, and its notification says *Waiting for your computers*.
- **With the screen off, the phone looks for your computers by itself** about once a minute (Android may stretch that to every few minutes in deep sleep), so it reconnects without you touching it. For this to work in deep sleep, set WinLink to run unrestricted: *Settings › In the background › Allow* in the app.

Everything from [2026.11.11](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.11) is included.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | `WinLink.apk` | Open it on the phone. |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0, and libopus (BSD-3-Clause).
