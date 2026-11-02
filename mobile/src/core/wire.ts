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

export class Conn {
  private buf: Uint8Array = new Uint8Array(0);       // raw bytes not consumed yet
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
      const bytes = typeof d === "string" ? enc(d) : new Uint8Array(d.buffer, d.byteOffset, d.byteLength);
      this.buf = this.buf.length ? concat(this.buf, bytes) : bytes.slice();
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
      const done = () => {
        if (t) clearTimeout(t);
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

  async recvText(limit = MAX_HANDSHAKE_LINE): Promise<string> {
    for (;;) {
      const i = this.buf.indexOf(10);
      if (i >= 0) {
        const line = this.buf.subarray(0, i);
        this.buf = this.buf.subarray(i + 1);
        return dec(line).replace(/\r$/, "");
      }
      if (this.buf.length > limit) throw new Error("line too long");
      await this.wait();
    }
  }

  async recvLine(): Promise<any> {
    for (;;) {
      const i = this.buf.indexOf(10);
      if (i >= 0) {
        const line = this.buf.subarray(0, i);
        this.buf = this.buf.subarray(i + 1);
        return parseObject(dec(line));
      }
      if (this.buf.length > MAX_HANDSHAKE_LINE) throw new Error("handshake line too long");
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

  /** Send bytes encrypted; resolves once the socket can take more (backpressure). */
  async send(data: Uint8Array): Promise<void> {
    if (!this.sealer) throw new Error("not encrypted yet");
    const ok = this.writeRaw(this.sealer.seal(data));
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
      if (this.buf.length >= 4) {
        const size = new DataView(this.buf.buffer, this.buf.byteOffset, 4).getUint32(0);
        if (size < 16 || size > FRAME + 16) throw new Error("bad frame");
        if (this.buf.length >= 4 + size) {
          const ct = this.buf.slice(4, 4 + size);
          this.buf = this.buf.subarray(4 + size);
          const pt = this.sealer!.open(ct);
          this.plain = this.plain.length ? concat(this.plain, pt) : pt;
          return true;
        }
      }
      try {
        await this.wait();
      } catch (e) {
        if (e instanceof Closed && this.buf.length === 0) return false;
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
