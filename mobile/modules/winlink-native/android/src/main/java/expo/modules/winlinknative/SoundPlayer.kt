package expo.modules.winlinknative

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.SystemClock
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.max

/** Plays a live PCM stream with a small, fixed delay: it waits for bufferMs of sound before
 * starting, and drops audio rather than letting the delay grow when the network bunches up. */
class SoundPlayer {
  private var track: AudioTrack? = null
  private var bytesPerFrame = 4
  private var rate = 48000
  private var isFloat = false
  private var prefill = 0
  private var buffered = 0
  private var started = false
  private var firstWriteAt = 0L
  @Volatile var level = 0f
  @Volatile var written = 0L         // bytes the speaker accepted
  @Volatile var errors = 0
  @Volatile var lastError = ""
  @Volatile var lowLatency = false
  // How far behind the sender we play. The track's buffer is capped just above it, so sound can't
  // pile up (the clocks drift and Wi-Fi delivers in bursts); in Auto it adapts to the network.
  @Volatile var targetMs = 40
  @Volatile var adaptive = true
  @Volatile var floorMs = 20
  private var lastUnderruns = 0
  private var changedAt = 0L
  private var framesWritten = 0L

  /** Auto (adaptive, never below floorMs) or a fixed delay. */
  @Synchronized
  fun tune(adaptive: Boolean, floorMs: Int) {
    this.adaptive = adaptive
    this.floorMs = floorMs.coerceIn(10, 500)
  }

  private fun frames(ms: Int) = rate * ms / 1000

  private fun applyCap() {
    val t = track ?: return
    if (android.os.Build.VERSION.SDK_INT >= 24) {
      try {
        t.bufferSizeInFrames = frames(targetMs + max(10, targetMs / 2))
      } catch (e: Throwable) {}
    }
  }

  /** Milliseconds of sound queued in the speaker right now (our share of the delay). */
  val queuedMs: Int
    get() {
      val t = track ?: return 0
      val head = t.playbackHeadPosition.toLong() and 0xffffffffL
      return (max(0L, framesWritten - head) * 1000 / rate).toInt()
    }
  var volume = 1f
    set(v) {
      field = v
      track?.setVolume(v)
    }

  @Synchronized
  fun start(rate: Int, channels: Int, format: String, bufferMs: Int) {
    stop()
    this.rate = rate
    isFloat = format == "f32"
    val enc = if (isFloat) AudioFormat.ENCODING_PCM_FLOAT else AudioFormat.ENCODING_PCM_16BIT
    val mask = if (channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
    val ch = if (channels == 1) 1 else 2
    bytesPerFrame = ch * (if (isFloat) 4 else 2)
    val minBuf = AudioTrack.getMinBufferSize(rate, mask, enc)
    targetMs = bufferMs.coerceIn(10, 2000)
    val want = rate * bytesPerFrame * targetMs / 1000
    prefill = want
    buffered = 0
    started = false
    firstWriteAt = 0L
    written = 0L
    framesWritten = 0L
    errors = 0
    lastError = ""
    lastUnderruns = 0
    changedAt = SystemClock.elapsedRealtime()
    // Low latency if the phone can do it at this format; otherwise the normal path (some phones
    // refuse the fast path, or build a track that never initialises).
    var t: AudioTrack? = null
    for (fast in listOf(true, false)) {
      try {
        val b = AudioTrack.Builder()
          .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
          .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setEncoding(enc).setChannelMask(mask).build())
          .setBufferSizeInBytes(max(minBuf, want * 3))
          .setTransferMode(AudioTrack.MODE_STREAM)
        if (fast) b.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
        val made = b.build()
        if (made.state == AudioTrack.STATE_INITIALIZED) {
          t = made
          lowLatency = fast
          break
        }
        made.release()
      } catch (e: Throwable) {
        lastError = "speaker (${if (fast) "low latency" else "normal"}): ${e.message}"
        Log.w("WinLink", lastError)
      }
    }
    track = t ?: throw IllegalStateException("this phone's speaker wouldn't open for $rate Hz ${if (isFloat) "float" else "16-bit"} sound")
    track?.setVolume(volume)
    applyCap()
  }

  /** pcm: interleaved frames from the network (may carry more than one channel layout already converted). */
  @Synchronized
  fun write(pcm: ByteBuffer, size: Int) {
    val t = track ?: return
    pcm.order(ByteOrder.LITTLE_ENDIAN)
    level = peak(pcm, size)
    val wrote = t.write(pcm, size, AudioTrack.WRITE_NON_BLOCKING)   // full = network bunched up: drop it
    if (wrote < 0) {
      errors++
      lastError = "speaker write failed ($wrote)"
      if (errors == 1 || errors % 500 == 0) Log.w("WinLink", lastError)
    } else {
      written += wrote
      framesWritten += wrote / bytesPerFrame
    }
    if (started && android.os.Build.VERSION.SDK_INT >= 24) {
      val now = SystemClock.elapsedRealtime()
      val u = t.underrunCount
      if (u > lastUnderruns) {                       // the speaker ran dry: this network needs more
        lastUnderruns = u
        if (adaptive && targetMs < 300) {
          targetMs = minOf(300, targetMs + 15)
          changedAt = now
          applyCap()
        }
      } else if (adaptive && targetMs > floorMs && now - changedAt > 15000) {
        targetMs = max(floorMs, targetMs - 5)       // smooth for a while: tighten
        changedAt = now
        applyCap()
      }
    }
    if (!started) {
      val now = SystemClock.elapsedRealtime()
      if (firstWriteAt == 0L) firstWriteAt = now
      buffered += max(wrote, 0)
      // Normally start once the buffer holds bufferMs; never wait more than a second for it.
      if (buffered >= prefill || (buffered > 0 && now - firstWriteAt > 1000)) {
        try {
          t.play()
          started = true
        } catch (e: IllegalStateException) {
          errors++
          lastError = "speaker wouldn't start: ${e.message}"
        }
      }
    }
  }

  private fun peak(b: ByteBuffer, size: Int): Float {
    var p = 0f
    val start = b.position()
    if (isFloat) {
      var i = start
      while (i + 4 <= start + size) {
        p = max(p, abs(b.getFloat(i)))
        i += 16
      }
    } else {
      var i = start
      while (i + 2 <= start + size) {
        p = max(p, abs(b.getShort(i).toFloat() / 32768f))
        i += 8
      }
    }
    return p
  }

  @Synchronized
  fun stop() {
    val t = track ?: return
    track = null
    level = 0f
    try {
      t.pause()
      t.flush()
      t.stop()
    } catch (e: IllegalStateException) {
    }
    t.release()
  }
}
