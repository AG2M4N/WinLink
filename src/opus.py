"""
Opus, for sound over Tailscale: about 100 kbps instead of CD quality's 1.5 Mbps, and it sounds
nearly the same. Uses the libopus that comes with WinLink (Windows, Mac) or the system's (Linux),
through ctypes. When there's none, available() is False and sound goes as PCM like before.

On the wire (after the audio handshake agreed on "opus"): one packet after another, each a
2-byte big-endian length and then the packet. Every packet is 20 ms of sound.
"""

import ctypes
import ctypes.util
import os
import struct
import sys

RATES = (8000, 12000, 16000, 24000, 48000)   # the only rates Opus takes
FRAME_MS = 20
MAX_PACKET = 4000                            # libopus's recommended upper bound
APPLICATION_AUDIO = 2049                     # OPUS_APPLICATION_AUDIO: music, films, games

_lib = None
_tried = False


def _candidates():
    names = {"win32": ["opus.dll", "libopus-0.dll", "libopus.dll"],
             "darwin": ["libopus.0.dylib", "libopus.dylib"]}.get(sys.platform, ["libopus.so.0", "libopus.so"])
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [getattr(sys, "_MEIPASS", None), here, os.path.join(here, "..", "bin")]
    if sys.platform == "darwin":
        dirs += ["/opt/homebrew/lib", "/usr/local/lib"]      # Homebrew's opus, for builds from source
    for d in dirs:
        if d:
            for n in names:
                p = os.path.join(d, n)
                if os.path.exists(p):
                    yield p
    found = ctypes.util.find_library("opus")
    if found:
        yield found
    yield from names                                         # whatever the system's loader finds


def _load():
    global _lib, _tried
    if _tried:
        return _lib
    _tried = True
    if os.environ.get("WINLINK_NO_OPUS"):
        return None
    for path in _candidates():
        try:
            lib = ctypes.CDLL(path)
            lib.opus_get_version_string.restype = ctypes.c_char_p
        except (OSError, AttributeError):
            continue
        p, i, ip = ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, i, i, ip]
        lib.opus_encoder_create.restype = p
        lib.opus_encode_float.argtypes = [p, p, i, p, ctypes.c_int32]
        lib.opus_encode_float.restype = ctypes.c_int32
        lib.opus_encoder_destroy.argtypes = [p]
        lib.opus_decoder_create.argtypes = [ctypes.c_int32, i, ip]
        lib.opus_decoder_create.restype = p
        lib.opus_decode_float.argtypes = [p, p, ctypes.c_int32, p, i, i]
        lib.opus_decode_float.restype = i
        lib.opus_decoder_destroy.argtypes = [p]
        lib.opus_strerror.argtypes = [i]
        lib.opus_strerror.restype = ctypes.c_char_p
        _lib = lib
        break
    return _lib


def available():
    return _load() is not None


def version():
    lib = _load()
    return lib.opus_get_version_string().decode() if lib else None


def _error(lib, code):
    return OSError(f"Opus: {lib.opus_strerror(code).decode(errors='replace')}")


class Encoder:
    """float32 interleaved PCM in (any amount), length-prefixed 20 ms Opus packets out.
    The bitrate is libopus's own choice: about 100 kbps for 48 kHz stereo, 50 for mono."""

    def __init__(self, rate, channels, np):
        lib = _load()
        if lib is None or rate not in RATES or channels not in (1, 2):
            raise OSError("Opus isn't available for this format")
        err = ctypes.c_int(0)
        self.enc = lib.opus_encoder_create(rate, channels, APPLICATION_AUDIO, ctypes.byref(err))
        if err.value != 0 or not self.enc:
            raise _error(lib, err.value)
        self.lib, self.np, self.channels = lib, np, channels
        self.frame = rate * FRAME_MS // 1000                 # samples per channel in one packet
        self.pending = np.zeros(0, dtype=np.float32)
        self.out = ctypes.create_string_buffer(MAX_PACKET)

    def encode(self, pcm):
        np = self.np
        self.pending = np.concatenate((self.pending, np.frombuffer(pcm, dtype="<f4")))
        step = self.frame * self.channels
        packets = []
        while len(self.pending) >= step:
            block = np.ascontiguousarray(self.pending[:step])
            self.pending = self.pending[step:]
            n = self.lib.opus_encode_float(self.enc, block.ctypes.data, self.frame, self.out, MAX_PACKET)
            if n < 0:
                raise _error(self.lib, n)
            packets.append(struct.pack(">H", n) + self.out.raw[:n])
        return b"".join(packets)

    def close(self):
        enc, self.enc = getattr(self, "enc", None), None
        if enc:
            self.lib.opus_encoder_destroy(enc)

    __del__ = close


class Decoder:
    """Bytes from the stream in (cut anywhere), float32 interleaved PCM out."""

    MAX_FRAME = 5760                                         # 120 ms at 48 kHz: the longest packet

    def __init__(self, rate, channels, np):
        lib = _load()
        if lib is None or rate not in RATES or channels not in (1, 2):
            raise OSError("Opus isn't available for this format")
        err = ctypes.c_int(0)
        self.dec = lib.opus_decoder_create(rate, channels, ctypes.byref(err))
        if err.value != 0 or not self.dec:
            raise _error(lib, err.value)
        self.lib, self.np, self.channels = lib, np, channels
        self.rest = b""
        self.pcm = np.zeros(self.MAX_FRAME * channels, dtype=np.float32)

    def decode(self, data):
        """Returns a float32 array (samples, channels) of everything complete so far."""
        np = self.np
        data = self.rest + data
        out, pos = [], 0
        while len(data) - pos >= 2:
            n = struct.unpack_from(">H", data, pos)[0]
            if len(data) - pos - 2 < n:
                break
            packet = data[pos + 2:pos + 2 + n]
            pos += 2 + n
            got = self.lib.opus_decode_float(self.dec, packet, n, self.pcm.ctypes.data, self.MAX_FRAME, 0)
            if got < 0:
                raise _error(self.lib, got)
            out.append(self.pcm[:got * self.channels].copy())
        self.rest = data[pos:]
        if not out:
            return np.zeros((0, self.channels), dtype=np.float32)
        return np.concatenate(out).reshape(-1, self.channels)

    def close(self):
        dec, self.dec = getattr(self, "dec", None), None
        if dec:
            self.lib.opus_decoder_destroy(dec)

    __del__ = close
