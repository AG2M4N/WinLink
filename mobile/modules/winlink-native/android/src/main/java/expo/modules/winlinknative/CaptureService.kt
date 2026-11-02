package expo.modules.winlinknative

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Base64

/** Captures what other apps play (Android 10+), in 10 ms chunks of 48 kHz 16-bit stereo, and hands
 * them to JS. Apps that forbid capture (some video and banking apps) come through silent. */
class CaptureService : Service() {
  companion object {
    const val CHANNEL = "winlink-capture"
    const val ID = 4202
    const val RATE = 48000
    @Volatile var running = false
    @Volatile var lastError: String? = null
  }

  private var projection: MediaProjection? = null
  private var record: AudioRecord? = null
  private var thread: Thread? = null

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
      nm.createNotificationChannel(NotificationChannel(CHANNEL, "Sharing sound", NotificationManager.IMPORTANCE_LOW))
    }
    val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
    val n = b.setContentTitle("WinLink").setContentText("Sharing this phone's sound")
      .setSmallIcon(android.R.drawable.ic_lock_silent_mode_off).setOngoing(true).build()
    if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
    else startForeground(ID, n)

    val code = intent?.getIntExtra("code", Activity.RESULT_CANCELED) ?: Activity.RESULT_CANCELED
    @Suppress("DEPRECATION")
    val data: Intent? = intent?.getParcelableExtra("data")
    if (code != Activity.RESULT_OK || data == null || Build.VERSION.SDK_INT < 29) {
      fail(if (Build.VERSION.SDK_INT < 29) "sharing a phone's sound needs Android 10 or later" else "permission to capture wasn't given")
      return START_NOT_STICKY
    }
    try {
      val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
      val proj = mpm.getMediaProjection(code, data) ?: throw IllegalStateException("no media projection")
      projection = proj
      proj.registerCallback(object : MediaProjection.Callback() {
        override fun onStop() {
          stopSelf()
        }
      }, null)
      val cfg = AudioPlaybackCaptureConfiguration.Builder(proj)
        .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
        .addMatchingUsage(AudioAttributes.USAGE_GAME)
        .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
        .build()
      val fmt = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(RATE)
        .setChannelMask(AudioFormat.CHANNEL_IN_STEREO).build()
      val chunk = RATE / 100 * 4
      val minBuf = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_16BIT)
      val rec = AudioRecord.Builder().setAudioFormat(fmt).setBufferSizeInBytes(maxOf(minBuf, chunk * 8))
        .setAudioPlaybackCaptureConfig(cfg).build()
      record = rec
      rec.startRecording()
      running = true
      lastError = null
      thread = Thread {
        val buf = ByteArray(chunk)
        while (running) {
          val n = rec.read(buf, 0, buf.size)
          if (n > 0) {
            Bridge.send("onPcm", mapOf("data" to Base64.encodeToString(buf, 0, n, Base64.NO_WRAP)))
          } else if (n < 0) {
            break
          }
        }
      }.also { it.start() }
      Bridge.send("onCapture", mapOf("state" to "started", "rate" to RATE, "channels" to 2))
    } catch (e: SecurityException) {
      fail("Android didn't allow recording: allow WinLink to record audio in Settings")
    } catch (e: Throwable) {
      fail(e.message ?: "couldn't start capturing")
    }
    return START_NOT_STICKY
  }

  private fun fail(msg: String) {
    lastError = msg
    running = false
    Bridge.send("onCapture", mapOf("state" to "error", "error" to msg))
    stopSelf()
  }

  override fun onDestroy() {
    running = false
    try {
      record?.stop()
    } catch (e: IllegalStateException) {
    }
    record?.release()
    record = null
    projection?.stop()
    projection = null
    Bridge.send("onCapture", mapOf("state" to "stopped"))
    super.onDestroy()
  }
}
