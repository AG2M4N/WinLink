<p align="center">
  <img src="assets/WinLink.png" width="96" alt="WinLink icon">
</p>

<h1 align="center">WinLink</h1>

<p align="center">
  <b>Your Mac, Windows and Linux computers and your Android phone, working as one.</b><br>
  Copy on one, paste on another · send files and folders · play sound anywhere · one keyboard and mouse for every computer · your phone as a trackpad · at home or from anywhere
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-blue"></a>
  <img alt="macOS" src="https://img.shields.io/badge/macOS-13%2B-555">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-10%20%7C%2011-555">
  <img alt="Linux" src="https://img.shields.io/badge/Linux-X11%20%7C%20Wayland-555">
  <img alt="Android" src="https://img.shields.io/badge/Android-10%2B-555">
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/devices-dark.png">
    <img src="docs/screenshots/devices-light.png" width="880" alt="WinLink's Devices page: computers and a phone connected, one asking to pair with a 4-digit code">
  </picture>
</p>

WinLink is a small free app you install on each of your devices. Once they're paired, they find each
other by themselves and stay connected: what you copy on one is ready to paste on the others, files go
where you drop them, sound plays wherever you want, and one keyboard and mouse can drive all your
computers. No accounts, no cloud storage, no IP addresses to type. Everything travels directly between
your own devices, encrypted.

---

