// Runs WinLink on the phone: the protocol node, Bonjour discovery, the background service,
// clipboard, shared files and sound. The screens read `snapshot()` and call the actions here.
import * as Clipboard from "expo-clipboard";
import { AppState, Linking, PermissionsAndroid, Platform as RN } from "react-native";
import Zeroconf from "react-native-zeroconf";
import Native from "../modules/winlink-native";
import { Anywhere, AnywhereView } from "./anywhere";
import { Batch, Node, NodeEvent, Peer, PORT, SoundSender } from "./core/node";
import { AppSettings, ReceivedFile, fileSource, loadSettings, logLines, platform, saveSettings, soundStats } from "./platform";

export interface DeviceView {
  id: string;
  name: string;
  os: string;
  state: "on" | "connecting" | "pairing" | "available" | "away";
  route: string | null;
  trusted: boolean;
  anywhere: boolean;          // it told us its Tailscale address, so it's reachable from other networks
  code: string | null;
  error: string;
}

export interface TransferView {
  key: string;
  dir: "in" | "out";
  name: string;
  peer: string;
  state: Batch["state"];
  frac: number;
  files: string;
  error: string | null;
}

export interface Snapshot {
  ready: boolean;
  me: { name: string; os: string };
  devices: DeviceView[];
  transfers: TransferView[];
  received: ReceivedFile[];
  clipboard: { enabled: boolean; history: ClipItem[] };
  sound: {
    canShare: boolean;
    sharing: boolean;
    shareText: string;
    shareTone: "good" | "bad" | "muted";
    target: string | null;
    playingFrom: string | null;
    playingInfo: string;
    latency: AppSettings["latency"];
    volume: number;
  };
  keepAlive: boolean;
  background: { unrestricted: boolean; running: boolean } | null;    // Android, newer builds
  tailscale: TailscaleView;
  anywhere: AnywhereView | null;
  notice: { text: string; at: number } | null;
}

export interface TailscaleView {
  installed: boolean | null;    // null: can't tell (iPhone)
  on: boolean;
  ips: string[];
}

export interface ClipItem {
  id: number;
  dir: "in" | "out";
  text: string;
  peer: string;
  at: number;
}

const LATENCY_MS: Record<string, number | null> = { auto: null, lowest: 20, low: 50, balanced: 120, smooth: 300 };

class Controller {
  node: Node | null = null;
  settings: AppSettings | null = null;
  private zc: Zeroconf | null = null;
  private listeners = new Set<() => void>();
  private clipHistory: ClipItem[] = [];
  private clipNext = 0;
  private sender: SoundSender | null = null;
  private soundText = "";
  private soundTone: "good" | "bad" | "muted" = "muted";
  private soundSharing = false;
  private playingInfo = "";
  private notice: Snapshot["notice"] = null;
  private bonjourNames = new Map<string, string>();    // service name -> device id
  private dirtyTimer: ReturnType<typeof setTimeout> | null = null;
  private keepAliveOn = false;
  private tailscale: TailscaleView = { installed: null, on: false, ips: [] };
  private anywhere: Anywhere | null = null;
  private soundCheck: ReturnType<typeof setInterval> | null = null;
  private keepAliveStop: ReturnType<typeof setTimeout> | null = null;
  private keepAliveText = "";
  private delayTick: ReturnType<typeof setInterval> | null = null;

  private background(): Snapshot["background"] {
    if (RN.OS !== "android") return null;
    try {
      const b = Native.backgroundStatus();
      return { unrestricted: b.unrestricted, running: b.serviceRunning };
    } catch {
      return null;                       // an older build
    }
  }

  allowBackground() {
    try {
      Native.allowBackground();
    } catch {
      this.setNotice("Update WinLink to change this from here (Android Settings › Apps › WinLink › Battery › Unrestricted).");
    }
  }

  /** " · 45 ms behind": how far the speaker is behind the computer right now (newer builds). */
  private delayText(): string {
    if (!this.node?.playingFrom) return "";
    try {
      const d = Native.playerStatus().delayMs;
      return typeof d === "number" && d > 0 ? ` · ${Math.round(d / 5) * 5} ms behind` : "";
    } catch {
      return "";
    }
  }

