package expo.modules.winlinknative

/** How the services and the clipboard activity reach the JS side (when the app is alive). */
object Bridge {
  @Volatile var module: WinlinkNativeModule? = null
  @Volatile var pendingClip: Map<String, Any?>? = null     // a clipboard share made while JS was busy starting

  fun send(name: String, body: Map<String, Any?>): Boolean {
    val m = module ?: return false
    return try {
      m.sendEvent(name, body)
      true
    } catch (e: Throwable) {
      false
    }
  }
}
