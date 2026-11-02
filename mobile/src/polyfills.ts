// The protocol code uses Web Crypto's getRandomValues (for keys and nonces).
import * as ExpoCrypto from "expo-crypto";

const g = globalThis as any;
if (!g.crypto?.getRandomValues) {
  g.crypto = { ...(g.crypto ?? {}), getRandomValues: <T extends ArrayBufferView>(a: T): T => ExpoCrypto.getRandomValues(a as any) as any };
}
