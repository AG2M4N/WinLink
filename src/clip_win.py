"""Windows clipboard: text and images, via the Win32 API (no extra packages)."""

import ctypes
import struct
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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


class Clipboard:
    def __init__(self, _window=None):
        # The clipboard needs an owner window, or SetClipboardData may fail: use a hidden,
        # message-only one. Create it on the thread that uses the clipboard.
        self.hwnd = user32.CreateWindowExW(0, "STATIC", "WinLink clipboard", 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, None, None, None)
        self.png_format = user32.RegisterClipboardFormatW("PNG")

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
            if user32.IsClipboardFormatAvailable(CF_HDROP):   # files copied in Explorer
                return None
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
            if item["kind"] == "text":
                text = item["text"].replace("\r\n", "\n").replace("\n", "\r\n")
                self._set(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
            else:
                if item.get("png"):
                    self._set(self.png_format, item["png"])
                bmp = item.get("bmp")
                if bmp and bmp[:2] == b"BM":
                    dib = bmp[14:]
                    header_size, = struct.unpack_from("<I", dib, 0)
                    self._set(CF_DIBV5 if header_size >= 108 else CF_DIB, dib)
        finally:
            user32.CloseClipboard()
        return self.seq()
