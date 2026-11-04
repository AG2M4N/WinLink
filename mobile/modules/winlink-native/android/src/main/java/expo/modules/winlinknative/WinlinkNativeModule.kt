package expo.modules.winlinknative

import android.app.Activity
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.typedarray.Uint8Array
import java.io.File
import java.io.FileInputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.NetworkInterface

private const val CAPTURE_REQUEST = 7301
private const val TAILSCALE = "com.tailscale.ipn"
private const val TICK_MS = 200L

class WinlinkNativeModule : Module() {
  private val player = SoundPlayer()
  private var capturePromise: Promise? = null
  private var multicast: WifiManager.MulticastLock? = null
  private var netCallback: ConnectivityManager.NetworkCallback? = null

  // Main thread: it keeps running with WinLink in the background (the service's wake lock keeps the
  // CPU up while connected), unlike React Native's own timers, which wait for the screen.
  private val tickHandler = android.os.Handler(android.os.Looper.getMainLooper())
  @Volatile private var ticking = false
  private val tick: Runnable = object : Runnable {
    override fun run() {
      if (!ticking) return
      try { sendEvent("onTick", mapOf<String, Any?>()) } catch (e: Throwable) {}
      tickHandler.postDelayed(this, TICK_MS)
    }
  }

  private val context: Context
    get() = appContext.reactContext ?: throw CodedException("NO_CONTEXT", "WinLink isn't ready yet", null)

