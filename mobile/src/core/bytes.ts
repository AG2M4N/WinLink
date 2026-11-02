// Small byte helpers shared by the protocol code (works in React Native and in Node for tests).
import { Buffer } from "buffer";

export const enc = (s: string): Uint8Array => new Uint8Array(Buffer.from(s, "utf8"));
const view = (b: Uint8Array) => Buffer.from(b.buffer as ArrayBuffer, b.byteOffset, b.byteLength);
export const dec = (b: Uint8Array): string => view(b).toString("utf8");
export const b64 = (b: Uint8Array): string => view(b).toString("base64");
export const unb64 = (s: string, size?: number): Uint8Array => {
  if (typeof s !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(s)) throw new Error("bad base64");
  const out = new Uint8Array(Buffer.from(s, "base64"));
  if (size !== undefined && out.length !== size) throw new Error("bad length");
  return out;
};
export const hex = (b: Uint8Array): string => view(b).toString("hex");

export function concat(...parts: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

export function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  (globalThis as any).crypto.getRandomValues(out);
  return out;
}

export function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}
