#!/bin/sh
# Show WinLink in the apps menu straight away.
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
# Phone trackpad: load uinput now (modules-load.d does it at boot) and apply its access rule.
command -v modprobe >/dev/null 2>&1 && modprobe uinput 2>/dev/null || true
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --name-match=uinput 2>/dev/null || udevadm trigger --subsystem-match=misc 2>/dev/null || true
fi
exit 0
