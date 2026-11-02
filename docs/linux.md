# WinLink on Linux

WinLink runs on Linux two ways: as a normal **desktop app** (window, tray icon, clipboard, right-click
menu), or **without a desktop** on a server or over SSH, controlled entirely with `winlink` commands.
Both talk to your other devices the same way, so a headless box can pair with your laptop and phone,
receive folders, share sound and join your tailnet.

- [Install](#install)
- [Check what works](#check-what-works)
- [Desktop use](#desktop-use)
- [Without a desktop](#without-a-desktop)
- [Command reference](#command-reference)
- [Scripting](#scripting)
- [Run at boot (systemd)](#run-at-boot-systemd)
- [Settings, files and environment](#settings-files-and-environment)
- [Firewall](#firewall)
- [Troubleshooting](#troubleshooting)
- [Update and remove](#update-and-remove)

## Install

Download **WinLink-for-Linux.zip** from the [Releases page](../../../releases) (or clone this repo and
build the helper with `sh helper/build_all.sh`), then:

```sh
unzip WinLink-for-Linux.zip -d WinLink && cd WinLink
sh install_linux.sh            # asks before installing packages with sudo
sh install_linux.sh --yes      # no questions (packages, start at login, firewall)
```

The installer:
1. installs the system packages WinLink needs, with apt, dnf, pacman or zypper (Python 3.10+, GTK and
   WebKitGTK for the window, PortAudio, `parec`, xclip / wl-clipboard, AppIndicator, secret-tool);
2. copies WinLink to `~/.local/share/winlink` with its own Python environment;
3. adds `~/.local/bin/winlink`, an apps-menu entry and (if you like) start at login;
4. offers to open WinLink's ports in ufw or firewalld;
5. runs `winlink --check`.

Supported: Ubuntu, Debian, Mint, Pop!_OS, Fedora, Arch, Manjaro, openSUSE; GNOME, KDE, Cinnamon, Xfce and
others; X11 and Wayland; x86-64 and ARM64. If `~/.local/bin` isn't on your `PATH`, add it, or call
`~/.local/bin/winlink`.

## Check what works

```sh
winlink --check
```

```text
WinLink on Ubuntu 24.04.1 LTS, Python 3.12.3

 ✓ Desktop                ubuntu:GNOME on wayland
 ✓ Window                 GTK + WebKitGTK
 ✓ Clipboard              X11 (xclip + XFixes); now holds text
 ✓ Playing sound          through pipewire
 ✓ Sharing sound          capturing what this PC plays (375 KB in 0.6 s)
 ✓ Finding devices        Bonjour + broadcast
 ✓ Port 50555             free
 ✓ Built-in Tailscale     /home/sam/.local/share/winlink/app/bin/linkts-linux-amd64
 ✓ Keyring                secret-tool: Tailscale's key goes in your keyring
 ✓ Tray icon              shown in the panel; minimising hides WinLink there
 ✓ Right-click menu       nautilus
 ✓ Opening files          xdg-open

All set.
```

Anything marked `✗` says what to install; `!` is optional.

## Desktop use

Open **WinLink** from your apps menu or run `winlink`. Everything in the main [README](../README.md#using-winlink)
applies; Linux specifics:

| | |
|---|---|
| **Clipboard** | Wayland desktops that allow clipboard watching (KDE, Sway, Hyprland) use wl-clipboard; everything else, including GNOME on Wayland, uses X11 (xclip). Images are sent as PNG, plus BMP for Windows apps if Pillow is installed. |
| **Sharing sound** | Captures what the PC plays from PulseAudio or PipeWire (`parec`, the monitor of the default output). |
| **Right-click menu** | Files: *Scripts → WinLink → device*. Dolphin: *Send with WinLink*. Nemo: *Send to device with WinLink*. Turn it off in Settings. |
| **Tray** | AppIndicator (KDE, Ubuntu, Cinnamon...) or the classic tray (Xfce, MATE). Vanilla GNOME needs the *AppIndicator and KStatusNotifierItem Support* extension. Minimising hides WinLink to the tray. |
| **Trash** | Auto-clean and *Delete* move files to the desktop's Trash (`~/.local/share/Trash`). |
| **Built-in Tailscale** | Its key is kept in your keyring through `secret-tool`, or in a private file if there's no keyring. |
| **Keyboard and mouse** | Install Deskflow from your distribution or its [releases](https://github.com/deskflow/deskflow/releases/latest) (`deskflow-core` must be on the `PATH`; the Flatpak isn't used). X11 works fully; on Wayland the desktop asks once to allow remote input. |
| **Awake** | While sound plays or a transfer runs, WinLink holds a `systemd-inhibit` lock so the PC doesn't suspend. |

## Without a desktop

On a server, a NAS, a Raspberry Pi or over SSH:

```sh
winlink daemon                         # runs in the foreground; Ctrl-C stops it
winlink daemon --downloads /srv/inbox  # where received files go (default: ~/Downloads)
winlink daemon --verbose               # debug logging
```

You rarely need to start it by hand: **any command starts WinLink in the background** if it isn't
running (as a daemon when there's no desktop, in the tray when there is). To keep it running at every
boot, see [Run at boot](#run-at-boot-systemd).

A typical first session on a server, pairing with a laptop on the same network:

```console
$ winlink devices
Starting WinLink in the background…
nas (Linux PC) · files arrive in /home/sam/Downloads

Devices:
 ○ Sam's MacBook Air        Mac         available, not paired

$ winlink pair sam
Connecting to Sam's MacBook Air…
Pairing code: 4821
Check that Sam's MacBook Air shows the same code, and press Allow there.
Connected to Sam's MacBook Air.

$ winlink send sam ~/backups/photos
photos → Sam's MacBook Air   100%  1.2 GB of 1.2 GB  48.0 MB/s
 3fa2c1  → Sam's MacBook Air    photos                              1.2 GB  done · 312/312 files
```

## Command reference

`<device>` is a device's name, or the start of it, case doesn't matter: `msi`, `"Sam's Mac"`, `pix`.
If a name matches more than one device WinLink says which, so you can be more specific.

### Devices and pairing

| Command | What it does |
|---|---|
| `winlink status` | This computer, every device, *Connect from anywhere*, sound, and recent transfers. |
| `winlink devices` | Just the devices: `●` connected, `○` not connected, `?` wants to pair (with its code), `…` connecting. |
| `winlink pair <device>` | Pair with a device WinLink can see. Prints the 4-digit code; check the other device shows the same and press **Allow** there. Waits until connected (up to ~2 min). |
| `winlink accept <device>` | Allow a device that asked to pair. Run `winlink devices` first and compare its code with the one on the other device. |
| `winlink decline <device>` | Say no to a pairing request. |
| `winlink add <address>[:port]` | Find WinLink at an address it can't discover by itself: another subnet, a VPN, a server: `winlink add 10.0.4.20`. Then `winlink pair`. |
| `winlink connect <device>` | Reconnect to a paired device you disconnected. |
| `winlink disconnect <device>` | Disconnect, and don't reconnect automatically until `winlink connect`. |
| `winlink forget <device>` | Remove the pairing. To use it again, pair again. |

### Files

| Command | What it does |
|---|---|
| `winlink send <device> <files or folders...>` | Send, with a live progress line. Folders keep their structure. Waits until done; exits 1 if anything failed. |
| `winlink send all <files...>` | Send to every connected device. |
| `winlink send <device> <files...> --no-wait` | Start sending and return at once. |
| `winlink transfers` | Recent transfers, newest first, with a short id: `3fa2c1  → Msi  report.pdf  4.2 MB  done`. |
| `winlink cancel <id>` | Cancel a transfer by its id (or the start of it); the other side deletes what already arrived. |
| `winlink cancel all` | Cancel everything in progress. |

Ctrl-C during `winlink send` stops *watching*; the transfer carries on (`winlink cancel <id>` stops it).
Transfers that drop resume where they stopped, and move from Tailscale to Wi‑Fi by themselves.

### Clipboard

| Command | What it does |
|---|---|
| `winlink clip "text"` | Put text on every connected device's clipboard. |
| `some-command \| winlink clip -` | Send standard input (keeps line breaks). |
| `winlink clip` | Print the last text copied on another device, to standard output (the sender goes to standard error). |
| `winlink set clipboard on\|off` | Share this computer's own clipboard (desktop only). |

### Sound

| Command | What it does |
|---|---|
| `winlink sound share <device>` | Play this computer's sound on a device. |
| `winlink sound stop` | Stop sharing. |
| `winlink sound stop-playing` | Stop sound another device is playing here. |
| `winlink set volume 0-100` | Volume for sound playing here. |
| `winlink set latency auto\|lowest\|low\|balanced\|smooth` | Delay for sound playing here: about 20, 50, 120 or 300 ms; Auto adapts. |

### Connect from anywhere (built-in Tailscale)

| Command | What it does |
|---|---|
| `winlink login` | Turns it on and prints a sign-in link. Open it on **any** device (your phone is fine), sign in with the same Tailscale account as your other devices; the command waits and says when it's done. |
| `winlink anywhere status` | On or off, signed in or not, this computer's Tailscale addresses. |
| `winlink anywhere on\|off` | Turn it on or off (stays signed in). |
| `winlink logout` | Sign out and erase the saved sign-in. |

### The running WinLink

| Command | What it does |
|---|---|
| `winlink show` / `winlink hide` | Open the window, or hide it in the tray (desktop only). |
| `winlink log` | The last 60 lines of the log. `winlink log -f` keeps following it. |
| `winlink quit` | Stop WinLink. |
| `winlink --check` | What works on this computer and what to install. |
| `winlink help` | A short version of this page. |

## Scripting

- **Exit codes:** `0` worked; `1` failed (the reason is on standard error, prefixed `winlink:`); `130`
  interrupted.
- **Output:** human-readable and stable enough to `grep`. `winlink clip` prints only the text, so it's
  safe in pipes: `winlink clip > note.txt`.
- **No prompts:** commands never ask questions; `pair` prints the code and waits for the other side.

```sh
# Send last night's backup to the laptop if it's around, quietly otherwise
winlink send laptop /srv/backup/latest.tar.zst || logger "winlink: laptop not reachable"

# Share a command's output with your devices' clipboards
ip -brief address | winlink clip -

# Wait until a device is connected (e.g. before a cron job sends something)
until winlink devices | grep -q "● Sam's MacBook"; do sleep 10; done
```

## Run at boot (systemd)

```sh
winlink service install                       # a systemd user service, started now and at login
winlink service install --downloads /srv/in   # with daemon options
winlink service status
winlink service remove
sudo loginctl enable-linger $USER             # on a server: keep it running when you're logged out
```

The service runs `winlink daemon` and restarts it if it crashes. Commands talk to it the same way.
Updating with `install_linux.sh` restarts it.

## Settings, files and environment

| Where | What |
|---|---|
| `~/.config/WinLink/` | Settings, pairings and the log (`winlink.log`, 1 MB × 4). Delete it to start over. |
| `~/Downloads` | Received files (change with `winlink daemon --downloads DIR`). |
| `~/.local/share/winlink/` | The installed app and its Python environment. |
| `~/.config/systemd/user/winlink.service` | The service, if installed. |

Environment variables (mostly for testing and special setups):

| Variable | Effect |
|---|---|
| `WINLINK_HEADLESS=1` | Act as if there's no desktop (start as a daemon, no window or tray). |
| `LINK_DOWNLOADS=DIR` | Where received files go (same as `--downloads`). |
| `LINK_HOME=DIR` | Use another settings folder (a second, separate WinLink). |
| `LINK_NAME="Name"` | The name other devices see (default: the computer's pretty hostname). |
| `LINK_PORT=N` | Listen on another port (default 50555). |
| `LINK_NO_CLIPBOARD=1` | Never touch this computer's clipboard. |
| `LINK_NO_BEACON=1` | Don't announce this computer or listen for others (use `winlink add`). |

## Firewall

WinLink uses **TCP 50555** (connections), **UDP 50556** (finding devices) and **UDP 5353** (Bonjour).
The installer offers to open them; by hand:

```sh
sudo ufw allow 50555/tcp && sudo ufw allow 50556/udp && sudo ufw allow 5353/udp
# or, with firewalld:
sudo firewall-cmd --permanent --add-port=50555/tcp --add-port=50556/udp --add-service=mdns && sudo firewall-cmd --reload
```

*Connect from anywhere* needs no open ports.

## Troubleshooting

| Problem | Try |
|---|---|
| Devices don't see this PC | Firewall (above). On different subnets or a VPN: `winlink add <address>`. |
| `winlink: WinLink isn't running` after `quit` | Expected; run any other command to start it again. |
| Clipboard says *off* | `winlink --check`; install xclip (X11) or wl-clipboard (Wayland). |
| No tray icon on GNOME | Install the AppIndicator extension; WinLink works fine without it. |
| Sound sharing unavailable | Install `pulseaudio-utils` (Debian, Ubuntu, Fedora) or `libpulse` (Arch). |
| Anything else | `winlink log -f` while it happens; `winlink daemon --verbose` for detail. |

## Update and remove

```sh
sh install_linux.sh              # from a newer zip: updates in place, keeps settings and pairings
sh install_linux.sh --uninstall  # removes the app, launcher, menu entries and service
rm -rf ~/.config/WinLink         # also forget settings and pairings
```
