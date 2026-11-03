**Copy files on one computer, paste them on another.**

**Clipboard**
- Files and folders copied in Finder, Explorer or a Linux file manager now go across too, and they travel only when you paste them: copying sends just their names. On Windows, paste anywhere; on a Mac, ⌘V in a Finder window brings them into that folder (needs WinLink under Accessibility); anywhere, *Save* on the Clipboard page puts them in Downloads.
- Fixed: Deskflow's own clipboard sharing was still running alongside WinLink's, because Deskflow ignored the setting that turns it off. It's really off now.

**Keyboard and mouse**
- **Swap Ctrl and ⌘** between a Mac and a PC (on by default): ⌘C on the Mac's keyboard is Ctrl+C on the PC, and the other way round. Turn it off under *Share this Mac's keyboard and mouse*.

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
