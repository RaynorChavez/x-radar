import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";
import { canonicalRequest, hmacHex, sha256Hex } from "../app/semantic-signing.mjs";

test("signs semantic POST bodies exactly", async () => {
  const body = new TextEncoder().encode(JSON.stringify({ query: "orbital manufacturing" }));
  const hash = await sha256Hex(body);
  assert.equal(hash, createHash("sha256").update(body).digest("hex"));
  const canonical = canonicalRequest("post", "/api/search", "1700000000", "nonce", hash, " User@Example.com ");
  const secret = "0123456789abcdef0123456789abcdef";
  assert.equal(await hmacHex(secret, canonical), createHmac("sha256", secret).update(canonical).digest("hex"));
  assert.match(canonical, /\nuser@example\.com$/);
});
