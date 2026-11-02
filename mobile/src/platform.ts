// The phone-specific pieces the protocol core needs: sockets, settings, files, sound.
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Device from "expo-device";
import { Directory, File, FileMode, Paths } from "expo-file-system";
import * as SecureStore from "expo-secure-store";
import { Platform as RN } from "react-native";
import TcpSocket from "react-native-tcp-socket";
import Native from "../modules/winlink-native";
import { hex, randomBytes } from "./core/bytes";
import type { FileSource, PartialFile, Platform, Saver, Settings, SoundIn, SoundOut, TrustedInfo } from "./core/node";
import { convertPcm } from "./core/node";

const SETTINGS_KEY = "winlink.settings";
const keyName = (id: string) => `pair_${id}`;

// ---------------------------------------------------------------- settings (pair keys in the keychain)
export interface AppSettings extends Settings {
  clipboard: boolean;          // share the clipboard
  keepAlive: boolean;          // Android: stay connected in the background
  anywhere: boolean;           // the built-in Tailscale ("Connect from anywhere")
  backgroundHinted: boolean;   // told once to allow running in the background (Android)
  latency: "auto" | "lowest" | "low" | "balanced" | "smooth";
  volume: number;
  soundTarget: string | null;
  received: ReceivedFile[];
}

export interface ReceivedFile {
  uri: string;
  name: string;
  from: string;
  size: number;
  at: number;
}

export async function loadSettings(fresh = false): Promise<AppSettings> {
  let data: any = {};
  try {
    if (!fresh) data = JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "{}") ?? {};
  } catch {
    data = {};
  }
  const trusted: Record<string, TrustedInfo> = {};
  for (const [id, t] of Object.entries<any>(data.trusted ?? {})) {
    const key = await SecureStore.getItemAsync(keyName(id)).catch(() => null);
    if (key) trusted[id] = { name: t.name, os: t.os, key, addrs: t.addrs ?? [], tsAddrs: t.tsAddrs ?? [] };
  }
  const s: AppSettings = {
    id: typeof data.id === "string" && /^[0-9a-f]{32}$/.test(data.id) ? data.id : hex(randomBytes(16)),
    name: data.name || defaultName(),
    trusted,
    clipboard: data.clipboard ?? true,
    keepAlive: data.keepAlive ?? true,
    latency: data.latency ?? "auto",
    volume: typeof data.volume === "number" ? data.volume : 1,
    soundTarget: data.soundTarget ?? null,
    anywhere: data.anywhere === true,
    backgroundHinted: data.backgroundHinted === true,
    received: Array.isArray(data.received) ? data.received.slice(0, 200) : [],
  };
  await saveSettingsNow(s);
  return s;
}

async function saveSettingsNow(s: AppSettings) {
  try {
    await writeSettings(s);
  } catch (e) {                // full storage, keystore hiccup: keep running, try again on the next change
    platform.log("error", `couldn't save settings: ${(e as Error).message}`);
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
export function saveSettings(s: Settings) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => void saveSettingsNow(s as AppSettings), 200);
}

async function writeSettings(s: AppSettings) {
  const trusted: Record<string, Omit<TrustedInfo, "key">> = {};
  for (const [id, t] of Object.entries(s.trusted)) {
    trusted[id] = { name: t.name, os: t.os, addrs: t.addrs, tsAddrs: t.tsAddrs };
    const have = await SecureStore.getItemAsync(keyName(id)).catch(() => null);
    if (have !== t.key) await SecureStore.setItemAsync(keyName(id), t.key);
  }
  let old: any = {};
  try {
    old = JSON.parse((await AsyncStorage.getItem(SETTINGS_KEY)) ?? "{}") ?? {};
  } catch {
    old = {};
  }
  for (const id of Object.keys(old.trusted ?? {})) {
    if (!s.trusted[id]) await SecureStore.deleteItemAsync(keyName(id)).catch(() => undefined);
  }
  const { trusted: _t, ...rest } = s;
  await AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...rest, trusted }));
}

function defaultName(): string {
  const n = Device.deviceName || Device.modelName || (RN.OS === "ios" ? "iPhone" : "Android phone");
  return n.slice(0, 48);
}

// ---------------------------------------------------------------- files
const partialsDir = () => {
  const d = new Directory(Paths.document, "partials");
  if (!d.exists) d.create({ intermediates: true });
  return d;
};
const safeKey = (k: string) => k.replace(/[^0-9a-f-]/gi, "").slice(0, 120);

