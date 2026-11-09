**Test release: your computer's mouse and keyboard on your Android phone.**

**New**
- **Use the phone with your computer's mouse and keyboard.** The phone becomes one more screen in *Keyboard and mouse*: drag it next to your computer's screen, then move the pointer off that edge and onto the phone.
  - Click to tap, hold to long-press, drag to scroll or move things. The wheel scrolls. Right-click is a long press.
  - Typing goes into the text box you're in. Ctrl+C, V, X and A work (⌘ on a Mac). Esc is Back, the Windows key is Home (Ctrl on a Mac), Alt+Tab is Recents. The volume and media keys work too.
  - **To turn it on:** in the Android app, open the **Trackpad** tab and tap **Turn on in Accessibility**, then turn on *WinLink mouse and keyboard*. On Android 13+, if it's greyed out: App info › ⋮ › **Allow restricted settings**, then try again.
  - On Android 13+ typing works like a real keyboard. On Android 10–12, WinLink edits the text box directly, so some apps may not take arrow keys or Enter.

**Not tested on a real phone yet**: if the pointer speed, scrolling, drags or keys feel off, please open an issue.

Everything from [2026.11.12](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.12) is included.

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
