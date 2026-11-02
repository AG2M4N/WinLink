import AVFoundation
import ExpoModulesCore
import Network

/// iPhone side of WinLink's native helpers: playing a live sound stream with a small, fixed delay
/// (it keeps playing in the background, like a music app), and keeping received files where the
/// Files app shows them. iPhones can't capture other apps' sound, and don't need a keep-alive service.
public class WinlinkNativeModule: Module {
  private let player = StreamPlayer()
  private var monitor: NWPathMonitor?

  public func definition() -> ModuleDefinition {
    Name("WinlinkNative")
    Events("onPcm", "onCapture", "onClipboardShare", "onNetwork")

    // Tell JS when the network changes (Wi-Fi back, switched to mobile data...) so it retries now
    OnCreate {
      let m = NWPathMonitor()
      var last = ""
      m.pathUpdateHandler = { [weak self] path in
        let up = path.status == .satisfied, wifi = path.usesInterfaceType(.wifi)
        let now = "\(up)/\(wifi)"
        if now == last { return }
        last = now
        self?.sendEvent("onNetwork", ["up": up, "wifi": wifi])
      }
      m.start(queue: DispatchQueue(label: "winlink.network"))
      self.monitor = m
    }
    OnDestroy {
      self.monitor?.cancel()
      self.monitor = nil
    }

    Function("playerStart") { (rate: Int, channels: Int, format: String, bufferMs: Int) in
      try self.player.start(rate: rate, channels: channels, isFloat: format == "f32", bufferMs: bufferMs)
    }
    Function("playerWrite") { (data: Uint8Array) in
      self.player.write(data.rawPointer, count: data.byteLength)
    }
    Function("playerStop") { self.player.stop() }
    Function("playerVolume") { (v: Double) in self.player.volume = Float(max(0, min(1, v))) }
    Function("playerLevel") { Double(self.player.level) }
    Function("playerStatus") { () -> [String: Any] in ["written": 0, "errors": 0, "lastError": "", "lowLatency": false, "delayMs": 0, "targetMs": 0] }
    Function("playerTune") { (_: Bool, _: Int) in }
    Function("backgroundStatus") { () -> [String: Any] in ["unrestricted": true, "serviceRunning": false] }
    Function("allowBackground") {}

    Function("canCapture") { false }
    AsyncFunction("startCapture") { () throws -> [String: Int] in
      throw Exception(name: "UNSUPPORTED", description: "iPhones don't let apps capture other apps' sound")
    }
    Function("stopCapture") {}
    Function("startKeepAlive") { (_: String) in true }
    Function("stopKeepAlive") {}
    Function("holdMulticast") { (_: Bool) in }
    // iOS can't see other apps, so only the phone's Tailscale addresses are reported
    Function("tailscaleState") { () -> [String: Any] in ["ips": tailscaleIps()] }
    Function("openTailscale") { () -> Bool in false }       // the app opens the App Store page itself
    // Built-in Tailscale isn't on iPhone yet (it needs the helper as a framework, not a program)
    AsyncFunction("tsStart") { (_: [String], _: [String: String]) -> String in "ERROR not available on iPhone yet" }
    Function("tsStop") {}
    Function("tsRunning") { false }
    Function("tsWipe") {}
    Function("takePendingClip") { () -> [String: String]? in nil }

    AsyncFunction("saveToDownloads") { (path: String, name: String, sub: String) throws -> String in
      try Self.save(path: path, name: name, sub: sub)
    }
    AsyncFunction("deleteSaved") { (uri: String) -> Bool in
      let url = URL(string: uri) ?? URL(fileURLWithPath: uri)
      return (try? FileManager.default.removeItem(at: url.isFileURL ? url : URL(fileURLWithPath: uri))) != nil
    }
    AsyncFunction("openSaved") { (_: String, _: String?) in }      // the app opens files with the share sheet
  }

  /// Move a finished download into Documents/WinLink/<sub> (visible in Files > On My iPhone > WinLink).
  static func save(path: String, name: String, sub: String) throws -> String {
    let fm = FileManager.default
    let docs = fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
    var dir = docs.appendingPathComponent("WinLink", isDirectory: true)
    if !sub.isEmpty { dir = dir.appendingPathComponent(sub, isDirectory: true) }
    try fm.createDirectory(at: dir, withIntermediateDirectories: true)
    let src = path.hasPrefix("file://") ? URL(string: path)! : URL(fileURLWithPath: path)
    var dest = dir.appendingPathComponent(name)
    let base = (name as NSString).deletingPathExtension
    let ext = (name as NSString).pathExtension
    var n = 2
    while fm.fileExists(atPath: dest.path) {
      dest = dir.appendingPathComponent(ext.isEmpty ? "\(base) (\(n))" : "\(base) (\(n)).\(ext)")
      n += 1
    }
    try fm.moveItem(at: src, to: dest)
    return dest.absoluteString
  }
}

