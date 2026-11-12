package expo.modules.winlinknative

import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.widget.Toast
import java.io.ByteArrayOutputStream

/** Android only lets the app in front read the clipboard, so the tile and the notification
 * button open this invisible screen for a moment: it reads the clipboard and hands it to WinLink.
 * ClipWatcher opens it too (AUTO), after each copy: then it says nothing and only sends. */
class ClipboardActivity : Activity() {
  companion object {
    const val AUTO = "auto"
  }

  private var done = false
  private val auto get() = intent?.getBooleanExtra(AUTO, false) == true

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    // Some phones never give a screen opened from the background the focus: don't stay around.
    Handler(Looper.getMainLooper()).postDelayed({ if (!done) { done = true; finish() } }, 2000)
  }

  override fun onWindowFocusChanged(hasFocus: Boolean) {
    super.onWindowFocusChanged(hasFocus)
    if (!hasFocus || done) return
    done = true
    val body = read()
    if (auto) {
      if (body != null) Bridge.send("onClipboardShare", body + ("auto" to true))   // WinLink not running: nothing to send to
    } else if (body == null) {
      Toast.makeText(this, "The clipboard is empty", Toast.LENGTH_SHORT).show()
    } else if (Bridge.send("onClipboardShare", body)) {
      Toast.makeText(this, "Sending the clipboard…", Toast.LENGTH_SHORT).show()
    } else {
      Bridge.pendingClip = body                // WinLink isn't running: open it, it sends on start
      packageManager.getLaunchIntentForPackage(packageName)?.let { startActivity(it) }
    }
    finish()
    if (auto) overridePendingTransition(0, 0)
  }

  private fun read(): Map<String, Any?>? {
    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val clip = cm.primaryClip ?: return null
    if (clip.itemCount == 0) return null
    ClipImages.read(this)?.let { return it }           // a photo or other picture
    val item = clip.getItemAt(0)
    val text = item.coerceToText(this)?.toString() ?: return null
    if (text.isEmpty()) return null
    return mapOf("kind" to "text", "text" to text)
  }
}
