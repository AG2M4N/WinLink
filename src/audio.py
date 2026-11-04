"""
Sound from any device to any device.

A device that shares its sound captures everything it plays (Windows: WASAPI
loopback; Mac: ScreenCaptureKit; Android: the phone app) and streams it to one
other device, which plays it through its speakers. On the same Wi-Fi it's
lossless 32-bit float; over Tailscale it's Opus (about 100 kbps, see opus.py)
with a bigger buffer, so it holds up on slow and relayed links. A receiver
without Opus (older versions, the phone app) gets CD-quality 16-bit PCM instead.

The stream is an encrypted "audio" connection:
  sender   -> {"rate", "channels", "format", "remote", "buffer_ms", "from", "opus"?}
              ("opus": true offers Opus; "format" is what's sent if the receiver doesn't take it)
  receiver -> {"ok": true, "format": "opus"?}  or  {"ok": false, "error": "why"}
              (older receivers ignore the offer and answer {"ok": true}: PCM it is)
  sender   -> raw PCM, or Opus packets (opus.py) if agreed, until either side closes
  receiver -> {"type": "stop", "reason": "why"} when it stops playing for a reason the sender should
              know (someone else started playing here, or the person pressed Stop); the sender
              then stops sharing instead of reconnecting.
"""

import queue
import threading
import time

import opus

FRAME = 8            # bytes per buffered frame: stereo float32
TARGET_MS = 40       # where Auto starts on the same Wi-Fi (it adapts: see Player)
REMOTE_MS = 200      # ...and over Tailscale
FLOOR_MS = 20        # Auto never goes below this on Wi-Fi...
REMOTE_FLOOR_MS = 80 # ...or this over Tailscale
CEILING_MS = 300     # ...nor above this, however bumpy the network
SEND_BACKLOG_MS = 60 # the sender keeps at most this much waiting for the network (older sound is dropped)
REMOTE_BACKLOG_MS = 200

# What the person playing the sound can pick (receiving side): how much to buffer. Auto adapts.
LATENCY = {"lowest": 20, "low": 50, "balanced": 120, "smooth": 300}

# What the device sharing its sound can pick (sending side).
QUALITY = {
    "lossless": {"format": "f32", "half_rate": False, "mono": False, "label": "lossless"},
    "cd":       {"format": "s16", "half_rate": False, "mono": False, "label": "CD quality"},
    "saver":    {"format": "s16", "half_rate": True,  "mono": True,  "label": "data saver"},
}
MAX_BACKLOG = 25     # 10 ms chunks waiting on the network before old ones are dropped (no growing delay)
REPLY_WAIT = 5       # seconds to wait for the receiver's yes/no (older versions don't answer)
SEND_STALL = 15      # a send stuck this long means the network is gone (Wi-Fi dropped without an error)


class Refused(Exception):
    """The receiver said no, or told us to stop. Don't retry until the person turns sharing on again."""


class NoPermission(Exception):
    """The OS won't let WinLink capture sound (the person has to allow it)."""


def convert(pcm, channels, q, np):
    """float32 interleaved capture -> what the chosen quality sends."""
    samples = np.frombuffer(pcm, dtype="<f4").reshape(-1, channels)
    if channels > 2:
        samples = samples[:, :2]
    if q["mono"]:
        samples = samples.mean(axis=1, keepdims=True)
    if q["half_rate"]:
        n = len(samples) - len(samples) % 2
        samples = (samples[0:n:2] + samples[1:n:2]) / 2        # average pairs: gentle low-pass
    if q["format"] == "s16":
        return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    if q["format"] == "opus":                     # float32 for the encoder (it clips by itself)
        return np.ascontiguousarray(samples, dtype="<f4").tobytes()
    return samples.astype("<f4").tobytes()


