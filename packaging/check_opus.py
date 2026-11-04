"""Release check: Opus loads and round-trips sound well (python packaging/check_opus.py <src dir>)."""
import math
import sys

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "src")
import numpy as np  # noqa: E402
import opus  # noqa: E402

assert opus.available(), "libopus not found"
rate = 48000
t = np.arange(rate * 2) / rate
sound = np.stack([0.5 * np.sin(2 * math.pi * 440 * t), 0.3 * np.sin(2 * math.pi * 1000 * t)], axis=1).astype("<f4")
for ch in (2, 1):
    src = sound if ch == 2 else np.ascontiguousarray(sound[:, :1])
    enc, dec = opus.Encoder(rate, ch, np), opus.Decoder(rate, ch, np)
    raw, wire = src.tobytes(), b""
    for i in range(0, len(raw), 1764 * ch):                 # odd-sized chunks, like a capture
        wire += enc.encode(raw[i:i + 1764 * ch])
    out = np.concatenate([dec.decode(wire[i:i + 1000]) for i in range(0, len(wire), 1000)])  # cut anywhere
    kbps = len(wire) * 8 / 2 / 1000
    lag = max(range(200, 500), key=lambda d: float(np.dot(src[rate // 2:rate, 0], out[rate // 2 + d:rate + d, 0])))
    a, b = src[rate // 2:rate], out[rate // 2 + lag:rate + lag]
    snr = 10 * math.log10(float((a ** 2).sum()) / float(((a - b) ** 2).sum()))
    print(f"{opus.version()}: {ch} channel(s), {kbps:.0f} kbps, {snr:.1f} dB")
    assert out.shape[1] == ch and len(out) >= len(src) - opus.FRAME_MS * rate // 1000, out.shape
    assert kbps < 200 and snr > 12, (kbps, snr)
print("Opus OK")
