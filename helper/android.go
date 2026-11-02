//go:build linux

package main

// Running as a plain program inside the Android app: Android hides network
// interfaces, /etc/resolv.conf and the usual CA paths from apps. Tailscale's
// androidbin feature works around all three.
import _ "tailscale.com/feature/androidbin"