# ---------------------------------------------------------------- playing
class Player:
    """Receiving side: jitter buffer + output stream on the default speakers."""

    def __init__(self):
        import numpy as np
        import sounddevice as sd
        self.np, self.sd = np, sd
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.buffering = True
        self.volume = 1.0
        self.peak = 0.0
        self.stream = None
        self.rate = 48000
        self.target_ms = TARGET_MS
        self.gen = 0             # which incoming stream is current (newest wins)
        self.latency_ms = None   # the person's choice; None = Auto (adapts to the network)
        self.adaptive = True
        self.floor_ms = FLOOR_MS
        self.underruns = 0
        self._changed_at = 0.0   # last time Auto moved the target
        self._excess_since = None
        self.sock = None
        self.source = None       # name of the device playing here
        self.source_id = None

    def start(self, rate, target_ms=TARGET_MS, floor_ms=FLOOR_MS):
        self.stop()
        self.rate = rate
        self.target_ms = target_ms
        self.floor_ms = floor_ms
        self.adaptive = self.latency_ms is None
        self.underruns = 0
        self._changed_at = time.monotonic()
        self._excess_since = None
        with self.lock:
            self.buf.clear()
            self.buffering = True
        # 5 ms blocks: the speaker asks for sound often, so little has to wait in our buffer
        self.stream = self.sd.OutputStream(samplerate=rate, channels=2, dtype="float32", latency="low",
                                           blocksize=rate // 200, callback=self._callback)
        self.stream.start()

    @property
    def delay_ms(self):
        """How far behind the sender the speakers are, roughly: our buffer plus the device's own."""
        if self.stream is None:
            return 0
        try:
            device = float(self.stream.latency) * 1000
        except Exception:  # noqa: BLE001
            device = 0.0
        return int(round((len(self.buf) / (self.rate * FRAME / 1000) + device) / 5) * 5)   # to 5 ms

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001  (device unplugged mid-stream)
                pass
        self.peak = 0.0

    def set_latency(self, ms):
        """Change the buffer now, even mid-song (None = Auto)."""
        self.latency_ms = ms
        self.adaptive = ms is None
        if ms and self.stream is not None:
            with self.lock:
                self.target_ms = ms
                per_ms = self.rate * FRAME // 1000
                if len(self.buf) > ms * per_ms:            # shorter buffer: catch up straight away
                    del self.buf[:len(self.buf) - ms * per_ms]

    def feed(self, pcm):
        per_ms = self.rate * FRAME // 1000
        with self.lock:
            self.buf += pcm
            # A big burst (the network stalled, then delivered everything at once): skip straight
            # back to the target instead of playing it all late.
            limit = (self.target_ms + max(100, self.target_ms)) * per_ms
            if len(self.buf) > limit:
                cut = len(self.buf) - self.target_ms * per_ms
                del self.buf[:cut - cut % FRAME]

    def _callback(self, outdata, frames, _time, _status):
        need = frames * FRAME
        per_ms = self.rate * FRAME // 1000
        now = time.monotonic()
        with self.lock:
            target = self.target_ms * per_ms
            if self.buffering and len(self.buf) >= target:
                self.buffering = False
            chunk = b""
            if not self.buffering:
                # Keep the delay at the target. The two devices' clocks never run at exactly the same
                # speed and Wi-Fi delivers in bursts, so sound slowly piles up; when more than a
                # little extra has been waiting for a while, drop 5 ms of it at a time (inaudible).
                excess = len(self.buf) - need - target
                if excess > max(8 * per_ms, target // 2):
                    if self._excess_since is None:
                        self._excess_since = now
                    elif now - self._excess_since > 0.3:
                        drop = min(excess, 5 * per_ms)
                        del self.buf[:drop - drop % FRAME]
                else:
                    self._excess_since = None
                chunk = bytes(self.buf[:need])
                del self.buf[:need]
                if len(chunk) < need:          # ran dry: rebuild the buffer
                    self.buffering = True
                    self.underruns += 1
                    if self.adaptive:            # Auto: this network needs a bit more
                        self.target_ms = min(CEILING_MS, self.target_ms + 15)
                        self._changed_at = now
                elif self.adaptive and now - self._changed_at > 15 and self.target_ms > self.floor_ms:
                    self.target_ms = max(self.floor_ms, self.target_ms - 5)   # smooth for a while: tighten
                    self._changed_at = now
        outdata.fill(0)
        if chunk:
            samples = self.np.frombuffer(chunk, dtype="<f4").reshape(-1, 2)
            outdata[:len(samples)] = samples * self.volume
            self.np.clip(outdata, -1.0, 1.0, out=outdata)
        self.peak = float(abs(outdata).max()) if frames else 0.0

    def stop_current(self, reason):
        """Stop what's playing and tell its sender why (it then stops sharing)."""
        with self.lock:
            sock = self.sock
        if sock is not None:
            _tell_stop(sock, reason)

    def receive(self, hdr, sock, peer_name, on_state, refuse=None, peer_id=None):
        """Play one incoming stream until it ends. on_state(info | None).
        A new stream replaces whatever was playing; the old sender is told who took over. A new
        stream from the same sender at the same rate is that sender moving to a faster route (Wi-Fi
        instead of Tailscale): the speakers keep playing and the buffered sound is kept.
        refuse: a reason not to play at all (sent back to the sender), or None."""
        if refuse:
            try:
                sock.send_json({"ok": False, "error": refuse})
            except (OSError, ValueError):
                pass
            sock.close()
            return
        np = self.np
        rate, ch = int(hdr["rate"]), int(hdr["channels"])
        dtype, size = ("<f4", 4) if hdr.get("format") == "f32" else ("<i2", 2)
        decoder = None
        if hdr.get("opus"):                          # offered: take it if this computer has Opus
            try:
                decoder = opus.Decoder(rate, ch, np)
            except OSError:
                decoder = None
        frame = ch * size
        remote = bool(hdr.get("remote"))
        target = self.latency_ms or int(hdr.get("buffer_ms", REMOTE_MS if remote else TARGET_MS))
        if self.latency_ms is None:                  # Auto: start low, it adapts from there
            target = min(target, REMOTE_MS if remote else TARGET_MS)
        floor = REMOTE_FLOOR_MS if remote else FLOOR_MS
        with self.lock:
            moving = (self.sock is not None and self.stream is not None and peer_id is not None
                      and self.source_id == peer_id and self.rate == rate)
        try:
            if moving:
                self.target_ms = target              # e.g. Wi-Fi needs less buffer than Tailscale
                self.floor_ms = floor
            else:
                self.start(rate, target, floor)
        except Exception as e:  # noqa: BLE001  (no output device, or it's busy)
            try:
                sock.send_json({"ok": False, "error": f"couldn't open the speakers: {e}"})
            except (OSError, ValueError):
                pass
            sock.close()
            return
        with self.lock:
            self.gen += 1
            gen = self.gen
            old, self.sock = self.sock, sock
            self.source, self.source_id = peer_name, peer_id
        if old is not None:
            if moving:
                _tell_stop(old, "moved to a faster connection", moved=True)
            else:
                _tell_stop(old, f"{peer_name} started playing there")
        try:
            sock.send_json({"ok": True, **({"format": "opus"} if decoder is not None else {})})
        except (OSError, ValueError):
            pass
        bits = "Opus" if decoder is not None else "32-bit float" if size == 4 else "16-bit"
        on_state({"rate": rate, "bits": bits,
                  "remote": bool(hdr.get("remote")), "channels": ch, "buffer_ms": self.target_ms})
        rest = b""
        try:
            while True:
                data = sock.read1(16384)
                if not data:
                    break
                if decoder is not None:
                    pcm = decoder.decode(data)
                    if not len(pcm):
                        continue
                else:
                    data = rest + data
                    usable = len(data) - len(data) % frame
                    rest = data[usable:]
                    if not usable:
                        continue
                    pcm = np.frombuffer(data[:usable], dtype=dtype).reshape(-1, ch)
                    if size == 2:
                        pcm = pcm.astype(np.float32) / 32768.0
                if ch == 1:
                    pcm = np.repeat(pcm, 2, axis=1)
                elif ch > 2:
                    pcm = pcm[:, :2]
                if self.gen != gen:                      # replaced (the sender moved): stop feeding
                    break
                self.feed(pcm.astype("<f4", copy=False).tobytes())
        except (OSError, ValueError, ConnectionError):
            pass
        finally:
            sock.close()
            if decoder is not None:
                decoder.close()
            with self.lock:
                current = self.gen == gen
                if current:
                    self.sock = None
                    self.source = self.source_id = None
            if current:                                  # still the current stream
                self.stop()
                on_state(None)


def _tell_stop(sock, reason, moved=False):
    try:
        sock.send_json({"type": "stop", "reason": reason, **({"moved": True} if moved else {})})
    except (OSError, ValueError):
        pass
    try:
        sock.shutdown()                                  # wakes its reader
    except OSError:
        pass


# ---------------------------------------------------------------- capturing
class WasapiCapture:
    """Windows: everything the PC plays, from the default output (WASAPI loopback)."""

    def __init__(self):
        self.pa = self.stream = None

    def start(self, on_pcm):
        """Calls on_pcm(float32 interleaved bytes) from an audio thread. Returns (rate, channels)."""
        import pyaudiowpatch as pyaudio
        self.pa = pyaudio.PyAudio()
        try:
            dev = self.pa.get_default_wasapi_loopback()
        except (OSError, LookupError) as e:
            raise OSError("no speakers or headphones to capture from") from e
        rate, channels = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])

        def callback(in_data, _frames, _time, _status):
            on_pcm(in_data)
            return (None, pyaudio.paContinue)

        self.stream = self.pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                                   input_device_index=dev["index"], frames_per_buffer=rate // 100,
                                   stream_callback=callback)
        self.stream.start_stream()
        return rate, channels

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except OSError:
                pass
        pa, self.pa = self.pa, None
        if pa is not None:
            pa.terminate()


