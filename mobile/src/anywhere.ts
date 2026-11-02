// "Connect from anywhere" on the phone: WinLink's built-in Tailscale, no Tailscale app needed.
// Runs the same helper as the computers (Android only for now), keeps it signed in, finds the
// computers on your tailnet, and routes their Tailscale addresses through it.
import * as SecureStore from "expo-secure-store";
import { Platform as RN } from "react-native";
import Native from "../modules/winlink-native";
import { hex, randomBytes } from "./core/bytes";
import type { Node, TailnetRoute } from "./core/node";
import { PORT } from "./core/node";
import { HelperClient, TailnetStatus, tailnetHostname } from "./core/tailnet";
import type { Conn } from "./core/wire";
import { platform } from "./platform";

const KEY_NAME = "tailscale_state_key";

export type AnywhereState = "Off" | "Starting" | "NeedsLogin" | "NeedsMachineAuth" | "Running" | "Error";

export interface AnywhereView {
  available: boolean;           // this phone can run it (Android)
  on: boolean;                  // the switch
  state: AnywhereState;
  error: string;
  name: string;                 // this phone on the tailnet
  ips: string[];
  keyExpiry: string;
  signingIn: boolean;
  computers: string[];          // WinLink computers seen on the tailnet
}

export class Anywhere implements TailnetRoute {
  private client: HelperClient | null = null;
  private st: TailnetStatus = { state: "Off" };
  private timer: ReturnType<typeof setTimeout> | null = null;
  private starting: Promise<void> | null = null;
  private wanted = false;
  private error = "";
  private signingIn = false;
  private probing = new Map<string, number>();     // tailnet address -> when we last asked who it is
  private fails = 0;

  constructor(private node: Node, private onChange: () => void, private openUrl: (url: string) => void) {}

  get available() {
    return RN.OS === "android";
  }

  get up() {
    return this.client !== null && this.st.state === "Running";
  }

  dial(host: string, port: number, timeout: number): Promise<Conn> {
    if (!this.client) return Promise.reject(new Error("Connect from anywhere is off"));
    return this.client.dial(host, port, timeout);
  }

  view(): AnywhereView {
    const state = (!this.wanted ? "Off" : this.error ? "Error" : !this.client ? "Starting"
      : ["NeedsLogin", "NeedsMachineAuth", "Running"].includes(this.st.state) ? this.st.state : "Starting") as AnywhereState;
    const ids = new Set<string>();
    const names: string[] = [];
    for (const p of this.node.peers.values()) {
      if (ids.has(p.id)) continue;
      if ([...p.addrs.keys()].some((a) => this.probing.has(a)) || p.control?.route === "tailscale") {
        ids.add(p.id);
        names.push(p.name);
      }
    }
    return {
      available: this.available,
      on: this.wanted,
      state,
      error: this.error,
      name: this.st.name ?? "",
      ips: this.st.ips ?? [],
      keyExpiry: this.st.key_expiry ?? "",
      signingIn: this.signingIn,
      computers: names,
    };
  }

  // ---- on / off
  setOn(on: boolean) {
    this.wanted = on;
    this.error = "";
    if (on) void this.start();
    else this.stop();
    this.onChange();
  }

  private async start() {
    if (!this.available || this.starting) return this.starting ?? undefined;
    this.starting = (async () => {
      try {
        let key = await SecureStore.getItemAsync(KEY_NAME).catch(() => null);
        if (!key || !/^[0-9a-f]{64}$/.test(key)) {
          key = hex(randomBytes(32));
          await SecureStore.setItemAsync(KEY_NAME, key);
        }
        const forward = await this.node.listenTailnet();
        const token = hex(randomBytes(16));
        const line = await Native.tsStart(
          ["-hostname", tailnetHostname(this.node.name), "-port", String(this.node.serverPort || PORT), "-forward", `127.0.0.1:${forward}`],
          { LINKTS_TOKEN: token, LINKTS_KEY: key });
        const m = /^READY (\d+)$/.exec(line.trim());
        if (!m) throw new Error(line.replace(/^ERROR\s*/, "") || "Tailscale didn't start");
        if (!this.wanted) {             // switched off while starting
          Native.tsStop();
          return;
        }
        this.client = new HelperClient(platform.net, Number(m[1]), token);
        this.error = "";
        this.fails = 0;
        platform.log("info", "Connect from anywhere: started");
        this.poll(0);
      } catch (e) {
        this.fail(e as Error);
      } finally {
        this.starting = null;
        this.onChange();
      }
    })();
    return this.starting;
  }

