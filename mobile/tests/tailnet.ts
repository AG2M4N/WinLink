// The phone's built-in Tailscale: the helper protocol against the real helper program, and the
// phone's routing through a stand-in helper against a real desktop node. Nothing signs in.
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { HARNESS, PYTHON, ROOT } from "./paths";
import { webcrypto } from "node:crypto";
(globalThis as any).crypto ??= webcrypto;
import { Node, Platform, delay, FileSource } from "../src/core/node";
import { HelperClient, tailnetHostname } from "../src/core/tailnet";
import { hex, randomBytes } from "../src/core/bytes";

const results: [string, boolean][] = [];
const check = (name: string, ok: boolean, info = "") => { results.push([name, ok]); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${info}`); };
const until = async (f: () => any, ms = 10000) => { const end = Date.now() + ms; while (Date.now() < end) { const v = f(); if (v) return v; await delay(50); } return null; };
const netlib = { createConnection: (o: any, cb: any) => net.createConnection(o, cb) as any, createServer: (cb: any) => net.createServer(cb) as any };

async function realHelper() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "zzts"));
  const token = hex(randomBytes(16));
  const h = spawn(path.join(ROOT, "bin", "linkts-mac-arm64"),
    ["-dir", dir, "-hostname", "zz-winlink-test-7731", "-forward", "127.0.0.1:1", "-port", "51579"],
    { env: { ...process.env, LINKTS_TOKEN: token, LINKTS_KEYCHAIN_ACCOUNT: "zz-test-7731" } });
  try {
    const line: string = await new Promise((res) => { let b = ""; h.stdout.on("data", (d) => { b += d; if (b.includes("\n")) res(b.split("\n")[0]); }); });
    const m = /^READY (\d+)$/.exec(line);
    check("real helper starts", !!m, line);
    if (!m) return;
    const c = new HelperClient(netlib, Number(m[1]), token);
    const st = await c.status();
    check("status before sign-in", ["NoState", "NeedsLogin", "Starting"].includes(st.state), st.state);
    const bad = new HelperClient(netlib, Number(m[1]), "wrong");
    check("wrong token is ignored", await bad.status().then(() => false, () => true));
    await c.login();
    let url = "";
    for (let i = 0; i < 30 && !url; i++) { await delay(500); url = (await c.status()).auth_url ?? ""; }
    check("sign-in link appears", url.startsWith("https://login.tailscale.com/"), url ? "(link received, not opened)" : "");
    const err = await c.dial("100.64.0.1", 50555, 3000).then(() => "", (e) => String(e.message));
    check("dial before sign-in fails cleanly", !!err, err.slice(0, 60));
    await c.logout();
    check("sign-out ok", true);
  } finally {
    h.stdin.end();
    await delay(300);
    h.kill();
    // sign-out makes a fresh key for next time; remove this test's keychain item
    spawn("/usr/bin/security", ["delete-generic-password", "-s", "WinLink Tailscale key", "-a", "zz-test-7731"]);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function routed() {
  // the desktop
  const DESK_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wldesk"));
  const DESK_DL = fs.mkdtempSync(path.join(os.tmpdir(), "wldeskdl"));
  const desk = spawn(PYTHON, ["-u", path.join(HARNESS, "desk.py")], {
    env: { ...process.env, LINK_HOME: DESK_HOME, LINK_PORT: "51570", LINK_NO_BEACON: "1", LINK_NAME: "ZZ Desk 7731", LINK_DOWNLOADS: DESK_DL },
  });
  const events: any[] = [];
  let partial = "";
  desk.stdout.on("data", (d) => { partial += d; let i; while ((i = partial.indexOf("\n")) >= 0) { const l = partial.slice(0, i); partial = partial.slice(i + 1); try { events.push(JSON.parse(l)); } catch { console.log("desk:", l); } } });
  const send = (o: any) => desk.stdin.write(JSON.stringify(o) + "\n");

  // a stand-in helper: 100.100.1.1 on the "tailnet" is the desktop
  const token = hex(randomBytes(16));
  let dials = 0;
  const fake = net.createServer((s) => {
    let b = Buffer.alloc(0);
    const onData = (d: Buffer) => {
      b = Buffer.concat([b, d]);
      const i = b.indexOf(10);
      if (i < 0) return;
      s.off("data", onData);
      const [tok, cmd, arg] = b.subarray(0, i).toString().trim().split(/\s+/);
      const rest = b.subarray(i + 1);
      if (tok !== token) return s.destroy();
      if (cmd === "STATUS") s.end(JSON.stringify({ state: "Running", name: "winlink-zz-phone", ips: ["100.100.2.2"],
        devices: [{ name: "zz-desk", ips: ["100.100.1.1"], online: true }] }) + "\n");
      else if (cmd === "DIAL") {
        if (arg !== "100.100.1.1:51570") { s.end("ERR no route\n"); return; }
        dials++;
        const r = net.createConnection({ host: "127.0.0.1", port: 51570 }, () => { s.write("OK\n"); if (rest.length) r.write(rest); s.pipe(r); r.pipe(s); });
        r.on("error", () => s.destroy()); s.on("error", () => r.destroy());
      }
    };
    s.on("data", onData);
  });
  await new Promise<void>((r) => fake.listen(0, "127.0.0.1", () => r()));
  const client = new HelperClient(netlib, (fake.address() as net.AddressInfo).port, token);

  const HOME = fs.mkdtempSync(path.join(os.tmpdir(), "wlphone"));
  const received: string[] = [];
  const plat: Platform = {
    net: netlib, os: "android", saveSettings: () => undefined,
    saver: {
      freeSpace: () => 50 * 2 ** 30,
      async openPartial(key) { const p = path.join(HOME, key); const fd = fs.openSync(p, "w"); return { offset: 0, write: async (b) => { fs.writeSync(fd, b); }, close: async () => { try { fs.closeSync(fd); } catch {} } }; },
      async finish(key, rel) { const d = path.join(HOME, "got_" + rel.replace(/\//g, "_")); fs.renameSync(path.join(HOME, key), d); received.push(d); return d; },
      async discard() {}, async remove() {},
    },
    soundOut: null, soundIn: null,
    log: (lv, m) => { if (lv !== "debug") console.log(`  phone ${lv}: ${m}`); },
  };
  const phoneEvents: any[] = [];
  const phone = new Node({ id: hex(randomBytes(16)), name: "ZZ Phone 7731", trusted: {} }, plat, (e) => phoneEvents.push(e));
  phone.tailnet = { up: true, dial: (h, p, t) => client.dial(h, p, t) };
  try {
    const ready = await until(() => events.find((e) => e.ev === "ready"));
    phone.start(51571);
    await delay(300);
    const st = await client.status();
    const peer = await phone.probe(st.devices![0].ips[0], 51570);
    check("finds the desktop through the tailnet", peer.id === ready.id && peer.addrs.has("100.100.1.1"));
    peer.port = 51570;
    phone.connect(peer, true);
    check("pairs over the tailnet", !!(await until(() => events.find((e) => e.ev === "pair_code"))));
    check("control link is 'tailscale'", (await until(() => peer.control))?.route === "tailscale");
    send({ cmd: "clip", text: "via ts ✓" });
    check("clip desktop -> phone", !!(await until(() => phoneEvents.find((e) => e.kind === "clip_in" && e.clip?.text === "via ts ✓"))));
    const data = new Uint8Array(1 << 20).map((_, i) => i & 255);
    const src: FileSource = { name: "ts.bin", size: data.length, key: "ts1", read: async (o, n) => data.subarray(o, o + n) };
    phone.sendFiles(peer, [src]);
    const got = await until(() => events.find((e) => e.ev === "received"), 20000);
    check("file phone -> desktop over the tailnet", got?.size === data.length, `${dials} tailnet dials`);
    check("dials went through the helper", dials >= 2);

    // the helper handing a tailnet connection to the phone
    const fwd = await phone.listenTailnet();
    const ask = (first: string) => new Promise<string>((res) => {
      const s = net.createConnection({ host: "127.0.0.1", port: fwd }, () => s.write(first));
      let b = ""; s.on("data", (d) => { b += d; }); s.on("close", () => res(b)); s.on("error", () => res(b));
      setTimeout(() => s.destroy(), 3000);
    });
    const reply = await ask(`FROM 100.100.1.1\n${JSON.stringify({ type: "probe" })}\n`);
    check("forwarded probe answered", reply.includes('"app":"link"'), reply.trim().slice(0, 60));
    check("forward without FROM refused", (await ask(`${JSON.stringify({ type: "probe" })}\n`)) === "");
    check("hostname", tailnetHostname("Sam’s Pixel 9!") === "winlink-sams-pixel-9");
  } finally {
    phone.stop(); phone.closeTailnet(); fake.close(); desk.kill();
  }
}

(async () => {
  try {
    await realHelper();
    await routed();
  } catch (e) {
    check("no crash", false, String((e as Error)?.stack ?? e));
  }
  const ok = results.filter((r) => r[1]).length;
  console.log(`\n${ok}/${results.length} passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