  override fun definition() = ModuleDefinition {
    Name("WinlinkNative")
    Events("onPcm", "onCapture", "onClipboardShare", "onNetwork", "onTick", "onScreen")

    OnCreate {
      Bridge.module = this@WinlinkNativeModule
      watchNetwork()
      try { ClipWatcher.sync(context) } catch (e: Throwable) {}
    }
    OnDestroy {
      if (Bridge.module === this@WinlinkNativeModule) Bridge.module = null
      ticking = false
      tickHandler.removeCallbacksAndMessages(null)
      player.stop()
      netCallback?.let { cb ->
        try { (context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(cb) } catch (e: Throwable) {}
      }
      netCallback = null
    }

    // ---- sound out
    Function("playerStart") { rate: Int, channels: Int, format: String, bufferMs: Int ->
      player.start(rate, channels, format, bufferMs)
    }
    Function("playerWrite") { data: Uint8Array ->
      val buf = data.toDirectBuffer()
      player.write(buf, data.byteLength)
    }
    Function("playerStop") { player.stop() }
    Function("playerVolume") { v: Double -> player.volume = v.toFloat().coerceIn(0f, 1f) }
    Function("playerLevel") { player.level.toDouble() }
    Function("playerStatus") {
      mapOf("written" to player.written.toDouble(), "errors" to player.errors, "lastError" to player.lastError,
        "lowLatency" to player.lowLatency, "delayMs" to player.queuedMs, "targetMs" to player.targetMs)
    }
    Function("playerTune") { adaptive: Boolean, floorMs: Int -> player.tune(adaptive, floorMs) }

    // ---- sound capture (Android 10+)
    Function("canCapture") { Build.VERSION.SDK_INT >= 29 }
    AsyncFunction("startCapture") { promise: Promise ->
      if (Build.VERSION.SDK_INT < 29) {
        promise.reject(CodedException("UNSUPPORTED", "sharing a phone's sound needs Android 10 or later", null))
        return@AsyncFunction
      }
      val activity = appContext.currentActivity
      if (activity == null) {
        promise.reject(CodedException("NO_ACTIVITY", "open WinLink to start sharing sound", null))
        return@AsyncFunction
      }
      val mpm = activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
      capturePromise = promise
      activity.startActivityForResult(mpm.createScreenCaptureIntent(), CAPTURE_REQUEST)
    }
    OnActivityResult { _, payload ->
      if (payload.requestCode != CAPTURE_REQUEST) return@OnActivityResult
      val promise = capturePromise ?: return@OnActivityResult
      capturePromise = null
      if (payload.resultCode != Activity.RESULT_OK || payload.data == null) {
        promise.reject(CodedException("DENIED", "permission to share the phone's sound wasn't given", null))
        return@OnActivityResult
      }
      val ctx = context
      val intent = Intent(ctx, CaptureService::class.java)
        .putExtra("code", payload.resultCode).putExtra("data", payload.data)
      if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(intent) else ctx.startService(intent)
      promise.resolve(mapOf("rate" to CaptureService.RATE, "channels" to 2))
    }
    Function("stopCapture") {
      val ctx = context
      ctx.stopService(Intent(ctx, CaptureService::class.java))
    }

    // ---- staying connected in the background
    Function("startKeepAlive") { text: String ->
      val ctx = context
      val intent = Intent(ctx, LinkService::class.java).putExtra("text", text)
      try {
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(intent) else ctx.startService(intent)
        true
      } catch (e: Throwable) {
        false              // Android refuses while the app is in the background: try again when it's open
      }
    }
    Function("stopKeepAlive") {
      val ctx = context
      if (!LinkService.running) return@Function null
      // Ask the service to stop itself (it finishes going foreground first, which Android insists on).
      try {
        ctx.startService(Intent(ctx, LinkService::class.java).setAction(LinkService.ACTION_STOP))
      } catch (e: Throwable) {        // the app is in the background and the service isn't running
        ctx.stopService(Intent(ctx, LinkService::class.java))
      }
    }
    Function("holdMulticast") { on: Boolean ->
      val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
      if (on && multicast == null) {
        multicast = wifi.createMulticastLock("winlink-bonjour").apply {
          setReferenceCounted(false)
          acquire()
        }
      } else if (!on) {
        multicast?.release()
        multicast = null
      }
    }

    // ---- Tailscale (the app does the networking; WinLink only needs to know whether it's on)
    Function("tailscaleState") {
      mapOf("installed" to isInstalled(TAILSCALE), "ips" to tailscaleIps())
    }
    Function("openTailscale") {
      val ctx = context
      val launch = ctx.packageManager.getLaunchIntentForPackage(TAILSCALE)
      val intent = launch ?: Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$TAILSCALE"))
      intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      try {
        ctx.startActivity(intent)
      } catch (e: Throwable) {        // no Play Store: open the web page
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$TAILSCALE"))
          .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
      }
      launch != null
    }

    // ---- built-in Tailscale (the helper process, see LinkTs.kt)
    AsyncFunction("tsStart") { args: List<String>, env: Map<String, String> ->
      LinkTs.start(context, args, env)
    }
    Function("tsStop") { LinkTs.stop() }
    Function("tsRunning") { LinkTs.running }
    Function("tsWipe") { LinkTs.wipe(context) }

    // ---- running in the background
    Function("backgroundStatus") {
      val ctx = context
      val pm = ctx.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
      mapOf("unrestricted" to pm.isIgnoringBatteryOptimizations(ctx.packageName), "serviceRunning" to LinkService.running)
    }
    Function("allowBackground") {
      val ctx = context
      // Android's own "Let WinLink always run in the background?" question; its settings list otherwise.
      val ask = Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      try {
        ctx.startActivity(ask)
      } catch (e: Throwable) {
        ctx.startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
      }
    }

    // ---- clipboard shared from the tile / notification while JS was starting
    Function("takePendingClip") {
      val c = Bridge.pendingClip
      Bridge.pendingClip = null
      c
    }

    // ---- battery: keep the phone awake with the screen off only while this is true (see LinkService)
    Function("setBusy") { on: Boolean -> LinkService.setBusy(on) }

    // ---- a clock for JS timers in the background (React Native stops them there; see src/bgtimers.ts)
    Function("setTicking") { on: Boolean ->
      tickHandler.post {
        if (on && !ticking) {
          ticking = true
          tickHandler.postDelayed(tick, TICK_MS)
        } else if (!on) {
          ticking = false
          tickHandler.removeCallbacks(tick)
        }
      }
    }

    // ---- sending copies automatically (see ClipWatcher)
    Function("autoClipStatus") {
      val ctx = context
      ClipWatcher.sync(ctx)               // picks up permissions given since (e.g. over ADB)
      mapOf("on" to ClipWatcher.enabled(ctx), "logs" to ClipWatcher.hasLogs(ctx), "overlay" to ClipWatcher.hasOverlay(ctx),
        "running" to ClipWatcher.running, "package" to ctx.packageName)
    }
    Function("setAutoClip") { on: Boolean -> ClipWatcher.setEnabled(context, on) }
    Function("markOwnClip") { ClipWatcher.markOwn() }
    Function("openOverlaySettings") {
      val ctx = context
      val i = Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${ctx.packageName}"))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      try {
        ctx.startActivity(i)
      } catch (e: Throwable) {
        ctx.startActivity(Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
      }
    }

    // ---- received files
    AsyncFunction("saveToDownloads") { path: String, name: String, sub: String ->
      saveToDownloads(path, name, sub)
    }
    AsyncFunction("deleteSaved") { uri: String ->
      val ctx = context
      if (uri.startsWith("content://")) ctx.contentResolver.delete(Uri.parse(uri), null, null) > 0
      else File(uri.removePrefix("file://")).delete()
    }
    AsyncFunction("openSaved") { uri: String, mime: String? ->
      val ctx = context
      val target = if (uri.startsWith("content://")) Uri.parse(uri)
        else FileProvider.getUriForFile(ctx, ctx.packageName + ".FileSystemFileProvider", File(uri.removePrefix("file://")))
      val type = mime ?: MimeTypeMap.getSingleton().getMimeTypeFromExtension(uri.substringAfterLast('.', "").lowercase()) ?: "*/*"
      val intent = Intent(Intent.ACTION_VIEW).setDataAndType(target, type)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
      ctx.startActivity(Intent.createChooser(intent, "Open with").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
  }

  /** Tell JS when the phone's network changes (Wi-Fi back, switched to mobile data...) so it retries now. */
  private fun watchNetwork() {
    try {
      val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
      val cb = object : ConnectivityManager.NetworkCallback() {
        private var last = ""
        private fun tell(up: Boolean, wifi: Boolean) {
          val now = "$up/$wifi"
          if (now == last) return
          last = now
          try { sendEvent("onNetwork", mapOf("up" to up, "wifi" to wifi)) } catch (e: Throwable) {}
        }
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
          tell(caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET), caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))
        }
        override fun onLost(network: Network) = tell(false, false)
      }
      cm.registerDefaultNetworkCallback(cb)
      netCallback = cb
    } catch (e: Throwable) { /* no network service: reconnects still happen on their own schedule */ }
  }

