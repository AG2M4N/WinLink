**Automatic copying on Android keeps working, and the phone trackpad now works on Linux with Wayland.**

**Fixed**
- **Android: automatic copying no longer stops after a while.** Before, after an hour or so it could go quiet with no sign, and only turning the switch off and on brought it back. Now, if Android stops it, a notification says so, and simply opening WinLink starts it again. If Android asks *"Allow WinLink to access all device logs?"*, tap **Allow**.
- **Linux: the phone trackpad works on Wayland** (the default on Ubuntu, Fedora and most current desktops), not only on X11. After installing, log out and back in once. `winlink --check` now shows whether the trackpad and compressed sound work.

Everything from [2026.11.9](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.9) is included: the phone trackpad, hands-free copying from Android, keyboard and mouse over admin windows on Windows, pictures both ways with Android, and smoother sound away from home.

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
