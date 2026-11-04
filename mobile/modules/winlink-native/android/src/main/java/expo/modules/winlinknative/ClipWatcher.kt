package expo.modules.winlinknative

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
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
 * Only the ClipboardService lines are read from the log, and only to spot that one line. */
object ClipWatcher {
  private const val PREFS = "winlink"
  private const val KEY = "autoClip"
  private const val TAG = "ClipboardService"

  @Volatile private var thread: Thread? = null
  @Volatile private var proc: Process? = null
  @Volatile private var ownUntil = 0L        // WinLink set the clipboard itself (from a computer): not a copy to send
  @Volatile private var lastTrigger = 0L
  private var listener: ClipboardManager.OnPrimaryClipChangedListener? = null

  fun enabled(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY, false)
  fun hasLogs(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.READ_LOGS) == PackageManager.PERMISSION_GRANTED
  fun hasOverlay(ctx: Context) = Settings.canDrawOverlays(ctx)
  val running get() = thread != null

  fun setEnabled(ctx: Context, on: Boolean) {
    ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY, on).apply()
    sync(ctx)
  }

  /** WinLink is about to set the clipboard itself: don't send that back. */
  fun markOwn() {
    ownUntil = SystemClock.elapsedRealtime() + 2000
  }

  /** Start or stop to match the setting and the permissions. Safe to call often. */
  @Synchronized
  fun sync(context: Context) {
    val ctx = context.applicationContext
    if (enabled(ctx) && hasLogs(ctx) && hasOverlay(ctx)) start(ctx) else stop(ctx)
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
    proc?.destroy()
    proc = null
    t.interrupt()
    Handler(Looper.getMainLooper()).post {
      listener?.let { (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).removePrimaryClipChangedListener(it) }
      listener = null
    }
  }

  private fun watch(ctx: Context) {
    val me = Thread.currentThread()
    val needle = "access to ${ctx.packageName}"
    var wait = 1000L
    while (thread === me) {
      try {
        // Only lines from now on (-T), only Android's clipboard service, errors and up.
        val since = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US).format(Date())
        val p = ProcessBuilder("logcat", "-T", since, "$TAG:E", "*:S").redirectErrorStream(true).start()
        proc = p
        p.inputStream.bufferedReader().use { r ->
          while (thread === me) {
            val line = r.readLine() ?: break
            if (line.contains(needle)) {
              wait = 1000L
              trigger(ctx)
            }
          }
        }
        p.destroy()
      } catch (e: Throwable) {
        Log.w("WinLink", "clipboard watcher: ${e.message}")
      }
      if (thread !== me) break
      // logcat ended (log access refused on the prompt, or the system restarted it): try again, slowly
      try { Thread.sleep(wait) } catch (e: InterruptedException) { break }
      wait = (wait * 2).coerceAtMost(60_000L)
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
