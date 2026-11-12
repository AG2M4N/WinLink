**Your computer's mouse and keyboard on Android, photos on the clipboard, and two Android apps.**

**New**
- **Use the phone with your computer's mouse and keyboard** (WinLink Full). The phone becomes one more screen in *Keyboard and mouse*: drag it next to your computer's screen and move the pointer onto it. Click to tap, drag to scroll, type into any text box; Esc is Back, the Windows key is Home (Ctrl on a Mac). Setup and every key: [README](https://github.com/AG2M4N/WinLink#your-computers-mouse-and-keyboard-on-android).
- **Two Android apps. Pick one:**
  - **`WinLink-Basic.apk`**: everything except the mouse and keyboard on the phone. Never asks for accessibility access, so Play Protect doesn't warn. Take this one if you're unsure.
  - **`WinLink-Full.apk`**: adds the mouse and keyboard on the phone. It has an accessibility service, so Play Protect may warn when installing (*More details → Install anyway*). On Android 13+, allow it once: *Settings › Apps › WinLink › ⋮ › Allow restricted settings*.
  - They're the same app: install either over the other at any time and your pairings stay.

**Fixed**
- **Photos copied on Android now reach your computers.** A photo copied in Gallery or Google Photos is sent as it was taken (JPEG, full quality), not as a huge PNG that often didn't arrive. HEIC and WebP are converted.
- **Photos copied on a computer now reach Android.** A photo *file* copied in Finder, Explorer or a file manager goes onto the phone's clipboard, ready to paste (up to 30 MB). Pictures copied as images, such as screenshots, keep working, and are no longer re-encoded on the phone.

Update both the computers and the phone: older versions can't read photos sent the new way.

Everything from [2026.11.12](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.12) is included.

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
