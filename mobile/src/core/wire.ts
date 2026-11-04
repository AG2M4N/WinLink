// One TCP connection: cleartext JSON lines for the handshake, then encrypted frames.
// Works over Node's `net` sockets (tests) and react-native-tcp-socket (the app): both look the same.
import { concat, dec, enc } from "./bytes";
import { FRAME, Sealer } from "./secure";

export interface RawSocket {
  write(data: Uint8Array): boolean;
  on(ev: "data", cb: (d: Uint8Array | string) => void): unknown;
  on(ev: "close" | "drain" | "connect", cb: () => void): unknown;
  on(ev: "error", cb: (e: Error) => void): unknown;
  destroy(): void;
  setNoDelay?(v?: boolean): unknown;
  pause?(): unknown;
  resume?(): unknown;
  remoteAddress?: string;
}

export class Closed extends Error {
  constructor(msg = "connection closed") {
    super(msg);
  }
}

const MAX_HANDSHAKE_LINE = 8 * 1024;
// A send that can't get anything out for this long means the network is gone (Wi-Fi dropped without
// an error): close, so transfers retry and sound reconnects instead of hanging. Same as the computers.
const WRITE_TIMEOUT = 30000;
// Incoming bytes waiting to be decrypted: above HIGH the socket stops reading (the sender then waits,
// as TCP does), below LOW it reads again. Otherwise a computer on fast Wi-Fi sends far quicker than the
// phone can decrypt, and the backlog grows until the app runs out of memory and time.
const HIGH = 4 * 2 ** 20;
const LOW = 1 * 2 ** 20;
// Decrypting and encrypting run on the JS thread, the same thread that draws the screen. Long runs of
// them (a big transfer with data always waiting) give the screen a turn every BREATH ms.
const BREATH = 25;
let lastBreath = Date.now();
// Only while the app is on screen: in the background there's nothing to draw, and React Native may
// stop running timers there, so a turn that waits on one could never end and the connection would
// hang (the computer then sees the phone go offline while the phone still thinks it's connected).
let onScreen = true;
const breathing = new Set<() => void>();

/** The app came on screen (true) or left it (false). Going off screen ends any turn in progress. */
export function setOnScreen(on: boolean) {
  onScreen = on;
  if (!on) {
    const waiting = [...breathing];
    breathing.clear();
    waiting.forEach((go) => go());
  }
}

/** Let the screen and touches run if the JS thread has been busy for a while. */
export async function breathe(): Promise<void> {
  if (!onScreen || Date.now() - lastBreath < BREATH) return;
  await new Promise<void>((resolve) => {
    let done = false;
    const go = () => {
      if (done) return;
      done = true;
      breathing.delete(go);
      resolve();
    };
    breathing.add(go);
    setTimeout(go, 0);
  });
  lastBreath = Date.now();
}

export class Conn {
  private buf: Uint8Array = new Uint8Array(0);       // raw bytes being read (handshake lines, the frame in hand)
  private queue: Uint8Array[] = [];                  // raw bytes received after that, not looked at yet
  private queued = 0;
  private paused = false;
  private bigSend: Promise<void> | null = null;      // a multi-frame send under way (others wait for it)
  private plain: Uint8Array = new Uint8Array(0);     // decrypted bytes not consumed yet
  private waiters: (() => void)[] = [];
  private drainWaiters: (() => void)[] = [];
  closed = false;
  error: Error | null = null;
  sealer: Sealer | null = null;
  route = "wifi";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private timeoutMs = 0;

  constructor(public sock: RawSocket, public addr = "") {
    sock.on("data", (d) => {
      const bytes = typeof d === "string" ? enc(d) : new Uint8Array(d.buffer, d.byteOffset, d.byteLength).slice();
      if (!bytes.length) return;
      // Kept as a list: joining every arrival onto one growing buffer copies it all again each time,
      // which on a big transfer is what froze the app.
      this.queue.push(bytes);
      this.queued += bytes.length;
      if (!this.paused && this.queued > HIGH && this.sock.pause) {
        this.paused = true;
        this.sock.pause();
      }
      this.wake();
    });
    sock.on("close", () => this.fail(new Closed()));
    sock.on("error", (e) => this.fail(e));
    sock.on("drain", () => {
      const w = this.drainWaiters;
      this.drainWaiters = [];
      w.forEach((f) => f());
    });
    sock.setNoDelay?.(true);
  }

  private fail(e: Error) {
    if (!this.closed) {
      this.closed = true;
      this.error = e;
    }
    this.wake();
    const w = this.drainWaiters;
    this.drainWaiters = [];
    w.forEach((f) => f());
  }

  /** Move queued bytes into `buf` until it holds at least n (or the queue runs out). True if it does. */
  private ensure(n: number): boolean {
    if (this.buf.length >= n || !this.queue.length) return this.buf.length >= n;
    const parts = [this.buf];
    let total = this.buf.length;
    while (total < n && this.queue.length) {
      const c = this.queue.shift()!;
      parts.push(c);
      total += c.length;
      this.queued -= c.length;
    }
    this.buf = parts.length === 2 && !this.buf.length ? parts[1] : concat(...parts);
    if (this.paused && this.queued < LOW) {
      this.paused = false;
      this.sock.resume?.();
    }
    return this.buf.length >= n;
  }

  private wake() {
    const w = this.waiters;
    this.waiters = [];
    w.forEach((f) => f());
  }

  /** Reads fail with "timed out" after ms without any data (0 = never). */
  setTimeout(ms: number) {
    this.timeoutMs = ms;
  }

