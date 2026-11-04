// This phone as a WinLink device: finding computers, pairing, the control link (clipboard,
// keep-alive, cancel), file transfers both ways with resume and cancel, and sound both ways.
// It speaks exactly the protocol of the desktop app (net.py / secure.py / audio.py).
//
// Phones always dial: they find computers with Bonjour (or remembered addresses, including
// Tailscale ones a computer tells us about) and keep the control link up. Computers then reach the
// phone on the address it connected from, for files and sound sent to it.
import { sha1 } from "@noble/hashes/legacy.js";
import { b64, enc, equalBytes, hex, randomBytes, unb64 } from "./bytes";
import { AuthError, Pairing, Sealer, nonce, proof, sessionKeys } from "./secure";
import { Closed, Conn, RawSocket } from "./wire";

export const PORT = 50555;
const PROTO = 2;
const HANDSHAKE_TIMEOUT = 8000;
const PROMPT_WAIT = 90000;
const CHUNK = 256 * 1024;
const MAX_FILE = 2 ** 40;
const SPACE_MARGIN = 256 * 2 ** 20;
const FILE_ATTEMPTS = 5;
const RETRY_DELAYS = [2, 5, 15, 30];
const FILE_PATIENCE = 20 * 60 * 1000;
const REPLY_WAIT = 5000;
const ID_RE = /^[0-9a-f]{16,64}$/;
export const OS_NAMES = ["mac", "windows", "linux", "android", "ios"] as const;

// ---------------------------------------------------------------- what the app provides
export interface NetLib {
  createConnection(opts: { host: string; port: number }, onConnect: () => void): RawSocket;
  createServer(onConn: (s: RawSocket) => void): {
    listen(opts: { port: number; host?: string }, cb?: () => void): unknown;
    on(ev: "error", cb: (e: Error) => void): unknown;
    close(): unknown;
    address?(): { port: number } | string | null;
  };
}

/** The built-in Tailscale connection, when it's on (see tailnet.ts). */
export interface TailnetRoute {
  readonly up: boolean;
  dial(host: string, port: number, timeout: number): Promise<Conn>;
}

export interface FileSource {
  name: string;          // relative path shown and saved on the other side ("photos/a.jpg")
  size: number;
  key: string;           // stays the same for the same file, so an interrupted send can resume
  read(offset: number, n: number): Promise<Uint8Array>;
  close?(): void;
}

export interface PartialFile {
  offset: number;
  write(b: Uint8Array): Promise<void>;
  close(): Promise<void>;
}

export interface Saver {
  freeSpace(): number;
  /** Open the unfinished download for this key (resume = keep what's there). */
  openPartial(key: string, resume: boolean): Promise<PartialFile>;
  /** Move a finished download to where received files go; returns where it ended up. */
  finish(key: string, relPath: string, peerName: string): Promise<string>;
  discard(key: string): Promise<void>;
  remove(savedPath: string): Promise<void>;
}

export interface SoundOut {
  /** adaptive: Auto (start at bufferMs, adapt to the network, never below floorMs). */
  start(rate: number, channels: number, format: "f32" | "s16", bufferMs: number, adaptive?: boolean, floorMs?: number): void;
  write(pcm: Uint8Array): void;
  stop(): void;
}

export interface SoundIn {
  /** Ask permission if needed and start capturing; throws with a readable reason. */
  start(onPcm: (pcm: Uint8Array) => void): Promise<{ rate: number; channels: number }>;
  stop(): void;
}

export interface Settings {
  id: string;
  name: string;
  trusted: Record<string, TrustedInfo>;
  [k: string]: unknown;
}

export interface TrustedInfo {
  name: string;
  os: string;
  key: string;           // base64 pair key
  addrs: string[];       // last addresses that worked (LAN), newest first
  tsAddrs: string[];     // the computer's Tailscale addresses, for when we're on another network
}

export interface Platform {
  net: NetLib;
  os: "android" | "ios";
  saveSettings(s: Settings): void;
  saver: Saver;
  soundOut: SoundOut | null;
  soundIn: SoundIn | null;
  log(level: "info" | "warn" | "error" | "debug", msg: string): void;
}

// ---------------------------------------------------------------- model
export class Peer {
  addrs = new Map<string, number>();   // address -> last seen on Bonjour (0 = remembered only)
  control: Control | null = null;
  connecting = false;
  paused = false;            // we pressed Disconnect: no automatic reconnects
  remotePaused = false;      // they pressed Disconnect
  fails = 0;
  retryAt = 0;
  waitingCode: string | null = null;
  lastError = "";
  lanFailedAt = 0;           // last time "same Wi-Fi again" turned out unreachable
  movingToWifi = false;
  constructor(public id: string, public name: string, public os: string, public port: number) {}

  /** Seen on this Wi-Fi (Bonjour) in the last minute: a direct route is likely, and much faster. */
  get onWifi(): boolean {
    const now = Date.now();
    for (const [a, t] of this.addrs) if (t && now - t < 60000 && !isTailscale(a)) return true;
    return false;
  }

  /** On Tailscale but back on the same Wi-Fi: worth moving over (not if that just failed). */
  get shouldMove(): boolean {
    return this.onWifi && Date.now() - this.lanFailedAt > 60000;
  }

  get nearby(): boolean {
    const now = Date.now();
    for (const t of this.addrs.values()) if (t && now - t < 60000) return true;
    return false;
  }
}

class Control {
  lastPing = 0;
  lastHeard = Date.now();
  started = Date.now();
  constructor(public conn: Conn) {}
  get route() {
    return this.conn.route;
  }
  send(obj: unknown) {
    return this.conn.sendJson(obj);
  }
  close() {
    this.conn.close();
  }
}

export type BatchState = "active" | "done" | "failed" | "cancelled";

export class Batch {
  filesDone = 0;
  state: BatchState = "active";
  error: string | null = null;
  cancelled = false;
  conn: Conn | null = null;
  done = 0;                 // bytes
  saved: string[] = [];     // incoming: where finished files went
  partials = new Set<string>();
  started = Date.now();
  updated = Date.now();
  constructor(
    public dir: "in" | "out",
    public id: string,
    public name: string,
    public peer: Peer,
    public filesTotal: number,
    public size: number,
  ) {}
  get key() {
    return this.dir === "out" ? this.id : `${this.peer.id}:${this.id}`;
  }
}

