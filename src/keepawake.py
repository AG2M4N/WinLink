"""
Keep WinLink responsive when its window is hidden, and the computer awake while it's busy.

  always:        Mac: no App Nap (macOS slows hidden apps' timers and network, so sound stutters).
                 Windows: no power throttling ("efficiency mode" for background apps).
  while busy:    the computer doesn't go to sleep on its own while sound is shared or playing, or a
                 transfer is running. The screen still turns off as usual.
                 Mac: an IdleSystemSleepDisabled activity. Windows: SetThreadExecutionState.
                 Linux: `systemd-inhibit --what=idle:sleep` (logind), if it's there.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading

log = logging.getLogger("winlink")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


class KeepAwake:
    def __init__(self):
        self.busy = False
        self._mac_always = None
        self._mac_busy = None
        self._linux = None
        self._win_queue = None
        self._always()

    # ---- always
    def _always(self):
        try:
            if IS_MAC:
                from Foundation import NSProcessInfo
                # NSActivityUserInitiatedAllowingIdleSystemSleep: no App Nap, but the Mac may still sleep
                self._mac_always = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                    0x00FFFFFF & ~(1 << 20), "WinLink keeps your devices connected")
            elif IS_WIN:
                self._windows_no_throttling()
                self._win_queue = _WindowsAwake()
        except Exception as e:  # noqa: BLE001
            log.debug("keep awake: %s", e)

    @staticmethod
    def _windows_no_throttling():
        import ctypes
        from ctypes import wintypes

        class State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        # PROCESS_POWER_THROTTLING_EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, both turned off
        st = State(1, 0x1 | 0x4, 0)
        k32 = ctypes.windll.kernel32
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))  # ProcessPowerThrottling

    # ---- while busy
    def set_busy(self, busy, why=""):
        if busy == self.busy:
            return
        self.busy = busy
        try:
            if IS_MAC:
                from Foundation import NSProcessInfo
                if busy:
                    # NSActivityUserInitiated: includes NSActivityIdleSystemSleepDisabled
                    self._mac_busy = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                        0x00FFFFFF | (1 << 20), why or "WinLink is sending or playing sound")
                elif self._mac_busy is not None:
                    NSProcessInfo.processInfo().endActivity_(self._mac_busy)
                    self._mac_busy = None
            elif IS_WIN:
                if self._win_queue is not None:
                    self._win_queue.set(busy)
            else:
                self._linux_busy(busy, why)
        except Exception as e:  # noqa: BLE001
            log.debug("keep awake: %s", e)
        log.info("keeping the computer awake: %s", why if busy else "no longer needed")

    def _linux_busy(self, busy, why):
        if busy:
            tool = shutil.which("systemd-inhibit")
            if tool and self._linux is None:
                self._linux = subprocess.Popen(
                    [tool, "--what=idle:sleep", "--who=WinLink", f"--why={why or 'Sharing sound or files'}",
                     "--mode=block", "sleep", "infinity"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self._linux is not None:
            self._linux.terminate()
            self._linux = None


class _WindowsAwake:
    """SetThreadExecutionState applies to the calling thread until it changes it or exits, so one
    thread of its own holds it."""

    def __init__(self):
        self.want = threading.Event()
        self.changed = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="keep awake").start()

    def set(self, busy):
        if busy:
            self.want.set()
        else:
            self.want.clear()
        self.changed.set()

    def _run(self):
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        k32 = ctypes.windll.kernel32
        while True:
            self.changed.wait()
            self.changed.clear()
            k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if self.want.is_set() else 0))