class LinuxCapture:
    """Linux: everything the PC plays, from the "monitor" of the default output, through parec.
    That works with PulseAudio and with PipeWire (its pipewire-pulse layer), i.e. every current
    desktop distribution. parec comes in pulseaudio-utils (Debian, Ubuntu, Fedora) or libpulse (Arch)."""

    RATE, CHANNELS = 48000, 2
    CHUNK = RATE // 100 * CHANNELS * 4          # 10 ms of float32 stereo

    def __init__(self):
        self.proc = None

    @staticmethod
    def available():
        import shutil
        return shutil.which("parec") is not None

    def start(self, on_pcm):
        import subprocess
        proc = subprocess.Popen(
            ["parec", "--device=@DEFAULT_MONITOR@", "--format=float32le", f"--rate={self.RATE}",
             f"--channels={self.CHANNELS}", "--latency-msec=20", "--client-name=WinLink",
             "--stream-name=Sharing this PC's sound"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.proc = proc
        first = proc.stdout.read(self.CHUNK)            # blocks until sound flows (silence counts)
        if not first:
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            self.stop()
            if "Connection refused" in err or "Connection failure" in err:
                raise OSError("no sound server running (PulseAudio or PipeWire)")
            raise OSError(f"couldn't capture this PC's sound: {err[:160] or 'parec stopped'}")
        on_pcm(first)

        def pump():
            try:
                while True:
                    data = proc.stdout.read(self.CHUNK)
                    if not data:
                        break
                    data = data[:len(data) - len(data) % 8]
                    if data:
                        on_pcm(data)
            except (OSError, ValueError):
                pass
        threading.Thread(target=pump, daemon=True, name="parec").start()
        return self.RATE, self.CHANNELS

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(2)
            except Exception:  # noqa: BLE001
                proc.kill()


class MacCapture:
    """Mac: everything the Mac plays, through ScreenCaptureKit (macOS 13+). Needs the
    "Screen & System Audio Recording" permission; WinLink's own sound is left out."""

    RATE, CHANNELS = 48000, 2

    def __init__(self):
        self.stream = None
        self.output = None
        self.stats = None

    def describe(self):
        """What macOS has delivered so far (for the log when nothing arrives)."""
        s = self.stats or {}
        return (f"{s.get('calls', 0)} buffers from macOS, {s.get('audio', 0)} of them sound, "
                f"{s.get('pcm_bytes', 0)} bytes usable, {s.get('empty', 0)} empty, {s.get('errors', 0)} failed"
                + (f" ({s['error']})" if s.get("error") else ""))

    @staticmethod
    def available():
        try:
            import ScreenCaptureKit  # noqa: F401
            import CoreMedia  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self, on_pcm):
        import CoreMedia
        import dispatch
        import objc
        import ScreenCaptureKit as SCK
        from Foundation import NSObject

        done = threading.Event()
        found = {}

        def got_content(content, error):
            found["content"], found["error"] = content, error
            done.set()

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(got_content)
        if not done.wait(10):
            raise OSError("macOS didn't answer the request to capture sound")
        if found["error"] is not None or found["content"] is None:
            raise NoPermission(_sck_error(found["error"]))
        displays = found["content"].displays()
        if not displays:
            raise OSError("no display to attach the sound capture to")

        flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setExcludesCurrentProcessAudio_(True)        # never capture WinLink playing someone else's sound
        cfg.setSampleRate_(self.RATE)
        cfg.setChannelCount_(self.CHANNELS)
        cfg.setWidth_(2)                                  # the picture isn't used: keep it tiny and slow
        cfg.setHeight_(2)
        cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))
        cfg.setQueueDepth_(3)

        channels = self.CHANNELS
        self.output = _mac_output_class(objc, NSObject, SCK, CoreMedia).alloc().init()
        self.output.on_pcm = on_pcm
        self.output.channels = channels
        self.stats = {"calls": 0, "audio": 0, "errors": 0, "error": "", "pcm_bytes": 0, "empty": 0}
        self.output.stats = self.stats
        self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
        q_audio = dispatch.dispatch_queue_create(b"winlink.sound", None)
        q_video = dispatch.dispatch_queue_create(b"winlink.frames", None)
        ok, err = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self.output, SCK.SCStreamOutputTypeAudio, q_audio, None)
        if not ok:
            raise OSError(f"couldn't set up sound capture: {err}")
        # A screen output too, so macOS doesn't log a warning for every dropped frame.
        self.stream.addStreamOutput_type_sampleHandlerQueue_error_(self.output, SCK.SCStreamOutputTypeScreen,
                                                                   q_video, None)
        started = threading.Event()
        result = {}

        def did_start(error):
            result["error"] = error
            started.set()

        self.stream.startCaptureWithCompletionHandler_(did_start)
        if not started.wait(10):
            raise OSError("macOS didn't start the sound capture")
        if result["error"] is not None:
            self.stream = None
            raise NoPermission(_sck_error(result["error"]))
        return self.RATE, channels

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            stopped = threading.Event()
            stream.stopCaptureWithCompletionHandler_(lambda _e: stopped.set())
            stopped.wait(3)


