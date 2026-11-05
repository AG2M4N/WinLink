package expo.modules.winlinknative

import android.Manifest
import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** "Send copies automatically" (opt-in, needs two permissions given once over ADB / in Settings).
 *
 * Since Android 10 only the app in front may read the clipboard, and apps in the background aren't
 * even told it changed. But when it changes, Android logs that it refused to tell WinLink
 * ("Denying clipboard access to local.akshat.winlink…"). With READ_LOGS, WinLink watches for that
 * line in the system log and then opens its invisible ClipboardActivity for a moment, which may read
 * the clipboard and hands it to the JS side to send. Opening a screen from the background needs
 * "display over other apps" (SYSTEM_ALERT_WINDOW).
 *
 * Android 13+ also asks the person ("Allow WinLink to access all device logs?") each time a log
 * reader starts, and only while WinLink is on screen: a reader started from the background is
 * refused without a word, and then reads nothing, forever, while looking alive. So the reader is
 * only (re)started with WinLink in front. If Android ends it later (it can end an app's helper
 * processes), the watcher pauses, says so in a notification, and starts again the next time
 * WinLink is opened.
 *
 * Only the ClipboardService lines are read from the log, and only to spot that one line. */
object ClipWatcher {
  private const val PREFS = "winlink"
  private const val KEY = "autoClip"
  private const val TAG = "ClipboardService"
  private const val CHANNEL = "winlink-autoclip"
  private const val NOTE_ID = 4202

  @Volatile private var thread: Thread? = null
  @Volatile private var proc: Process? = null
  @Volatile private var ownUntil = 0L        // WinLink set the clipboard itself (from a computer): not a copy to send
  @Volatile private var lastTrigger = 0L
  private var listener: ClipboardManager.OnPrimaryClipChangedListener? = null
  private val gate = java.util.concurrent.Semaphore(0)   // the paused watcher waits here for WinLink to come in front

  /** Not reading: waiting for WinLink to be opened (Android only allows starting the reader then). */
  @Volatile var paused = false
    private set
  /** Android's log prompt was answered "Don't allow" (or not answered): not asked again until the switch is turned off and on. */
  @Volatile var refused = false
    private set

  fun enabled(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY, false)
  fun hasLogs(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.READ_LOGS) == PackageManager.PERMISSION_GRANTED
  fun hasOverlay(ctx: Context) = Settings.canDrawOverlays(ctx)
  /** Actually reading the log. */
  val running get() = thread != null && !paused

