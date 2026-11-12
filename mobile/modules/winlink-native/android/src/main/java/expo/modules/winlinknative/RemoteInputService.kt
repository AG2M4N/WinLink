package expo.modules.winlinknative

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ComponentName
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Point
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * "Use this phone with your computer's mouse and keyboard" (kvm_phone.py on the computer).
 *
 * The computer's Deskflow treats the phone as one more screen; the computer passes the pointer, the
 * buttons, the wheel and the keys on as {"type": "remote", ...} messages, and this service acts on
 * them: it draws a pointer on top of everything, taps and swipes with dispatchGesture, types into
 * the focused text box (Android 13+: as a real keyboard, through the accessibility input method;
 * older: by editing the box's text) and presses Back, Home and Recents.
 */
class RemoteInputService : AccessibilityService() {
  companion object {
    @Volatile var instance: RemoteInputService? = null
    private val main = Handler(Looper.getMainLooper())

    // Deskflow's modifier bits and the keys WinLink acts on (deskflow/KeyTypes.h)
    const val SHIFT = 0x1; const val CTRL = 0x2; const val ALT = 0x4; const val META = 0x8; const val SUPER = 0x10
    const val ALTGR = 0x20
    const val K_BACKSPACE = 0xEF08; const val K_TAB = 0xEF09; const val K_RETURN = 0xEF0D; const val K_ESCAPE = 0xEF1B
    const val K_DELETE = 0xEFFF; const val K_HOME = 0xEF50; const val K_LEFT = 0xEF51; const val K_UP = 0xEF52
    const val K_RIGHT = 0xEF53; const val K_DOWN = 0xEF54; const val K_PAGEUP = 0xEF55; const val K_PAGEDOWN = 0xEF56
    const val K_END = 0xEF57; const val K_KP_ENTER = 0xEF8D; const val K_SUPER_L = 0xEFEB; const val K_SUPER_R = 0xEFEC
    const val K_MENU = 0xEF67; const val K_F5 = 0xEFC2
    const val K_BACK = 0xE0A6; const val K_MUTE = 0xE0AD; const val K_VOL_DOWN = 0xE0AE; const val K_VOL_UP = 0xE0AF
    const val K_NEXT = 0xE0B0; const val K_PREV = 0xE0B1; const val K_STOP = 0xE0B2; const val K_PLAY = 0xE0B3

    fun enabled(ctx: Context): Boolean {
      val list = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
      val me = ComponentName(ctx, RemoteInputService::class.java)
      return list.split(':').any { ComponentName.unflattenFromString(it) == me }
    }

    /** In this APK at all? WinLink-Basic.apk leaves the service out of its manifest. */
    fun available(ctx: Context): Boolean = try {
      ctx.packageManager.getServiceInfo(ComponentName(ctx, RemoteInputService::class.java), 0)
      true
    } catch (e: Throwable) {
      false
    }

    /** For the app and the computers: is it on, and the screen size the pointer moves in. */
    fun status(ctx: Context): Map<String, Any?> {
      val s = instance
      val (w, h) = s?.shape() ?: Pair(0, 0)
      return mapOf("available" to available(ctx), "on" to (s != null), "enabled" to enabled(ctx), "w" to w, "h" to h,
        "keyboard" to (Build.VERSION.SDK_INT >= 33))
    }

    fun handle(msg: Map<String, Any?>) {
      val s = instance ?: return
      main.post { try { s.onRemote(msg) } catch (e: Throwable) { android.util.Log.w("WinLink", "remote input: $e") } }
    }

    private fun changed() {
      main.post { Bridge.send("onRemoteStatus", mapOf()) }
    }
  }

  private lateinit var wm: WindowManager
  private var pointer: View? = null
  private var pointerParams: WindowManager.LayoutParams? = null
  private var scale = 1f
  private var px = 0f
  private var py = 0f
  private var inside = false

  // left button: a tap, a long press or a drag, decided when it's let go
  private var leftDown = false
  private var downAt = 0L
  private var firstMoveAt = 0L
  private val trail = ArrayList<FloatArray>()
  // the wheel, gathered for a moment into one swipe
  private var wheelX = 0
  private var wheelY = 0
  private var wheelPosted = false
  // one gesture at a time (a new one would cancel the one under way)
  private var gesturing = false
  private val waiting = ArrayDeque<() -> Unit>()
  private var superAlone = false

  override fun onServiceConnected() {
    super.onServiceConnected()
    wm = getSystemService(WINDOW_SERVICE) as WindowManager
    scale = max(1f, resources.displayMetrics.density / 1.5f)
    instance = this
    changed()
  }

  override fun onUnbind(intent: android.content.Intent?): Boolean {
    leave()
    instance = null
    changed()
    return super.onUnbind(intent)
  }

  override fun onDestroy() {
    leave()
    if (instance === this) instance = null
    changed()
    super.onDestroy()
  }

  override fun onConfigurationChanged(newConfig: Configuration) {
    super.onConfigurationChanged(newConfig)
    scale = max(1f, resources.displayMetrics.density / 1.5f)
    changed()                                   // turned: the computer gets the new size
  }

  override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
  override fun onInterrupt() {}

  // ---- the screen
  private fun realSize(): Point {
    if (Build.VERSION.SDK_INT >= 30) {
      val b = wm.currentWindowMetrics.bounds
      return Point(b.width(), b.height())
    }
    val p = Point()
    @Suppress("DEPRECATION") wm.defaultDisplay.getRealSize(p)
    return p
  }

  /** The size Deskflow moves the pointer in: the screen in pixels, divided so that crossing it takes
   * about as much mouse movement as a computer screen. */
  fun shape(): Pair<Int, Int> {
    val p = realSize()
    return Pair((p.x / scale).roundToInt(), (p.y / scale).roundToInt())
  }

  private fun dp(v: Float) = v * resources.displayMetrics.density

  // ---- what the computer sends
  fun onRemote(m: Map<String, Any?>) {
    fun num(k: String) = (m[k] as? Number)?.toInt() ?: 0
    when (m["e"]) {
      "enter" -> { enter(); moveTo(num("x"), num("y")) }
      "leave" -> leave()
      "move" -> moveTo(num("x"), num("y"))
      "down" -> button(num("b"), true)
      "up" -> button(num("b"), false)
      "wheel" -> wheel(num("dx"), num("dy"))
      "key" -> key(m["k"] as? String ?: "", num("id"), num("mask"), max(1, num("n")))
    }
  }

  private fun enter() {
    inside = true
    if (pointer != null) return
    val size = dp(26f).roundToInt()
    val v = PointerView(this)
    val lp = WindowManager.LayoutParams(size, size, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
      WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
      PixelFormat.TRANSLUCENT)
    lp.gravity = Gravity.TOP or Gravity.START
    if (Build.VERSION.SDK_INT >= 30) {
      lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
      lp.setFitInsetsTypes(0)
    } else if (Build.VERSION.SDK_INT >= 28) {
      lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
    }
    try {
      wm.addView(v, lp)
      pointer = v
      pointerParams = lp
    } catch (e: Throwable) {
      android.util.Log.w("WinLink", "remote input: can't show the pointer: $e")
    }
  }

  private fun leave() {
    inside = false
    if (leftDown) button(1, false)
    pointer?.let { try { wm.removeView(it) } catch (e: Throwable) {} }
    pointer = null
    pointerParams = null
  }

  private fun moveTo(x: Int, y: Int) {
    if (!inside) enter()
    val size = realSize()
    px = (x * scale).coerceIn(0f, size.x - 1f)
    py = (y * scale).coerceIn(0f, size.y - 1f)
    val lp = pointerParams
    val v = pointer
    if (lp != null && v != null) {
      lp.x = px.roundToInt()
      lp.y = py.roundToInt()
      try { wm.updateViewLayout(v, lp) } catch (e: Throwable) {}
    }
    if (leftDown) {
      if (firstMoveAt == 0L && moved() > dp(6f)) firstMoveAt = SystemClock.uptimeMillis()
      if (trail.size < 400) trail.add(floatArrayOf(px, py))
    }
  }

  private fun moved(): Float {
    val a = trail.firstOrNull() ?: return 0f
    return hypot(px - a[0], py - a[1])
  }

  // ---- buttons
  private fun button(b: Int, down: Boolean) {
    when (b) {
      1 -> if (down) {
        leftDown = true
        downAt = SystemClock.uptimeMillis()
        firstMoveAt = 0L
        trail.clear()
        trail.add(floatArrayOf(px, py))
      } else if (leftDown) {
        leftDown = false
        release()
      }
      3 -> if (down) press(px, py, 650)                  // right-click: a long press (Android's menus)
      4 -> if (down) performGlobalAction(GLOBAL_ACTION_BACK)
      5 -> if (down) performGlobalAction(GLOBAL_ACTION_RECENTS)
    }
  }

  private fun release() {
    val now = SystemClock.uptimeMillis()
    val held = (now - downAt).coerceIn(1, 20_000)
    val start = trail.first()
    if (trail.size < 2 || firstMoveAt == 0L) {               // didn't move: a tap or a long press
      press(start[0], start[1], if (held >= 450) min(held, 3000L) else 40L)
      return
    }
    // a drag: hold where it began as long as the button was held still (picks things up), then follow
    val hold = (firstMoveAt - downAt).coerceAtLeast(0)
    val path = Path()
    path.moveTo(start[0], start[1])
    val step = max(1, trail.size / 80)
    var i = step
    while (i < trail.size) { path.lineTo(trail[i][0], trail[i][1]); i += step }
    val end = trail.last()
    path.lineTo(end[0], end[1])
    val moveMs = (now - max(firstMoveAt, downAt)).coerceIn(60, 10_000)
    if (hold >= 450) {
      val first = GestureDescription.StrokeDescription(dot(start[0], start[1]), 0, min(hold, 3000L), true)
      gesture(GestureDescription.Builder().addStroke(first).build()) {
        val rest = first.continueStroke(path, 0, moveMs, false)
        gesture(GestureDescription.Builder().addStroke(rest).build(), now = true)
      }
    } else {
      stroke(path, moveMs, still = true)
    }
  }

  private fun dot(x: Float, y: Float) = Path().apply { moveTo(x, y) }

  private fun press(x: Float, y: Float, ms: Long) {
    val s = GestureDescription.StrokeDescription(dot(x, y), 0, ms)
    gesture(GestureDescription.Builder().addStroke(s).build())
  }

  /** A swipe along path; still: stop for a moment at the end so it doesn't fling. */
  private fun stroke(path: Path, ms: Long, still: Boolean) {
    val s = GestureDescription.StrokeDescription(path, 0, ms, still)
    gesture(GestureDescription.Builder().addStroke(s).build()) {
      if (still) {
        val end = FloatArray(2)
        android.graphics.PathMeasure(path, false).let { it.getPosTan(it.length, end, null) }
        val stop = Path().apply { moveTo(end[0], end[1]); lineTo(end[0] + 0.5f, end[1]) }
        gesture(GestureDescription.Builder().addStroke(s.continueStroke(stop, 0, 90, false)).build(), now = true)
      }
    }
  }

  /** One gesture after another. then() runs when it's done (now: it goes ahead of the queue). */
  private fun gesture(g: GestureDescription, now: Boolean = false, then: (() -> Unit)? = null) {
    val run = {
      gesturing = true
      val ok = dispatchGesture(g, object : GestureResultCallback() {
        override fun onCompleted(d: GestureDescription?) { done(then) }
        override fun onCancelled(d: GestureDescription?) { done(null) }
      }, main)
      if (!ok) done(null)
    }
    if (gesturing && !now) waiting.addLast(run) else run()
  }

  private fun done(then: (() -> Unit)?) {
    gesturing = false
    if (then != null) { then(); return }
    waiting.removeFirstOrNull()?.invoke()
  }

  // ---- the wheel: a short swipe per burst of notches (up: the page moves down, like on a computer)
  private fun wheel(dx: Int, dy: Int) {
    wheelX += dx
    wheelY += dy
    if (wheelPosted) return
    wheelPosted = true
    main.postDelayed({
      wheelPosted = false
      val size = realSize()
      val step = dp(70f)
      val mx = (wheelX / 120f * step).coerceIn(-size.x * 0.4f, size.x * 0.4f)
      val my = (wheelY / 120f * step).coerceIn(-size.y * 0.4f, size.y * 0.4f)
      wheelX = 0; wheelY = 0
      if (abs(mx) < 1 && abs(my) < 1) return@postDelayed
      // start where the pointer is, moved in so the whole swipe stays on the screen
      val sx = px.coerceIn(max(0f, -mx) + 2, size.x - 2 - max(0f, mx))
      val sy = py.coerceIn(max(0f, -my) + 2, size.y - 2 - max(0f, my))
      val path = Path().apply { moveTo(sx, sy); lineTo(sx + mx, sy + my) }
      stroke(path, 140, still = true)
    }, 45)
  }

  // ---- keys
  private fun key(kind: String, id: Int, mask: Int, count: Int) {
    if (kind == "up") {
      if ((id == K_SUPER_L || id == K_SUPER_R) && superAlone) performGlobalAction(GLOBAL_ACTION_HOME)
      superAlone = false
      return
    }
    if (id == K_SUPER_L || id == K_SUPER_R) { superAlone = kind == "down"; return }
    superAlone = false
    if (id in 0xEFE1..0xEFEE || id == 0xEF7E) return               // other modifiers on their own
    val ctrl = mask and CTRL != 0
    val sup = mask and SUPER != 0
    val alt = mask and ALT != 0
    // the system's own keys
    when {
      id == K_ESCAPE || id == K_BACK -> { performGlobalAction(GLOBAL_ACTION_BACK); return }
      id == K_TAB && (alt || sup) -> { performGlobalAction(GLOBAL_ACTION_RECENTS); return }
      sup && (id == 'n'.code || id == 'N'.code) -> { performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS); return }
      sup && (id == 'a'.code || id == 'A'.code) -> { performGlobalAction(GLOBAL_ACTION_QUICK_SETTINGS); return }
      sup && (id == 'l'.code || id == 'L'.code) && Build.VERSION.SDK_INT >= 28 -> {
        performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN); return
      }
      sup && (id == 'd'.code || id == 'D'.code || id == 'h'.code || id == 'H'.code) -> {
        performGlobalAction(GLOBAL_ACTION_HOME); return
      }
      id == K_MENU -> { press(px, py, 650); return }
      id in K_MUTE..K_PLAY -> { media(id); return }
    }
    repeat(min(count, 30)) { type(id, mask, ctrl) }
  }

  private fun media(id: Int) {
    val am = getSystemService(AUDIO_SERVICE) as AudioManager
    when (id) {
      K_VOL_UP -> am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
      K_VOL_DOWN -> am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
      K_MUTE -> am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_TOGGLE_MUTE, AudioManager.FLAG_SHOW_UI)
      else -> {
        val code = when (id) {
          K_NEXT -> KeyEvent.KEYCODE_MEDIA_NEXT; K_PREV -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
          K_STOP -> KeyEvent.KEYCODE_MEDIA_STOP; else -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
        }
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
      }
    }
  }

  private fun keyCode(id: Int): Int = when (id) {
    K_BACKSPACE -> KeyEvent.KEYCODE_DEL; K_DELETE -> KeyEvent.KEYCODE_FORWARD_DEL
    K_RETURN, K_KP_ENTER -> KeyEvent.KEYCODE_ENTER; K_TAB -> KeyEvent.KEYCODE_TAB
    K_LEFT -> KeyEvent.KEYCODE_DPAD_LEFT; K_RIGHT -> KeyEvent.KEYCODE_DPAD_RIGHT
    K_UP -> KeyEvent.KEYCODE_DPAD_UP; K_DOWN -> KeyEvent.KEYCODE_DPAD_DOWN
    K_HOME -> KeyEvent.KEYCODE_MOVE_HOME; K_END -> KeyEvent.KEYCODE_MOVE_END
    K_PAGEUP -> KeyEvent.KEYCODE_PAGE_UP; K_PAGEDOWN -> KeyEvent.KEYCODE_PAGE_DOWN
    in 'a'.code..'z'.code -> KeyEvent.KEYCODE_A + (id - 'a'.code)
    in 'A'.code..'Z'.code -> KeyEvent.KEYCODE_A + (id - 'A'.code)
    in '0'.code..'9'.code -> KeyEvent.KEYCODE_0 + (id - '0'.code)
    else -> 0
  }

  private fun metaOf(mask: Int): Int {
    var m = 0
    if (mask and SHIFT != 0) m = m or KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON
    if (mask and CTRL != 0) m = m or KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON
    if (mask and ALT != 0) m = m or KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON
    return m
  }

  private fun printable(id: Int, mask: Int): String? {
    if (id < 0x20 || id in 0x7F..0x9F || id in 0xE000..0xF8FF || id > 0x10FFFF) return null
    if (mask and (CTRL or SUPER) != 0) return null
    if (mask and ALT != 0 && mask and ALTGR == 0) return null
    return String(Character.toChars(id))
  }

  /** One key press into the focused text box. */
  private fun type(id: Int, mask: Int, ctrl: Boolean) {
    if (Build.VERSION.SDK_INT >= 33) {
      val ic = inputMethod?.currentInputConnection
      if (ic != null) {
        if (ctrl) {
          val action = when (id) {
            'a'.code, 'A'.code -> android.R.id.selectAll; 'c'.code, 'C'.code -> android.R.id.copy
            'x'.code, 'X'.code -> android.R.id.cut; 'v'.code, 'V'.code -> android.R.id.paste
            else -> 0
          }
          if (action != 0) { ic.performContextMenuAction(action); return }
        }
        val text = printable(id, mask)
        if (text != null) { ic.commitText(text, 1, null); return }
        val code = keyCode(id)
        if (code != 0) {
          val t = SystemClock.uptimeMillis()
          val meta = metaOf(mask)
          ic.sendKeyEvent(KeyEvent(t, t, KeyEvent.ACTION_DOWN, code, 0, meta))
          ic.sendKeyEvent(KeyEvent(t, t, KeyEvent.ACTION_UP, code, 0, meta))
        }
        return
      }
    }
    edit(id, mask, ctrl)
  }

  /** Android 10–12 (or no keyboard connection): change the focused box's text directly. */
  private fun edit(id: Int, mask: Int, ctrl: Boolean) {
    val node = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return
    if (!node.isEditable) return
    val text = (node.text ?: "").toString().let { if (node.isShowingHintText) "" else it }
    var a = node.textSelectionStart.let { if (it < 0) text.length else it }.coerceIn(0, text.length)
    var b = node.textSelectionEnd.let { if (it < 0) a else it }.coerceIn(0, text.length)
    if (a > b) { val t = a; a = b; b = t }
    fun select(x: Int, y: Int) {
      node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
        putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, x)
        putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, y)
      })
    }
    fun replace(with: String, from: Int, to: Int) {
      val out = text.substring(0, from) + with + text.substring(to)
      node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, out)
      })
      select(from + with.length, from + with.length)
    }
    if (ctrl) {
      when (id) {
        'a'.code, 'A'.code -> select(0, text.length)
        'c'.code, 'C'.code -> node.performAction(AccessibilityNodeInfo.ACTION_COPY)
        'x'.code, 'X'.code -> node.performAction(AccessibilityNodeInfo.ACTION_CUT)
        'v'.code, 'V'.code -> node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
      }
      return
    }
    val s = printable(id, mask)
    when {
      s != null -> replace(s, a, b)
      id == K_BACKSPACE -> if (a != b) replace("", a, b) else if (a > 0) replace("", a - 1, a)
      id == K_DELETE -> if (a != b) replace("", a, b) else if (b < text.length) replace("", a, b + 1)
      id == K_LEFT -> if (a != b) select(a, a) else select(max(0, a - 1), max(0, a - 1))
      id == K_RIGHT -> if (a != b) select(b, b) else select(min(text.length, b + 1), min(text.length, b + 1))
      id == K_HOME -> select(0, 0)
      id == K_END -> select(text.length, text.length)
      id == K_RETURN || id == K_KP_ENTER -> {
        if (Build.VERSION.SDK_INT >= 30 && !node.isMultiLine) {
          node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
        } else {
          replace("\n", a, b)
        }
      }
    }
  }

  /** The pointer: an arrow, white with a dark edge, its tip at the view's top-left corner. */
  private class PointerView(ctx: Context) : View(ctx) {
    private val d = ctx.resources.displayMetrics.density
    private val arrow = Path().apply {
      moveTo(1.5f * d, 1.5f * d); lineTo(1.5f * d, 19f * d); lineTo(6f * d, 14.8f * d); lineTo(9.2f * d, 22f * d)
      lineTo(12.2f * d, 20.7f * d); lineTo(9.1f * d, 13.6f * d); lineTo(15.2f * d, 13.6f * d); close()
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFFFFF.toInt(); style = Paint.Style.FILL }
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
      color = 0xFF111111.toInt(); style = Paint.Style.STROKE; strokeWidth = 1.4f * d; strokeJoin = Paint.Join.ROUND
    }

    override fun onDraw(c: Canvas) {
      c.drawPath(arrow, fill)
      c.drawPath(arrow, edge)
    }
  }
}
