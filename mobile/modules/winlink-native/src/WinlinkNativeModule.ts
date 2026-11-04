import { NativeModule, requireNativeModule } from "expo";

type Events = {
  onPcm(e: { data: string }): void;
  onCapture(e: { state: "started" | "stopped" | "error"; error?: string; rate?: number; channels?: number }): void;
  /** auto: sent by "Send copies automatically" right after a copy (not a tap). */
  onClipboardShare(e: ({ kind: "text"; text: string } | { kind: "image"; png: string }) & { auto?: boolean }): void;
  onNetwork(e: { up: boolean; wifi: boolean }): void;
  onTick(e: Record<string, never>): void;
  onScreen(e: { on: boolean }): void;
};

declare class WinlinkNativeModule extends NativeModule<Events> {
  playerStart(rate: number, channels: number, format: "f32" | "s16", bufferMs: number): void;
  playerWrite(data: Uint8Array): void;
  playerStop(): void;
  playerVolume(v: number): void;
  playerLevel(): number;
  /** Android: what the speaker accepted, and why it refused anything. */
  playerStatus(): { written: number; errors: number; lastError: string; lowLatency: boolean; delayMs?: number; targetMs?: number };
  /** Auto (adapts to the network, never below floorMs) or a fixed delay. Newer builds only. */
  playerTune(adaptive: boolean, floorMs: number): void;
  /** Android: left out of battery optimisation? Is the background service running? Newer builds only. */
  backgroundStatus(): { unrestricted: boolean; serviceRunning: boolean };
  /** Android: ask to run unrestricted in the background (the system's own prompt). */
  allowBackground(): void;
  canCapture(): boolean;
  startCapture(): Promise<{ rate: number; channels: number }>;
  stopCapture(): void;
  startKeepAlive(text: string): boolean;
  stopKeepAlive(): void;
  holdMulticast(on: boolean): void;
  /** installed is only known on Android. */
  tailscaleState(): { installed?: boolean; ips: string[] };
  /** Opens the Tailscale app (true) or its store page (false). iOS: always false. */
  openTailscale(): boolean;
  /** Start the built-in Tailscale helper; resolves with its first line: "READY <port>" or "ERROR ...". */
  tsStart(args: string[], env: Record<string, string>): Promise<string>;
  tsStop(): void;
  tsRunning(): boolean;
  /** Stop it and delete its stored state (after signing out). */
  tsWipe(): void;
  takePendingClip(): { kind: "text"; text: string } | { kind: "image"; png: string } | null;
  /** Android, newer builds: "Send copies automatically" and the two permissions it needs. */
  autoClipStatus(): { on: boolean; logs: boolean; overlay: boolean; running: boolean; package: string };
  setAutoClip(on: boolean): void;
  /** WinLink is about to set the clipboard itself (from a computer): don't send it back. */
  markOwnClip(): void;
  openOverlaySettings(): void;
  /** Android, newer builds: tick every 200 ms (onTick), for JS timers while in the background. */
  setTicking(on: boolean): void;
  /** Android, newer builds: something's under way (sound, a transfer): stay awake with the screen off. */
  setBusy(on: boolean): void;
  saveToDownloads(path: string, name: string, sub: string): Promise<string>;
  deleteSaved(uri: string): Promise<boolean>;
  openSaved(uri: string, mime: string | null): Promise<void>;
}

export default requireNativeModule<WinlinkNativeModule>("WinlinkNative");