  private fun isInstalled(pkg: String): Boolean = try {
    context.packageManager.getPackageInfo(pkg, 0)
    true
  } catch (e: Throwable) {
    false
  }

  /** This phone's Tailscale addresses (100.64.0.0/10 and fd7a:115c:a1e0::/48), IPv4 first. */
  private fun tailscaleIps(): List<String> {
    val out = mutableListOf<String>()
    try {
      for (nif in NetworkInterface.getNetworkInterfaces() ?: return out) {
        if (!nif.isUp) continue
        for (a in nif.inetAddresses) {
          val b = a.address
          if (a is Inet4Address && b[0] == 100.toByte() && (b[1].toInt() and 0xC0) == 64) out.add(0, a.hostAddress ?: continue)
          else if (a is Inet6Address && b[0] == 0xfd.toByte() && b[1] == 0x7a.toByte() && b[2] == 0x11.toByte()
                   && b[3] == 0x5c.toByte() && b[4] == 0xa1.toByte() && b[5] == 0xe0.toByte()) out.add((a.hostAddress ?: continue).substringBefore('%'))
        }
      }
    } catch (e: Throwable) { /* no permission or no network: report nothing */ }
    return out.sortedBy { it.contains(':') }
  }

  /** Copy a finished download into Downloads/WinLink (where people look for it) and return its URI. */
  private fun saveToDownloads(path: String, name: String, sub: String): String {
    val ctx = context
    val src = File(path.removePrefix("file://"))
    val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
      ?: "application/octet-stream"
    val folder = listOf(Environment.DIRECTORY_DOWNLOADS, "WinLink", sub).filter { it.isNotEmpty() }.joinToString("/")
    if (Build.VERSION.SDK_INT >= 29) {
      val values = ContentValues().apply {
        put(MediaStore.Downloads.DISPLAY_NAME, name)
        put(MediaStore.Downloads.MIME_TYPE, mime)
        put(MediaStore.Downloads.RELATIVE_PATH, folder)
        put(MediaStore.Downloads.IS_PENDING, 1)
      }
      val resolver = ctx.contentResolver
      val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
        ?: throw CodedException("SAVE_FAILED", "Android didn't make room in Downloads", null)
      try {
        resolver.openOutputStream(uri)?.use { out -> FileInputStream(src).use { it.copyTo(out, 1 shl 20) } }
          ?: throw CodedException("SAVE_FAILED", "couldn't write to Downloads", null)
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
      } catch (e: Throwable) {
        resolver.delete(uri, null, null)
        throw e
      }
      src.delete()
      return uri.toString()
    }
    // Android 9 and older: the app's own Downloads folder (no extra permission needed)
    val dir = File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), sub)
    dir.mkdirs()
    var dest = File(dir, name)
    var n = 2
    while (dest.exists()) {
      dest = File(dir, name.substringBeforeLast('.') + " ($n)" + (if (name.contains('.')) "." + name.substringAfterLast('.') else ""))
      n++
    }
    if (!src.renameTo(dest)) {
      src.copyTo(dest)
      src.delete()
    }
    return dest.absolutePath
  }
}