/// AVAudioEngine player for interleaved PCM arriving from the network. It waits for bufferMs of
/// sound before starting, and drops the oldest audio instead of letting the delay grow.
final class StreamPlayer {
  private var engine: AVAudioEngine?
  private var node: AVAudioPlayerNode?
  private var format: AVAudioFormat?
  private var channels = 2
  private var isFloat = false
  private var targetFrames: AVAudioFramePosition = 0
  private var queued: AVAudioFramePosition = 0
  private var started = false
  private let lock = NSLock()
  var level: Float = 0
  var volume: Float = 1 {
    didSet { node?.volume = volume }
  }

  func start(rate: Int, channels: Int, isFloat: Bool, bufferMs: Int) throws {
    stop()
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
    try session.setActive(true)
    self.channels = channels == 1 ? 1 : 2
    self.isFloat = isFloat
    guard let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(rate),
                                  channels: AVAudioChannelCount(self.channels), interleaved: false) else {
      throw Exception(name: "FORMAT", description: "unsupported sound format")
    }
    let engine = AVAudioEngine()
    let node = AVAudioPlayerNode()
    engine.attach(node)
    engine.connect(node, to: engine.mainMixerNode, format: fmt)
    try engine.start()
    node.volume = volume
    self.engine = engine
    self.node = node
    self.format = fmt
    targetFrames = AVAudioFramePosition(max(bufferMs, 40) * rate / 1000)
    queued = 0
    started = false
  }

  func write(_ ptr: UnsafeMutableRawPointer, count: Int) {
    guard let node = node, let fmt = format else { return }
    let bytesPerSample = isFloat ? 4 : 2
    let frames = count / (bytesPerSample * channels)
    if frames <= 0 { return }
    lock.lock()
    let backlog = queued
    lock.unlock()
    if started && backlog > targetFrames * 3 { return }          // fell behind: drop this chunk
    guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(frames)) else { return }
    buf.frameLength = AVAudioFrameCount(frames)
    var peak: Float = 0
    for c in 0..<channels {
      let out = buf.floatChannelData![c]
      if isFloat {
        let src = ptr.bindMemory(to: Float.self, capacity: frames * channels)
        for i in 0..<frames { let v = src[i * channels + c]; out[i] = v; peak = max(peak, abs(v)) }
      } else {
        let src = ptr.bindMemory(to: Int16.self, capacity: frames * channels)
        for i in 0..<frames { let v = Float(src[i * channels + c]) / 32768; out[i] = v; peak = max(peak, abs(v)) }
      }
    }
    level = peak
    lock.lock()
    queued += AVAudioFramePosition(frames)
    lock.unlock()
    node.scheduleBuffer(buf) { [weak self] in
      guard let self = self else { return }
      self.lock.lock()
      self.queued -= AVAudioFramePosition(frames)
      self.lock.unlock()
    }
    if !started {
      lock.lock()
      let ready = queued >= targetFrames
      lock.unlock()
      if ready {
        node.play()
        started = true
      }
    }
  }

  func stop() {
    node?.stop()
    engine?.stop()
    node = nil
    engine = nil
    format = nil
    level = 0
  }
}


/// This phone's Tailscale addresses (100.64.0.0/10 and fd7a:115c:a1e0::/48), IPv4 first.
private func tailscaleIps() -> [String] {
  var v4: [String] = [], v6: [String] = []
  var list: UnsafeMutablePointer<ifaddrs>?
  guard getifaddrs(&list) == 0, let first = list else { return [] }
  defer { freeifaddrs(list) }
  var cur: UnsafeMutablePointer<ifaddrs>? = first
  while let p = cur {
    defer { cur = p.pointee.ifa_next }
    guard let sa = p.pointee.ifa_addr, (p.pointee.ifa_flags & UInt32(IFF_UP)) != 0 else { continue }
    var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
    if sa.pointee.sa_family == UInt8(AF_INET) {
      let a = sa.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { $0.pointee.sin_addr.s_addr }
      let b0 = UInt8(a & 0xff), b1 = UInt8((a >> 8) & 0xff)
      guard b0 == 100, (b1 & 0xC0) == 64 else { continue }
      guard getnameinfo(sa, socklen_t(MemoryLayout<sockaddr_in>.size), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 else { continue }
      v4.append(String(cString: host))
    } else if sa.pointee.sa_family == UInt8(AF_INET6) {
      let b = sa.withMemoryRebound(to: sockaddr_in6.self, capacity: 1) { s6 -> [UInt8] in
        withUnsafeBytes(of: s6.pointee.sin6_addr) { Array($0.prefix(6)) }
      }
      guard b == [0xfd, 0x7a, 0x11, 0x5c, 0xa1, 0xe0] else { continue }
      guard getnameinfo(sa, socklen_t(MemoryLayout<sockaddr_in6>.size), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 else { continue }
      v6.append(String(cString: host).components(separatedBy: "%")[0])
    }
  }
  return v4 + v6
}
