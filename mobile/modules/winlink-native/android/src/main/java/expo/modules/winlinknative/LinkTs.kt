package expo.modules.winlinknative

import android.content.Context
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

/**
 * The built-in Tailscale connection: runs the same linkts helper the computers use (helper/main.go)
 * as a child process. It's packaged as liblinkts.so because Android only lets apps run programs
 * from their native library folder. WinLink talks to it over a private localhost port.
 */
object LinkTs {
  private var proc: Process? = null

  val running: Boolean
    @Synchronized get() = proc?.isAlive == true

  /** Starts the helper and returns its first line ("READY <port>" or "ERROR ..."). */
  @Synchronized
  fun start(ctx: Context, args: List<String>, env: Map<String, String>): String {
    stop()
    val exe = File(ctx.applicationInfo.nativeLibraryDir, "liblinkts.so")
    if (!exe.exists()) return "ERROR Tailscale isn't included in this build of WinLink"
    val dir = File(ctx.filesDir, "tailscale").apply { mkdirs() }
    val pb = ProcessBuilder(listOf(exe.absolutePath, "-dir", dir.absolutePath) + args)
      .directory(dir)
      .redirectError(File(ctx.cacheDir, "linkts.log"))
    pb.environment().apply {
      putAll(env)
      put("HOME", dir.absolutePath)
      put("TMPDIR", ctx.cacheDir.absolutePath)
      putIfAbsent("TS_DEBUG_MTU", "1180")      // same as the computers: fits every mobile network
    }
    val p = try {
      pb.start()
    } catch (e: Throwable) {
      return "ERROR couldn't start Tailscale: ${e.message}"
    }
    proc = p
    val out = BufferedReader(InputStreamReader(p.inputStream))
    var first: String? = null
    val reader = Thread {
      first = try { out.readLine() } catch (e: Throwable) { null }
      try { while (out.readLine() != null) { /* keep the pipe drained */ } } catch (e: Throwable) {}
    }.apply { isDaemon = true; start() }
    reader.join(20_000)
    val line = first
    if (line == null || !line.startsWith("READY ")) {
      stop()
      return line ?: "ERROR Tailscale didn't start in time"
    }
    return line
  }

  /** The helper quits when its stdin closes; force it if it doesn't. */
  @Synchronized
  fun stop() {
    val p = proc ?: return
    proc = null
    try { p.outputStream.close() } catch (e: Throwable) {}
    try {
      if (!p.waitFor(2, TimeUnit.SECONDS)) p.destroyForcibly()
    } catch (e: Throwable) {
      p.destroyForcibly()
    }
  }

  /** Sign-out: the helper wipes its own state; this removes anything left in the folder. */
  fun wipe(ctx: Context) {
    stop()
    File(ctx.filesDir, "tailscale").deleteRecursively()
  }
}