export type NodeEvent =
  | { kind: "peers" }
  | { kind: "batch"; batch: Batch }
  | { kind: "received"; path: string; name: string; from: string; size: number }
  | { kind: "removed"; paths: string[] }
  | { kind: "clip_in"; peer: Peer; clip: { kind: "text"; text: string } | { kind: "image"; png?: string; bmp?: string } }
  | { kind: "pair_code"; peer: Peer; code: string }
  | { kind: "notice"; text: string }
  | { kind: "sound_in"; peer: Peer | null; info: SoundInfo | null }
  | { kind: "sound_out"; text: string; state: "playing" | "retry" | "off" | "idle" };

export interface SoundInfo {
  rate: number;
  channels: number;
  format: "f32" | "s16";
  remote: boolean;
  bufferMs: number;
}

class Declined extends AuthError {}
class Paused extends AuthError {}
class MoveToWifi extends Error {}
class Refused extends Error {}
class Cancelled extends Error {}

// ---------------------------------------------------------------- the node
export class Node {
  peers = new Map<string, Peer>();
  batches = new Map<string, Batch>();
  cancelledIds = new Map<string, number>();
  private server: ReturnType<NetLib["createServer"]> | null = null;
  private tailnetServer: ReturnType<NetLib["createServer"]> | null = null;
  tailnet: TailnetRoute | null = null;      // set while the built-in Tailscale is on
  private timer: ReturnType<typeof setInterval> | null = null;
  listening = false;
  serverPort = PORT;
  private soundStream: { conn: Conn; peer: Peer; rate: number; channels: number; format: string } | null = null;
  soundSending: SoundSender | null = null;
  latencyMs: number | null = null;     // the person's choice for sound played here (null = follow sender)

  constructor(public settings: Settings, private p: Platform, public emit: (e: NodeEvent) => void) {
    for (const [id, t] of Object.entries(settings.trusted)) {
      const peer = new Peer(id, t.name, t.os, PORT);
      for (const a of t.addrs ?? []) peer.addrs.set(a, 0);
      this.peers.set(id, peer);
    }
  }

  get id() {
    return this.settings.id;
  }
  get name() {
    return this.settings.name;
  }

  log(level: "info" | "warn" | "error" | "debug", msg: string) {
    this.p.log(level, msg);
  }

