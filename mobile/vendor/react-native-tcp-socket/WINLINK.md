# react-native-tcp-socket 6.4.3, with WinLink's fixes

A copy of [react-native-tcp-socket](https://github.com/Rapsssito/react-native-tcp-socket) 6.4.3 (MIT,
see LICENSE), used by the phone app through `"file:./vendor/react-native-tcp-socket"` in
`mobile/package.json`. Changed, Android only (each change is marked "WinLink" in the code):

- **Thread leak.** Every socket got two single-thread executors that were never shut down, so each
  connection left two threads behind forever. WinLink checks the built-in Tailscale's status every
  10 seconds over a new connection, so after a few hours Android refused to create more threads
  and the app crashed (`OutOfMemoryError: pthread_create failed`). The executors now end their
  thread when idle (`Threads.java`).
- Destroyed and failed sockets are removed from the module's socket map (they stayed forever).
- `end()`, `write()`, `pause()`, `resume()` and the socket options no longer throw for a socket
  that's already gone; a write to one reports an error to its callback instead.
- A socket destroyed before its `connect()` started is closed as soon as it connects.
- The network-request timeout's thread is ended.

To update the library: copy the new version here, then re-apply these changes.