_OUTPUT = None


def _mac_output_class(objc, NSObject, SCK, CoreMedia):
    """The ScreenCaptureKit receiver. An Objective-C class can only be defined once per process,
    so it's made the first time and reused (each capture gives its instance its own callback)."""
    global _OUTPUT
    if _OUTPUT is None:
        np_mod = __import__("numpy")

        class WinLinkSoundOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            on_pcm = None
            channels = 2
            stats = None                 # what macOS delivered: for "I can't hear anything"

            def stream_didOutputSampleBuffer_ofType_(self, _stream, sbuf, kind):
                st = self.stats
                if st is not None:
                    st["calls"] += 1
                if kind != SCK.SCStreamOutputTypeAudio:
                    return
                if st is not None:
                    st["audio"] += 1
                if not CoreMedia.CMSampleBufferIsValid(sbuf):
                    return
                try:
                    pcm = _sample_buffer_pcm(CoreMedia, sbuf, self.channels, np_mod)
                except Exception as e:  # noqa: BLE001  (one odd buffer: skip it, but remember why)
                    if st is not None:
                        st["errors"] += 1
                        st["error"] = st["error"] or f"{type(e).__name__}: {e}"[:200]
                    return
                if st is not None:
                    st["pcm_bytes"] += len(pcm)
                    if not pcm:
                        st["empty"] += 1
                if pcm and self.on_pcm is not None:
                    self.on_pcm(pcm)
        _OUTPUT = WinLinkSoundOutput
    return _OUTPUT


