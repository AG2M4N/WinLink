package expo.modules.winlinknative

import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
import android.graphics.BitmapFactory
import android.os.Bundle
import android.util.Base64
import android.widget.Toast
import java.io.ByteArrayOutputStream

/** Android only lets the app in front read the clipboard, so the tile and the notification
 * button open this invisible screen for a moment: it reads the clipboard and hands it to WinLink. */
class ClipboardActivity : Activity() {
  private var done = false

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
  }

  override fun onWindowFocusChanged(hasFocus: Boolean) {
    super.onWindowFocusChanged(hasFocus)
    if (!hasFocus || done) return
    done = true
    val body = read()
    if (body == null) {
      Toast.makeText(this, "The clipboard is empty", Toast.LENGTH_SHORT).show()
    } else if (Bridge.send("onClipboardShare", body)) {
      Toast.makeText(this, "Sending the clipboard…", Toast.LENGTH_SHORT).show()
    } else {
      Bridge.pendingClip = body                // WinLink isn't running: open it, it sends on start
      packageManager.getLaunchIntentForPackage(packageName)?.let { startActivity(it) }
    }
    finish()
  }

  private fun read(): Map<String, Any?>? {
    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val clip = cm.primaryClip ?: return null
    if (clip.itemCount == 0) return null
    val item = clip.getItemAt(0)
    val uri = item.uri
    val mime = clip.description?.getMimeType(0) ?: ""
    if (uri != null && mime.startsWith("image/")) {
      try {
        contentResolver.openInputStream(uri)?.use { input ->
          val bmp = BitmapFactory.decodeStream(input) ?: return@use
          val out = ByteArrayOutputStream()
          bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
          return mapOf("kind" to "image", "png" to Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP))
        }
      } catch (e: Throwable) {
      }
    }
    val text = item.coerceToText(this)?.toString() ?: return null
    if (text.isEmpty()) return null
    return mapOf("kind" to "text", "text" to text)
  }
}
