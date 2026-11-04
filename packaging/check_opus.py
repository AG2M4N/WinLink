"""Release check: Opus loads and round-trips sound well (python packaging/check_opus.py <src dir>)."""
import math
import sys

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "src")
import numpy as np  # noqa: E402
import opus  # noqa: E402

assert opus.available(), "libopus not found"
rate, seconds = 48000, 2
t = np.arange(rate * seconds) / rate


def sweep(f0, f1):
    """A tone gliding from f0 to f1 Hz: it never repeats, so it lines up with itself at one offset only."""
    return np.sin(2 * math.pi * (f0 * t + (f1 - f0) * t * t / (2 * seconds)))


sound = np.stack([0.5 * sweep(200, 2000), 0.3 * sweep(3000, 500)], axis=1).astype("<f4")
for ch in (2, 1):
    src = sound if ch == 2 else np.ascontiguousarray(sound[:, :1])
    enc, dec = opus.Encoder(rate, ch, np), opus.Decoder(rate, ch, np)
    raw, wire = src.tobytes(), b""
    for i in range(0, len(raw), 1764 * ch):                 # odd-sized chunks, like a capture
        wire += enc.encode(raw[i:i + 1764 * ch])
    out = np.concatenate([dec.decode(wire[i:i + 1000]) for i in range(0, len(wire), 1000)])  # cut anywhere
    assert out.ndim == 2 and out.shape[1] == ch and len(out) >= len(src) - rate // 50, out.shape
    kbps = len(wire) * 8 / seconds / 1000
    # Opus delays the sound a little (6.5 ms at 48 kHz): find that offset over all channels, then compare
    a = src[rate // 4:rate]
    lag = max(range(0, 1000), key=lambda d: float((a * out[rate // 4 + d:rate + d]).sum()))
    b = out[rate // 4 + lag:rate + lag]
    snr = 10 * math.log10(float((a ** 2).sum()) / float(((a - b) ** 2).sum()))
    print(f"{opus.version()}: {ch} channel(s), {kbps:.0f} kbps, delay {lag / rate * 1000:.1f} ms, {snr:.1f} dB")
    assert kbps < 200, kbps                                  # far smaller than CD quality's 1536
    assert snr > 6, snr                                      # broken decoding is about 0 dB or worse
print("Opus OK")