  // ---- lifecycle
  async start() {
    if (this.node) return;
    try {
      this.settings = await loadSettings();
    } catch (e) {               // storage unreadable: start fresh rather than not at all
      platform.log("error", `couldn't load settings: ${(e as Error).message}`);
      this.settings = await loadSettings(true);
    }
    this.node = new Node(this.settings, platform, (e) => this.onEvent(e));
    this.node.latencyMs = LATENCY_MS[this.settings.latency];
    Native.playerVolume(this.settings.volume);
    if (platform.soundIn) {
      this.sender = new SoundSender(this.node, platform.soundIn, () => "auto");
      this.node.soundSending = this.sender;
    }
    this.node.start(PORT);
    this.startBonjour();
    this.anywhere = new Anywhere(this.node, () => this.changed(), (url) => void Linking.openURL(url));
    this.node.tailnet = this.anywhere;
    if (this.settings.anywhere && this.anywhere.available) setTimeout(() => this.anywhere?.setOn(true), 500);   // after the port is open
    this.checkTailscale();
    setInterval(() => this.checkTailscale(), 4000);
    AppState.addEventListener("change", (s) => this.onAppState(s));
    Native.addListener("onClipboardShare", (clip) => this.shareClip(clip));
    Native.addListener("onNetwork", (e) => this.onNetwork(e.up, e.wifi));
    Native.addListener("onCapture", (e) => {
      if (e.state === "error") this.setNotice(e.error ?? "Couldn't capture the phone's sound");
    });
    const pending = Native.takePendingClip();
    if (pending) setTimeout(() => this.shareClip(pending), 3000);     // sent once a computer connects
    if (RN.OS === "android") {
      PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS).catch(() => undefined);
      Native.holdMulticast(true);
    }
    this.changed();
  }

  private onAppState(s: string) {
    if (!this.node) return;
    if (s === "active") {
      this.node.start(PORT);
      this.startBonjour();
      for (const p of this.node.peers.values()) {
        if (!p.control) p.retryAt = 0;             // back in front: try everyone straight away
      }
      this.anywhere?.nudge();
      this.updateKeepAlive();
      this.changed();                              // e.g. back from allowing background use
    } else if (s === "background" && RN.OS === "ios") {
      // iOS suspends apps soon after they leave the screen; sound keeps us alive while it plays.
      if (!this.node.playingFrom) this.node.pauseAll();
    }
  }

  // ---- the phone's network changed: retry now instead of waiting out a backoff
  private netTimer: ReturnType<typeof setTimeout> | null = null;
  private onNetwork(up: boolean, wifi: boolean) {
    platform.log("info", up ? `network: ${wifi ? "Wi‑Fi" : "mobile data"}` : "network: offline");
    if (!up || !this.node) return;
    if (this.netTimer) clearTimeout(this.netTimer);
    this.netTimer = setTimeout(() => {         // let the new network settle (DHCP, DNS) first
      this.netTimer = null;
      this.node?.networkChanged();
      if (wifi) this.rescan();
      this.anywhere?.nudge();
      this.checkTailscale();
    }, 1500);
  }

  // ---- Tailscale: the Tailscale app carries the traffic; we notice when it comes on and use it
  private checkTailscale() {
    let st: { installed?: boolean; ips: string[] };
    try {
      st = Native.tailscaleState();
    } catch {
      return;
    }
    const ips = Array.isArray(st.ips) ? st.ips : [];
    const next: TailscaleView = { installed: typeof st.installed === "boolean" ? st.installed : ips.length ? true : null,
      on: ips.length > 0, ips };
    const was = this.tailscale;
    if (next.on === was.on && next.installed === was.installed && next.ips.join() === was.ips.join()) return;
    this.tailscale = next;
    if (next.on !== was.on && this.node) {
      platform.log("info", next.on ? `Tailscale is on (${ips.join(", ")})` : "Tailscale is off");
      for (const p of this.node.peers.values()) {
        if (!p.control) p.retryAt = 0;          // a new route: try the computers that weren't reachable now
      }
    }
    this.changed();
  }

  openTailscale() {
    if (RN.OS === "ios") {
      void Linking.openURL("https://apps.apple.com/app/tailscale/id1470499037");
      return;
    }
    try {
      Native.openTailscale();             // opens the app, or its Play Store page
    } catch {                             // an older build without it
      void Linking.openURL("https://play.google.com/store/apps/details?id=com.tailscale.ipn");
    }
  }


  // ---- Bonjour
  private startBonjour() {
    try {
      if (!this.zc) {
        this.zc = new Zeroconf();
        this.zc.on("resolved", (svc) => {
          const txt = svc.txt || {};
          if (!txt.id || txt.id === this.settings?.id) return;
          const addrs = [...(svc.ipv4 ?? []), ...(svc.ipv6 ?? []).filter((a) => !a.startsWith("fe80"))];
          this.bonjourNames.set(svc.name, txt.id);
          this.node?.seen({ id: txt.id, name: txt.name || svc.name, os: txt.os || "?", port: Number(txt.port) || svc.port || PORT }, addrs);
        });
        this.zc.on("remove", (name) => {
          const id = this.bonjourNames.get(name);
          if (id) this.node?.gone(id);
        });
        this.zc.on("error", (e) => platform.log("warn", `Bonjour: ${e.message} (${e.code})`));
      } else {
        this.zc.stop();
      }
      this.zc.scan({ type: "winlink", protocol: "tcp", domain: "local." });
    } catch (e) {
      platform.log("warn", `Bonjour unavailable: ${(e as Error).message}`);
    }
  }

  rescan() {
    this.startBonjour();
  }

  // ---- events from the node
  private onEvent(e: NodeEvent) {
    switch (e.kind) {
      case "clip_in": {
        if (!this.settings?.clipboard) break;
        if (e.clip.kind === "text") {
          Clipboard.setStringAsync(e.clip.text).catch((err) => platform.log("warn", `couldn't set the clipboard: ${err}`));
          this.remember("in", e.clip.text, e.peer.name);
        } else if (e.clip.png) {
          Clipboard.setImageAsync(e.clip.png).catch((err) => platform.log("warn", `couldn't set the clipboard image: ${err}`));
          this.remember("in", "Image", e.peer.name);
        }
        break;
      }
      case "received": {
        const s = this.settings!;
        s.received = [{ uri: e.path, name: e.name, from: e.from, size: e.size, at: Date.now() }, ...s.received].slice(0, 200);
        saveSettings(s);
        break;
      }
      case "removed": {
        const s = this.settings!;
        s.received = s.received.filter((r) => !e.paths.includes(r.uri));
        saveSettings(s);
        break;
      }
      case "notice":
        this.setNotice(e.text);
        break;
      case "sound_in":
        if (this.soundCheck) clearInterval(this.soundCheck);
        this.soundCheck = null;
        if (this.delayTick) clearInterval(this.delayTick);
        this.delayTick = e.info ? setInterval(() => this.changed(), 1000) : null;     // live delay on screen
        if (e.info && e.peer) {
          platform.log("info", `sound from ${e.peer.name}: ${e.info.rate} Hz ${e.info.format} ×${e.info.channels}, `
            + `${e.info.bufferMs} ms buffer${e.info.remote ? ", via Tailscale" : ""}`);
          const report = () => {
            const secs = (Date.now() - soundStats.startedAt) / 1000;
            let level = -1;
            try {
              level = Native.playerLevel();
            } catch {
              /* older build */
            }
            const msg = `speaker: ${Math.round(soundStats.bytes / 1024)} KB played in ${secs.toFixed(0)} s as `
              + `${soundStats.playFormat}, loudness ${level.toFixed(2)}${soundStats.errors ? `, ${soundStats.errors} errors (${soundStats.lastError})` : ""}`;
            let native = "";
            try {
              const n = Native.playerStatus();
              native = ` · Android took ${Math.round(n.written / 1024)} KB${n.lowLatency ? " (low latency)" : ""}`
                + (n.errors ? `, ${n.errors} refused: ${n.lastError}` : "");
            } catch {
              /* this build doesn't report it */
            }
            platform.log(soundStats.bytes === 0 || soundStats.errors ? "warn" : "info", msg + native);
          };
          setTimeout(report, 3000);
          this.soundCheck = setInterval(report, 20000);
        }
        this.playingInfo = e.info
          ? `${e.info.rate / 1000} kHz ${e.info.format === "f32" ? "32-bit" : "16-bit"} · ${e.info.bufferMs} ms buffer${e.info.remote ? " · via Tailscale" : ""}`
          : "";
        if (e.peer && this.soundSharing) {
          this.soundSharing = false;          // its sound plays here now: stop sharing ours
          this.sender?.stop();
        }
        break;
      case "sound_out":
        if (e.state === "playing") {
          this.soundText = e.text;
          this.soundTone = "good";
        } else if (e.state === "retry" && this.soundSharing) {
          this.soundText = e.text;
          this.soundTone = "muted";
          if (!e.text.startsWith("Connecting")) setTimeout(() => this.maybeShareSound(), 3000);
        } else if (e.state === "off") {
          this.soundSharing = false;
          this.soundText = e.text;
          this.soundTone = "bad";
          this.setNotice(e.text);
        } else {
          this.soundText = "";
          this.soundTone = "muted";
        }
        break;
      case "peers":
        this.updateKeepAlive();
        if (this.soundSharing) this.maybeShareSound();
        break;
    }
    this.changed();
  }

  private remember(dir: "in" | "out", text: string, peer: string) {
    this.clipHistory.unshift({ id: ++this.clipNext, dir, text: text.slice(0, 500), peer, at: Date.now() });
    this.clipHistory = this.clipHistory.slice(0, 50);
  }

  private setNotice(text: string) {
    this.notice = { text, at: Date.now() };
    this.changed();
  }

  private updateKeepAlive() {
    if (RN.OS !== "android" || !this.node || !this.settings) return;
    const on = this.node.connected();
    const want = this.settings.keepAlive && on.length > 0;
    if (want && !this.settings.backgroundHinted) {
      const b = this.background();
      if (b && !b.unrestricted) {       // once: without this, many phones pause WinLink when you leave it
        this.settings.backgroundHinted = true;
        saveSettings(this.settings);
        this.setNotice("To stay connected when you leave WinLink, allow it in the background: Settings › In the background › Allow.");
      }
    }
    if (want) {
      if (this.keepAliveStop) clearTimeout(this.keepAliveStop);
      this.keepAliveStop = null;
      const text = `Connected to ${on.map((p) => p.name).join(", ")}`;
      if (this.keepAliveOn && text === this.keepAliveText) return;     // already showing that
      if (Native.startKeepAlive(text)) {
        this.keepAliveOn = true;
        this.keepAliveText = text;
      }
    } else if (this.keepAliveOn && !this.keepAliveStop) {
      // Connections drop and come back (Wi-Fi hiccups, moving to Tailscale): only stop the
      // background service once nothing has been connected for a while. Starting and stopping it
      // within seconds is also what makes Android end the app.
      this.keepAliveStop = setTimeout(() => {
        this.keepAliveStop = null;
        if (this.node?.connected().length && this.settings?.keepAlive) return;
        Native.stopKeepAlive();
        this.keepAliveOn = false;
        this.keepAliveText = "";
      }, this.settings.keepAlive ? 30000 : 0);
    }
  }

  // ---- subscriptions
  subscribe(f: () => void) {
    this.listeners.add(f);
    return () => this.listeners.delete(f);
  }

  private changed() {
    if (this.dirtyTimer) return;
    this.dirtyTimer = setTimeout(() => {
      this.dirtyTimer = null;
      this.listeners.forEach((f) => f());
    }, 50);
  }

  snapshot(): Snapshot {
    const node = this.node;
    const s = this.settings;
    if (!node || !s) {
      return { ready: false, me: { name: "", os: platform.os }, devices: [], transfers: [], received: [],
        clipboard: { enabled: true, history: [] }, sound: { canShare: false, sharing: false, shareText: "", shareTone: "muted",
          target: null, playingFrom: null, playingInfo: "", latency: "auto", volume: 1 }, keepAlive: true,
        background: null, tailscale: this.tailscale, anywhere: null, notice: null };
    }
    const devices: DeviceView[] = [...node.peers.values()].map((p) => ({
      id: p.id,
      name: p.name,
      os: p.os,
      state: p.control ? "on" : p.waitingCode ? "pairing" : p.connecting ? "connecting" : p.nearby ? "available" : "away",
      route: p.control?.route ?? null,
      trusted: node.isTrusted(p.id),
      anywhere: (s.trusted[p.id]?.tsAddrs?.length ?? 0) > 0,
      code: p.waitingCode,
      error: p.lastError,
    }));
    const order = { on: 0, pairing: 1, connecting: 2, available: 3, away: 4 };
    devices.sort((a, b) => order[a.state] - order[b.state] || a.name.localeCompare(b.name));
    const transfers: TransferView[] = [...node.batches.values()].sort((a, b) => b.started - a.started).map((b) => ({
      key: b.key, dir: b.dir, name: b.name, peer: b.peer.name, state: b.state,
      frac: b.size ? Math.min(1, b.done / b.size) : b.state === "done" ? 1 : 0,
      files: b.filesTotal > 1 ? `${b.filesDone} of ${b.filesTotal} files` : "",
      error: b.error,
    }));
    const targets = node.connected();
    const target = targets.find((p) => p.id === s.soundTarget) ?? targets[0] ?? null;
    return {
      ready: true,
      me: { name: s.name, os: platform.os },
      devices,
      transfers,
      received: s.received,
      clipboard: { enabled: s.clipboard, history: this.clipHistory },
      sound: {
        canShare: !!this.sender && Native.canCapture(),
        sharing: this.soundSharing,
        shareText: this.soundText,
        shareTone: this.soundTone,
        target: target?.id ?? null,
        playingFrom: node.playingFrom?.name ?? null,
        playingInfo: this.playingInfo + this.delayText(),
        latency: s.latency,
        volume: s.volume,
      },
      keepAlive: s.keepAlive,
      background: this.background(),
      tailscale: this.tailscale,
      anywhere: this.anywhere?.view() ?? null,
      notice: this.notice,
    };
  }

  // ---- actions
  connect(id: string) {
    const p = this.node?.peers.get(id);
    if (p) this.node!.connect(p, true);
  }

  disconnect(id: string) {
    const p = this.node?.peers.get(id);
    if (p) this.node!.disconnect(p);
  }

  forget(id: string) {
    const p = this.node?.peers.get(id);
    if (p) this.node!.forget(p);
  }

  async addByAddress(host: string) {
    if (!this.node) return;
    const [h, port] = host.trim().split(/:(?=\d+$)/);
    try {
      const peer = await this.node.probe(h, port ? Number(port) : PORT);
      this.node.connect(peer, true);
    } catch (e) {
      this.setNotice(`Couldn't find WinLink at ${host}: ${(e as Error).message}`);
    }
  }

  /** Send files (picked or shared) to one computer or all connected ones. */
  sendFiles(target: string, files: { uri: string; name?: string; size?: number | null }[]) {
    const node = this.node;
    if (!node) return 0;
    const peers = target === "all" ? node.connected() : [node.peers.get(target)].filter((p): p is Peer => !!p?.control);
    if (!peers.length) {
      this.setNotice("Connect to a computer first.");
      return 0;
    }
    // each computer gets its own readers (a file is read separately for each one)
    const bad: string[] = [];
    const open = (note: boolean) => files.flatMap((f) => {
      try {
        return [fileSource(f.uri, f.name, f.size)];
      } catch (e) {
        if (note) {
          bad.push(f.name || f.uri.split("/").pop() || "a file");
          platform.log("warn", `can't open ${f.uri}: ${(e as Error).message}`);
        }
        return [];
      }
    });
    let count = 0;
    peers.forEach((peer, i) => {
      const sources = open(i === 0);
      count = Math.max(count, sources.length);
      if (sources.length) node.sendFiles(peer, sources);
    });
    if (bad.length) this.setNotice(`Couldn't open ${bad.join(", ")}.${count ? " Sending the rest." : ""}`);
    return count;
  }

  cancel(key: string) {
    this.node?.cancel(key);
  }

  dismiss(key: string) {
    const b = this.node?.batches.get(key);
    if (b && b.state !== "active") {
      this.node!.batches.delete(key);
      this.changed();
    }
  }

  async deleteReceived(uri: string) {
    const s = this.settings!;
    await Native.deleteSaved(uri).catch(() => false);
    s.received = s.received.filter((r) => r.uri !== uri);
    saveSettings(s);
    this.changed();
  }

  // clipboard
  async sendClipboard() {
    if (!this.node?.connected().length) {
      this.setNotice("Connect to a computer first.");
      return;
    }
    let text = "";
    try {
      if (await Clipboard.hasImageAsync()) {
        const img = await Clipboard.getImageAsync({ format: "png" });
        if (img?.data) {
          this.shareClip({ kind: "image", png: img.data.replace(/^data:image\/png;base64,/, "") });
          return;
        }
      }
      text = await Clipboard.getStringAsync();
    } catch (e) {
      this.setNotice(`Couldn't read the clipboard: ${(e as Error).message}`);
      return;
    }
    if (!text) {
      this.setNotice("The clipboard is empty.");
      return;
    }
    this.shareClip({ kind: "text", text });
  }

  shareClip(clip: { kind: "text"; text: string } | { kind: "image"; png: string }) {
    if (!this.node) return;
    const on = this.node.connected();
    if (!on.length) {
      this.setNotice("Not connected to a computer yet.");
      return;
    }
    this.node.sendClip(clip);
    this.remember("out", clip.kind === "text" ? clip.text : "Image", on.map((p) => p.name).join(", "));
    this.setNotice(`Clipboard sent to ${on.map((p) => p.name).join(", ")}.`);
  }

  async copyAgain(item: ClipItem) {
    try {
      await Clipboard.setStringAsync(item.text);
    } catch (e) {
      this.setNotice(`Couldn't copy it: ${(e as Error).message}`);
      return;
    }
    if (this.settings?.clipboard) this.shareClip({ kind: "text", text: item.text });
  }

  clearClipHistory() {
    this.clipHistory = [];
    this.changed();
  }

  // sound
  setSharing(on: boolean) {
    this.soundSharing = on;
    if (on) {
      this.maybeShareSound();
    } else {
      this.sender?.stop();
      this.soundText = "";
      this.soundTone = "muted";
    }
    this.changed();
  }

  private maybeShareSound() {
    const node = this.node;
    if (!node || !this.sender || !this.soundSharing || this.sender.running) return;
    const targets = node.connected();
    const target = targets.find((p) => p.id === this.settings?.soundTarget) ?? targets[0];
    if (!target) {
      this.soundText = "Waiting for a computer to connect…";
      this.soundTone = "muted";
      return;
    }
    this.sender.start(target);
  }

  setSoundTarget(id: string) {
    const s = this.settings!;
    if (s.soundTarget === id) return;
    s.soundTarget = id;
    saveSettings(s);
    if (this.sender?.running) {
      this.sender.stop();
      setTimeout(() => this.maybeShareSound(), 800);
    }
    this.changed();
  }

  stopPlayingHere() {
    this.node?.stopSoundHere();
  }

  setLatency(v: AppSettings["latency"]) {
    const s = this.settings!;
    s.latency = v;
    saveSettings(s);
    if (this.node) this.node.latencyMs = LATENCY_MS[v];
    this.changed();
  }

  setVolume(v: number) {
    const s = this.settings!;
    s.volume = v;
    Native.playerVolume(v);
    saveSettings(s);
  }

  level(): number {
    try {
      return Native.playerLevel();
    } catch {
      return 0;
    }
  }

  // settings
  setName(name: string) {
    const s = this.settings!;
    s.name = name.trim().slice(0, 48) || s.name;
    saveSettings(s);
    for (const p of this.node?.connected() ?? []) p.control?.close();     // reconnect under the new name
    this.changed();
  }

  setClipboard(on: boolean) {
    this.settings!.clipboard = on;
    saveSettings(this.settings!);
    this.changed();
  }

  // ---- Connect from anywhere
  setAnywhere(on: boolean) {
    if (!this.anywhere) return;
    this.settings!.anywhere = on;
    saveSettings(this.settings!);
    this.anywhere.setOn(on);
  }

  async anywhereSignIn() {
    if (!this.anywhere) return;
    if (!this.settings!.anywhere) this.setAnywhere(true);
    try {
      await this.anywhere.signIn();
    } catch (e) {
      this.setNotice(`Couldn't start Tailscale sign-in: ${(e as Error).message}`);
    }
  }

  async anywhereSignOut() {
    if (!this.anywhere) return;
    this.settings!.anywhere = false;
    saveSettings(this.settings!);
    await this.anywhere.signOut();
  }

  anywhereRetry() {
    this.anywhere?.nudge();
  }

  setKeepAlive(on: boolean) {
    this.settings!.keepAlive = on;
    saveSettings(this.settings!);
    this.updateKeepAlive();
    this.changed();
  }

  logs() {
    return logLines.slice(-200);
  }
}

export const controller = new Controller();
