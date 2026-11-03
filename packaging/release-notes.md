**Keyboard and mouse sharing works again on Mac.**

**Keyboard and mouse**
- Fixed: on a Mac, keyboard and mouse sharing stopped straight away with "deskflow-core exited with -6". Deskflow's engine inside WinLink was signed differently from its Qt parts, so macOS refused to start it. Everything inside WinLink is now signed the same way, and every Mac build checks that Deskflow's engine starts before it's released.
- After updating, macOS may ask again to allow Deskflow (and WinLink) under Privacy & Security → Accessibility. Allow it once.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | `WinLink.apk` | Open it on the phone. |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0
