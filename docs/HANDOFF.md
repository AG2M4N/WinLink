# Handoff: WinLink 2026.11.9 – 2026.11.11

What changed from commit `b67e49a` ("No second encryption inside the built-in Tailscale", the state this
work started from) to `v2026.11.11`, why, and how. `git log b67e49a..v2026.11.11` has every commit;
`git diff b67e49a v2026.11.11 -- <file>` shows any one change.

Three releases came out of it: [2026.11.9](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.9),
[2026.11.10](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.10) and
[2026.11.11](https://github.com/AG2M4N/WinLink/releases/tag/v2026.11.11). `main` and `test-opus` point at the same commit.

---

## 1. Android app

### 1.1 Freezing while receiving files
- **Problem:** the app froze while a big file arrived.
- **Cause:** `mobile/src/core/wire.ts` joined every incoming chunk onto one growing buffer (quadratic
  copying), and the JS thread never yielded.
- **Fix:** incoming data is kept as a queue of chunks (`ensure(n)` joins only what a read needs). The
  socket is paused above 4 MB buffered and resumed below 1 MB. `breathe()` yields to the UI, but only
  while the app is on screen (`setOnScreen()`). Large sends go frame by frame, and other sends wait for
  them (`bigSend`).

### 1.2 Staying connected in the background
- **Problem:** the app dropped offline a few minutes after leaving the screen.
- **Cause:** React Native pauses all JS timers in the background (`JavaTimerManager.onHostPause`), so
  keep-alives and retries stopped.
- **Fix:** a native 200 ms clock (`onTick` from `WinlinkNativeModule.kt`) drives JS timers.
  `mobile/src/bgtimers.ts` replaces `setTimeout`/`setInterval`, with ids starting at 2^30 and errors
  rethrown through the original timer. It is installed from `polyfills.ts`.

### 1.3 Battery
- `LinkService.kt` holds the wake lock and Wi-Fi locks only while the screen is on, or while the app is
  busy (sound playing or shared, a transfer running), via a SCREEN_ON/OFF receiver and `markBusy()`.
  The function is not called `setBusy`, which clashed with the `busy` property's setter on the JVM.
- On screen-on it sends `onScreen` so JS reconnects at once.

### 1.4 Hands-free copying ("Send copies automatically")
- **How it works:** with `READ_LOGS` (granted once over ADB) and "display over other apps",
  `ClipWatcher.kt` runs `logcat -b system -T <now> ClipboardService:E *:S`. When Android logs that it
  denied WinLink clipboard access, the watcher opens the invisible `ClipboardActivity` (`AUTO` extra,
  2 s timeout), which reads the clip and passes it to JS. `markOwnClip()` stops clips that came from a
  computer being sent back. The UI is `AutoClipCard` in `App.tsx`; the manifest declares both permissions.
- **Fix in 2026.11.10, stopping after about an hour:** on Android 13+ the system asks the user before
  every new log reader, and refuses silently if the app isn't on screen. A reader restarted from the
  background read nothing forever while looking alive. Now the reader is only (re)started while the app
  is in front (`inFront()`). A probe (`canRead()`, `logcat -d -t 5`) runs first, so the prompt is
  answered before the long-running reader starts. If the reader ends, the watcher pauses, posts a
  notification, and resumes from `OnActivityEntersForeground` or the JS status poll. If the user
  answers "Don't allow", it isn't asked again until the switch is toggled. `paused`/`refused` show in the card.
- The reader's end is logged as `clipboard watcher: the log reader ended after N min (exit code X)`.

### 1.5 Crash after a few hours (2026.11.11)
- **Symptom:** `OutOfMemoryError: pthread_create (1040KB stack) failed`, with the app at about 640 MB.
  Seen in `adb logcat -b crash -d` and `dumpsys activity exit-info`.
- **Cause:** `react-native-tcp-socket` 6.4.3 (the latest) gives each socket two
  `Executors.newSingleThreadExecutor()`s and never shuts them down, so every connection leaked two
  threads. The built-in Tailscale status check (`src/anywhere.ts`) opens a connection every 10 s, which
  is about 720 leaked threads an hour.
- **Fix:** the library is now a copy in `mobile/vendor/react-native-tcp-socket`, used through
  `"file:./vendor/react-native-tcp-socket"`, with `mobile/.npmrc` `install-links=true` so it's copied,
  not symlinked. Changes, each marked "WinLink" and listed in its `WINLINK.md`:
  - `Threads.single()` executors whose thread ends after 5 idle seconds.
  - Destroyed and failed sockets are removed from the socket map.
  - `end()`, `write()`, `pause()`, `resume()` and the socket options tolerate unknown ids.
  - A socket destroyed before it connected is closed on connect.
  - The network-request timer is shut down.
- **Updating the library later:** copy the new version over it and re-apply `WINLINK.md`'s list.

### 1.6 Phone as a trackpad
- A **Trackpad** tab (`TrackpadPage` and `Trackpad` in `App.tsx`, icon `assets/tabs/trackpad.png`)
  handles PanResponder gestures: move, tap, double-tap, two-finger tap (right-click), two-finger
  scroll, tap-and-drag, and Left/Right buttons.
- `controller.ts` coalesces moves to one message per 16 ms and sends
  `{"type":"pointer","op":"move|button|click|scroll",…}` on the control link (`node.ts` `sendPointer`).
  If the computer refuses (`op:"no"`), the reason shows as a notice.

### 1.7 Pairing
- **Problem:** a pairing request flashed up and disappeared.
- **Fix:** a Bonjour "gone" no longer deletes a peer that's mid-pairing (`node.ts`).
- The matching fixes on the computers are under 2.3.

---

## 2. Mac, Windows and Linux app (`src/`)

### 2.1 Trackpad receiver: `src/pointer.py`
- `Pointer.handle(peer, msg)` is used only while kvm conf `allow` ("Let other computers control this
  one") is on. If it can't work, it tells the phone why, once.
- **Backends:**
  - **Windows:** `user32.mouse_event`.
  - **Mac:** CoreGraphics, with `CGEventCreateScrollWheelEvent2`. It's the non-variadic one; the
    variadic call breaks on Apple Silicon. Needs Accessibility.
  - **Linux** (2026.11.10): a virtual mouse through `/dev/uinput` (`_Uinput`: REL_X/Y, hi-res wheel
    plus whole notches), which works on Wayland and X11. It falls back to XTest on X11 without uinput
    access.
- Fractions of a pixel are carried over, so slow moves still move. Scrolling follows the fingers.
- **Linux access:** the packages install `/usr/lib/udev/rules.d/70-winlink-uinput.rules` (`TAG+="uaccess"`,
  the same rule Steam uses for controllers) and `/usr/lib/modules-load.d/winlink-uinput.conf`, and
  `postinstall.sh` loads the module and triggers udev. `install_linux.sh` offers the same with sudo.
  `winlink --check` reports the trackpad and Opus.

### 2.2 Keyboard and mouse (`src/kvm.py`)
- **"Starting" forever after an update** (PermissionError on `deskflow-core.exe`): `portable_core`
  stops stray `deskflow-core` processes from its own folder (`_stop_strays`) and renames locked old
  files to `.old-*` (`_old_copies`).
- **Certificate mismatch between two computers:** detected in Deskflow's output and fixed by swapping
  fingerprints again with a `rejoin` op (`_reswap`, `RESWAP_*`). Dial-backs are limited to 6 a minute,
  and only while the server runs.
- **Windows admin windows** (`src/kvm_elevated.py`, new): with *Work over admin windows* on, WinLink
  starts itself elevated through UAC (`ShellExecuteExW` "runas") as `WinLink --kvm-helper <port>
  <token>` (routed in `link.py`). The helper connects back to 127.0.0.1 with the one-time token and can
  only start or stop `deskflow-core`; it exits when WinLink closes. This avoids Deskflow's own daemon
  and its named-pipe weakness (CVE-2026-41477). `Engine.set_elevated` and `Sharing.set_elevated` are
  exposed in `app.py`; `state()` gains `can_elevate`, `elevated` and `elevated_problem`.

### 2.3 Pairing (`src/net.py`)
- The peer clean-up skips peers that are waiting for a code or connecting.
- `_peer_for` refreshes `last_seen`, and `_ask` and `_run_control` re-register a peer that was dropped
  meanwhile.
- The decline cool-down applies only after an explicit **Decline** (`peer.last_answer`).

### 2.4 Pictures
- `net.bmp_to_png` and `net.png_to_bmp` use Pillow, and `send_clip` always includes PNG (what phones use).
- `clip_win.py` also writes a DIB made from the PNG, so Paint and Office can paste.
- `clip_mac.py` reads JPEG, HEIC or any NSImage (iPhone photos through Universal Clipboard) and sends PNG.

### 2.5 Window (`src/webui/index.html`)
- Responsive layout: icon sidebar below 760 px, bottom bar below 480 px.
- The keyboard-and-mouse arrangement scales (`putAt`).
- The KVM tile shows a short status, with notes underneath.
- The *Detailed logging* switch layout is fixed, and there's a *Work over admin windows* row.

---

## 3. Builds, tests, docs

- **`packaging/build_opus.sh`:** curl retries, plus `--ssl-revoke-best-effort` on Windows runners,
  fixing CRYPT_E_REVOCATION_OFFLINE.
- **`.github/workflows/tests.yml`:** new jobs alongside the existing ones:
  - `linux-trackpad`: `mobile/tests/harness/linux_trackpad.py` creates the real uinput device and
    reads its events back, 10 checks.
  - `phone-socket-threads`: `mobile/tests/harness/TcpThreadsTest.java` shows that 1,000 connections
    use 2,000 threads briefly, then 0, with tasks kept in order.
- **Phone suites** (`npm run test:protocol / faults / switch`) pass. On Windows, run them with
  `PYTHONUTF8=1`.
- **Docs:**
  - **README:** rewritten twice; now short, with every feature in a collapsible section and the apps
    named by system.
  - **Screenshots:** `docs/screenshots/` was re-rendered, including replicas of the Android app.
  - **Linux guide:** notes on Wayland, uinput, Opus and apt's harmless "unsandboxed" message.
- **Releases:** pushing a `v<version>` tag (matching `VERSION` in `src/net.py`) builds a draft with 8
  downloads; it was published with `gh release edit <tag> --draft=false --latest`.

## 4. Not verified, and open questions

- The real UAC prompt for *Work over admin windows*. The helper was tested end to end with a stand-in
  program.
- The Linux trackpad on a real Wayland desktop. CI proves the kernel gets the right events.
- The 2026.11.11 thread fix on the phone over a full day. Confirm with
  `adb shell dumpsys activity exit-info local.akshat.winlink` (no new `APP CRASH`) and
  `adb shell ps -T -p $(adb shell pidof local.akshat.winlink) | wc -l` (the thread count should stay
  flat, in the low hundreds).
- On Android 13+, a restarted log reader always needs the user's **Allow**. A way round it with no
  prompt is a Shizuku helper (shell-level access); this was discussed but not built.
- Keyboard and mouse sharing on Wayland depends on the desktop: GNOME and KDE Plasma 6.1+ work, Sway
  and Hyprland don't. Untested here.
