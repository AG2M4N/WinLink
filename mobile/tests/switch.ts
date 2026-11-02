// Moving from Tailscale to Wi-Fi mid-way, phone (this TypeScript) <-> a real desktop node (Python).
// "Tailscale" is simulated by a second address on this Mac ([::1], through a 4 MB/s proxy, or
// 127.0.0.1 for the desktop in phase 2). Private ports, "ZZ … 7731" names, nothing on the network.
import { spawn } from "node:child_process";
import { createHash, randomBytes as nodeRandom, webcrypto } from "node:crypto";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { HARNESS, PYTHON, ROOT } from "./paths";
(globalThis as any).crypto ??= webcrypto;
import { extraTailscaleAddrs, FileSource, Node, Platform, SoundSender, delay } from "../src/core/node";
import { hex, randomBytes } from "../src/core/bytes";

const results: boolean[] = [];
const check = (name: string, ok: boolean, info = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${info}`); };
const until = async <T>(f: () => T, ms: number): Promise<T | null> => { const end = Date.now() + ms; while (Date.now() < end) { const v = f(); if (v) return v; await delay(100); } return null; };
let crashes = 0;
process.on("unhandledRejection", (e) => { crashes++; console.log("UNHANDLED", e); });

function proxy(host: string, port: number, target: number, rate = 4 * 2 ** 20) {
  const srv = net.createServer((a) => {
    const b = net.createConnection({ host: "127.0.0.1", port: target });
    const pipe = (x: net.Socket, y: net.Socket) => {
      x.on("data", (d) => { x.pause(); y.write(d, () => setTimeout(() => x.resume(), (d.length / rate) * 1000)); });
      x.on("end", () => y.end()); x.on("error", () => y.destroy()); x.on("close", () => y.destroy());
    };
    pipe(a, b); pipe(b, a);
  });
  return new Promise<net.Server>((r) => srv.listen({ host, port }, () => r(srv)));
}

function desk(home: string, port: number, tsAddr: string, quality: string) {
  const p = spawn(PYTHON, ["-u", path.join(HARNESS, "desk_switch.py")], {
    env: { ...process.env, LINK_HOME: home, LINK_PORT: String(port), LINK_NO_BEACON: "1", LINK_NAME: "ZZ Desk 7731",
      LINK_DOWNLOADS: path.join(home, "dl"), DESK_TS_ADDR: tsAddr, DESK_QUALITY: quality },
  });
  const events: any[] = [];
  let part = "";
  p.stdout.on("data", (d) => { part += d; let i; while ((i = part.indexOf("\n")) >= 0) { const l = part.slice(0, i); part = part.slice(i + 1); try { const e = JSON.parse(l); e.at = Date.now(); events.push(e); if (e.ev === "thread_crash") { crashes++; console.log("DESK CRASH", e); } } catch { /* */ } } });
  return { p, events, send: (o: any) => p.stdin.write(JSON.stringify(o) + "\n") };
}

function phoneNode(home: string, name: string) {
  fs.mkdirSync(path.join(home, "parts"), { recursive: true });
  const st = { starts: 0, bytes: 0, stopped: false, formats: [] as string[] };
  const sound: { text: string; state: string }[] = [];
  const plat: Platform = {
    net: { createConnection: (o, cb) => net.createConnection(o, cb) as any, createServer: (cb) => net.createServer(cb as any) as any },
    os: "android", saveSettings: () => undefined,
    saver: {
      freeSpace: () => 50 * 2 ** 30,
      async openPartial(key, resume) { const p = path.join(home, "parts", key); if (!resume && fs.existsSync(p)) fs.unlinkSync(p); const offset = fs.existsSync(p) ? fs.statSync(p).size : 0; const fd = fs.openSync(p, offset ? "a" : "w"); return { offset, write: async (b) => { fs.writeSync(fd, b); }, close: async () => { try { fs.closeSync(fd); } catch { /* */ } } }; },
      async finish(key, rel) { const d = path.join(home, "got_" + rel.replace(/\//g, "_")); fs.renameSync(path.join(home, "parts", key), d); return d; },
      async discard() { /* */ }, async remove() { /* */ },
    },
    soundOut: { start: (_r, _c, fmt) => { st.starts++; st.stopped = false; st.formats.push(fmt); }, write: (b) => { st.bytes += b.length; }, stop: () => { st.stopped = true; } },
    soundIn: null,
    log: (lv, m) => { if (/moved|staying|stays/.test(m) || lv === "warn") console.log(`  phone ${lv}: ${m}`); },
  };
  const node = new Node({ id: hex(randomBytes(16)), name, trusted: {} }, plat, (e: any) => { if (e.kind === "sound_out") sound.push({ text: e.text, state: e.state }); });
  return { node, st, sound };
}

async function pair(phone: Node, d: ReturnType<typeof desk>, deskId: string, addr: string, port: number) {
  phone.seen({ id: deskId, name: "ZZ Desk 7731", os: "mac", port }, [addr]);
  const peer = phone.peers.get(deskId)!;
  phone.connect(peer, true);
  await until(() => d.events.find((e) => e.ev === "pair_code"), 10000);
  await until(() => peer.control, 10000);
  return peer;
}

(async () => {
  const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");
  // ================= phase 1: the phone moves (its control link, a file it sends, its sound)
  {
    console.log("== phase 1: the phone moves to Wi-Fi");
    extraTailscaleAddrs.clear(); extraTailscaleAddrs.add("::1");
    const H = fs.mkdtempSync(path.join(os.tmpdir(), "wlsw1"));
    const D = desk(path.join(H, "desk"), 51880, "::1", "cd");
    const ready = await until(() => D.events.find((e) => e.ev === "ready"), 15000);
    const tsProxy = await proxy("::1", 51880, 51880);
    const { node: phone, sound } = phoneNode(path.join(H, "phone"), "ZZ Phone 7731");
    phone.start(51881);
    await delay(300);
    const peer = await pair(phone, D, ready.id, "::1", 51880);
    check("pairs over Tailscale", peer.control?.route === "tailscale", peer.control?.route);

    const data = nodeRandom(40 * 2 ** 20);
    const src: FileSource = { name: "phone-big.bin", size: data.length, key: "pbig", read: async (o, n) => new Uint8Array(data.subarray(o, o + n)) };
    const t0 = Date.now();
    const batch = phone.sendFiles(peer, [src])!;
    await delay(2500);
    let home = true;
    const beacons = setInterval(() => { if (home) phone.seen({ id: ready.id, name: "ZZ Desk 7731", os: "mac", port: 51880 }, ["127.0.0.1"]); }, 2000);
    phone.seen({ id: ready.id, name: "ZZ Desk 7731", os: "mac", port: 51880 }, ["127.0.0.1"]);
    const got = await until(() => D.events.find((e) => e.ev === "received" && e.path.endsWith("phone-big.bin")), 30000);
    await until(() => batch.state !== "active", 5000);
    const took = (Date.now() - t0) / 1000;
    check("file moved to Wi-Fi and arrived intact", !!got && sha(fs.readFileSync(got.path)) === sha(data) && batch.state === "done", `${took.toFixed(1)} s (all over Tailscale: ~10 s)`);
    check("file was faster than staying on Tailscale", took < 8, `${took.toFixed(1)} s`);
    check("control link moved to Wi-Fi", !!(await until(() => peer.control?.route === "wifi", 20000)), peer.control?.route);

    home = false; peer.addrs.delete("127.0.0.1"); peer.lanFailedAt = 0;
    const sender = new SoundSender(phone, {
      async start(onPcm) { const t = setInterval(() => onPcm(new Uint8Array(1920 * 2)), 10); (this as any).t = t; return { rate: 48000, channels: 2 }; },
      stop() { clearInterval((this as any).t); },
    }, () => "cd");
    phone.soundSending = sender;
    sender.start(peer);
    check("phone's sound starts over Tailscale", !!(await until(() => sound.find((s) => s.state === "playing" && /Tailscale/.test(s.text)), 10000)));
    const startsBefore = D.events.filter((e) => e.ev === "player_start").length;
    home = true;
    phone.seen({ id: ready.id, name: "ZZ Desk 7731", os: "mac", port: 51880 }, ["127.0.0.1"]);
    check("phone's sound moved to Wi-Fi", !!(await until(() => sound.find((s) => s.state === "playing" && !/Tailscale/.test(s.text)), 15000)));
    await delay(2000);
    check("the computer's speakers weren't restarted", D.events.filter((e) => e.ev === "player_start").length === startsBefore);
    check("no stop or retry", !sound.some((s) => s.state === "off" || (s.state === "retry" && !s.text.startsWith("Connecting"))), JSON.stringify(sound.map((s) => s.state)));
    sender.stop();
    clearInterval(beacons); phone.stop(); D.p.kill(); tsProxy.close();
  }
  // ================= phase 2: the computer moves the sound it plays on the phone
  {
    console.log("== phase 2: the computer moves its sound to the phone to Wi-Fi");
    extraTailscaleAddrs.clear();
    const H = fs.mkdtempSync(path.join(os.tmpdir(), "wlsw2"));
    const D = desk(path.join(H, "desk"), 51890, "127.0.0.1", "auto");   // for the computer, 127.0.0.1 is Tailscale
    const ready = await until(() => D.events.find((e) => e.ev === "ready"), 15000);
    const { node: phone, st } = phoneNode(path.join(H, "phone"), "ZZ Phone 7731");
    phone.start(51891);
    await delay(300);
    const lanProxy = await proxy("::1", 51891, 51891, 50 * 2 ** 20);      // the phone's "Wi-Fi" address, for the computer
    const peer = await pair(phone, D, ready.id, "127.0.0.1", 51890);
    check("paired", !!peer.control);
    D.send({ cmd: "sound", peer: phone.id });
    check("the computer's sound plays on the phone (over Tailscale, CD quality)", !!(await until(() => st.bytes > 50000, 10000)), st.formats.join());
    const t1 = Date.now();
    for (let i = 0; i < 4; i++) { D.send({ cmd: "probe_seen", addr: "::1", port: 51891 }); await delay(2000); }
    const moved = D.events.find((e) => e.at >= t1 && e.ev === "sound_out" && e.state === "playing" && !/Tailscale/.test(e.text));
    check("the computer moved it to Wi-Fi (lossless)", !!moved, moved?.text);
    const b = st.bytes; await delay(2000);
    check("the phone kept playing without restarting", st.starts === 1 && !st.stopped && st.bytes - b > 100000, `${st.starts} start(s), +${st.bytes - b} bytes in 2 s, formats ${st.formats.join()}`);
    check("no stop on the computer", !D.events.some((e) => e.ev === "sound_out" && (e.state === "off" || (e.state === "retry" && !/^Connecting/.test(e.text)))));
    D.send({ cmd: "sound_stop" }); await delay(500);
    phone.stop(); D.p.kill(); lanProxy.close();
  }
  check("no unhandled errors", crashes === 0, String(crashes));
  const ok = results.filter(Boolean).length;
  console.log(`\n${ok}/${results.length} passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
