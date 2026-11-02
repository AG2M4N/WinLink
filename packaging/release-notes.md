**A snappier pointer, and the Android app built with every release.**

**Keyboard and mouse**
- WinLink now picks the quickest way for the pointer: straight to the other computer (same Wi-Fi or the Tailscale app), else through the built-in Tailscale's helper, else over WinLink's own link. Before, every movement went through WinLink twice.
- Each computer shows the pointer's delay and how it travels, e.g. *Connected · 4 ms · Wi-Fi* or *38 ms · Tailscale, direct*. **"Tailscale, relayed"** means Tailscale couldn't connect the two directly and uses one of its relay servers, which is what makes the pointer lag: allow WinLink's `linkts` helper through the firewall when asked (or UDP port 41642).
- The built-in Tailscale now uses a fixed port (UDP 41642) that firewalls and routers can allow, so devices connect directly more often.

**Android:** `WinLink.apk` is now built here with the other downloads, signed with WinLink's own key. **Once only:** uninstall the old WinLink from the phone before installing this one (it was signed by Expo's build service, and Android won't update across keys), then pair it again. Updates after this install over the top as usual.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | `WinLink.apk` | Open it on the phone (see above the first time). |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0