  // ---- lifecycle
  start(port = PORT) {
    this.listen(port);
    if (!this.timer) this.timer = setInterval(() => this.maintain(), 1000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    for (const p of this.peers.values()) p.control?.close();
    try {
      this.server?.close();
    } catch {
      /* ignore */
    }
    this.server = null;
    this.listening = false;
  }

  /** The phone's network changed (Wi-Fi back, switched to mobile data...): retry everyone now. */
  networkChanged() {
    for (const p of this.peers.values()) {
      if (!p.control) {
        p.fails = 0;
        p.retryAt = 0;
      }
    }
  }

  /** The OS suspended us (iOS in the background): drop connections; start() brings them back. */
  pauseAll() {
    for (const p of this.peers.values()) p.control?.close();
  }

  private listen(port = PORT) {
    if (this.server) return;
    const srv = this.p.net.createServer((s) => this.handle(s));
    srv.on("error", (e) => {
      this.log("warn", `can't listen on port ${port}: ${e.message}`);
      this.server = null;
      this.listening = false;
      if (port === PORT) setTimeout(() => this.listen(PORT + 7), 500);     // something else has it: use another
    });
    srv.listen({ port, host: "0.0.0.0" }, () => {
      this.listening = true;
      this.serverPort = port;
    });
    this.server = srv;
  }

  isTrusted(id: string) {
    return !!this.settings.trusted[id]?.key;
  }

  keyFor(id: string): Uint8Array | null {
    const k = this.settings.trusted[id]?.key;
    return k ? unb64(k, 32) : null;
  }

  private saveTrusted(peer: Peer, patch: Partial<TrustedInfo>) {
    const t = this.settings.trusted[peer.id] ?? { name: peer.name, os: peer.os, key: "", addrs: [], tsAddrs: [] };
    this.settings.trusted = { ...this.settings.trusted, [peer.id]: { ...t, name: peer.name, os: peer.os, ...patch } };
    this.p.saveSettings(this.settings);
  }

  forget(peer: Peer) {
    const t = { ...this.settings.trusted };
    delete t[peer.id];
    this.settings.trusted = t;
    this.p.saveSettings(this.settings);
    this.disconnect(peer);
    this.peers.delete(peer.id);
    this.emit({ kind: "peers" });
  }

  connected(): Peer[] {
    return [...this.peers.values()].filter((p) => p.control);
  }

  // ---- discovery (the app feeds Bonjour results here)
  seen(info: { id: string; name: string; os: string; port: number }, addrs: string[]) {
    if (!ID_RE.test(info.id) || info.id === this.id) return;
    let peer = this.peers.get(info.id);
    let changed = false;
    if (!peer) {
      peer = new Peer(info.id, cleanName(info.name), OS_NAMES.includes(info.os as any) ? info.os : "?", info.port || PORT);
      this.peers.set(info.id, peer);
      changed = true;
    }
    if (!peer.nearby) changed = true;
    if (!peer.nearby || addrs.some((a) => !peer!.addrs.has(a))) {
      peer.fails = 0;                 // back (or somewhere new): don't sit out a backoff
      peer.retryAt = 0;
    }
    peer.name = cleanName(info.name) || peer.name;
    peer.port = info.port || PORT;
    const now = Date.now();
    for (const a of addrs) peer.addrs.set(a, now);
    if (changed) this.emit({ kind: "peers" });
  }

  /** Bonjour says it's gone. */
  gone(id: string) {
    const peer = this.peers.get(id);
    if (!peer) return;
    for (const a of peer.addrs.keys()) peer.addrs.set(a, 0);
    if (!this.isTrusted(id) && !peer.control) this.peers.delete(id);
    this.emit({ kind: "peers" });
  }

  /** "Add by address": ask whatever is at host:port who it is. */
  async probe(host: string, port = PORT): Promise<Peer> {
    const conn = await this.open(host, port, 6000);
    try {
      conn.setTimeout(HANDSHAKE_TIMEOUT);
      conn.sendLine({ type: "probe" });
      const info = await conn.recvLine();
      if (info.app !== "link" || !ID_RE.test(String(info.id))) throw new Error("that isn't WinLink");
      if (info.id === this.id) throw new Error("that's this phone");
      this.seen({ id: info.id, name: String(info.name || host), os: String(info.os), port: Number(info.port) || PORT }, [host]);
      return this.peers.get(info.id)!;
    } finally {
      conn.close();
    }
  }

  private maintain() {
    const now = Date.now();
    for (const p of this.peers.values()) {
      if (p.control) {
        if (p.control.route === "tailscale" && p.shouldMove && !p.movingToWifi && now - p.control.started > 10000) {
          void this.moveToWifi(p);
        }
        if (now - p.control.lastPing > 4000) {
          p.control.lastPing = now;
          p.control.send({ type: "ping" }).catch(() => p.control?.close());
        }
        if (now - p.control.lastHeard > 15000) p.control.close();      // the computer went quiet
      } else if (this.isTrusted(p.id) && !p.paused && !p.remotePaused && !p.connecting && now >= p.retryAt
                 && this.addressesOf(p).length) {
        this.connect(p);
      }
    }
    for (const b of this.batches.values()) {
      if (b.dir === "in" && b.state === "active" && !b.conn && now - b.updated > 15 * 60000) {
        b.state = "failed";
        b.error = `${b.peer.name} stopped sending`;
        this.emit({ kind: "batch", batch: b });
      }
    }
  }

  addressesOf(peer: Peer): { host: string; ts: boolean }[] {
    const lan = [...peer.addrs.entries()].sort((a, b) => b[1] - a[1]).map(([h]) => ({ host: h, ts: isTailscale(h) }));
    const ts = (this.settings.trusted[peer.id]?.tsAddrs ?? []).map((h) => ({ host: h, ts: true }));
    const seen = new Set<string>();
    return [...lan.filter((a) => !a.ts), ...lan.filter((a) => a.ts), ...ts].filter((a) => !seen.has(a.host) && !!seen.add(a.host));
  }

  /** A private local port the Tailscale helper hands tailnet connections to ("FROM <ip>" first). */
  listenTailnet(): Promise<number> {
    return new Promise((resolve, reject) => {
      try {
        this.tailnetServer?.close();
      } catch {
        /* ignore */
      }
      const srv = this.p.net.createServer((s) => this.handle(s, true));
      srv.on("error", (e) => reject(e));
      srv.listen({ port: 0, host: "127.0.0.1" }, () => {
        const a = srv.address?.();
        const port = a && typeof a === "object" ? a.port : 0;
        if (port) resolve(port);
        else reject(new Error("no local port for Tailscale"));
      });
      this.tailnetServer = srv;
    });
  }

  closeTailnet() {
    try {
      this.tailnetServer?.close();
    } catch {
      /* ignore */
    }
    this.tailnetServer = null;
  }

  // ---- connecting
  open(host: string, port: number, timeout: number): Promise<Conn> {
    if (isTailscale(host) && this.tailnet?.up) return this.tailnet.dial(host, port, timeout);
    return new Promise((resolve, reject) => {
      let done = false;
      const sock = this.p.net.createConnection({ host, port }, () => {
        if (done) return;
        done = true;
        clearTimeout(t);
        const c = new Conn(sock, host);
        c.route = isTailscale(host) ? "tailscale" : "wifi";
        resolve(c);
      });
      const t = setTimeout(() => {
        if (done) return;
        done = true;
        try {
          sock.destroy();
        } catch {
          /* ignore */
        }
        reject(new Error("timed out"));
      }, timeout);
      sock.on("error", (e) => {
        if (done) return;
        done = true;
        clearTimeout(t);
        reject(e);
      });
    });
  }

  /** An encrypted connection to a paired peer (or a cleartext one, ready to pair). Tries the LAN
   * addresses at once (first to connect wins), then Tailscale ones one by one. */
  async dial(peer: Peer, kind: "control" | "file" | "audio", manual = false, allowPair = false, lanOnly = false): Promise<Conn> {
    const paired = this.keyFor(peer.id) !== null;
    const addrs = this.addressesOf(peer);
    if (!addrs.length) throw new Error(`no address for ${peer.name}`);
    const lan = addrs.filter((a) => !a.ts);
    let last: Error = new Error("no address");
    let conn: Conn | null = null;
    if (lan.length) {
      try {
        conn = await firstOf(lan.map((a, i) => delay(i * 250).then(() => this.open(a.host, peer.port, 3000))));
      } catch (e) {
        last = e as Error;
      }
    }
    if (!conn && !lanOnly) {
      for (const a of addrs.filter((x) => x.ts)) {
        try {
          conn = await this.open(a.host, peer.port, 8000);
          break;
        } catch (e) {
          last = e as Error;
        }
      }
    }
    if (!conn) throw last;
    try {
      if (!paired && !allowPair) throw new AuthError("not paired");
      return await this.secureClient(conn, peer, kind, manual, !paired);
    } catch (e) {
      conn.close();
      throw e;
    }
  }

  private async secureClient(conn: Conn, peer: Peer, kind: string, manual: boolean, pair: boolean): Promise<Conn> {
    conn.setTimeout(HANDSHAKE_TIMEOUT);
    let key = this.keyFor(peer.id);
    const nc = nonce();
    const hello: Record<string, unknown> = { v: PROTO, type: kind, id: this.id, name: this.name, os: this.p.os,
      port: this.serverPort, nonce: b64(nc), manual };
    if (!key || pair) hello.pair = true;
    conn.sendLine(hello);
    let reply: any;
    try {
      reply = await conn.recvLine();
    } catch (e) {
      throw new Error(`${peer.name} closed the connection before the security check`);
    }
    const mode = reply.mode;
    if (mode === "paused") throw new Paused("paused");
    if (mode !== "auth" && mode !== "pair") {
      if (reply.reason === "declined") throw new Declined("declined");
      throw new AuthError(String(reply.reason || "refused"));
    }
    const ns = unb64(String(reply.nonce ?? ""), 16);
    if (mode === "auth") {
      if (!key) throw new AuthError("not paired");
      conn.sendLine({ proof: b64(proof(key, "client", nc, ns, kind)) });
      const ok = await conn.recvLine();
      const theirs = ok.ok ? unb64(String(ok.proof ?? ""), 32) : new Uint8Array(0);
      if (!equalBytes(theirs, proof(key, "server", nc, ns, kind))) {
        throw new AuthError(`${peer.name} couldn't prove it's the computer you paired`);
      }
    } else {
      if (kind !== "control") throw new AuthError("not paired");
      const pairing = new Pairing();
      conn.sendLine({ pub: b64(pairing.pub) });
      const [k, code] = pairing.finish(unb64(String(reply.pub ?? ""), 32), this.id, peer.id, nc, ns);
      peer.waitingCode = code;
      this.emit({ kind: "pair_code", peer, code });
      this.emit({ kind: "peers" });
      try {
        conn.setTimeout(PROMPT_WAIT + 5000);
        const answer = await conn.recvLine();
        if (answer.type !== "welcome") throw new Declined("declined");
      } finally {
        peer.waitingCode = null;
      }
      key = k;
      this.saveTrusted(peer, { key: b64(k) });
    }
    const [c2s, s2c] = sessionKeys(key!, nc, ns);
    conn.startEncryption(new Sealer(c2s, s2c));
    conn.setTimeout(0);
    return conn;
  }

  connect(peer: Peer, manual = false) {
    if (peer.control || peer.connecting) return;
    if (manual) {
      peer.paused = peer.remotePaused = false;
      peer.fails = 0;
      peer.retryAt = 0;
    }
    peer.connecting = true;
    this.emit({ kind: "peers" });
    void this.doConnect(peer, manual);
  }

  private async doConnect(peer: Peer, manual: boolean) {
    try {
      const conn = await this.dial(peer, "control", manual, manual);
      peer.connecting = false;
      peer.remotePaused = false;
      peer.fails = 0;
      peer.retryAt = 0;
      peer.lastError = "";
      const used = conn.addr;
      if (!isTailscale(used)) {
        const t = this.settings.trusted[peer.id];
        const addrs = [used, ...(t?.addrs ?? []).filter((a) => a !== used)].slice(0, 4);
        this.saveTrusted(peer, { addrs });
      }
      await this.runControl(peer, conn);
    } catch (e) {
      const err = e as Error;
      if (err instanceof Declined) {
        if (manual) this.emit({ kind: "notice", text: `${peer.name} didn't accept the connection.` });
        peer.remotePaused = true;
      } else if (err instanceof Paused) {
        peer.remotePaused = true;
        peer.lastError = `Disconnected on ${peer.name}`;
      } else if (err instanceof AuthError) {
        this.log("warn", `secure connection to ${peer.name} failed: ${err.message}`);
        peer.lastError = err.message;
        this.backoff(peer);
        if (manual) this.emit({ kind: "notice", text: `Couldn't connect securely to ${peer.name}: ${err.message}.` });
      } else {
        this.log("info", `connect to ${peer.name} failed: ${err.message}`);
        peer.lastError = why(err);
        this.backoff(peer);
        if (manual) this.emit({ kind: "notice", text: `Couldn't reach ${peer.name}. Is WinLink open on it?` });
      }
    } finally {
      peer.connecting = false;
      peer.waitingCode = null;
      this.emit({ kind: "peers" });
    }
  }

  /** On Tailscale, but back on the same Wi-Fi: open the control link over Wi-Fi (it replaces the
   * Tailscale one). If Wi-Fi doesn't answer, keep Tailscale and try again in a minute. */
  private async moveToWifi(peer: Peer) {
    peer.movingToWifi = true;
    let conn: Conn;
    try {
      conn = await this.dial(peer, "control", false, false, true);
    } catch (e) {
      peer.lanFailedAt = Date.now();
      this.log("info", `${peer.name}: staying on Tailscale (Wi-Fi didn't answer: ${why(e as Error)})`);
      return;
    } finally {
      peer.movingToWifi = false;
    }
    this.log("info", `${peer.name}: moved from Tailscale to Wi-Fi`);
    await this.runControl(peer, conn).catch(() => undefined);
  }

  private backoff(peer: Peer) {
    peer.fails += 1;
    peer.retryAt = Date.now() + Math.min(60, 2 ** Math.min(peer.fails, 6)) * 1000;
  }

  disconnect(peer: Peer) {
    peer.paused = true;
    if (peer.control) {
      peer.control.send({ type: "bye" }).catch(() => undefined);
      setTimeout(() => peer.control?.close(), 200);
    }
    this.emit({ kind: "peers" });
  }

  private async runControl(peer: Peer, conn: Conn) {
    const ctrl = new Control(conn);
    const old = peer.control;
    peer.control = ctrl;
    old?.close();
    this.emit({ kind: "peers" });
    this.log("info", `connected to ${peer.name} via ${conn.route} (${conn.addr})`);
    try {
      for (;;) {
        const msg = await conn.recvJson(160 * 2 ** 20);
        ctrl.lastHeard = Date.now();
        const kind = msg.type;
        if (kind === "clip") {
          const clip = cleanClip(msg);
          if (clip) this.emit({ kind: "clip_in", peer, clip });
        } else if (kind === "cancel" && typeof msg.batch === "string" && /^[0-9a-f]{32}$/.test(msg.batch)) {
          this.onCancelMsg(peer, msg.batch);
        } else if (kind === "info" && Array.isArray(msg.ts_addrs)) {
          const ts = msg.ts_addrs.filter((a: unknown) => typeof a === "string" && a.length < 64).slice(0, 6);
          this.saveTrusted(peer, { tsAddrs: ts });
        } else if (kind === "pointer" && msg.op === "no" && typeof msg.reason === "string") {
          this.emit({ kind: "notice", text: `${peer.name}: ${msg.reason.slice(0, 200)}` });
        } else if (kind === "bye") {
          peer.remotePaused = true;
          break;
        }
      }
    } catch (e) {
      this.log("debug", `control with ${peer.name} ended: ${(e as Error).message}`);
    } finally {
      ctrl.close();
      if (peer.control === ctrl) peer.control = null;
      this.emit({ kind: "peers" });
    }
  }

  // ---- incoming connections (computers sending files or sound to this phone)
  private async handle(sock: RawSocket, viaTailnet = false) {
    const conn = new Conn(sock, (sock.remoteAddress ?? "").replace(/^::ffff:/, ""));
    conn.route = isTailscale(conn.addr) ? "tailscale" : "wifi";
    try {
      conn.setTimeout(HANDSHAKE_TIMEOUT);
      if (viaTailnet) {       // from the built-in Tailscale: it says who's calling first
        const m = /^FROM (\S{1,64})$/.exec(await conn.recvText());
        if (!m) throw new Error("bad forward from Tailscale");
        conn.addr = m[1].replace(/^\[|\]$/g, "");
        conn.route = "tailscale";
      }
      const hello = await conn.recvLine();
      if (hello.type === "probe") {
        conn.sendLine({ app: "link", id: this.id, name: this.name, os: this.p.os, port: this.serverPort });
        return;
      }
      if (!["control", "file", "audio"].includes(hello.type)) throw new Error("unknown request");
      const h = cleanHello(hello);
      let peer = this.peers.get(h.id);
      if (!peer) {
        if (!this.isTrusted(h.id)) {
          conn.sendLine({ mode: "denied", reason: `pair from the phone: open WinLink on ${this.name} and tap this computer` });
          return;
        }
        peer = new Peer(h.id, h.name, h.os, h.port);
        this.peers.set(h.id, peer);
      }
      peer.name = h.name || peer.name;
      peer.port = h.port;
      if (conn.addr && !peer.addrs.has(conn.addr)) peer.addrs.set(conn.addr, 0);
      const key = this.keyFor(peer.id);
      if (!key || h.pair) {
        conn.sendLine({ mode: "denied", reason: `pair from the phone: open WinLink on ${this.name} and tap this computer` });
        return;
      }
      if (h.type === "control" && peer.paused && !h.manual) {
        conn.sendLine({ mode: "paused" });
        return;
      }
      const ns = nonce();
      conn.sendLine({ mode: "auth", nonce: b64(ns) });
      const msg = await conn.recvLine();
      const theirs = unb64(String(msg.proof ?? ""), 32);
      if (!equalBytes(theirs, proof(key, "client", h.nonce, ns, h.type))) {
        this.log("warn", `${peer.name} at ${conn.addr} failed to prove it's paired`);
        return;
      }
      conn.sendLine({ ok: true, proof: b64(proof(key, "server", h.nonce, ns, h.type)) });
      const [c2s, s2c] = sessionKeys(key, h.nonce, ns);
      conn.startEncryption(new Sealer(s2c, c2s));
      conn.setTimeout(0);
      if (h.type === "control") {
        peer.paused = false;
        await this.runControl(peer, conn);
      } else if (h.type === "file") {
        await this.receiveFile(peer, conn);
      } else {
        conn.setTimeout(10000);
        const hdr = cleanSound(await conn.recvJson());
        conn.setTimeout(30000);
        await this.receiveSound(peer, hdr, conn);
      }
    } catch (e) {
      this.log("debug", `connection from ${conn.addr} ended: ${(e as Error).message}`);
    } finally {
      conn.close();
    }
  }

  // ---- clipboard
  /** Trackpad: move, click or scroll a computer's pointer (pointer.py on the computer). */
  sendPointer(peerId: string, msg: Record<string, unknown>) {
    const peer = this.peers.get(peerId);
    peer?.control?.send({ type: "pointer", ...msg }).catch(() => undefined);
  }

  sendClip(clip: { kind: "text"; text: string } | { kind: "image"; png: string }, except?: string) {
    const msg = { type: "clip", ...clip };
    for (const p of this.connected()) {
      if (p.id === except) continue;
      p.control!.send(msg).catch(() => undefined);
    }
  }

  // ---- files out
  sendFiles(target: Peer, sources: FileSource[], batchName?: string): Batch | null {
    if (!sources.length) return null;
    const name = batchName ?? (sources.length === 1 ? sources[0].name.split("/").pop()! : `${sources.length} items`);
    const batch = new Batch("out", hex(randomBytes(16)), name, target, sources.length, sources.reduce((n, s) => n + s.size, 0));
    this.track(batch);
    void this.sendBatch(batch, sources);
    return batch;
  }

  private track(batch: Batch) {
    this.batches.set(batch.key, batch);
    if (this.batches.size > 40) {
      const old = [...this.batches.values()].filter((b) => b.state !== "active").sort((a, b) => a.updated - b.updated);
      for (const b of old.slice(0, this.batches.size - 40)) this.batches.delete(b.key);
    }
    this.emit({ kind: "batch", batch });
  }

  private emitBatch(batch: Batch, force = false) {
    const now = Date.now();
    if (force || now - batch.updated > 150) {
      batch.updated = now;
      this.emit({ kind: "batch", batch });
    }
  }

  private async sendBatch(batch: Batch, sources: FileSource[]) {
    const peer = batch.peer;
    let doneBefore = 0;
    let moving = false;
    for (const src of sources) {
      if (batch.cancelled) break;
      const began = Date.now();
      let attempt = 0;
      let state: "done" | "failed" = "failed";
      let progress = 0;
      for (;;) {
        const before = progress;
        try {
          const wasMoving = moving;
          moving = false;
          await this.sendOne(peer, batch, src, wasMoving, (n) => {
            progress = n;
            batch.done = doneBefore + n;
            this.emitBatch(batch);
          });
          state = "done";
          batch.filesDone += 1;
          batch.error = null;
          this.log("info", `sent ${src.name} (${src.size} bytes) to ${peer.name}`);
          break;
        } catch (e) {
          const err = e as Error;
          if (err instanceof Cancelled || batch.cancelled) break;
          if (err instanceof MoveToWifi) {           // straight back in, over Wi-Fi, from the same byte
            moving = true;
            continue;
          }
          if (err instanceof Refused) {
            if (/cancelled/.test(err.message)) this.cancelLocal(batch, true);
            else batch.error = err.message;
            break;
          }
          attempt = progress > before ? 0 : attempt + 1;
          this.log("warn", `sending ${src.name} to ${peer.name} stopped (${err.message}); attempt ${attempt}`);
          if (attempt >= FILE_ATTEMPTS || Date.now() - began > FILE_PATIENCE) {
            batch.error = err.message;
            break;
          }
          batch.error = `retrying: ${why(err)}`;
          this.emitBatch(batch, true);
          const wait = progress > before ? 1 : RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length) - 1];
          for (let i = 0; i < wait * 10 && !batch.cancelled; i++) await delay(100);
        } finally {
          batch.conn = null;
        }
      }
      src.close?.();
      doneBefore += src.size;
      batch.done = doneBefore;
      if (state === "failed" && !batch.cancelled) {
        batch.state = "failed";
        break;
      }
      this.emitBatch(batch, true);
    }
    if (batch.state === "active") batch.state = batch.cancelled ? "cancelled" : "done";
    batch.updated = Date.now();
    this.emit({ kind: "batch", batch });
  }

  private async sendOne(peer: Peer, batch: Batch, src: FileSource, moving: boolean, onProgress: (n: number) => void) {
    let stage = "connecting";
    let conn: Conn | null = null;
    try {
      conn = await this.dial(peer, "file");
      if (batch.cancelled) throw new Cancelled();
      if (moving) {
        if (conn.route === "tailscale") peer.lanFailedAt = Date.now();     // Wi-Fi didn't answer
        else this.log("info", `${src.name} to ${peer.name} moved from Tailscale to Wi-Fi`);
      }
      batch.conn = conn;
      stage = `waiting for ${peer.name} to accept the file`;
      conn.setTimeout(30000);
      await conn.sendJson({ rel: src.name, size: src.size, resume: resumeKey(this.id, src), batch: batch.id,
        batch_name: batch.name, batch_files: batch.filesTotal, batch_size: batch.size });
      const reply = await conn.recvJson();
      if (!reply.ok) throw new Refused(String(reply.error || "refused").slice(0, 200));
      let offset = Number.isInteger(reply.offset) && reply.offset >= 0 && reply.offset <= src.size ? reply.offset : 0;
      stage = "sending";
      onProgress(offset);
      let nextCheck = Date.now() + 3000;
      while (offset < src.size) {
        if (batch.cancelled) throw new Cancelled();
        if (conn.route === "tailscale" && Date.now() >= nextCheck) {
          nextCheck = Date.now() + 3000;
          if (peer.shouldMove) throw new MoveToWifi();     // same Wi-Fi again: finish there, faster
        }
        let chunk: Uint8Array;
        try {
          chunk = await src.read(offset, Math.min(CHUNK, src.size - offset));
        } catch (e) {               // deleted, or the app that shared it took access back: retrying won't help
          throw new Refused(`couldn't read ${src.name.split("/").pop()}: ${why(e as Error)}`);
        }
        if (!chunk.length) throw new Refused("the file changed while it was being sent");
        await conn.send(chunk);
        offset += chunk.length;
        onProgress(offset);
      }
      stage = `waiting for ${peer.name} to save it`;
      conn.setTimeout(120000);
      const saved = await conn.recvJson();
      if (!saved.saved) throw new Error(String(saved.error || "not saved on the other side").slice(0, 200));
    } catch (e) {
      if (e instanceof Refused || e instanceof Cancelled || e instanceof MoveToWifi) throw e;
      if (batch.cancelled) throw new Cancelled();
      throw new Error(`${stage}: ${why(e as Error)}`);
    } finally {
      conn?.close();
    }
  }

  cancel(key: string): boolean {
    const batch = this.batches.get(key);
    if (!batch || batch.state !== "active") return false;
    this.cancelLocal(batch);
    batch.peer.control?.send({ type: "cancel", batch: batch.id }).catch(() => undefined);
    return true;
  }

  private cancelLocal(batch: Batch, byOther = false) {
    batch.cancelled = true;
    this.cancelledIds.set(batch.id, Date.now());
    batch.conn?.close();
    if (batch.dir === "in") void this.discardIncoming(batch);
    batch.state = "cancelled";
    batch.error = byOther ? `cancelled on ${batch.peer.name}` : "cancelled";
    batch.updated = Date.now();
    this.emit({ kind: "batch", batch });
  }

  private async discardIncoming(batch: Batch) {
    for (const k of batch.partials) await this.p.saver.discard(k).catch(() => undefined);
    batch.partials.clear();
    const removed = [...batch.saved];
    for (const path of removed) await this.p.saver.remove(path).catch(() => undefined);
    batch.saved = [];
    if (removed.length) this.emit({ kind: "removed", paths: removed });
  }

  private onCancelMsg(peer: Peer, bid: string) {
    for (const b of this.batches.values()) {
      if (b.id === bid && b.peer === peer && b.state === "active") {
        this.cancelLocal(b, true);
        return;
      }
    }
    this.cancelledIds.set(bid, Date.now());
  }

  // ---- files in
  private async receiveFile(peer: Peer, conn: Conn) {
    conn.setTimeout(30000);
    const meta = await conn.recvJson();
    const rel = meta.rel;
    const size = meta.size;
    if (typeof rel !== "string" || !rel || rel.length > 1024 || !Number.isInteger(size) || size < 0 || size > MAX_FILE) {
      await conn.sendJson({ error: "invalid file details" });
      return;
    }
    if (this.cancelledIds.has(meta.batch)) {
      await conn.sendJson({ error: "cancelled" });
      return;
    }
    if (size + SPACE_MARGIN > this.p.saver.freeSpace()) {
      await conn.sendJson({ error: `not enough space on ${this.name}` });
      this.emit({ kind: "notice", text: `${peer.name} tried to send a ${Math.round(size / 2 ** 20)} MB file, but this phone is low on space.` });
      return;
    }
    const batch = this.incomingBatch(peer, meta);
    if (batch.cancelled) {
      await conn.sendJson({ error: "cancelled" });
      return;
    }
    const resume = typeof meta.resume === "string" && /^[0-9a-f]{40}$/.test(meta.resume);
    const key = resume ? `${peer.id}-${meta.resume}` : `${peer.id}-${hex(randomBytes(8))}`;
    let part: PartialFile;
    try {
      part = await this.p.saver.openPartial(key, resume);
      if (part.offset > size) {
        await part.close();
        part = await this.p.saver.openPartial(key, false);
      }
    } catch (e) {
      await conn.sendJson({ error: `${this.name} couldn't save the file: ${why(e as Error)}` });
      return;
    }
    batch.partials.add(key);
    batch.conn = conn;
    let got = part.offset;
    const base = batch.done;
    await conn.sendJson({ ok: true, offset: got });
    this.emitBatch(batch, true);
    let stage = "receiving";
    try {
      while (got < size) {
        const chunk = await conn.read(Math.min(CHUNK, size - got));
        if (!chunk.length) throw new Error("the sender stopped");
        await part.write(chunk);
        got += chunk.length;
        batch.done = base + got;
        this.emitBatch(batch);
      }
      await part.close();
      if (batch.cancelled) throw new Error("cancelled");
      stage = "saving";
      const path = await this.p.saver.finish(key, rel, peer.name);
      batch.partials.delete(key);
      batch.saved.push(path);
      await conn.sendJson({ saved: true });
      batch.filesDone += 1;
      batch.error = null;
      if (batch.filesDone >= batch.filesTotal && batch.state === "active") batch.state = "done";
      this.log("info", `received ${rel} (${size} bytes) from ${peer.name}`);
      this.emit({ kind: "received", path, name: rel.split("/").pop()!, from: peer.name, size });
      if (batch.cancelled) await this.discardIncoming(batch);
    } catch (e) {
      await part.close().catch(() => undefined);
      if (batch.cancelled) {
        await this.p.saver.discard(key).catch(() => undefined);
        batch.partials.delete(key);
      } else {
        this.log("warn", `receiving ${rel} from ${peer.name} failed while ${stage}: ${(e as Error).message}`);
        batch.error = `waiting for ${peer.name} to continue`;
        await conn.sendJson({ saved: false, error: `${this.name} ${stage} failed: ${why(e as Error)}` }).catch(() => undefined);
        if (!resume || stage === "saving") {
          await this.p.saver.discard(key).catch(() => undefined);
          batch.partials.delete(key);
        }
      }
    } finally {
      batch.conn = null;
      this.emitBatch(batch, true);
    }
  }

  private incomingBatch(peer: Peer, meta: any): Batch {
    let bid: string = meta.batch;
    if (typeof bid !== "string" || !/^[0-9a-f]{32}$/.test(bid)) bid = hex(randomBytes(16));
    const key = `${peer.id}:${bid}`;
    let batch = this.batches.get(key);
    if (!batch) {
      const files = Number.isInteger(meta.batch_files) && meta.batch_files > 0 ? meta.batch_files : 1;
      const size = Number.isInteger(meta.batch_size) && meta.batch_size >= 0 ? meta.batch_size : meta.size;
      batch = new Batch("in", bid, cleanName(meta.batch_name || meta.rel || "file"), peer, files, size);
      this.track(batch);
    }
    return batch;
  }

  // ---- sound in
  private async receiveSound(peer: Peer, hdr: SoundInfo, conn: Conn) {
    const out = this.p.soundOut;
    if (!out) {
      await conn.sendJson({ ok: false, error: `${this.name} can't play sound` });
      return;
    }
    if (this.soundSending?.running) {
      await conn.sendJson({ ok: false, error: `${this.name} is sharing its own sound` });
      return;
    }
    const old = this.soundStream;
    // The same computer at the same rate is moving its stream to a faster route (Wi-Fi instead of
    // Tailscale): keep the speaker going and what's buffered.
    // (Its quality may change with the route, e.g. CD over Tailscale, lossless on Wi-Fi: the
    // player keeps its format and the new stream is converted to it.)
    const moving = !!old && old.peer === peer && old.rate === hdr.rate && old.channels === hdr.channels;
    const playFormat = moving ? old!.format : hdr.format;
    this.soundStream = { conn, peer, rate: hdr.rate, channels: hdr.channels, format: playFormat };
    if (old) {
      old.conn.sendJson(moving ? { type: "stop", reason: "moved to a faster connection", moved: true }
        : { type: "stop", reason: `${peer.name} started playing there` }).catch(() => undefined);
      setTimeout(() => old.conn.close(), 100);
    }
    // Auto starts low (40 ms on Wi-Fi, 200 over Tailscale) and adapts; a chosen latency is fixed.
    const auto = this.latencyMs === null;
    const bufferMs = auto ? Math.min(hdr.bufferMs, hdr.remote ? 200 : 40) : this.latencyMs!;
    try {
      if (!moving) out.start(hdr.rate, hdr.channels, hdr.format, bufferMs, auto, hdr.remote ? 80 : 20);
    } catch (e) {
      await conn.sendJson({ ok: false, error: `couldn't open the speaker: ${why(e as Error)}` });
      if (this.soundStream?.conn === conn) this.soundStream = null;
      return;
    }
    await conn.sendJson({ ok: true });
    this.emit({ kind: "sound_in", peer, info: { ...hdr, bufferMs } });
    const frame = hdr.channels * (hdr.format === "f32" ? 4 : 2);
    let rest = new Uint8Array(0);
    try {
      for (;;) {
        const data = await conn.read(65536);
        if (!data.length || this.soundStream?.conn !== conn) break;     // ended, or replaced
        const all = rest.length ? concatBytes(rest, data) : data;
        const usable = all.length - (all.length % frame);
        rest = all.slice(usable);
        if (usable) out.write(convertPcm(all.subarray(0, usable), hdr.format, playFormat));
      }
    } catch {
      /* the sender went away */
    } finally {
      if (this.soundStream?.conn === conn) {
        this.soundStream = null;
        out.stop();
        this.emit({ kind: "sound_in", peer: null, info: null });
      }
    }
  }

  /** Stop the sound playing here; its sender is told and stops sharing. */
  stopSoundHere() {
    const s = this.soundStream;
    if (!s) return;
    s.conn.sendJson({ type: "stop", reason: `stopped on ${this.name}` }).catch(() => undefined);
    setTimeout(() => s.conn.close(), 100);
  }

  get playingFrom(): Peer | null {
    return this.soundStream?.peer ?? null;
  }
}

