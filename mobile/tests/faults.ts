// Network trouble between the phone (this TypeScript) and a real desktop node (Python): connections
// cut mid-way, and links that go silent (Wi-Fi gone: no error, the data just stops) during the
// control link, file transfers both ways, sound both ways and the handshake. Everything runs through
// a local proxy that can cut, stall and restore traffic. Uses private ports and "ZZ … 7731" names.
import { spawn } from "node:child_process";
import { createHash, randomBytes as nodeRandom, webcrypto } from "node:crypto";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { HARNESS, PYTHON, ROOT } from "./paths";
(globalThis as any).crypto ??= webcrypto;
import { FileSource, Node, Platform, SoundSender, delay } from "../src/core/node";
import { hex, randomBytes } from "../src/core/bytes";

const only = process.argv[2] ?? "";
const results: [string, boolean][] = [];
const check = (name: string, ok: boolean, info = "") => { results.push([name, ok]); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${info}`); };
const until = async <T>(f: () => T, ms: number): Promise<T | null> => { const end = Date.now() + ms; while (Date.now() < end) { const v = f(); if (v) return v; await delay(100); } return null; };
const secs = (t: number) => `${((Date.now() - t) / 1000).toFixed(1)} s`;
let crashes = 0;
process.on("unhandledRejection", (e) => { crashes++; console.log("UNHANDLED REJECTION", e); });
process.on("uncaughtException", (e) => { crashes++; console.log("UNCAUGHT", e); });

// ---------------------------------------------------------------- a proxy that can misbehave
class Proxy {
  mode: "pass" | "stall" = "pass";
  rate = 4 * 2 ** 20;                     // bytes per second each way
  pairs = new Set<{ a: net.Socket; b: net.Socket | null; pumps: (() => void)[] }>();
  private waiting: (() => void)[] = [];
  private later: (() => void)[] = [];
  port = 0;
  constructor(private target: number) {}

  listen(): Promise<number> {
    const srv = net.createServer((a) => {
      const pair = { a, b: null as net.Socket | null, pumps: [] as (() => void)[] };
      this.pairs.add(pair);
      a.on("error", () => undefined);
      const go = () => {
        const b = net.createConnection({ host: "127.0.0.1", port: this.target });
        pair.b = b;
        // while stalled nothing gets through, not even a reset: that waits until the network is back
        const kill = (x: net.Socket) => () => { if (this.mode === "stall") this.later.push(() => x.destroy()); else x.destroy(); };
        b.on("error", kill(a));
        a.on("error", kill(b));
        pair.pumps = [this.pipe(a, b), this.pipe(b, a)];
        a.on("close", () => this.pairs.delete(pair));
      };
      if (this.mode === "stall") this.waiting.push(go);     // like a network that doesn't answer
      else go();
    });
    return new Promise((r) => srv.listen(0, "127.0.0.1", () => { this.port = (srv.address() as net.AddressInfo).port; r(this.port); }));
  }

  private pipe(src: net.Socket, dst: net.Socket) {
    const q: Buffer[] = [];
    let queued = 0, ended = false;
    src.on("data", (d: Buffer) => { q.push(d); queued += d.length; if (queued > 2 ** 20) src.pause(); });
    src.on("end", () => { ended = true; });
    src.on("close", () => { ended = true; });
    const tick = setInterval(() => {
      if (this.mode === "stall") { src.pause(); return; }
      let budget = Math.floor(this.rate / 100);
      while (q.length && budget > 0) {
        let c = q[0];
        if (c.length > budget) { q[0] = c.subarray(budget); c = c.subarray(0, budget); } else q.shift();
        budget -= c.length; queued -= c.length;
        if (!dst.destroyed) dst.write(c);
      }
      if (queued < 2 ** 18 && !src.destroyed) src.resume();
      if (ended && !q.length) { clearInterval(tick); if (!dst.destroyed) dst.end(); }
    }, 10);
    return () => clearInterval(tick);
  }

  cut() {
    for (const p of this.pairs) { p.pumps.forEach((f) => f()); p.a.destroy(); p.b?.destroy(); }
    this.pairs.clear();
  }
  stall() { this.mode = "stall"; }
  restore() {
    this.mode = "pass";
    const w = [...this.waiting, ...this.later];
    this.waiting = []; this.later = [];
    w.forEach((f) => f());
  }
}

// ---------------------------------------------------------------- the desktop
const DESK_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wldesk"));
const DESK_DL = fs.mkdtempSync(path.join(os.tmpdir(), "wldeskdl"));
const DESK_PORT = 51580, PHONE_PORT = 51581;
const desk = spawn(PYTHON, ["-u", path.join(HARNESS, "desk_fault.py")], {
  env: { ...process.env, LINK_HOME: DESK_HOME, LINK_PORT: String(DESK_PORT), LINK_NO_BEACON: "1", LINK_NAME: "ZZ Desk 7731", LINK_DOWNLOADS: DESK_DL },
});
const events: any[] = [];
let partial = "";
desk.stdout.on("data", (d) => { partial += d; let i; while ((i = partial.indexOf("\n")) >= 0) { const l = partial.slice(0, i); partial = partial.slice(i + 1); try { const e = JSON.parse(l); e.at = Date.now(); events.push(e); if (e.ev === "thread_crash") { crashes++; console.log("DESKTOP THREAD CRASH", e); } } catch { /* log line */ } } });
desk.stderr.on("data", (d) => { const s = String(d); if (/Traceback|Error/.test(s)) console.log("desk stderr:", s.slice(0, 400)); });
const send = (o: any) => desk.stdin.write(JSON.stringify(o) + "\n");
const since = (t: number, f: (e: any) => boolean) => events.find((e) => e.at >= t && f(e));

// ---------------------------------------------------------------- the phone
const HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wlphone"));
const partials = path.join(HOME, "partials"); fs.mkdirSync(partials);
const received: { path: string; at: number }[] = [];
let soundBytes = 0, soundStopped = false, phoneSound: { text: string; state: string; at: number }[] = [];
const plat: Platform = {
  net: { createConnection: (o, cb) => net.createConnection(o, cb) as any, createServer: (cb) => net.createServer(cb as any) as any },
  os: "android",
  saveSettings: () => undefined,
  saver: {
    freeSpace: () => 50 * 2 ** 30,
    async openPartial(key, resume) {
      const p = path.join(partials, key);
      if (!resume && fs.existsSync(p)) fs.unlinkSync(p);
      const offset = fs.existsSync(p) ? fs.statSync(p).size : 0;
      const fd = fs.openSync(p, offset ? "a" : "w");
      return { offset, write: async (b) => { fs.writeSync(fd, b); }, close: async () => { try { fs.closeSync(fd); } catch { /* closed */ } } };
    },
    async finish(key, rel) { const d = path.join(HOME, "got_" + rel.replace(/\//g, "_")); fs.renameSync(path.join(partials, key), d); received.push({ path: d, at: Date.now() }); return d; },
    async discard(key) { try { fs.unlinkSync(path.join(partials, key)); } catch { /* gone */ } },
    async remove(p) { try { fs.unlinkSync(p); } catch { /* gone */ } },
  },
  soundOut: { start: () => { soundStopped = false; }, write: (b) => { soundBytes += b.length; }, stop: () => { soundStopped = true; } },
  soundIn: null,
  log: (lv, m) => { if (lv === "warn" || lv === "error") console.log(`  phone ${lv}: ${m}`); },
};
const phoneEvents: any[] = [];
const phone = new Node({ id: hex(randomBytes(16)), name: "ZZ Phone 7731", trusted: {} }, plat, (e) => {
  phoneEvents.push({ ...e, at: Date.now() });
  if (e.kind === "sound_out") phoneSound.push({ text: e.text, state: e.state, at: Date.now() });
});
let pcmTimer: ReturnType<typeof setInterval> | null = null;
const fakeMic = {
  async start(onPcm: (b: Uint8Array) => void) { pcmTimer = setInterval(() => onPcm(new Uint8Array(1920 * 2)), 10); return { rate: 48000, channels: 2 }; },
  stop() { if (pcmTimer) clearInterval(pcmTimer); pcmTimer = null; },
};
const phoneSender = new SoundSender(phone, fakeMic, () => "cd");
phone.soundSending = phoneSender;

const sha = (p: string) => createHash("sha256").update(fs.readFileSync(p)).digest("hex");
function bigFile(name: string, mb: number) {
  const p = path.join(HOME, name);
  fs.writeFileSync(p, nodeRandom(mb * 2 ** 20));
  return p;
}
function source(p: string): FileSource {
  const data = fs.readFileSync(p);
  return { name: path.basename(p), size: data.length, key: p, read: async (o, n) => new Uint8Array(data.subarray(o, o + n)) };
}

(async () => {
  const toDesk = new Proxy(DESK_PORT), toPhone = new Proxy(PHONE_PORT);
  try {
    const ready = await until(() => events.find((e) => e.ev === "ready"), 15000);
    if (!ready) throw new Error("desktop didn't start");
    await toDesk.listen(); await toPhone.listen();
    phone.start(PHONE_PORT);
    await delay(300);
    phone.serverPort = toPhone.port;                  // the desktop reaches the phone through the proxy too
    phone.seen({ id: ready.id, name: "ZZ Desk 7731", os: "mac", port: toDesk.port }, ["127.0.0.1"]);
    const peer = phone.peers.get(ready.id)!;
    phone.connect(peer, true);
    await until(() => events.find((e) => e.ev === "pair_code"), 10000);
    check("paired and connected", !!(await until(() => peer.control, 10000)));
    const phoneId = phone.id;
    const run = (name: string) => !only || name.startsWith(only);

    if (run("control")) {
      // 1. connection cut
      let t = Date.now();
      const first = peer.control;
      toDesk.cut();
      const back = await until(() => peer.control && peer.control !== first, 20000);
      check("control: reconnects after a cut", !!back, secs(t));

      // 2. silent link (Wi-Fi gone)
      t = Date.now();
      const before = peer.control;
      toDesk.stall();
      const noticed = await until(() => peer.control !== before, 30000);
      check("control: a silent link is noticed", !!noticed, secs(t));
      const probe = Date.now();
      const err = await phone.probe("127.0.0.1", toDesk.port).then(() => "", (e) => String(e.message));
      check("handshake on a silent link gives up", !!err && Date.now() - probe < 20000, `${secs(probe)} (${err})`);
      await delay(5000);
      t = Date.now();
      toDesk.restore();
      check("control: reconnects when the network is back", !!(await until(() => peer.control, 70000)), secs(t));
    }

    if (run("file-out")) {
      for (const how of ["cut", "stall"] as const) {
        await until(() => peer.control, 30000);
        peer.port = toDesk.port;
        const p = bigFile(`phone-${how}.bin`, 24);
        const t = Date.now();
        const batch = phone.sendFiles(peer, [source(p)])!;
        await until(() => batch.done > 6 * 2 ** 20, 20000);
        let noticed = "";
        if (how === "cut") toDesk.cut();
        else {
          toDesk.stall();
          noticed = (await until(() => batch.error?.startsWith("retrying") && secs(t), 50000)) || "";
          toDesk.restore();
        }
        const got = await until(() => since(t, (e) => e.ev === "received" && e.path.endsWith(path.basename(p))), 120000);
        await until(() => batch.state !== "active", 10000);
        const ok = !!got && got.size === fs.statSync(p).size && sha(got.path) === sha(p);
        if (how === "stall") check("file phone → desktop: silent link noticed", !!noticed, noticed);
        check(`file phone → desktop survives a ${how}`, ok && batch.state === "done", `${secs(t)}, state ${batch.state}${batch.error ? `, ${batch.error}` : ""}`);
      }
    }

    if (run("file-in")) {
      for (const how of ["cut", "stall"] as const) {
        await until(() => peer.control, 30000);
        const p = bigFile(`desk-${how}.bin`, 24);
        const t = Date.now();
        send({ cmd: "send", peer: phoneId, paths: [p] });
        await until(() => [...phone.batches.values()].find((b) => b.dir === "in" && b.done > 6 * 2 ** 20 && b.updated >= t), 20000);
        let noticed = "";
        if (how === "cut") toPhone.cut();
        else {
          toPhone.stall();
          noticed = (await until(() => since(t, (e) => e.ev === "batch" && /retrying/.test(e.error ?? "")) && secs(t), 50000)) || "";
          toPhone.restore();
        }
        const got = await until(() => received.find((r) => r.at >= t && r.path.endsWith(path.basename(p))), 120000);
        if (how === "stall") check("file desktop → phone: silent link noticed", !!noticed, noticed);
        check(`file desktop → phone survives a ${how}`, !!got && sha(got.path) === sha(p), secs(t));
      }
    }

    if (run("sound-in")) {
      await until(() => peer.control, 30000);
      let t = Date.now();
      send({ cmd: "sound", peer: phoneId });
      check("sound desktop → phone plays", !!(await until(() => soundBytes > 100000, 10000)));
      t = Date.now();
      toPhone.stall();
      const stopped = await until(() => soundStopped && secs(t), 45000);
      check("sound desktop → phone: phone notices the silent link", !!stopped, stopped || "");
      const retry = await until(() => since(t, (e) => e.ev === "sound_out" && e.state === "retry" && !/^Connecting/.test(e.text)) && secs(t), 45000);
      check("sound desktop → phone: desktop notices it", !!retry, retry || "");
      // stopping while the link is silent must be quick
      toPhone.restore();
      await delay(1000);
      send({ cmd: "sound", peer: phoneId });
      await until(() => since(t, (e) => e.ev === "sound_out" && e.state === "playing" && e.at > Date.now() - 20000), 20000);
      const playing = Date.now();
      check("sound desktop → phone resumes when the network is back", !!(await until(() => soundBytes > 0 && !soundStopped, 15000)));
      toPhone.stall();
      await delay(2000);
      t = Date.now();
      send({ cmd: "sound_stop" });
      const idle = await until(() => since(t, (e) => e.ev === "sound_out" && (e.state === "idle" || e.state === "off")) && secs(t), 20000);
      check("sound: Stop works even on a silent link", !!idle && Date.now() - t < 5000, idle || `no answer (started playing ${secs(playing)} ago)`);
      toPhone.restore();
      toPhone.cut();
    }

    if (run("sound-out")) {
      await until(() => peer.control, 40000);
      peer.port = toDesk.port;
      const t0 = Date.now();
      phoneSender.start(peer);
      check("sound phone → desktop plays", !!(await until(() => phoneSound.find((s) => s.at >= t0 && s.state === "playing"), 10000)));
      const t = Date.now();
      toDesk.stall();
      const noticed = await until(() => phoneSound.find((s) => s.at >= t && s.state === "retry") && secs(t), 50000);
      check("sound phone → desktop: phone notices the silent link", !!noticed, noticed || "");
      phoneSender.stop();
      const s2 = Date.now();
      const ended = await until(() => !phoneSender.running, 5000);
      check("sound phone → desktop: Stop is quick", !!ended, secs(s2));
      toDesk.restore();
    }
  } catch (e) {
    check("no crash", false, String((e as Error)?.stack ?? e));
  } finally {
    check("no unhandled errors on either side", crashes === 0, `${crashes}`);
    phone.stop(); desk.kill(); toDesk.cut(); toPhone.cut();
    const ok = results.filter((r) => r[1]).length;
    console.log(`\n${ok}/${results.length} passed`);
    process.exit(ok === results.length ? 0 : 1);
  }
})();
