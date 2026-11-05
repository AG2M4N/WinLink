**A much more reliable Android app.**

**Fixed**
- **The Android app no longer crashes after a few hours.** Every connection left a little behind inside the app, and with *Connect from anywhere* on (which checks in every few seconds) that added up until Android closed WinLink. This also paused hands-free copying from the phone.
- **Hands-free copying recovers by itself.** If Android pauses it, a notification says so, and opening WinLink starts it again. If Android asks *"Allow WinLink to access all device logs?"*, tap **Allow**.
- **Linux: the phone trackpad works on Wayland** (the default on Ubuntu and Fedora), not only X11. Log out and back in once after installing.

**Also**
- A shorter README, with every feature in a section you can open.
- Linux: apt's *"Download is performed unsandboxed as root"* note is explained (it's harmless).

Everything from [2026.11.9](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.9) is included: the phone trackpad, hands-free copying from Android, keyboard and mouse over admin windows on Windows, pictures both ways with the Android app, and smoother sound away from home.

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
