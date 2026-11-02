// WinLink's security layer, byte-for-byte the same as the desktop's secure.py:
// X25519 pairing with a 4-digit code from the shared secret, HMAC proofs over fresh nonces,
// and ChaCha20-Poly1305 frames (64 KB of data each, counter nonces) once connected.
import { x25519 } from "@noble/curves/ed25519.js";
import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { hkdf } from "@noble/hashes/hkdf.js";
import { hmac } from "@noble/hashes/hmac.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { concat, enc, randomBytes } from "./bytes";

export const FRAME = 64 * 1024;

export class AuthError extends Error {}

export function nonce(): Uint8Array {
  return randomBytes(16);
}

export class Pairing {
  private priv = randomBytes(32);
  readonly pub = x25519.getPublicKey(this.priv);

  /** [pair key, 4-digit code]: the same on both sides only if nobody tampered. */
  finish(theirPub: Uint8Array, idA: string, idB: string, nc: Uint8Array, ns: Uint8Array): [Uint8Array, string] {
    const shared = x25519.getSharedSecret(this.priv, theirPub);
    const salt = concat(enc([idA, idB].sort().join("|")), nc, ns);
    const key = hkdf(sha256, shared, salt, enc("winlink pair key"), 32);
    const c = hkdf(sha256, shared, salt, enc("winlink pair code"), 4);
    const code = (((c[0] << 24) >>> 0) + (c[1] << 16) + (c[2] << 8) + c[3]) % 10000;
    return [key, code.toString().padStart(4, "0")];
  }
}

export function proof(key: Uint8Array, who: "client" | "server", nc: Uint8Array, ns: Uint8Array, kind: string): Uint8Array {
  return hmac(sha256, key, concat(enc(who + "|"), nc, ns, enc(kind)));
}

/** [client->server key, server->client key] for one connection. */
export function sessionKeys(key: Uint8Array, nc: Uint8Array, ns: Uint8Array): [Uint8Array, Uint8Array] {
  const okm = hkdf(sha256, key, concat(nc, ns), enc("winlink session"), 64);
  return [okm.slice(0, 32), okm.slice(32)];
}

function counterNonce(n: number): Uint8Array {
  const out = new Uint8Array(12);
  // 4 zero bytes, then the counter as 8 bytes big-endian (counters stay far below 2^53)
  let v = n;
  for (let i = 11; i >= 4; i--) {
    out[i] = v % 256;
    v = Math.floor(v / 256);
  }
  return out;
}

/** Encrypts outgoing frames and decrypts incoming ones; the socket plumbing is in wire.ts. */
export class Sealer {
  private txN = 0;
  private rxN = 0;
  constructor(private sendKey: Uint8Array, private recvKey: Uint8Array) {}

  /** Plaintext -> length-prefixed encrypted frames ready to write. */
  seal(data: Uint8Array): Uint8Array {
    const parts: Uint8Array[] = [];
    const n = Math.max(data.length, 1);
    for (let i = 0; i < n; i += FRAME) {
      const chunk = data.subarray(i, Math.min(i + FRAME, data.length));
      if (!chunk.length && i) break;
      const ct = chacha20poly1305(this.sendKey, counterNonce(this.txN++)).encrypt(chunk);
      const head = new Uint8Array(4);
      new DataView(head.buffer).setUint32(0, ct.length);
      parts.push(head, ct);
    }
    return concat(...parts);
  }

  open(ct: Uint8Array): Uint8Array {
    try {
      return chacha20poly1305(this.recvKey, counterNonce(this.rxN++)).decrypt(ct);
    } catch {
      throw new Error("data failed integrity check");
    }
  }
}