def _asbd(asbd, channels):
    """(channels, format flags) from an AudioStreamBasicDescription. PyObjC hands it over as a
    plain tuple (mSampleRate, mFormatID, mFormatFlags, mBytesPerPacket, mFramesPerPacket,
    mBytesPerFrame, mChannelsPerFrame, mBitsPerChannel, mReserved), or as a struct with names."""
    if asbd is None:
        return channels, 1 | (1 << 5)                 # ScreenCaptureKit's usual: float, one block per channel
    if hasattr(asbd, "mChannelsPerFrame"):
        return int(asbd.mChannelsPerFrame), int(asbd.mFormatFlags)
    if isinstance(asbd, tuple) and len(asbd) == 1:    # (struct,) on some PyObjC versions
        return _asbd(asbd[0], channels)
    return int(asbd[6]), int(asbd[2])


def _sample_buffer_pcm(CoreMedia, sbuf, channels, np):
    """float32 interleaved bytes from a ScreenCaptureKit audio sample buffer (which is usually
    float32 with one block per channel)."""
    block = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
    if block is None:
        return b""
    length = CoreMedia.CMBlockBufferGetDataLength(block)
    if not length:
        return b""
    status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
    if status != 0 or not data:
        return b""
    data = bytes(data)
    frames = CoreMedia.CMSampleBufferGetNumSamples(sbuf)
    ch, flags = _asbd(CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(
        CoreMedia.CMSampleBufferGetFormatDescription(sbuf)), channels)
    planar = bool(flags & (1 << 5))         # kAudioFormatFlagIsNonInterleaved
    is_float = bool(flags & 1)              # kAudioFormatFlagIsFloat
    if not is_float or ch < 1 or frames <= 0 or len(data) < frames * ch * 4:
        return b""
    samples = np.frombuffer(data[:frames * ch * 4], dtype="<f4")
    if planar:
        samples = samples.reshape(ch, frames).T                 # one block per channel -> interleaved
    else:
        samples = samples.reshape(frames, ch)
    if ch == 1:
        samples = np.repeat(samples, 2, axis=1)
    elif ch > 2:
        samples = samples[:, :2]
    return np.ascontiguousarray(samples, dtype="<f4").tobytes()


