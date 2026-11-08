# react-native-zeroconf 0.17.7, with one WinLink change

A copy of [react-native-zeroconf](https://github.com/balthazar/react-native-zeroconf) 0.17.7 (MIT,
see LICENSE), used by the phone app through `"file:vendor/react-native-zeroconf"` in
`mobile/package.json`. The Windows folder is left out (the app doesn't build for Windows).

- **Android:** `ZeroconfModule.onCatalystInstanceDestroy()` is removed (marked "WinLink"). It is
  deprecated and marked for removal in React Native, so it warned on every build; the app's React
  Native calls `invalidate()` instead, which does the same clean-up.

To update the library: copy the new version here, then re-apply this change.
