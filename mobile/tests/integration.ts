// Phone node (this TypeScript) against a real desktop node (Python), over local sockets.
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { HARNESS, PYTHON, ROOT } from "./paths";
import { webcrypto, randomBytes as nodeRandom } from "node:crypto";
(globalThis as any).crypto ??= webcrypto;
import { Node, Platform, Saver, SoundSender, FileSource, delay } from "../src/core/node";
import { hex, randomBytes } from "../src/core/bytes";

const HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wlphone"));
const DESK_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wldesk"));
const DESK_DL = fs.mkdtempSync(path.join(os.tmpdir(), "wldeskdl"));
const results: [string, boolean, string?][] = [];
const check = (name: string, ok: boolean, info = "") => { results.push([name, ok, info]); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${info}`); };

const desk = spawn(PYTHON, ["-u", path.join(HARNESS, "desk.py")], {
  env: { ...process.env, LINK_HOME: DESK_HOME, LINK_PORT: "51560", LINK_NO_BEACON: "1", LINK_NAME: "ZZ Desk 7731", LINK_DOWNLOADS: DESK_DL },
});
const events: any[] = [];
let partial = "";
desk.stdout.on("data", (d) => { partial += d; let i; while ((i = partial.indexOf("\n")) >= 0) { const l = partial.slice(0, i); partial = partial.slice(i + 1); try { events.push(JSON.parse(l)); } catch { console.log("desk:", l); } } });
desk.stderr.on("data", (d) => process.stderr.write("desk err: " + d));
const send = (o: any) => desk.stdin.write(JSON.stringify(o) + "\n");
const until = async (f: () => any, ms = 10000) => { const end = Date.now() + ms; while (Date.now() < end) { const v = f(); if (v) return v; await delay(50); } return null; };

// ---- phone platform for Node
const partials = path.join(HOME, "partials"); fs.mkdirSync(partials);
const received = path.join(HOME, "received"); fs.mkdirSync(received);
const saver: Saver = {
  freeSpace: () => 50 * 2 ** 30,
  async openPartial(key, resume) {
    const p = path.join(partials, key);
    if (!resume && fs.existsSync(p)) fs.unlinkSync(p);
    const offset = fs.existsSync(p) ? fs.statSync(p).size : 0;
    const fd = fs.openSync(p, offset ? "a" : "w");
    return { offset, write: async (b) => { fs.writeSync(fd, b); }, close: async () => { try { fs.closeSync(fd); } catch {} } };
  },
  async finish(key, rel) { const dest = path.join(received, rel.replace(/\//g, "_")); fs.renameSync(path.join(partials, key), dest); return dest; },
  async discard(key) { try { fs.unlinkSync(path.join(partials, key)); } catch {} },
  async remove(p) { try { fs.unlinkSync(p); } catch {} },
};
let soundBytes = 0, soundStarted: any = null, soundStopped = false;
const plat: Platform = {
  net: { createConnection: (o, cb) => net.createConnection(o, cb) as any, createServer: (cb) => net.createServer(cb as any) as any },
  os: "android",
  saveSettings: () => undefined,
  saver,
  soundOut: { start: (rate, ch, fmt, ms) => { soundStarted = { rate, ch, fmt, ms }; soundStopped = false; }, write: (b) => { soundBytes += b.length; }, stop: () => { soundStopped = true; } },
  soundIn: null,
  log: (lv, m) => { if (lv !== "debug") console.log(`  phone ${lv}: ${m}`); },
};
const settings = { id: hex(randomBytes(16)), name: "ZZ Phone 7731", trusted: {} as any };
const phoneEvents: any[] = [];
const phone = new Node(settings, plat, (e) => phoneEvents.push(e));

(async () => {
  try {
    const ready = await until(() => events.find((e) => e.ev === "ready"));
    phone.start(51561);
    await delay(300);
    // discovery as Bonjour would report it
    phone.seen({ id: ready.id, name: "ZZ Desk 7731", os: "mac", port: 51560 }, ["127.0.0.1"]);
    const peer = phone.peers.get(ready.id)!;
    phone.connect(peer, true);
    const deskCode = await until(() => events.find((e) => e.ev === "pair_code"));
    const phoneCode = await until(() => phoneEvents.find((e) => e.kind === "pair_code"));
    check("pairing codes match", !!deskCode && !!phoneCode && deskCode.code === phoneCode.code, `${deskCode?.code} / ${phoneCode?.code}`);
    check("control connected", !!(await until(() => peer.control)));
    check("desktop sees phone as android", !!(await until(() => events.find((e) => e.ev === "connected" && e.os === "android"))));

    // clipboard both ways
    send({ cmd: "clip", text: "from desk ✓" });
    const ci = await until(() => phoneEvents.find((e) => e.kind === "clip_in"));
    check("clip desktop -> phone", ci?.clip?.text === "from desk ✓");
    phone.sendClip({ kind: "text", text: "from phone ✓" });
    const di = await until(() => events.find((e) => e.ev === "clip_in"));
    check("clip phone -> desktop", di?.clip?.text === "from phone ✓");

    // file phone -> desktop (3 MB)
    const big = new Uint8Array(nodeRandom(3 * 2 ** 20));
    const src: FileSource = { name: "phone photo.bin", size: big.length, key: "k1", read: async (o, n) => big.subarray(o, o + n) };
    const b = phone.sendFiles(peer, [src])!;
    await until(() => b.state !== "active", 20000);
    const got = await until(() => events.find((e) => e.ev === "received"));
    check("file phone -> desktop", b.state === "done" && got?.size === big.length && Buffer.compare(fs.readFileSync(got.path), Buffer.from(big)) === 0, `${b.state} ${b.error ?? ""}`);

    // file desktop -> phone
    const deskFile = path.join(DESK_HOME, "desk doc.pdf"); fs.writeFileSync(deskFile, nodeRandom(2 * 2 ** 20));
    send({ cmd: "send", peer: settings.id, paths: [deskFile] });
    const pr = await until(() => phoneEvents.find((e) => e.kind === "received"), 20000);
    check("file desktop -> phone", !!pr && Buffer.compare(fs.readFileSync(pr.path), fs.readFileSync(deskFile)) === 0, pr?.path ?? "");

    // cancel: desktop sends a big folder, phone cancels midway; desktop must see it cancelled
    const folder = path.join(DESK_HOME, "Holiday"); fs.mkdirSync(folder);
    for (let i = 0; i < 6; i++) fs.writeFileSync(path.join(folder, `img${i}.raw`), nodeRandom(8 * 2 ** 20));
    send({ cmd: "send", peer: settings.id, paths: [folder] });
    const inb = await until(() => [...phone.batches.values()].find((x) => x.dir === "in" && x.name === "Holiday" && x.filesDone >= 1), 20000);
    if (inb) phone.cancel(inb.key);
    const dc = await until(() => events.find((e) => e.ev === "batch" && e.name === "Holiday" && e.state === "cancelled"), 10000);
    const leftovers = fs.readdirSync(received).filter((f) => f.startsWith("Holiday")).length + fs.readdirSync(partials).length;
    check("cancel from phone cleans both sides", !!inb && !!dc && leftovers === 0, `desk=${dc?.state} leftover files=${leftovers}`);

    // sound desktop -> phone
    send({ cmd: "sound", peer: settings.id });
    await until(() => soundBytes > 100000, 10000);
    check("sound desktop -> phone", soundBytes > 100000 && soundStarted?.rate === 48000, JSON.stringify(soundStarted) + ` ${soundBytes} bytes`);
    phone.stopSoundHere();
    const off = await until(() => events.find((e) => e.ev === "sound_out" && e.state === "off"), 8000);
    check("stop on phone tells the desktop", !!off, off?.text);

    // sound phone -> desktop
    let fakeTimer: any;
    const input = { start: async (onPcm: (b: Uint8Array) => void) => { fakeTimer = setInterval(() => onPcm(new Uint8Array(1920)), 10); return { rate: 48000, channels: 2 }; }, stop: () => clearInterval(fakeTimer) };
    const snd = new SoundSender(phone, input, () => "auto");
    phone.soundSending = snd;
    snd.start(peer);
    const sin = await until(() => events.find((e) => e.ev === "sound_in"), 8000);
    await delay(1500);
    snd.stop();
    const send_end = await until(() => events.find((e) => e.ev === "sound_end"), 8000);
    check("sound phone -> desktop", !!sin && (send_end?.bytes ?? 0) > 50000, `${JSON.stringify(sin?.hdr)} ${send_end?.bytes}`);

    // disconnect from the phone: desktop sees it gone, no auto-reconnect
    phone.disconnect(peer);
    await delay(1500);
    check("disconnect sticks", !peer.control && peer.paused);
  } catch (e) {
    check("no crash", false, String((e as Error).stack));
  } finally {
    phone.stop(); desk.kill();
    const failed = results.filter((r) => !r[1]).length;
    console.log(`\n${results.length - failed}/${results.length} passed`);
    process.exit(failed ? 1 : 0);
  }
})();