  fun setEnabled(ctx: Context, on: Boolean) {
    ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY, on).apply()
    refused = false                          // turning it on again: ask again
    sync(ctx)
  }

  /** WinLink is about to set the clipboard itself: don't send that back. */
  fun markOwn() {
    ownUntil = SystemClock.elapsedRealtime() + 2000
  }

  /** Start or stop to match the setting and the permissions, and resume a paused watcher if WinLink
   * is in front now. Safe to call often. */
  @Synchronized
  fun sync(context: Context) {
    val ctx = context.applicationContext
    if (enabled(ctx) && hasLogs(ctx) && hasOverlay(ctx)) {
      start(ctx)
      if (paused && !refused && inFront(ctx)) gate.release()
    } else {
      stop(ctx)
    }
  }

  /** WinLink has a screen in front (Android's "top" state, the only one in which it asks about logs). */
  private fun inFront(ctx: Context): Boolean {
    val info = ActivityManager.RunningAppProcessInfo()
    ActivityManager.getMyMemoryState(info)
    return info.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
  }

  private fun start(ctx: Context) {
    if (thread != null) return
    // Being registered is what makes Android log the refusal (and, with WinLink in front, it's simply called).
    Handler(Looper.getMainLooper()).post {
      if (listener == null && thread != null) {
        val l = ClipboardManager.OnPrimaryClipChangedListener { trigger(ctx) }
        (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).addPrimaryClipChangedListener(l)
        listener = l
      }
    }
    val t = Thread({ watch(ctx) }, "winlink clip watcher")
    t.isDaemon = true
    thread = t
    t.start()
  }

  private fun stop(ctx: Context) {
    val t = thread ?: return
    thread = null
    paused = false
    proc?.destroy()
    proc = null
    t.interrupt()
    note(ctx, null)
    Handler(Looper.getMainLooper()).post {
      listener?.let { (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).removePrimaryClipChangedListener(it) }
      listener = null
    }
  }

  private fun watch(ctx: Context) {
    val me = Thread.currentThread()
    val needle = "access to ${ctx.packageName}"
    val asks = Build.VERSION.SDK_INT >= 33          // Android 13+: the log prompt, only for the app in front
    var quickEnds = 0
    while (thread === me) {
      if (asks && (refused || !inFront(ctx))) {
        if (!paused) {
          if (thread !== me) break
          paused = true
          note(ctx, if (refused) "Android didn't allow reading its log. Open WinLink and turn the switch off and on to ask again."
                    else "Paused by Android. Open WinLink to turn it back on (tap Allow if Android asks).")
          Bridge.send("onAutoClip", mapOf("running" to false))
        }
        try { gate.drainPermits(); gate.tryAcquire(60, java.util.concurrent.TimeUnit.SECONDS) } catch (e: InterruptedException) { break }
        continue
      }
      if (!canRead()) {                               // the prompt was refused, or not answered in time
        Log.w("WinLink", "clipboard watcher: Android refused access to its log")
        if (asks) { refused = true; paused = false; continue }
        sleepOrStop(60_000) ?: break
        continue
      }
      if (paused) {
        paused = false
        note(ctx, null)
        Bridge.send("onAutoClip", mapOf("running" to true))
      }
      val began = SystemClock.elapsedRealtime()
      var code: Int? = null
      try {
        // Only lines from now on (-T), only Android's clipboard service, errors and up. Only the system
        // log (-b system), where Android's own services write: most apps write to the main one, and
        // logcat would be woken for each of their lines just to throw it away (battery).
        // Started straight after canRead(), so Android answers it from the approval it just gave.
        val since = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US).format(Date())
        val p = ProcessBuilder("logcat", "-b", "system", "-T", since, "$TAG:E", "*:S").redirectErrorStream(true).start()
        proc = p
        p.inputStream.bufferedReader().use { r ->
          while (thread === me) {
            val line = r.readLine() ?: break
            if (line.contains(needle)) trigger(ctx)
          }
        }
        p.destroy()
        code = try { p.waitFor() } catch (e: InterruptedException) { null }
      } catch (e: Throwable) {
        Log.w("WinLink", "clipboard watcher: ${e.message}")
      }
      if (thread !== me) break
      val minutes = (SystemClock.elapsedRealtime() - began) / 60_000
      Log.w("WinLink", "clipboard watcher: the log reader ended after $minutes min (exit code $code)")
      // Ended at once again and again (no log access after all?): don't spin.
      quickEnds = if (minutes < 1) quickEnds + 1 else 0
      if (quickEnds >= 3) { sleepOrStop(60_000) ?: break }
      else { sleepOrStop(1000) ?: break }
    }
  }

  private fun sleepOrStop(ms: Long): Unit? =
    try { Thread.sleep(ms) } catch (e: InterruptedException) { null }

  /** Can WinLink read other apps' lines in the system log? It asks Android, which on Android 13+
   * shows its prompt (WinLink is in front) or answers from the approval given a moment ago. Refused,
   * logcat only shows WinLink's own lines, and WinLink writes none to the system log. */
  private fun canRead(): Boolean = try {
    val p = ProcessBuilder("logcat", "-d", "-b", "system", "-t", "5").redirectErrorStream(true).start()
    proc = p
    val lines = p.inputStream.bufferedReader().use { it.readLines() }
    p.waitFor()
    lines.any { it.isNotBlank() && !it.startsWith("---------") }
  } catch (e: Throwable) {
    false
  }

  /** The "paused" notification (tap: open WinLink, which starts the watcher again); null removes it. */
  private fun note(ctx: Context, text: String?) {
    try {
      val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
      if (text == null) { nm.cancel(NOTE_ID); return }
      if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Sending copies automatically", NotificationManager.IMPORTANCE_LOW))
      }
      val open = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)
      val pi = PendingIntent.getActivity(ctx, 2, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
      val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(ctx, CHANNEL) else Notification.Builder(ctx)
      nm.notify(NOTE_ID, b.setContentTitle("Sending copies automatically is paused")
        .setContentText(text)
        .setStyle(Notification.BigTextStyle().bigText(text))
        .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
        .setContentIntent(pi)
        .setAutoCancel(true)
        .build())
    } catch (e: Throwable) {
    }
  }

  private fun trigger(ctx: Context) {
    val now = SystemClock.elapsedRealtime()
    if (now < ownUntil || now - lastTrigger < 400) return     // our own paste, or the same copy logged twice
    lastTrigger = now
    try {
      ctx.startActivity(Intent(ctx, ClipboardActivity::class.java)
        .putExtra(ClipboardActivity.AUTO, true)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION))
    } catch (e: Throwable) {
      Log.w("WinLink", "clipboard watcher: couldn't read the clipboard: ${e.message}")
    }
  }
}