## Contents
- [Everything WinLink does](#everything-winlink-does)
- [What's new](#whats-new)
- [Screenshots](#screenshots)
- [Download and install](#download-and-install)
- [Getting started: pair your devices](#getting-started-pair-your-devices)
- [Clipboard](#clipboard)
- [Automatic copying from an Android phone (one-time setup)](#automatic-copying-from-an-android-phone-one-time-setup)
- [Your phone as a trackpad](#your-phone-as-a-trackpad)
- [Files and folders](#files-and-folders)
- [Sound](#sound)
- [Keyboard and mouse](#keyboard-and-mouse)
- [Connect from anywhere](#connect-from-anywhere)
- [The phone in the background, and battery](#the-phone-in-the-background-and-battery)
- [The window, tray and menu bar](#the-window-tray-and-menu-bar)
- [Troubleshooting](#troubleshooting)
- [Privacy and security](#privacy-and-security)
- [For developers](#for-developers)
- [License](#license)

## Everything WinLink does

| | |
|---|---|
| 📋 **Shared clipboard** | Copy text, a picture or files on one device and paste them on the others. Keeps a history of the last 50 things you copied. |
| 📱 **Phone clipboard, hands-free** | On Android, what you copy can go to your computers automatically, with no tap (after a [one-time setup](#automatic-copying-from-an-android-phone-one-time-setup)). |
| 🍎 **iPhone, through your Mac** | Copy on your iPhone, and your Mac (with Apple's Universal Clipboard) passes it on to every other device, Windows PCs and Android phones included. |
| 🖱️ **Phone as a trackpad** | Turn your phone's screen into a touchpad for any of your computers: move, click, right-click, scroll and drag. |
| 📁 **Files and folders** | Drop them on the window, the Mac's notch, Windows' *Send to* menu or your phone's *Share* menu. Big transfers pick up where they left off if the connection drops. |
| 🔊 **Sound anywhere** | Play one computer's sound on another device, or your phone's sound on a computer. Lossless at home, compact and smooth over the internet. |
| ⌨️ **One keyboard and mouse** | Move the pointer off the edge of one screen and keep working on the next computer, Mac and PC alike. Works over Task Manager and other admin windows on Windows too. |
| 🌍 **From anywhere** | Switch on *Connect from anywhere*, sign in once, and your devices reach each other on any network: at work, on mobile data, travelling. |
| 🔒 **Private** | Pairing uses a 4-digit code you check on both screens. After that, everything is encrypted, even on your own Wi‑Fi. |
| 🪶 **Light** | Lives in the menu bar or tray. On the phone, it lets Android sleep when the screen is off and reconnects the moment you turn it on. |

## What's new

- **Your phone as a trackpad.** A new *Trackpad* tab on the phone turns its screen into a touchpad for
  any of your connected computers. [How to use it](#your-phone-as-a-trackpad)
- **Copy on Android, paste on your computer, with no tap.** An optional, one-time setup lets everything
  you copy on the phone reach your computers automatically. [Step-by-step guide](#automatic-copying-from-an-android-phone-one-time-setup)
- **Pictures go both ways with Android.** Screenshots and images copied on Windows now reach the phone,
  and pictures from the phone paste into any Windows app (Paint, Word, Outlook…).
- **iPhone photos through your Mac.** Photos copied on an iPhone (which arrive on the Mac through
  Universal Clipboard) are now passed on to your other devices, not just text.
- **Keyboard and mouse over admin windows on Windows.** Task Manager and other "Run as administrator"
  windows used to freeze the shared pointer. A new switch fixes that. [Details](#windows-task-manager-and-other-admin-windows)
- **Smoother sound over the internet.** When you're away from home, sound is compressed (about 100 kbps
  instead of about 1,500) so it keeps up on mobile data and slow connections, with the same clear quality.
- **A phone that stays connected, and saves battery.** The phone no longer drops off every few minutes in
  the background. With the screen off and nothing playing, it now lets Android sleep, and reconnects as
  soon as you wake it.
- **No more freezing while receiving big files** on the phone.
- **Pairing is more reliable.** The pairing request no longer flashes up and disappears.
- **The window fits any size.** Make it as small or as large as you like: in a narrow window the pages
  move to a bar along the bottom, and nothing gets cut off.
- **Keyboard and mouse sharing recovers by itself** after an update or when the two computers' security
  certificates get out of step. No more being stuck on "Starting".

## Screenshots

### Computer

<table>
  <tr>
    <td width="50%">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/files-dark.png"><img src="docs/screenshots/files-light.png" alt="Files: a folder sending with progress and a retry, received files"></picture>
      <p align="center"><b>Files</b>: transfers with progress, resume and cancel</p>
    </td>
    <td width="50%">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/sound-dark.png"><img src="docs/screenshots/sound-light.png" alt="Sound: share this Mac's sound, and sound from another PC playing here"></picture>
      <p align="center"><b>Sound</b>: share or play, with the real delay shown</p>
    </td>
  </tr>
  <tr>
    <td>
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/clipboard-dark.png"><img src="docs/screenshots/clipboard-light.png" alt="Clipboard history"></picture>
      <p align="center"><b>Clipboard</b>: shared automatically, with history</p>
    </td>
    <td>
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/settings-dark.png"><img src="docs/screenshots/settings-light.png" alt="Settings: notch, open at login, menu bar icon, about"></picture>
      <p align="center"><b>Settings</b>: notch, open at login, menu bar or tray</p>
    </td>
  </tr>
  <tr>
    <td>
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/activity-dark.png"><img src="docs/screenshots/activity-light.png" alt="Activity: the live log"></picture>
      <p align="center"><b>Activity</b>: what's happening, for when something doesn't work</p>
    </td>
    <td>
      <p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/small-window-dark.png"><img src="docs/screenshots/small-window-light.png" width="210" alt="WinLink in a narrow window: the pages move to a bar along the bottom"></picture></p>
      <p align="center"><b>Any size</b>: a narrow window moves the pages to the bottom</p>
    </td>
  </tr>
</table>

### Android phone

<table>
  <tr>
    <td width="33%" align="center">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/phone-devices-dark.png"><img src="docs/screenshots/phone-devices-light.png" width="240" alt="Phone: Devices tab with two computers connected"></picture>
      <p><b>Devices</b>: your computers, at home or away</p>
    </td>
    <td width="33%" align="center">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/phone-trackpad-dark.png"><img src="docs/screenshots/phone-trackpad-light.png" width="240" alt="Phone: the Trackpad tab controlling Studio PC"></picture>
      <p><b>Trackpad</b>: control a computer's pointer</p>
    </td>
    <td width="33%" align="center">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/phone-clipboard-dark.png"><img src="docs/screenshots/phone-clipboard-light.png" width="240" alt="Phone: Clipboard tab with automatic sending on, and history"></picture>
      <p><b>Clipboard</b>: copies sent automatically</p>
    </td>
  </tr>
</table>

Screenshots use sample data. The computer window looks the same on Mac, Windows and Linux.

## Download and install

Ready-made downloads are on the **[Releases](../../releases) page**.

| System | Download | Install |
|---|---|---|
| **macOS 13+** | `WinLink-mac-AppleSilicon.dmg` (M1 and newer) or `WinLink-mac-Intel.dmg` | Open it and drag **WinLink** to Applications. It isn't notarised by Apple, so the first time right-click WinLink → **Open** (on macOS 15: System Settings → Privacy & Security → **Open Anyway**). |
| **Windows 10/11** | `WinLink-Setup.exe` | Run it. It installs just for you, no administrator needed. Works on Windows on ARM too. If Windows Firewall asks, allow **Private networks**. |
| **Ubuntu, Debian, Mint, Pop!_OS** | `winlink_…_amd64.deb` (`_arm64.deb` for ARM) | `sudo apt install ./winlink_*.deb` |
| **Fedora** | `winlink-….x86_64.rpm` (`.aarch64.rpm` for ARM) | `sudo dnf install ./winlink-*.rpm` |
| **Other Linux** (Arch, openSUSE…) | the source code | `sh install_linux.sh` (see [Linux](#linux)). |
| **Android 10+** | `WinLink.apk` | Open it on the phone and allow installing from your browser when asked. |

**Updating:** install the new version over the old one. Your pairings and settings stay.

### Mac
If macOS asks to let WinLink find devices on your local network, click **Allow**. To share the Mac's
sound, macOS asks once for **Screen & System Audio Recording**; WinLink shows a button that opens the
right page. Keyboard sharing and the trackpad need WinLink allowed under **Privacy & Security →
Accessibility** (WinLink shows a button for that too).

### Windows
WinLink's window uses Microsoft Edge WebView2, which is built into Windows 10 and 11. Remove WinLink from
Settings → Apps like any other app.

### Linux
📖 **[The full Linux guide and command reference](docs/linux.md)**: desktop and headless use, every
`winlink` command, scripting, systemd, firewall and troubleshooting.

The `.deb` and `.rpm` packages include everything: the window, a mode without a window for servers
(`winlink daemon`) and the `winlink` command. On other distributions, `install_linux.sh` installs what
WinLink needs, sets it up for your user and adds it to your apps menu. Works with GNOME, KDE, Cinnamon,
Xfce and others, on X11 or Wayland.

- `winlink --check` shows what works on your PC and what to install for anything that doesn't.
- **Clipboard:** wl-clipboard on Wayland desktops that allow it (KDE, Sway, Hyprland); xclip everywhere
  else, including GNOME on Wayland.
- **Firewall:** if devices can't connect, allow TCP 50555, UDP 50556 and UDP 5353. The installer offers
  to do this for ufw and firewalld.

### Phones
**Android:** install `WinLink.apk` from the Releases page. **iPhone:** there's no ready-made app yet, but
your iPhone can still join in through your Mac: see [iPhone copies, through your Mac](#iphone-copies-through-your-mac).

## Getting started: pair your devices

1. Install and open WinLink on two devices, on the same Wi‑Fi.
2. They find each other by themselves. On one of them, click **Connect** (on the phone: tap the computer).
3. The other shows **"Wants to connect"** with a 4-digit code. Check that **both screens show the same
   code**, then click **Allow**.

That's it: from now on they reconnect by themselves whenever both are on. Pair each pair of devices once.

> **Why the code?** It's made from a secret the two devices just agreed on. If anything on the network
> tried to listen in or pretend to be one of them, the two codes would be different. So if they don't
> match, click **Decline**.

With several devices, the clipboard goes to all of them, and *Send to* / *Play on* let you pick one or all.

## Clipboard

Copy on one device, paste on the others. Text and pictures just appear; there's nothing to press.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/clipboard-dark.png"><img src="docs/screenshots/clipboard-light.png" width="720" alt="Clipboard page with history"></picture></p>

- **History:** the Clipboard page keeps the last 50 things copied on any device. Click one to copy it again.
- **Pictures:** screenshots and copied images work in every direction, phones included. On Windows they
  paste into any app (Paint, Word, Outlook, Photoshop…).
- **Files and folders** copied in Finder, Explorer or a Linux file manager work too. Only their names go
  across when you copy; the files themselves travel when you paste:
  - **Windows:** paste anywhere (Explorer, an email…). The paste waits while the files arrive, with
    progress in WinLink's Files page.
  - **Mac:** press ⌘V in a Finder window and the files come straight into that folder (WinLink needs to
    be allowed under *Privacy & Security → Accessibility*, and asks once to talk to Finder).
  - **Anywhere:** *Save* on the Clipboard page puts them in your Downloads folder.
- **Phones** don't take copied files; send them as files instead.

### From an Android phone to your computers
WinLink can't read your phone's clipboard on its own: Android only lets the app that's on screen do
that. So there are two ways:

- **One tap (no setup):** the **Send clipboard** button in the app, the **Quick Settings tile** (add
  "Send clipboard" to the panel you pull down from the top of the screen), or the button on WinLink's
  notification.
- **No tap at all:** a one-time setup, below.

### From your computers to an Android phone
Automatic. Copy on any computer and it's ready to paste on the phone, text or picture.

### iPhone copies, through your Mac
If you have a Mac and an iPhone on the same Apple ID, Apple's **Universal Clipboard** already puts what
you copy on the iPhone onto the Mac. WinLink watches the Mac's clipboard, so it **passes it on to all
your other devices**: Windows PCs, Linux and Android phones. Text and photos both work.

To make sure Universal Clipboard is on:
1. On the iPhone: **Settings → General → AirPlay & Continuity → Handoff** on.
2. On the Mac: **System Settings → General → AirDrop & Handoff → Allow Handoff between this Mac and your
   iCloud devices** on.
3. Both signed in to the same Apple ID, with Wi‑Fi and Bluetooth on.

Then copy something on the iPhone: within a few seconds it's on the Mac and on everything WinLink connects.

## Automatic copying from an Android phone (one-time setup)

<table>
  <tr>
    <td width="36%" align="center">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/phone-clipboard-setup-dark.png"><img src="docs/screenshots/phone-clipboard-setup-light.png" width="260" alt="The setup steps shown in WinLink's Clipboard tab, with the adb command"></picture>
    </td>
    <td>

**What it does:** after this setup, *everything you copy on your phone*, in any app, goes to your
computers straight away. No button, no tile, nothing to remember.

**Why it needs a setup:** Android deliberately stops apps from reading the clipboard in the background,
to protect your privacy. The only way around it that works on normal (not rooted) phones is a
permission that you, the phone's owner, give to WinLink once **from a computer**. It's the same approach
other well-known apps such as KDE Connect use.

**What you need:**
- your phone and a USB cable (or Wi‑Fi, on Android 11 and newer),
- a computer (Windows, Mac or Linux),
- about 10 minutes, once.

The app shows these same steps in **Clipboard → Send copies automatically**, with a button that copies
the command for you.

</td>
  </tr>
</table>

### Step 1: Install ADB on your computer

ADB ("Android Debug Bridge") is Google's free, official tool for talking to an Android phone from a
computer. You only use it once here.

| Computer | How to install |
|---|---|
| **Windows** | Open **Terminal** (or Command Prompt) and run `winget install Google.PlatformTools`. Or download **SDK Platform-Tools for Windows** from [Google's page](https://developer.android.com/tools/releases/platform-tools), unzip it, and open a terminal in that folder. |
| **Mac** | With [Homebrew](https://brew.sh): `brew install android-platform-tools`. Or download **SDK Platform-Tools for Mac** from [Google's page](https://developer.android.com/tools/releases/platform-tools), unzip it, and open Terminal in that folder (then type `./adb` instead of `adb` below). |
| **Linux** | `sudo apt install adb` (Ubuntu, Debian, Mint), `sudo dnf install android-tools` (Fedora), or `sudo pacman -S android-tools` (Arch). |

### Step 2: Turn on USB debugging on the phone

1. Open **Settings → About phone** and tap **Build number** seven times. You'll see *"You are now a
   developer"*. (On Samsung: **Settings → About phone → Software information → Build number**. On
   Xiaomi: **Settings → About phone → MIUI / OS version**.)
2. Go back to **Settings → System → Developer options** (on Samsung, **Developer options** is at the
   bottom of Settings) and turn on **USB debugging**.

### Step 3: Connect the phone

Plug the phone into the computer with a USB cable. Then, in the terminal, run:

```bash
adb devices
```

The phone asks **"Allow USB debugging?"**. Tick *Always allow from this computer* and tap **Allow**. Run
`adb devices` again: you should see your phone listed with the word `device` next to it.

<details>
<summary><b>No cable? Connect over Wi‑Fi instead (Android 11 and newer)</b></summary>

1. Phone and computer on the same Wi‑Fi.
2. On the phone: **Developer options → Wireless debugging** → turn it on → **Pair device with pairing code**.
3. On the computer, run `adb pair` followed by the address and port the phone shows, then type the
   6-digit code. For example: `adb pair 192.168.1.42:37199`
4. Back on the phone's *Wireless debugging* screen, note the **IP address & Port** shown at the top, and run
   `adb connect` with it. For example: `adb connect 192.168.1.42:41853`
5. `adb devices` now lists the phone.

</details>

### Step 4: Give WinLink the permission

Copy and run this one command (it's the same command the app shows):

```bash
adb shell pm grant local.akshat.winlink android.permission.READ_LOGS
```

**If it prints nothing, it worked.** That's normal: Android only prints something when there's a problem.

To double-check:
```bash
adb shell dumpsys package local.akshat.winlink | grep READ_LOGS
```
(on Windows, use `findstr READ_LOGS` instead of `grep READ_LOGS`). You should see `granted=true`.

### Step 5: Finish on the phone

1. **Close WinLink completely:** open your recent apps and swipe it away. Then open it again.
2. If Android asks **"Allow WinLink to access all device logs?"**, tap **Allow**.
3. Go to **Clipboard → Send copies automatically**, turn it on, and tap **Allow** to let WinLink
   **display over other apps** (it needs this to read each copy for a split second while you're in
   another app). Turn the switch on for WinLink in the screen that opens, then go back.
4. The card now says **"On. Copies go to your connected computers as you make them."**

**Try it:** copy some text in any app (Chrome, WhatsApp, Notes…) and paste on your computer. 🎉

You can unplug the phone and turn **USB debugging** back off now. The permission stays.

### Good to know

- **It stays set up** through phone restarts and WinLink updates. You'd only redo it after uninstalling
  WinLink, clearing its storage, or resetting the phone.
- **Android 13 and newer** may ask *"Allow WinLink to access all device logs?"* again after the phone
  restarts or WinLink is closed. Tap **Allow** whenever you see it. (If you dismiss it, automatic sending
  pauses until WinLink is reopened; the Send clipboard tile still works.)
- **Keeping WinLink running** makes this work best: turn on **Settings → Stay connected** in the app, and
  allow WinLink to run unrestricted in the background when it asks.
- **What WinLink can see:** this permission lets WinLink read Android's system log, which is broader than
  the clipboard. WinLink only looks for the one line Android writes when something is copied, never
  keeps or sends anything else, and the copied text only ever goes to your own paired devices, encrypted.
  WinLink is open source, so anyone can check.
- **Copies coming from your computers** are never sent back, so nothing bounces around.

### Turning it off

- Switch off **Clipboard → Send copies automatically**. That stops it straight away.
- To take the permission back completely, run
  `adb shell pm revoke local.akshat.winlink android.permission.READ_LOGS`, or simply uninstall WinLink.
- "Display over other apps" can be turned off in **Settings → Apps → WinLink**.

### If something doesn't work

| What you see | What to do |
|---|---|
| `adb: command not found` / *'adb' is not recognized* | ADB isn't installed, or you're not in its folder. Go back to [Step 1](#step-1-install-adb-on-your-computer). On Windows, open a **new** terminal after installing. |
| `adb devices` shows nothing, or `unauthorized` | Check the cable (some only charge), unlock the phone and tap **Allow** on the *USB debugging* prompt. Try another USB port. |
| `has not requested permission android.permission.READ_LOGS` | Your WinLink is too old. Install the latest `WinLink.apk`, then run the command again. |
| `Shell does not have permission to access user 150` (or another number) | You ran it with `--user`. That number is a protected profile such as Samsung's Secure Folder; it doesn't matter unless you use WinLink there. The normal command (without `--user`) is all you need. |
| The card says **"Waiting to start: open WinLink again."** | Close WinLink fully (swipe it away from recent apps) and open it again. |
| It worked for a while, then stopped | Open WinLink: Android may have closed it. Turn on **Stay connected**, allow it to run in the background, and tap **Allow** if Android asks about device logs. |

## Your phone as a trackpad

<table>
  <tr>
    <td width="36%" align="center">
      <picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/phone-trackpad-dark.png"><img src="docs/screenshots/phone-trackpad-light.png" width="260" alt="The Trackpad tab controlling Studio PC"></picture>
    </td>
    <td>

Open the **Trackpad** tab on the phone. If several computers are connected, pick one at the top. The
rest of the screen is now a touchpad for that computer:

| Do this | To |
|---|---|
| Slide one finger | move the pointer (a quick swipe goes further) |
| Tap | click |
| Tap twice | double-click |
| Tap with two fingers | right-click |
| Slide two fingers | scroll (the page follows your fingers) |
| Tap, then touch again and slide | drag (select text, move a window…) |

**Left** and **Right** buttons along the bottom work like a mouse's: hold **Left** and slide to drag.

**On the computer**, this uses the same switch as keyboard sharing: *Devices → Keyboard and mouse →
Let other computers control this one* (on by default). On a **Mac**, allow WinLink under **System Settings
→ Privacy & Security → Accessibility**. On **Linux**, it works on X11 desktops (Wayland doesn't allow
apps to move the pointer). If something stops it, the phone tells you why.

</td>
  </tr>
</table>

## Files and folders

- **The window:** drop files or folders on WinLink, or use *Choose files*.
- **Mac notch:** turn on *Drop files on the notch*, then drag files to the top of the screen and drop them
  on a device.
- **Windows:** right-click a file → *Send to* → *WinLink - &lt;device&gt;* (on Windows 11: *Show more options* first).
- **Linux file managers:** Files (*Scripts → WinLink*), Dolphin (*Send with WinLink*), Nemo.
- **Phone:** *Share → WinLink* from any app. Received files go to *Downloads › WinLink*.

Each transfer shows its progress, with **Cancel** on both sides (cancelling removes what already
arrived). If the connection drops, it continues where it stopped. If you get home mid-transfer, it moves
from the internet to your faster home Wi‑Fi by itself. Optional **auto-clean** moves received files to the
Trash after a number of days (only files WinLink saved, never ones you've edited or moved).

## Sound

Play any computer's sound on another device, phones included, or your Android phone's sound on a computer.

1. On the device whose sound you want to hear elsewhere: **Sound → Share sound**.
2. Choose where it plays.

The device playing it shows **Stop**, which stops both ends. A device sharing its own sound won't play
anyone else's, so sound can't echo back and forth.

- **At home:** lossless, about 20–50 milliseconds behind (fine for videos).
- **Away from home:** compressed to about a fifteenth of the size (the same codec video calls use), so it
  stays smooth on mobile data and hotel Wi‑Fi. The status shows *Opus, via Tailscale*.
- **Delay** (on the device that plays): **Auto** starts low and only adds delay if the sound breaks up.
  Or pick **Lowest** (about 20 ms), **Low** (50), **Balanced** (120) or **Smooth** (300). The window shows how
  far behind it really is.
- **Quality** (on the device that shares): Auto, Lossless, CD quality, or **Data saver** for weak connections.

## Keyboard and mouse

Use one computer's keyboard and mouse on all of them, like one big desk: move the pointer off the edge
of the screen and carry on, typing included, on the next computer.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/keyboard-mouse-dark.png"><img src="docs/screenshots/keyboard-mouse-light.png" width="680" alt="Keyboard and mouse on a PC: a Mac on the left, another PC on the right and an iMac below, with the Work over admin windows switch on"></picture></p>

1. **Mac and Windows:** nothing to install, it's built in. **Linux:** install
   [Deskflow](https://github.com/deskflow/deskflow/releases/latest) 1.27 or newer (WinLink finds it).
2. On the computer whose keyboard and mouse you want to use: **Devices → Keyboard and mouse → Share this
   computer's keyboard and mouse**.
3. **Arrange the screens** the way they stand on your desk: drag your other computers from the tray onto
   the arrangement, next to this screen or next to each other. Screens that touch are linked, and the
   pointer crosses their shared edge. A screen that doesn't touch any other is outlined in red. Drag one
   back to the tray to take it out.

Good to know:
- On a **Mac**, allow WinLink under *Privacy & Security → Accessibility* (WinLink shows a button).
- **Ctrl and ⌘ swap** between Mac and PC (on by default): ⌘C on the Mac's keyboard is Ctrl+C on the PC,
  and the other way round. Turn off *Swap Ctrl and ⌘* to keep every key as it is.
- The other computers follow by themselves. Turn off **Let other computers control this one** on a
  computer to refuse.
- It works wherever WinLink connects, at home or from anywhere, and picks the quickest way by itself.
- Each computer shows the pointer's delay and how it travels, such as *4 ms · Wi‑Fi*. If it says
  **"Tailscale, relayed"**, the two computers couldn't connect directly and go through a relay server,
  which makes the pointer lag: see [Troubleshooting](#troubleshooting).
- WinLink sets up Deskflow's security for you: only your paired computers are accepted, and you never
  have to compare fingerprints. If the two computers' certificates ever get out of step (after a
  reinstall, say), WinLink swaps them again by itself.

### Windows: Task Manager and other admin windows

Windows doesn't let ordinary apps control windows that run "as administrator", such as Task Manager,
installers and some settings. So while one of those is in front, the shared keyboard and mouse stop.

Turn on **Work over admin windows** (under *Keyboard and mouse*, on the Windows PC). Windows shows its
usual *"Do you want to allow this app to make changes?"* prompt: click **Yes**. From then on, the
keyboard and mouse keep working over admin windows.

- Windows asks each time WinLink starts it. Nothing stays installed, and nothing runs with those rights
  when WinLink isn't running.
- If you click **No**, the switch turns itself off and everything else carries on as normal.
- The sign-in screen and the UAC prompt itself stay out of reach: Windows reserves those for itself.

## Connect from anywhere

Away from home, on mobile data or a different Wi‑Fi, your devices can still reach each other.

1. Switch on **Connect from anywhere** on each device (Devices page; on the phone, *Away from home*).
2. Click **Sign in** and sign in to Tailscale in the browser (a free account is enough). Use the **same
   account on every device**. You only do this once per device.
3. Devices show *Connected · Tailscale* whenever they're online, wherever they are.

There's nothing else to install: Tailscale is built into WinLink. If you already use the Tailscale app,
WinLink finds your devices through it too. When you're back home, WinLink switches to your Wi‑Fi (faster)
without interrupting anything.

Tailscale asks you to sign in again every 180 days by default; WinLink warns you two weeks ahead (or turn
off *key expiry* for the *winlink-…* devices on Tailscale's website).

## The phone in the background, and battery

- **Stay connected** (Settings in the app): keeps receiving files, clipboard and sound with the app
  closed. A small notification shows while it's connected, with a **Send clipboard** button.
- **Allow it to run in the background** when asked (or Settings → In the background → Allow). Otherwise
  many phones pause WinLink a few minutes after you leave it.
- **Battery:** with the screen off and nothing going on, WinLink lets the phone sleep like any other
  app. Your computers will show it as offline. As soon as you turn the screen on, it reconnects. It
  only stays awake with the screen off while it's busy: sound playing or being shared, or a file moving.

## The window, tray and menu bar

- **Any size:** make the window as small or large as you like. In a medium window the sidebar shrinks to
  icons, and in a narrow one the pages move to a bar along the bottom.
- **Open at login** (Settings → Startup) starts WinLink quietly in the background.
- Its icon lives in the **menu bar** (Mac) or **tray** (Windows, Linux) and shows who's connected, with
  quick switches for sound sharing and *Connect from anywhere*. Minimising hides WinLink completely (no
  Dock or taskbar entry); the icon brings it back.
- While sound plays or a transfer runs, the computer doesn't fall asleep (the screen still turns off).
  Afterwards it sleeps as usual.

## Troubleshooting

| Problem | What to try |
|---|---|
| **Devices don't find each other** | Both on the same Wi‑Fi? Some routers keep devices apart ("guest network" or "AP isolation"): turn that off, or use *Connect from anywhere*. On Windows, allow WinLink through the firewall (Private networks). |
| **The pairing code doesn't appear, or disappears** | Update both devices to the latest version, then click **Connect** again. A request that wasn't answered no longer blocks the next one. |
| **The phone keeps going offline** | Update the app. Turn on **Stay connected** and allow it to run in the background. With the screen off and nothing playing, showing as offline is normal: it reconnects when you wake the phone. |
| **The pointer or sound lags away from home** | Look at the keyboard and mouse status. If it says **"relayed"**, Tailscale couldn't link the two devices directly: allow WinLink (and its `linkts` helper) through the firewall, or let UDP port 41642 in on your router. Phone hotspots and some mobile networks always relay. Setting sound to **Balanced** helps it stay smooth. |
| **Keyboard sharing stuck on "Starting"** | Update to the latest version (it now fixes this by itself after updates). On a Mac, check WinLink is allowed under Accessibility. |
| **"Doesn't accept keyboard and mouse from other computers"** | On that computer, turn on **Let other computers control this one**. |
| **Pointer freezes over Task Manager (Windows)** | Turn on **Work over admin windows** and click Yes on Windows' prompt. |
| **iPhone copies don't reach my PC** | Check Handoff is on on both the iPhone and the Mac, same Apple ID, Wi‑Fi and Bluetooth on, and that the Mac is connected in WinLink. |
| **Something else** | The **Activity** page shows what's happening. *Copy* puts the log on your clipboard to share in an issue. Turn on *Detailed logging* for more. |

## Privacy and security

- **Only your devices.** Devices pair once, with a 4-digit code you check on both screens. After that,
  every connection has to prove it's a device you paired before anything happens.
- **Everything is encrypted,** clipboard, files and sound, on your home Wi‑Fi too. Anything tampered
  with on the way is thrown away.
- **No cloud.** Your things go straight from one of your devices to another. When you're away from home,
  they travel through Tailscale's encrypted tunnel; Tailscale can't see them.
- **Protected from strangers.** Limits on connection attempts and pairing requests stop anyone on the
  network from flooding or guessing.
- **Keys stay safe.** Pairing keys and the Tailscale sign-in are stored with your system's own protection
  (Mac Keychain, Windows' data protection, Linux keyring, Android's keystore).
- **Optional extras are opt-in** and explained before you use them: automatic phone copying (a
  permission you grant yourself, which you can take back anytime) and *Work over admin windows*
  (Windows asks you each time).

## For developers

<details>
<summary><b>How it works</b></summary>

```text
┌───────── Mac · Windows · Linux ──────────┐               ┌───────── Android ──────────┐
│ window (pywebview, src/webui) · tray     │               │ React Native UI (Expo)     │
│ app.py, net.py: discovery, pairing,      │   TCP 50555   │ node.ts: the same protocol │
│   control link, files, clipboard         │◄─────────────►│   in TypeScript            │
│ audio.py: capture, adaptive player, Opus │  encrypted    │ Kotlin: player, capture,   │
│ kvm.py: Deskflow · pointer.py: trackpad  │               │   service, clipboard watch │
│ linkts (Go, tsnet): built-in Tailscale   │◄── tailnet ──►│ linkts for Android         │
└──────────────────────────────────────────┘               └────────────────────────────┘
```

- **Discovery:** UDP broadcast/multicast beacons (port 50556) and Bonjour (`_winlink._tcp`).
- **Connections:** one long-lived, encrypted *control* link per pair of devices (clipboard, pings,
  cancel, trackpad), plus one connection per file and per sound stream. Pairing is an X25519 key
  exchange; every connection proves the pairing key (HMAC) and is encrypted with ChaCha20-Poly1305
  (`secure.py`, `secure.ts`). Inside the built-in Tailscale tunnel, which is already encrypted end to end,
  WinLink skips its second layer of encryption.
- **Sound:** lossless PCM on the local network; Opus (libopus, 20 ms frames) when the route is remote.
  An adaptive jitter buffer holds its target delay.
- **Android background:** React Native pauses JS timers off screen, so a native 200 ms clock drives them
  (`mobile/src/bgtimers.ts`). The automatic clipboard uses `READ_LOGS` to spot the clipboard service's
  "denying clipboard access" line, then a transparent activity reads the clip (`ClipWatcher.kt`).
- **Admin windows on Windows:** `kvm_elevated.py` starts WinLink as an elevated helper through UAC; it
  connects out to WinLink with a one-time token and only runs `deskflow-core`.

</details>

<details>
<summary><b>Building from source</b></summary>

**Desktop** (Python 3.11+; Go 1.23+ for the Tailscale helper; cmake for Opus):

```sh
sh helper/build_all.sh          # Tailscale helper for every system, into bin/
sh build_mac.sh                 # Mac: WinLink.app (set WINLINK_SIGN_IDENTITY to sign with your identity)
build_windows.bat               # Windows: dist\WinLink\WinLink.exe
sh install_linux.sh             # Linux: installs for your user
```

The downloads on the Releases page are built by [`.github/workflows/release.yml`](.github/workflows/release.yml):
it compiles Deskflow's engine and libopus from source for Mac and Windows, builds WinLink for every
system, packages it ([`packaging/`](packaging)) and builds the Android APK. Push a tag `v<version>`
(matching `VERSION` in `src/net.py`) to make a draft release; run the workflow by hand on a branch to
build without releasing.

Run from source on a Mac without building:
`python3 -m venv .venv && .venv/bin/pip install -r requirements-mac.txt && .venv/bin/python src/link.py`

**Phone** (Node 22.13+ or 24, an [Expo](https://expo.dev) account for cloud builds):

```sh
cd mobile
npm install
sh ../helper/build_android.sh   # the Tailscale helper for Android
npx eas build -p android --profile preview      # a standalone APK
npx eas build -p android --profile development  # a development build, then: npx expo start --dev-client
```

**Tests** (they run the phone's TypeScript against the real desktop code, on private ports; on Windows
set `PYTHONUTF8=1`):

```sh
cd mobile
npm run test:protocol   # pairing, clipboard, files and sound both ways
npm run test:faults     # cut and silent connections during every kind of transfer
npm run test:switch     # moving from Tailscale to Wi-Fi mid-transfer and mid-song
npm run test:tailnet    # the built-in Tailscale helper
```

</details>

<details>
<summary><b>Project layout</b></summary>

```text
src/            the desktop app (Python): app.py window and state, net.py networking, audio.py + opus.py
                sound, secure.py encryption, kvm.py + kvm_elevated.py keyboard and mouse (Deskflow),
                pointer.py trackpad, cli.py + commands.py command line, clip_*.py clipboards,
                tray.py, autostart.py, keepawake.py, notch_mac.py, webui/ the window
helper/         the built-in Tailscale helper (Go, tsnet) and its build scripts
packaging/      the Windows installer, the Mac disk image, the Linux packages, the Opus build
mobile/         the phone app (Expo): src/core protocol, modules/winlink-native (Kotlin, Swift), tests/
assets/         icons
docs/           the Linux guide and screenshots
```

</details>

### Roadmap
- Phone → computer clipboard on Android without the ADB step (text-selection menu, send on open).
- iPhone app.
- Keyboard and mouse on the Windows sign-in and UAC screens.

## License
WinLink is free software under the [GNU General Public License v3.0](LICENSE).

Keyboard and mouse sharing runs [Deskflow](https://github.com/deskflow/deskflow) (GPL-2.0) as its own
program; the Mac and Windows downloads include its engine, built from Deskflow's unmodified source (its
licence and a link to that source are next to it). WinLink also builds on
[Tailscale](https://github.com/tailscale/tailscale) (BSD-3-Clause), [Opus](https://opus-codec.org)
(BSD-3-Clause), [pywebview](https://github.com/r0x0r/pywebview), [Expo](https://expo.dev) and
[React Native](https://reactnative.dev), [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf),
[sounddevice](https://github.com/spatialaudio/python-sounddevice),
[cryptography](https://github.com/pyca/cryptography) and the [noble](https://paulmillr.com/noble/) libraries.