def _sck_error(error):
    if error is None:
        return "macOS didn't allow capturing sound"
    code = error.code() if hasattr(error, "code") else 0
    try:
        import logging
        logging.getLogger("winlink").info("sound capture: macOS said %s %s: %s", error.domain(), code,
                                          error.localizedDescription())
    except Exception:  # noqa: BLE001
        pass
    if code in (-3801, -3802):         # SCStreamErrorUserDeclined / failed to start (no permission)
        return "macOS hasn't allowed WinLink to capture sound"
    return str(error.localizedDescription()) if hasattr(error, "localizedDescription") else str(error)


def capture_backend():
    """A capture for this computer, or None if it can't share its sound."""
    import sys
    if sys.platform == "win32":
        try:
            import pyaudiowpatch  # noqa: F401
            return WasapiCapture
        except ImportError:
            return None
    if sys.platform.startswith("linux"):
        return LinuxCapture if LinuxCapture.available() else None
    if sys.platform == "darwin" and MacCapture.available():
        import platform
        try:
            major = int(platform.mac_ver()[0].split(".")[0])
        except ValueError:
            major = 0
        return MacCapture if major >= 13 else None
    return None


# ---------------------------------------------------------------- sharing
class SoundSender:
    """Streams this computer's sound to one device.
    on_status(text, state): state is "playing", "retry" (try again soon), "off" (stopped for a reason
    the person must deal with: refused, stopped on the other side, no permission) or "idle".
    quality() returns "auto" | "lossless" | "cd" | "saver" (read each time it starts)."""

    def __init__(self, node, backend, on_status, quality=lambda: "auto"):
        self.node = node
        self.backend = backend
        self.on_status = on_status
        self.quality = quality
        self.stop_event = threading.Event()
        self.thread = None
        self.peer = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, peer):
        if self.running:
            return
        self.stop_event.clear()
        self.peer = peer
        self.thread = threading.Thread(target=self._run, args=(peer,), daemon=True, name="share sound")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self, peer):
        import numpy as np

        capture = None
        chunks = queue.Queue(maxsize=MAX_BACKLOG * 8)
        why_stopped = {}
        stalled = {}
        finished = threading.Event()
        sending = [0.0]                  # when the send in progress started (0 = none)

        def on_pcm(pcm):
            try:
                chunks.put_nowait(pcm)
            except queue.Full:       # network can't keep up: drop the OLDEST audio, never lag behind
                try:
                    chunks.get_nowait()
                    chunks.put_nowait(pcm)
                except (queue.Empty, queue.Full):
                    pass

        cur = {"sock": None}             # the connection in use (it changes when we move to Wi-Fi)
        moved = {}                       # a faster connection, ready to take over

        def handshake(sock, rate, channels):
            """Send the format, wait for the yes. Returns what to stream with."""
            remote = sock.route == "tailscale"
            choice = self.quality()
            auto = choice not in QUALITY
            if auto:
                choice = "cd" if remote else "lossless"
            q = QUALITY[choice]
            out_rate = rate // 2 if q["half_rate"] else rate
            out_ch = 1 if q["mono"] else min(channels, 2)
            # Opus over Tailscale (Auto) and for Data saver, if both ends have it. Lossless and CD
            # quality, when picked, stay PCM.
            offer = ((auto and remote) or choice == "saver") and opus.available() and out_rate in opus.RATES
            sock.send_json({"rate": out_rate, "channels": out_ch, "format": q["format"], "remote": remote,
                            "buffer_ms": REMOTE_MS if remote else TARGET_MS, "from": self.node.name,
                            **({"opus": True} if offer else {})})
            sock.settimeout(REPLY_WAIT)
            try:
                reply = sock.recv_json()
            except OSError:
                reply = {"ok": True}                      # an older WinLink: it doesn't answer
            if reply.get("ok") is False:
                raise Refused(str(reply.get("error") or "it said no")[:160])
            sock.settimeout(None)
            encoder = None
            if offer and reply.get("format") == "opus":
                encoder = opus.Encoder(out_rate, out_ch, np)
                q = {**q, "format": "opus", "label": "Opus"}
            threading.Thread(target=listen, args=(sock,), daemon=True, name="sound replies").start()
            return {"q": q, "out_rate": out_rate, "out_ch": out_ch, "remote": remote, "encoder": encoder}

        def listen(sock):
            """Messages from the receiver while we stream (it may tell us to stop)."""
            try:
                while not self.stop_event.is_set():
                    msg = sock.recv_json()
                    if msg.get("type") == "stop":
                        if msg.get("moved") or sock is not cur["sock"]:   # the route we're leaving
                            return
                        why_stopped["reason"] = str(msg.get("reason") or "stopped there")[:160]
                        self.stop_event.set()
                        return
            except (OSError, ValueError, ConnectionError):
                pass

        def watchdog():
            """The stream has no timeout (the receiver rarely talks), so a dead network would leave
            sendall stuck for many minutes, and Stop with it. Shut the connection instead."""
            while not finished.wait(0.25):
                started = sending[0]
                if started and (self.stop_event.is_set() or time.time() - started > SEND_STALL):
                    if not self.stop_event.is_set():
                        stalled["why"] = f"nothing got through for {SEND_STALL} s"
                    if cur["sock"] is not None:
                        cur["sock"].close()
                    return

        def put(data):
            for _ in range(2):
                sock = cur["sock"]
                sending[0] = time.time()
                try:
                    sock.sendall(data)
                    return
                except OSError:
                    # The receiver closes the old route as soon as the new one says hello, which can
                    # be a moment before the switch below: carry on over the new one.
                    end = time.time() + 8
                    while moved.get("busy") and time.time() < end:
                        time.sleep(0.02)
                    if cur["sock"] is sock:
                        raise
                finally:
                    sending[0] = 0.0

        def try_wifi(rate, channels):
            """In the background: connect over Wi-Fi; hand it to the stream if that worked."""
            try:
                sock = self.node.dial(peer, "audio")
                if sock.route == "tailscale":             # Wi-Fi didn't answer after all
                    sock.close()
                    peer.lan_failed_at = time.time()
                    return
                fmt = handshake(sock, rate, channels)
                if finished.is_set() or self.stop_event.is_set():
                    sock.close()
                    return
                old, cur["sock"], cur["fmt"] = cur["sock"], sock, fmt     # switch now, mid-stream
                old.close()
                net_log().info("sound to %s moved from Tailscale to Wi-Fi", peer.name)
                status(fmt)
            except Exception as e:  # noqa: BLE001  (keep playing over Tailscale)
                peer.lan_failed_at = time.time()
                net_log().info("sound to %s stays on Tailscale: %s", peer.name, _short(e))
            finally:
                moved["busy"] = False

        def status(fmt):
            where = ", via Tailscale" if fmt["remote"] else ""
            self.on_status(f"Playing on {peer.name} · {fmt['out_rate'] / 1000:g} kHz {fmt['q']['label']}"
                           f"{' mono' if fmt['out_ch'] == 1 else ''}{where}", "playing")

        try:
            self.on_status(f"Connecting to {peer.name}…", "retry")
            cur["sock"] = self.node.dial(peer, "audio")    # authenticated, encrypted
            capture = self.backend()
            rate, channels = capture.start(on_pcm)
            cur["fmt"] = handshake(cur["sock"], rate, channels)
            threading.Thread(target=watchdog, daemon=True, name="sound watchdog").start()
            status(cur["fmt"])
            last_sent = time.time()
            next_check = time.time() + 3
            began, got_any, warned = time.time(), [False], False
            while not self.stop_event.is_set():
                fmt = cur["fmt"]
                if not warned and not got_any[0] and time.time() - began > 5:
                    warned = True
                    detail = capture.describe() if hasattr(capture, "describe") else ""
                    net_log().warning("sharing sound: nothing captured in 5 s%s", f" ({detail})" if detail else "")
                if (fmt["remote"] and time.time() >= next_check and not moved.get("busy")
                      and peer.has_fresh_lan() and time.time() - getattr(peer, "lan_failed_at", 0) > 60):
                    moved["busy"] = True                   # on the same Wi-Fi again: try the faster route
                    threading.Thread(target=try_wifi, args=(rate, channels), daemon=True,
                                     name="sound to wifi").start()
                if fmt["remote"] and time.time() >= next_check:
                    next_check = time.time() + 3
                try:
                    pcm = chunks.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_sent > 5:       # silent for a while: check the link is alive
                        put(b"")
                        last_sent = time.time()
                    continue
                # Only fresh sound goes out: if the network fell behind, skip what's now too old
                # (late sound would just add delay on the other side).
                chunk_ms = len(pcm) / (rate * channels * 4) * 1000 or 10
                keep = max(1, int((REMOTE_BACKLOG_MS if fmt["remote"] else SEND_BACKLOG_MS) / chunk_ms))
                while chunks.qsize() > keep:
                    try:
                        pcm = chunks.get_nowait()
                    except queue.Empty:
                        break
                got_any[0] = True
                data = convert(pcm, channels, fmt["q"], np)
                if fmt["encoder"] is not None:
                    data = fmt["encoder"].encode(data)    # empty until a whole 20 ms packet is ready
                    if not data:
                        continue
                put(data)
                last_sent = time.time()
            if "reason" in why_stopped:
                self.on_status(f"Stopped: {why_stopped['reason']}.", "off")
            else:
                self.on_status("", "idle")
        except Refused as e:
            self.on_status(f"{peer.name} didn't play it: {e}.", "off")
        except NoPermission as e:
            self.on_status(f"{e}.", "off")
        except (OSError, LookupError, ConnectionError, ValueError) as e:
            if "reason" in why_stopped:
                self.on_status(f"Stopped: {why_stopped['reason']}.", "off")
            elif self.stop_event.is_set():
                self.on_status("", "idle")
            else:
                self.on_status(f"Lost {peer.name} ({stalled.get('why') or _short(e)}). Trying again…", "retry")
        except Exception as e:  # noqa: BLE001  (audio driver trouble etc.)
            self.on_status(f"Stopped: {_short(e)}.", "off")
        finally:
            finished.set()
            if capture is not None:
                try:
                    capture.stop()
                except Exception:  # noqa: BLE001
                    pass
            if cur["sock"] is not None:
                cur["sock"].close()


def net_log():
    import logging
    return logging.getLogger("winlink")


def _short(e):
    text = str(e) or type(e).__name__
    if "timed out" in text:
        return "no response in time"
    if "refused" in text.lower():
        return "WinLink isn't open on it"
    return text[:120]
