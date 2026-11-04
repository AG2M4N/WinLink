package expo.modules.winlinknative

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.net.wifi.WifiManager
import android.os.IBinder
import android.os.PowerManager
import android.util.Log

/** Keeps WinLink running in the background so files, clipboard and sound keep arriving.
 * Its notification has a "Send clipboard" button.
 *
 * Battery: the wake and Wi-Fi locks (CPU and Wi-Fi fully awake) are held only while the screen is on,
 * or while WinLink is busy (sound playing or being shared, a file transferring). With the screen off
 * and nothing going on, the phone sleeps as usual; computers see it go offline, and it reconnects
 * as soon as the screen comes back on. */
class LinkService : Service() {
  companion object {
    const val CHANNEL = "winlink-link"
    const val ID = 4201
    const val ACTION_STOP = "expo.modules.winlinknative.STOP"
    @Volatile var running = false
    @Volatile var busy = false            // set from JS: sound or a transfer under way
    @Volatile var instance: LinkService? = null

    /** JS says whether something's under way that must keep going with the screen off. */
    fun markBusy(on: Boolean) {
      busy = on
      instance?.let { s -> android.os.Handler(android.os.Looper.getMainLooper()).post { s.applyLocks() } }
    }
    fun notification(ctx: Context, text: String): Notification {
      val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
      if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Connected computers", NotificationManager.IMPORTANCE_LOW))
      }
      val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
      val open = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)
      val openPi = PendingIntent.getActivity(ctx, 0, open, flags)
      val clip = Intent(ctx, ClipboardActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      val clipPi = PendingIntent.getActivity(ctx, 1, clip, flags)
      val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(ctx, CHANNEL) else Notification.Builder(ctx)
      return b.setContentTitle("WinLink")
        .setContentText(text)
        .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
        .setOngoing(true)
        .setContentIntent(openPi)
        .addAction(Notification.Action.Builder(null, "Send clipboard", clipPi).build())
        .build()
    }
  }

  override fun onBind(intent: Intent?): IBinder? = null

  // Android ends the app if a service started with startForegroundService() hasn't called
  // startForeground() within a few seconds, even if it's being stopped. So: right away in onCreate,
  // again for every start (the text may have changed), and before stopping.
  override fun onCreate() {
    super.onCreate()
    running = true
    instance = this
    registerReceiver(screen, android.content.IntentFilter().apply {
      addAction(Intent.ACTION_SCREEN_ON)
      addAction(Intent.ACTION_SCREEN_OFF)
    })
    goForeground("Connected")
    try { ClipWatcher.sync(this) } catch (e: Throwable) {}
  }

  override fun onDestroy() {
    running = false
    if (instance === this) instance = null
    try { unregisterReceiver(screen) } catch (e: Throwable) {}
    releaseLocks()
    super.onDestroy()
  }

  // While connected, keep the CPU and Wi-Fi awake with the screen off: otherwise Android parks the
  // radio and sound stutters, transfers stall and computers can't reach the phone.
  private val screen = object : android.content.BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
      applyLocks()
      if (intent.action == Intent.ACTION_SCREEN_ON) {
        Bridge.send("onScreen", mapOf("on" to true))      // reconnect now, don't wait for a retry
      }
    }
  }

  /** Hold the locks while the screen is on or something's under way; otherwise let the phone sleep. */
  fun applyLocks() {
    if (!running) return
    val on = (getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive
    if (on || busy) holdLocks() else releaseLocks()
  }

  private var wake: PowerManager.WakeLock? = null
  private var wifiLocks = mutableListOf<WifiManager.WifiLock>()

  @Suppress("DEPRECATION")      // WIFI_MODE_FULL_HIGH_PERF: still what keeps Wi-Fi awake on older phones
  private fun holdLocks() {
    try {
      if (wake == null) {
        wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
          .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WinLink:connected").apply {
            setReferenceCounted(false)
            acquire()
          }
      }
      if (wifiLocks.isEmpty()) {
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val modes = mutableListOf(WifiManager.WIFI_MODE_FULL_HIGH_PERF)
        if (Build.VERSION.SDK_INT >= 29) modes.add(WifiManager.WIFI_MODE_FULL_LOW_LATENCY)
        for (m in modes) {
          wifiLocks.add(wm.createWifiLock(m, "WinLink:connected").apply {
            setReferenceCounted(false)
            acquire()
          })
        }
      }
    } catch (e: Throwable) {
      Log.w("WinLink", "background service: couldn't keep Wi-Fi awake: ${e.message}")
    }
  }

  private fun releaseLocks() {
    try { wake?.release() } catch (e: Throwable) {}
    wake = null
    for (l in wifiLocks) try { l.release() } catch (e: Throwable) {}
    wifiLocks.clear()
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (intent?.action == ACTION_STOP) {
      goForeground("Disconnected")
      releaseLocks()
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return START_NOT_STICKY
    }
    goForeground(intent?.getStringExtra("text") ?: "Connected")
    return START_STICKY
  }

  private fun goForeground(text: String) {
    try {
      val n = notification(this, text)
      if (Build.VERSION.SDK_INT >= 29) {
        startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
      } else {
        startForeground(ID, n)
      }
      applyLocks()
    } catch (e: Throwable) {        // not allowed right now (e.g. started from the background): give up quietly
      Log.w("WinLink", "background service: ${e.message}")
      stopSelf()
    }
  }
}
