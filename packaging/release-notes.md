**Download it and run it.** WinLink now comes as a Windows installer, Mac disk images and Linux packages, built on GitHub from this repository's source. Nothing else to install: keyboard and mouse sharing has Deskflow's engine built in on Mac and Windows.

**Keyboard and mouse**
- Works wherever WinLink connects: same Wi-Fi, the Tailscale app, WinLink's built-in Tailscale, and networks that only let one computer reach the other. Deskflow's connection travels over WinLink's encrypted link.
- Fixed "the other computer's certificate didn't match" on Windows.
- Deskflow's warnings and errors now show in WinLink's log; an installed Deskflow older than 1.27 asks to be updated instead of failing.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | from [2026.11.2](https://github.com/AG2M4N/winlink/releases/tag/v2026.11.2) | unchanged |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0