// ---------------------------------------------------------------- sound out (Android)
export class SoundSender {
  running = false;
  private stopRequested = false;
  private conn: Conn | null = null;
  constructor(private node: Node, private input: SoundIn, private quality: () => "cd" | "lossless" | "saver" | "auto") {}

  start(peer: Peer) {
    if (this.running) return;
    this.running = true;
    this.stopRequested = false;
    void this.run(peer);
  }

  stop() {
    this.stopRequested = true;
    this.conn?.close();
  }

  private status(text: string, state: "playing" | "retry" | "off" | "idle") {
    this.node.emit({ kind: "sound_out", text, state });
  }

  private async run(peer: Peer) {
    let reason: string | null = null;
    const queue: Uint8Array[] = [];
    let wake: (() => void) | null = null;
    let capturing = false;
    let moving = false;                    // a Wi-Fi connection is being set up in the background
    try {
      if (this.node.playingFrom) this.node.stopSoundHere();
      this.status(`Connecting to ${peer.name}…`, "retry");
      this.conn = await this.node.dial(peer, "audio");
      const fmt = await this.input.start((pcm) => {
        queue.push(pcm);
        // only fresh sound goes out: 10 ms chunks, at most ~60 ms waiting (200 over Tailscale)
        const keep = this.conn?.route === "tailscale" ? 20 : 6;
        while (queue.length > keep) queue.shift();
        wake?.();
      });
      capturing = true;

      const handshake = async (conn: Conn) => {
        const remote = conn.route === "tailscale";
        await conn.sendJson({ rate: fmt.rate, channels: fmt.channels, format: "s16", remote,
          buffer_ms: remote ? 200 : 40, from: this.node.name });       // phones capture 16-bit already
        conn.setTimeout(REPLY_WAIT);
        let reply: any = { ok: true };
        try {
          reply = await conn.recvJson();
        } catch (e) {
          if (!/timed out/.test((e as Error).message)) throw e;
        }
        conn.setTimeout(0);
        if (reply.ok === false) throw new Refused(String(reply.error || "it said no").slice(0, 160));
        void (async () => {                                // the receiver may tell us to stop
          try {
            for (;;) {
              const m = await conn.recvJson();
              if (m.type === "stop") {
                if (m.moved || conn !== this.conn) return;   // the route we're leaving
                reason = String(m.reason || "stopped there").slice(0, 160);
                this.stop();
                return;
              }
            }
          } catch {
            /* closed */
          }
        })();
        this.status(`Playing on ${peer.name} · ${fmt.rate / 1000} kHz${remote ? ", via Tailscale" : ""}`, "playing");
      };

      // Over Tailscale but back on the same Wi-Fi: connect there in the background, then switch.
      const tryWifi = async () => {
        moving = true;
        try {
          const conn = await this.node.dial(peer, "audio", false, false, true);
          if (this.stopRequested) {
            conn.close();
            return;
          }
          await handshake(conn);
          const old = this.conn;
          this.conn = conn;
          old?.close();
          this.node.log("info", `sound to ${peer.name} moved from Tailscale to Wi-Fi`);
        } catch (e) {
          peer.lanFailedAt = Date.now();
          this.node.log("info", `sound to ${peer.name} stays on Tailscale: ${why(e as Error)}`);
        } finally {
          moving = false;
        }
      };

      const send = async (data: Uint8Array) => {
        for (let i = 0; i < 2; i++) {
          const conn = this.conn!;
          try {
            await conn.send(data);
            return;
          } catch (e) {
            // the receiver closes the old route as soon as the new one says hello: carry on there
            for (let w = 0; w < 400 && moving; w++) await delay(20);
            if (this.conn === conn) throw e;
          }
        }
      };

      await handshake(this.conn);
      let lastSent = Date.now();
      let nextCheck = Date.now() + 3000;
      while (!this.stopRequested) {
        if (this.conn!.route === "tailscale" && Date.now() >= nextCheck) {
          nextCheck = Date.now() + 3000;
          if (!moving && peer.shouldMove) void tryWifi();
        }
        const pcm = queue.shift();
        if (!pcm) {
          if (Date.now() - lastSent > 5000) {
            await send(new Uint8Array(0));        // keep-alive while silent
            lastSent = Date.now();
          }
          await new Promise<void>((r) => {
            wake = r;
            setTimeout(r, 500);
          });
          wake = null;
          continue;
        }
        await send(pcm);
        lastSent = Date.now();
      }
      if (reason) this.status(`Stopped: ${reason}.`, "off");
      else this.status("", "idle");
    } catch (e) {
      const err = e as Error;
      if (reason) this.status(`Stopped: ${reason}.`, "off");
      else if (err instanceof Refused) this.status(`${peer.name} didn't play it: ${err.message}.`, "off");
      else if (this.stopRequested) this.status("", "idle");
      else if (/permission|denied|allow/i.test(err.message)) this.status(err.message, "off");
      else this.status(`Lost ${peer.name} (${why(err)}). Trying again…`, "retry");
    } finally {
      if (capturing) this.input.stop();
      this.conn?.close();
      this.conn = null;
      this.running = false;
    }
  }
}