const saver: Saver = {
  freeSpace: () => Paths.availableDiskSpace,

  async openPartial(key, resume): Promise<PartialFile> {
    const f = new File(partialsDir(), safeKey(key));
    if (!resume && f.exists) f.delete();
    if (!f.exists) f.create();
    const handle = f.open(FileMode.ReadWrite);
    const offset = handle.size ?? 0;
    handle.offset = offset;
    return {
      offset,
      write: (b) => handle.writeBytes(b),
      close: async () => {
        try {
          handle.close();
        } catch {
          /* already closed */
        }
      },
    };
  },

  async finish(key, relPath) {
    const f = new File(partialsDir(), safeKey(key));
    const parts = relPath.split("/").map(cleanPart).filter(Boolean);
    const name = parts.pop() || "file";
    return Native.saveToDownloads(f.uri, name, parts.join("/"));
  },

  async discard(key) {
    const f = new File(partialsDir(), safeKey(key));
    if (f.exists) f.delete();
  },

  async remove(saved) {
    await Native.deleteSaved(saved);
  },
};

function cleanPart(p: string): string {
  return p.replace(/[\\:*?"<>|\u0000-\u001f]/g, "_").replace(/^\.+/, "").trim().slice(0, 120);
}

/** A file picked or shared on the phone, read in chunks while it's sent. */
export function fileSource(uri: string, name?: string, sizeHint?: number | null): FileSource {
  if (uri.startsWith("/")) uri = "file://" + uri;          // shared files can arrive as plain paths
  const f = new File(uri);
  const size = typeof f.size === "number" && f.size >= 0 ? f.size : sizeHint ?? 0;
  let handle: ReturnType<File["open"]> | null = null;
  return {
    name: cleanPart(name || f.name || "file") || "file",
    size,
    key: `${uri}|${size}|${f.modificationTime ?? ""}`,
    async read(offset, n) {
      if (!handle) handle = f.open(FileMode.ReadOnly);
      handle.offset = offset;
      return handle.readBytes(n);
    },
    close() {
      try {
        handle?.close();
      } catch {
        /* ignore */
      }
      handle = null;
    },
  };
}

// ---------------------------------------------------------------- sound
/** What the speaker is doing, for the log (and for working out "I can't hear anything"). */
export const soundStats = { format: "", playFormat: "", rate: 0, channels: 0, bytes: 0, writes: 0, errors: 0,
  lastError: "", startedAt: 0 };

const soundOut: SoundOut = {
  start: (rate, channels, format, bufferMs, adaptive = true, floorMs = 20) => {
    // Android plays 16-bit: some phones refuse 32-bit float in low-latency mode and simply stay silent.
    const play = RN.OS === "android" ? "s16" : format;
    Object.assign(soundStats, { format, playFormat: play, rate, channels, bytes: 0, writes: 0, errors: 0,
      lastError: "", startedAt: Date.now() });
    Native.playerStart(rate, channels, play, bufferMs);
    try {
      Native.playerTune(adaptive, floorMs);
    } catch {
      /* an older build: it plays at a fixed delay */
    }
  },
  write: (pcm) => {
    try {
      const out = convertPcm(pcm, soundStats.format, soundStats.playFormat);
      Native.playerWrite(out);
      soundStats.bytes += out.length;
      soundStats.writes++;
    } catch (e) {
      soundStats.errors++;
      soundStats.lastError = String((e as Error)?.message ?? e).slice(0, 200);
    }
  },
  stop: () => Native.playerStop(),
};

function base64ToBytes(s: string): Uint8Array {
  const bin = globalThis.atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const soundIn: SoundIn | null = RN.OS === "android" ? {
  async start(onPcm) {
    const { PermissionsAndroid } = require("react-native");
    const ok = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
    if (ok !== PermissionsAndroid.RESULTS.GRANTED) {
      throw new Error("Android needs the microphone permission to capture what the phone plays (nothing is recorded from the microphone)");
    }
    const subPcm = Native.addListener("onPcm", (e) => onPcm(base64ToBytes(e.data)));
    const fmt = await Native.startCapture().catch((e: Error) => {
      subPcm.remove();
      throw e;
    });
    capture = { subPcm };
    return fmt;
  },
  stop() {
    capture?.subPcm.remove();
    capture = null;
    Native.stopCapture();
  },
} : null;
let capture: { subPcm: { remove(): void } } | null = null;

// ---------------------------------------------------------------- the platform object
export const platform: Platform = {
  net: {
    createConnection: (opts, cb) => TcpSocket.createConnection({ host: opts.host, port: opts.port }, cb) as any,
    createServer: (cb) => TcpSocket.createServer((s: any) => cb(s)) as any,
  },
  os: RN.OS === "ios" ? "ios" : "android",
  saveSettings,
  saver,
  soundOut,
  soundIn,
  log: (level, msg) => {
    logLines.push({ t: Date.now(), level, msg });
    if (logLines.length > 400) logLines.splice(0, logLines.length - 400);
    if (level !== "debug") console.log(`[winlink] ${level}: ${msg}`);
  },
};

export const logLines: { t: number; level: string; msg: string }[] = [];
