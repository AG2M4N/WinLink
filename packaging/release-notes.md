**Your phone as a trackpad, hands-free copying from Android, keyboard and mouse over admin windows, and a phone that stays connected.**

Install this version on all your devices: several improvements need both ends updated.

**New**
- **Phone as a trackpad.** A new *Trackpad* tab on the phone turns its screen into a touchpad for any connected computer: move, tap to click, two-finger tap to right-click, two-finger scroll, tap-and-drag.
- **Copy on Android, paste on your computer, with no tap.** An optional one-time setup (a permission you give from a computer with ADB) sends everything you copy on the phone to your computers automatically. Step-by-step guide: [Automatic copying from an Android phone](https://github.com/AG2M4N/WinLink#automatic-copying-from-an-android-phone-one-time-setup).
- **Keyboard and mouse over Task Manager (Windows).** Turn on *Work over admin windows* and click Yes on Windows' prompt: the shared keyboard and mouse no longer freeze over Task Manager, installers and other administrator windows.
- **Pictures both ways with Android.** Screenshots and images copied on Windows now reach the phone, and pictures from the phone paste into any Windows app.
- **iPhone photos through your Mac.** Photos copied on an iPhone (arriving on the Mac through Universal Clipboard) now go on to your other devices, not just text.
- **Smoother sound away from home.** Over the internet, sound is compressed with Opus (about 100 kbps instead of about 1,500), so it keeps up on mobile data. At home it stays lossless.
- **The window fits any size.** In a narrow window the pages move to a bar along the bottom; nothing gets cut off.

**Fixed**
- The phone no longer drops offline every few minutes in the background.
- With the screen off and nothing playing, the phone now lets Android sleep (better battery), and reconnects as soon as you wake it.
- The phone no longer freezes while receiving big files.
- Pairing: the request no longer flashes up and disappears, and a missed request no longer blocks the next one for a minute.
- Keyboard and mouse sharing no longer gets stuck on "Starting" after an update, and fixes a certificate mismatch between two computers by itself.
- A computer that refuses keyboard and mouse no longer overflows its tile in the arrangement; the *Detailed logging* switch is laid out properly.

**Downloads**
| System | File | How |
|---|---|---|
| Windows 10/11 (x64, ARM) | `WinLink-Setup.exe` | Run it. Installs for your user, no administrator needed. |
| Mac, Apple Silicon | `WinLink-mac-AppleSilicon.dmg` | Drag WinLink to Applications. First time: right-click → Open (macOS 15: Privacy & Security → Open Anyway). |
| Mac, Intel | `WinLink-mac-Intel.dmg` | Same. |
| Ubuntu, Debian, Mint, Pop!_OS | `winlink_*_amd64.deb` / `_arm64.deb` | `sudo apt install ./winlink_*.deb` |
| Fedora | `winlink-*.x86_64.rpm` / `.aarch64.rpm` | `sudo dnf install ./winlink-*.rpm` |
| Android 10+ | `WinLink.apk` | Open it on the phone. |

On Linux, install Deskflow 1.27+ for keyboard and mouse sharing (see the [Linux guide](https://github.com/AG2M4N/winlink/blob/main/docs/linux.md)); Linux packages now also need libopus (installed automatically). The Mac and Windows downloads include Deskflow's engine (GPL-2.0), built from its unmodified source: https://github.com/deskflow/deskflow/tree/v1.27.0, and libopus (BSD-3-Clause).