// ---------------------------------------------------------------- helpers
/** PCM from one sample format to the other (little-endian, interleaved). */
export function convertPcm(pcm: Uint8Array, from: string, to: string): Uint8Array {
  if (from === to) return pcm;
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  if (from === "f32") {
    const out = new Uint8Array(pcm.length / 2);
    const ov = new DataView(out.buffer);
    for (let i = 0, j = 0; i + 4 <= pcm.length; i += 4, j += 2) {
      const v = Math.max(-1, Math.min(1, view.getFloat32(i, true)));
      ov.setInt16(j, Math.round(v * 32767), true);
    }
    return out;
  }
  const out = new Uint8Array(pcm.length * 2);
  const ov = new DataView(out.buffer);
  for (let i = 0, j = 0; i + 2 <= pcm.length; i += 2, j += 4) ov.setFloat32(j, view.getInt16(i, true) / 32768, true);
  return out;
}

function concatBytes(a: Uint8Array, b: Uint8Array) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

export function delay(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms));
}

function firstOf<T extends { close(): void }>(ps: Promise<T>[]): Promise<T> {
  return new Promise((resolve, reject) => {
    let left = ps.length;
    let won = false;
    let last: unknown = new Error("no address");
    for (const p of ps) {
      p.then((v) => {
        if (won) v.close();
        else {
          won = true;
          resolve(v);
        }
      }, (e) => {
        last = e;
        if (--left === 0 && !won) reject(last);
      });
    }
  });
}

