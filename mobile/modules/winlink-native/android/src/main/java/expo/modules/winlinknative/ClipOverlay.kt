package expo.modules.winlinknative

import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager

/** Reads the clipboard after a copy without opening a screen: a 1-pixel, invisible window drawn over
 * other apps ("display over other apps") takes the focus for a moment, which is what Android asks of
 * an app reading the clipboard; then it's removed.
 *
 * Why not ClipboardActivity: since Android 15, "display over other apps" only lets an app open a
 * screen from the background while it's already showing a window over other apps, so the launch is
 * blocked ("Background activity launch blocked", BAL_BLOCK; OnePlus with Android 16 does this every
 * time). Adding the window itself needs no such permission. */
object ClipOverlay {
  private const val WAIT_MS = 1500L
  private val main = Handler(Looper.getMainLooper())

  /** Starts a read; false if it can't (no overlay permission, Android before 8): use ClipboardActivity then.
   * onResult runs on the main thread with what was read (null: empty) and whether the window got the focus. */
  fun read(ctx: Context, onResult: (Map<String, Any?>?, Boolean) -> Unit): Boolean {
    if (Build.VERSION.SDK_INT < 26 || !Settings.canDrawOverlays(ctx)) return false
    val app = ctx.applicationContext
    main.post { show(app, onResult) }
    return true
  }

  private fun show(ctx: Context, onResult: (Map<String, Any?>?, Boolean) -> Unit) {
    val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    var done = false
    lateinit var view: View
    fun finish(body: Map<String, Any?>?, focused: Boolean) {
      if (done) return
      done = true
      try { wm.removeView(view) } catch (e: Throwable) {}
      onResult(body, focused)
    }
    view = object : View(ctx) {
      override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
        super.onWindowFocusChanged(hasWindowFocus)
        if (!hasWindowFocus || done) return
        val body = try { ClipboardActivity.readClip(ctx) } catch (e: Throwable) {
          Log.w("WinLink", "clipboard overlay: ${e.message}"); null
        }
        finish(body, true)
      }
    }
    val lp = WindowManager.LayoutParams(
      1, 1,
      WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
      // Focusable (no FLAG_NOT_FOCUSABLE), but not touchable, and the keyboard stays with the app below.
      WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM or
        WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
      PixelFormat.TRANSLUCENT)
    lp.gravity = Gravity.TOP or Gravity.START
    lp.title = "WinLink clipboard"
    try {
      wm.addView(view, lp)
    } catch (e: Throwable) {
      Log.w("WinLink", "clipboard overlay: couldn't add the window: ${e.message}")
      done = true
      onResult(null, false)
      return
    }
    main.postDelayed({ finish(null, false) }, WAIT_MS)         // never got the focus: give up quietly
  }
}
