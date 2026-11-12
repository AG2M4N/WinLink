package expo.modules.winlinknative

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.Base64
import androidx.core.content.FileProvider
import java.io.ByteArrayOutputStream
import java.io.File

/** Our own FileProvider (a subclass, so it never clashes with another library's). */
class ClipImageProvider : FileProvider()

/**
 * Pictures on Android's clipboard. A photo copied in Gallery or Google Photos is a content:// link,
 * not pixels: read it and send it as it was taken (JPEG or PNG), converting other kinds (HEIC, WebP)
 * to JPEG, and scaling down only a very large one. A picture from a computer is written to a file and
 * put on the clipboard as a link to it, which is how Android apps paste pictures.
 */
object ClipImages {
  private const val RAW_MAX = 30 * 1024 * 1024        // send a JPEG or PNG as it is up to this size
  private const val LONG_SIDE = 4096                  // otherwise (or converted): at most this many pixels

  fun read(ctx: Context): Map<String, Any?>? {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val clip = cm.primaryClip ?: return null
    val desc = clip.description
    for (i in 0 until clip.itemCount) {
      val uri = clip.getItemAt(i).uri ?: continue
      val type = (try { ctx.contentResolver.getType(uri) } catch (e: Throwable) { null })
        ?: (0 until (desc?.mimeTypeCount ?: 0)).map { desc!!.getMimeType(it) }.firstOrNull { it.startsWith("image/") }
        ?: continue
      if (!type.startsWith("image/")) continue
      picture(ctx, uri, type)?.let { return it }
    }
    return null
  }

  private fun picture(ctx: Context, uri: Uri, type: String): Map<String, Any?>? {
    val kind = when (type.lowercase()) { "image/jpeg", "image/jpg" -> "jpeg"; "image/png" -> "png"; else -> null }
    if (kind != null) {
      val bytes = try { readUpTo(ctx, uri, RAW_MAX) } catch (e: Throwable) { null }
      if (bytes != null && looksLike(bytes, kind)) return mapOf("kind" to "image", kind to b64(bytes))
    }
    // something else (HEIC, WebP, GIF...) or too big: decode, scaled to LONG_SIDE, as JPEG
    return try {
      val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
      ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
      if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
      var sample = 1
      while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= LONG_SIDE) sample *= 2
      val opts = BitmapFactory.Options().apply { inSampleSize = sample }
      var bmp = ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) } ?: return null
      val long = maxOf(bmp.width, bmp.height)
      if (long > LONG_SIDE) {
        val k = LONG_SIDE.toFloat() / long
        bmp = Bitmap.createScaledBitmap(bmp, (bmp.width * k).toInt(), (bmp.height * k).toInt(), true)
      }
      val out = ByteArrayOutputStream()
      val alpha = bmp.hasAlpha() && type.lowercase() != "image/heic" && type.lowercase() != "image/heif"
      bmp.compress(if (alpha) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, 92, out)
      mapOf("kind" to "image", (if (alpha) "png" else "jpeg") to b64(out.toByteArray()))
    } catch (e: Throwable) {
      android.util.Log.w("WinLink", "clipboard: couldn't read the picture: $e")
      null
    }
  }

  private fun readUpTo(ctx: Context, uri: Uri, max: Int): ByteArray? =
    ctx.contentResolver.openInputStream(uri)?.use { input ->
      val out = ByteArrayOutputStream()
      val buf = ByteArray(1 shl 16)
      while (true) {
        val n = input.read(buf)
        if (n < 0) break
        out.write(buf, 0, n)
        if (out.size() > max) return null
      }
      out.toByteArray()
    }

  private fun looksLike(b: ByteArray, kind: String) = if (kind == "jpeg")
    b.size > 3 && b[0] == 0xFF.toByte() && b[1] == 0xD8.toByte() && b[2] == 0xFF.toByte()
  else b.size > 8 && b[0] == 0x89.toByte() && b[1] == 'P'.code.toByte() && b[2] == 'N'.code.toByte()

  private fun b64(b: ByteArray) = Base64.encodeToString(b, Base64.NO_WRAP)

  /** A picture from a computer onto the clipboard. kind: "jpeg" or "png". */
  fun write(ctx: Context, data: String, kind: String) {
    val bytes = Base64.decode(data, Base64.DEFAULT)
    val dir = File(ctx.cacheDir, "winlink-clip").apply { mkdirs() }
    dir.listFiles()?.forEach { it.delete() }                 // only the latest one is kept
    val ext = if (kind == "jpeg") "jpg" else "png"
    val file = File(dir, "WinLink-${System.currentTimeMillis()}.$ext")
    file.writeBytes(bytes)
    val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".winlinkclip", file)
    val mime = if (kind == "jpeg") "image/jpeg" else "image/png"
    val clip = ClipData(ClipDescription("Image", arrayOf(mime)), ClipData.Item(uri))
    (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(clip)
  }
}