  private wait(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.closed) return reject(this.error ?? new Closed());
      let t: ReturnType<typeof setTimeout> | null = null;
      const since = Date.now();
      const done = () => {
        if (t) clearTimeout(t);
        // Waited a while: the JS thread was idle, so the screen had its turn. (A wait that ends at
        // once doesn't count: incoming data can wake us again and again without the screen running.)
        if (Date.now() - since >= 5) lastBreath = Date.now();
        resolve();
      };
      this.waiters.push(done);
      if (this.timeoutMs) {
        t = setTimeout(() => {
          this.waiters = this.waiters.filter((f) => f !== done);
          reject(new Error("timed out"));
        }, this.timeoutMs);
      }
    });
  }

  close() {
    if (!this.closed) {
      this.closed = true;
      this.error = new Closed();
    }
    try {
      this.sock.destroy();
    } catch {
      /* already gone */
    }
    this.wake();
  }

  // ---- cleartext (handshake)
  sendLine(obj: unknown) {
    this.writeRaw(enc(JSON.stringify(obj) + "\n"));
  }

  /** A plain text line (the Tailscale helper's own protocol). */
  sendText(line: string) {
    this.writeRaw(enc(line + "\n"));
  }

  /** The next cleartext line, without the newline (null: none yet; throws past `limit`). */
  private takeLine(limit: number, what: string): Uint8Array | null {
    for (;;) {
      const i = this.buf.indexOf(10);
      if (i >= 0) {
        const line = this.buf.subarray(0, i);
        this.buf = this.buf.subarray(i + 1);
        return line;
      }
      if (this.buf.length > limit) throw new Error(what);
      if (!this.ensure(this.buf.length + 1)) return null;
    }
  }

  async recvText(limit = MAX_HANDSHAKE_LINE): Promise<string> {
    for (;;) {
      const line = this.takeLine(limit, "line too long");
      if (line) return dec(line).replace(/\r$/, "");
      await this.wait();
    }
  }

  async recvLine(): Promise<any> {
    for (;;) {
      const line = this.takeLine(MAX_HANDSHAKE_LINE, "handshake line too long");
      if (line) return parseObject(dec(line));
      await this.wait();
    }
  }

  private writeRaw(data: Uint8Array): boolean {
    if (this.closed) throw this.error ?? new Closed();
    return this.sock.write(data);
  }

  // ---- encrypted
  startEncryption(sealer: Sealer) {
    this.sealer = sealer;
  }

  /** Send bytes encrypted; resolves once the socket can take more (backpressure).
   * Up to one frame goes out at once, as one piece. More (a file chunk) goes a frame at a time with
   * breaks for the screen in between; other sends wait for it, so their frames never land inside it. */
  async send(data: Uint8Array): Promise<void> {
    if (!this.sealer) throw new Error("not encrypted yet");
    while (this.bigSend) await this.bigSend.catch(() => undefined);
    if (data.length <= FRAME) return this.sendSealed(this.sealer.seal(data));
    const big = (async () => {
      for (let i = 0; i < data.length; i += FRAME) {
        await this.sendSealed(this.sealer!.seal(data.subarray(i, Math.min(i + FRAME, data.length))));
        await breathe();
      }
    })();
    this.bigSend = big;
    try {
      await big;
    } finally {
      if (this.bigSend === big) this.bigSend = null;
    }
  }

  private async sendSealed(frames: Uint8Array): Promise<void> {
    const ok = this.writeRaw(frames);
    if (!ok && !this.closed) {
      await new Promise<void>((resolve, reject) => {
        const done = () => {
          clearTimeout(t);
          resolve();
        };
        const t = setTimeout(() => {
          this.drainWaiters = this.drainWaiters.filter((f) => f !== done);
          const e = new Error("timed out: nothing got through for 30 s");
          this.fail(e);
          try {
            this.sock.destroy();
          } catch {
            /* already gone */
          }
          reject(e);
        }, WRITE_TIMEOUT);
        this.drainWaiters.push(done);
      });
      if (this.closed) throw this.error ?? new Closed();
    }
  }

  sendJson(obj: unknown): Promise<void> {
    return this.send(enc(JSON.stringify(obj) + "\n"));
  }

  /** Decrypt one more frame into `plain`; false at a clean end of stream. */
  private async fill(): Promise<boolean> {
    for (;;) {
      if (this.ensure(4)) {
        const size = new DataView(this.buf.buffer, this.buf.byteOffset, 4).getUint32(0);
        if (size < 16 || size > FRAME + 16) throw new Error("bad frame");
        if (this.ensure(4 + size)) {
          const ct = this.buf.slice(4, 4 + size);
          this.buf = this.buf.subarray(4 + size);
          const pt = this.sealer!.open(ct);
          this.plain = this.plain.length ? concat(this.plain, pt) : pt;
          await breathe();
          return true;
        }
      }
      try {
        await this.wait();
      } catch (e) {
        if (e instanceof Closed && this.buf.length === 0 && this.queued === 0) return false;
        throw e;
      }
    }
  }

  /** Up to n decrypted bytes (empty at the end of the stream). */
  async read(n: number): Promise<Uint8Array> {
    while (!this.plain.length) {
      if (!(await this.fill())) return new Uint8Array(0);
    }
    const out = this.plain.subarray(0, n);
    this.plain = this.plain.subarray(out.length);
    return out;
  }

  async recvJson(limit = 64 * 1024): Promise<any> {
    for (;;) {
      const i = this.plain.indexOf(10);
      if (i >= 0) {
        const line = this.plain.subarray(0, i);
        this.plain = this.plain.subarray(i + 1);
        return parseObject(dec(line));
      }
      if (this.plain.length > limit) throw new Error("message too long");
      if (!(await this.fill())) throw new Closed();
    }
  }
}

function parseObject(text: string): any {
  const obj = JSON.parse(text);
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) throw new Error("not an object");
  return obj;
}
