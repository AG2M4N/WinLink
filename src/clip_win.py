"""Windows clipboard: text, images and copied files, via the Win32 API (no extra packages).

Files from another computer travel only when they're pasted: the clipboard says "files" but the list
is filled in later (delayed rendering). When Explorer (or any app) pastes, Windows asks this window
for it (WM_RENDERFORMAT), the files are fetched, and the paste carries on with them.
"""

import ctypes
import struct
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

CF_DIB, CF_UNICODETEXT, CF_HDROP, CF_DIBV5 = 8, 13, 15, 17
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
HWND_MESSAGE = wintypes.HWND(-3)
WM_RENDERFORMAT, WM_RENDERALLFORMATS, WM_DESTROYCLIPBOARD = 0x0305, 0x0306, 0x0307
PM_REMOVE = 1
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.GetClipboardOwner.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def dib_to_bmp(dib):
    """Add the 14-byte file header Windows leaves off clipboard bitmaps."""
    header_size, = struct.unpack_from("<I", dib, 0)
    bit_count, = struct.unpack_from("<H", dib, 14)
    compression, colors_used = struct.unpack_from("<I", dib, 16)[0], struct.unpack_from("<I", dib, 32)[0]
    offset = 14 + header_size
    if header_size == 40 and compression == 3:       # BI_BITFIELDS: 3 masks follow the header
        offset += 12
    elif header_size == 40 and compression == 6:     # BI_ALPHABITFIELDS: 4 masks
        offset += 16
    if bit_count <= 8:
        offset += 4 * (colors_used or (1 << bit_count))
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib


def drop_files(paths):
    """A CF_HDROP block: DROPFILES (20 bytes, wide names) then the paths, each ending in a NUL, then a NUL."""
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(paths) + "\0\0").encode("utf-16-le")


class Clipboard:
    def __init__(self, _window=None):
        # The clipboard needs an owner window, or SetClipboardData may fail, and delayed rendering
        # needs one that answers messages: a hidden, message-only window of our own. Create it on the
        # thread that uses the clipboard; pump() delivers its messages.
        self.on_render = None            # callable(offer id) -> local paths (blocks while they come) or None
        self.promise = None              # offer id of another computer's files on the clipboard
        self._proc = WNDPROC(self._wndproc)       # kept: Windows calls it
        cls = WNDCLASSW(lpfnWndProc=self._proc, hInstance=kernel32.GetModuleHandleW(None),
                        lpszClassName="WinLinkClipboard")
        user32.RegisterClassW(ctypes.byref(cls))
        self.hwnd = user32.CreateWindowExW(0, "WinLinkClipboard", "WinLink clipboard", 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, None, None, None)
        self.png_format = user32.RegisterClipboardFormatW("PNG")
        self.effect_format = user32.RegisterClipboardFormatW("Preferred DropEffect")
        self.remote_format = user32.RegisterClipboardFormatW("WinLink remote files")

    def pump(self):
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_RENDERFORMAT and wparam == CF_HDROP and self.promise and self.on_render:
                paths = self.on_render(self.promise)          # the paste waits while they come
                if paths:
                    self._set(CF_HDROP, drop_files(paths))   # (the clipboard is already open for us)
                return 0
            if msg == WM_DESTROYCLIPBOARD:
                self.promise = None
                return 0
            if msg == WM_RENDERALLFORMATS:                    # closing: files not pasted yet aren't fetched
                return 0
        except Exception:  # noqa: BLE001  (an exception can't cross back into Windows)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def remote_id(self):
        return self.promise

    def seq(self):
        return user32.GetClipboardSequenceNumber()

    def _open(self):
        for _ in range(20):                 # another app may be holding it for a moment
            if user32.OpenClipboard(self.hwnd):
                return True
            time.sleep(0.015)
        return False

    def _get(self, fmt):
        handle = user32.GetClipboardData(fmt)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, kernel32.GlobalSize(handle))
        finally:
            kernel32.GlobalUnlock(handle)

    def _set(self, fmt, data):
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)

    def read(self):
        if not self._open():
            return None
        try:
            if user32.IsClipboardFormatAvailable(CF_HDROP):   # files copied in Explorer: their paths
                if user32.IsClipboardFormatAvailable(self.remote_format) or \
                        user32.GetClipboardOwner() == self.hwnd:
                    return None                                # another computer's, put here by us
                handle = user32.GetClipboardData(CF_HDROP)
                if not handle:
                    return None
                paths = []
                for i in range(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)):
                    n = shell32.DragQueryFileW(handle, i, None, 0)
                    buf = ctypes.create_unicode_buffer(n + 1)
                    shell32.DragQueryFileW(handle, i, buf, n + 1)
                    paths.append(buf.value)
                return {"kind": "files", "paths": paths} if paths else None
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                raw = self._get(CF_UNICODETEXT)
                if raw:
                    text = raw.decode("utf-16-le", "ignore").split("\0", 1)[0]
                    if text:
                        return {"kind": "text", "text": text}
            png = self._get(self.png_format) if user32.IsClipboardFormatAvailable(self.png_format) else None
            dib = self._get(CF_DIB) if user32.IsClipboardFormatAvailable(CF_DIB) else None
            if png or dib:
                return {"kind": "image", "png": png, "bmp": dib_to_bmp(dib) if dib else None}
            return None
        finally:
            user32.CloseClipboard()

    def write(self, item):
        if not self._open():
            return self.seq()
        try:
            user32.EmptyClipboard()
            self.promise = None
            if item["kind"] == "files":
                self._set(self.effect_format, struct.pack("<I", 1))      # DROPEFFECT_COPY, never a move
                if item.get("paths"):
                    self._set(CF_HDROP, drop_files(item["paths"]))
                else:                       # another computer's: the list comes when it's pasted
                    self._set(self.remote_format, item["id"].encode("ascii"))
                    user32.SetClipboardData(CF_HDROP, None)
                    self.promise = item["id"]
            elif item["kind"] == "text":
                text = item["text"].replace("\r\n", "\n").replace("\n", "\r\n")
                self._set(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
            else:
                if item.get("png"):
                    self._set(self.png_format, item["png"])
                # Many Windows apps (Paint, Office, …) only paste the standard bitmap, and phones send
                # PNG only: make the bitmap too.
                from net import png_to_bmp
                bmp = item.get("bmp") or png_to_bmp(item.get("png"))
                if bmp and bmp[:2] == b"BM":
                    dib = bmp[14:]
                    header_size, = struct.unpack_from("<I", dib, 0)
                    self._set(CF_DIBV5 if header_size >= 108 else CF_DIB, dib)
        finally:
            user32.CloseClipboard()
        return self.seq()
