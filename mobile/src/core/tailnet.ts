// Talks to the built-in Tailscale helper (helper/main.go, the same program the computers run)
// over its private localhost port: status, sign-in, sign-out, and connections into the tailnet.
import type { NetLib } from "./node";
import { Conn } from "./wire";

export interface TailnetDevice {
  name: string;
  ips: string[];
  online: boolean;
  direct?: string;
  relay?: string;
}

export interface TailnetStatus {
  state: string;            // Tailscale's: NeedsLogin, NeedsMachineAuth, Starting, Running... (or Error)
  auth_url?: string;
  name?: string;
  ips?: string[];
  key_expiry?: string;
  devices?: TailnetDevice[];
  error?: string;
}

export class HelperClient {
  constructor(private net: NetLib, public port: number, private token: string) {}

  private request(command: string, timeout: number): Promise<Conn> {
    return new Promise((resolve, reject) => {
      let done = false;
      const sock = this.net.createConnection({ host: "127.0.0.1", port: this.port }, () => {
        if (done) return;
        done = true;
        clearTimeout(t);
        const c = new Conn(sock, "127.0.0.1");
        try {
          c.sendText(`${this.token} ${command}`);
        } catch (e) {
          c.close();
          return reject(e);
        }
        c.setTimeout(timeout);
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
        reject(new Error("Tailscale didn't answer"));
      }, timeout);
      sock.on("error", (e) => {
        if (done) return;
        done = true;
        clearTimeout(t);
        reject(e);
      });
    });
  }

  private async simple(command: string, timeout: number): Promise<void> {
    const c = await this.request(command, timeout);
    try {
      const reply = await c.recvText();
      if (!reply.startsWith("OK")) throw new Error(reply.replace(/^ERR\s*/, "") || "Tailscale said no");
    } finally {
      c.close();
    }
  }

  async status(): Promise<TailnetStatus> {
    const c = await this.request("STATUS", 10000);
    try {
      const st = JSON.parse(await c.recvText(4 * 1024 * 1024));
      if (!st || typeof st.state !== "string") throw new Error("odd answer from Tailscale");
      return st;
    } finally {
      c.close();
    }
  }

  login(): Promise<void> {
    return this.simple("LOGIN", 15000);
  }

  logout(): Promise<void> {
    return this.simple("LOGOUT", 20000);
  }

  /** A connection to host:port on the tailnet; it behaves like a direct one from here on. */
  async dial(host: string, port: number, timeout: number): Promise<Conn> {
    const target = host.includes(":") ? `[${host}]:${port}` : `${host}:${port}`;
    const c = await this.request(`DIAL ${target}`, timeout);
    try {
      const reply = await c.recvText();
      if (!reply.startsWith("OK")) throw new Error(reply.replace(/^ERR\s*/, "") || "couldn't reach it over Tailscale");
    } catch (e) {
      c.close();
      throw e;
    }
    c.setTimeout(0);
    c.addr = host;
    c.route = "tailscale";
    return c;
  }
}

/** "winlink-akshats-pixel", like the computers' names on the tailnet. */
export function tailnetHostname(deviceName: string): string {
  const slug = deviceName.toLowerCase().replace(/[’'`]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return ("winlink-" + slug).slice(0, 60).replace(/-+$/, "") || "winlink";
}