/** Addresses to treat as Tailscale ones (tests simulate a second route with these). */
export const extraTailscaleAddrs = new Set<string>();

export function isTailscale(addr: string): boolean {
  const a = addr.replace(/^ts:/, "");
  if (extraTailscaleAddrs.has(a)) return true;
  if (a.toLowerCase().startsWith("fd7a:115c:a1e0")) return true;
  const m = /^(\d+)\.(\d+)\./.exec(a);
  return !!m && Number(m[1]) === 100 && Number(m[2]) >= 64 && Number(m[2]) <= 127;
}

export function why(e: Error): string {
  const t = e?.message || String(e);
  if (/timed out|ETIMEDOUT/i.test(t)) return "no response in time";
  if (/ECONNREFUSED|refused/i.test(t)) return "WinLink isn't open there";
  if (/EHOSTUNREACH|ENETUNREACH|unreachable/i.test(t)) return "not reachable from this network";
  if (e instanceof Closed) return "the connection closed";
  return t.slice(0, 160);
}

export function cleanName(name: unknown): string {
  const s = String(name ?? "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return s.slice(0, 48) || "Unknown device";
}

function cleanHello(h: any) {
  if (typeof h.id !== "string" || !ID_RE.test(h.id)) throw new Error("bad id");
  const port = h.port ?? PORT;
  if (!Number.isInteger(port) || port <= 0 || port >= 65536) throw new Error("bad port");
  return { type: h.type as "control" | "file" | "audio", id: h.id as string, name: cleanName(h.name),
    os: OS_NAMES.includes(h.os) ? (h.os as string) : "?", port: port as number,
    nonce: unb64(String(h.nonce ?? ""), 16), pair: !!h.pair, manual: !!h.manual };
}

function cleanClip(msg: any): { kind: "text"; text: string } | { kind: "image"; png?: string; bmp?: string } | null {
  if (msg.kind === "text" && typeof msg.text === "string") return { kind: "text", text: msg.text.slice(0, 4 << 20) };
  if (msg.kind === "image" && (typeof msg.png === "string" || typeof msg.bmp === "string")) {
    return { kind: "image", png: typeof msg.png === "string" ? msg.png : undefined, bmp: typeof msg.bmp === "string" ? msg.bmp : undefined };
  }
  return null;
}

function cleanSound(p: any): SoundInfo {
  const rate = p.rate;
  const ch = p.channels;
  if (!Number.isInteger(rate) || rate < 8000 || rate > 192000 || !Number.isInteger(ch) || ch < 1 || ch > 8) {
    throw new Error("bad audio format");
  }
  let bufferMs = p.buffer_ms ?? 120;
  if (!Number.isInteger(bufferMs) || bufferMs < 20 || bufferMs > 2000) bufferMs = 120;
  return { rate, channels: ch, format: p.format === "f32" ? "f32" : "s16", remote: !!p.remote, bufferMs };
}

function resumeKey(myId: string, src: FileSource): string {
  // sha1 hex of id|name|size|key, like the desktop (any stable 40-hex string works)
  return hex(sha1(enc(`${myId}|${src.name}|${src.size}|${src.key}`)));
}