  private stop() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.client = null;
    this.st = { state: "Off" };
    this.signingIn = false;
    this.probing.clear();
    try {
      Native.tsStop();
    } catch {
      /* older build */
    }
    this.node.closeTailnet();
    for (const p of this.node.peers.values()) {
      if (p.control?.route === "tailscale") p.control.close();    // they reconnect on Wi-Fi if they can
    }
  }

  private fail(e: Error) {
    const msg = e?.message || String(e);
    this.error = /not a function|undefined is not|Cannot find native/i.test(msg)
      ? "This version of the app doesn't include Tailscale yet. Install the latest build."
      : msg.slice(0, 200);
    platform.log("warn", `Connect from anywhere: ${msg}`);
    this.client = null;
    this.fails++;
    if (this.wanted && this.fails < 6) {        // a crash or a slow start: try again, more slowly each time
      if (this.timer) clearTimeout(this.timer);
      this.timer = setTimeout(() => {
        this.error = "";
        void this.start();
      }, Math.min(60, 5 * 2 ** (this.fails - 1)) * 1000);
    }
  }

  // ---- watching it
  private poll(ms: number) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.tick(), ms);
  }

  private async tick() {
    const c = this.client;
    if (!c || !this.wanted) return;
    if (!Native.tsRunning()) {
      this.fail(new Error("Tailscale stopped unexpectedly; restarting it"));
      this.onChange();
      return;
    }
    let st: TailnetStatus;
    try {
      st = await c.status();
    } catch (e) {
      st = { state: "Error", error: (e as Error).message };
    }
    if (c !== this.client) return;
    const was = this.st.state;
    this.st = st;
    if (st.state === "Running" && was !== "Running") {
      this.signingIn = false;
      platform.log("info", `Connect from anywhere: on as ${st.name} (${(st.ips ?? []).join(", ")})`);
      for (const p of this.node.peers.values()) {
        if (!p.control) p.retryAt = 0;       // a new way to reach them: try now
      }
    }
    if (st.state === "Running") this.discover(st);
    this.onChange();
    this.poll(st.state === "Running" ? 10000 : 2000);
  }

  /** Ask each online device on the tailnet whether it's running WinLink (like the computers do). */
  private discover(st: TailnetStatus) {
    const now = Date.now();
    const connected = new Set<string>();
    for (const p of this.node.peers.values()) if (p.control) for (const a of p.addrs.keys()) connected.add(a);
    for (const d of st.devices ?? []) {
      if (!d.online) continue;
      const ip = (d.ips ?? []).find((a) => !a.includes(":")) ?? d.ips?.[0];
      if (!ip || connected.has(ip)) continue;
      if (now - (this.probing.get(ip) ?? 0) < 30000) continue;
      this.probing.set(ip, now);
      this.node.probe(ip, PORT).then(
        () => this.onChange(),
        () => this.probing.set(ip, now + 90000),      // not WinLink (or offline): ask less often
      );
    }
  }

  // ---- signing in and out
  async signIn() {
    if (!this.wanted) this.setOn(true);
    await this.starting;
    const c = this.client;
    if (!c) return;
    this.signingIn = true;
    this.onChange();
    try {
      let st = await c.status();
      if (!st.auth_url) {
        await c.login();
        for (let i = 0; i < 20 && !st.auth_url && st.state !== "Running"; i++) {
          await new Promise((r) => setTimeout(r, 500));
          st = await c.status();
        }
      }
      this.st = st;
      if (st.state === "Running") this.signingIn = false;
      else if (st.auth_url) this.openUrl(st.auth_url);
      else throw new Error("Tailscale didn't give a sign-in link; check the internet connection");
    } catch (e) {
      this.signingIn = false;
      this.error = "";
      platform.log("warn", `Tailscale sign-in: ${(e as Error).message}`);
      throw e;
    } finally {
      this.onChange();
      this.poll(1000);
    }
  }

  /** Sign out, forget this phone's Tailscale identity, and turn it off. */
  async signOut() {
    try {
      await this.client?.logout();
    } catch {
      /* offline: the local identity is erased anyway */
    }
    this.wanted = false;
    this.stop();
    try {
      Native.tsWipe();
    } catch {
      /* ignore */
    }
    await SecureStore.deleteItemAsync(KEY_NAME).catch(() => undefined);
    this.onChange();
  }

  /** The phone went to sleep or the network changed: check sooner. */
  nudge() {
    if (this.client) this.poll(0);
    else if (this.wanted && !this.starting) {
      this.error = "";
      this.fails = 0;
      void this.start();
    }
  }
}
