import { env } from "cloudflare:workers";

type SemanticBindings = {
  XRADAR_SEMANTIC_LOCAL_MODE?: string;
  XRADAR_SEMANTIC_UPSTREAM_URL?: string;
  XRADAR_SEMANTIC_HMAC_ACTIVE_KEY_ID?: string;
  XRADAR_SEMANTIC_HMAC_KEYS?: string;
};

function binding(name: keyof SemanticBindings): string | undefined {
  const value = (env as unknown as SemanticBindings)[name] ??
    (typeof process !== "undefined" ? process.env[name] : undefined);
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export type SemanticProxyConfig = { upstream: URL; activeKeyId: string; activeSecret: string };

export function semanticProxyConfig(): SemanticProxyConfig {
  const upstreamValue = binding("XRADAR_SEMANTIC_UPSTREAM_URL");
  const activeKeyId = binding("XRADAR_SEMANTIC_HMAC_ACTIVE_KEY_ID");
  const rawKeys = binding("XRADAR_SEMANTIC_HMAC_KEYS");
  if (!upstreamValue || !activeKeyId || !rawKeys) throw new Error("semantic proxy is not configured");
  const upstream = new URL(upstreamValue);
  const localHttp = binding("XRADAR_SEMANTIC_LOCAL_MODE") === "1" && upstream.protocol === "http:" &&
    ["127.0.0.1", "localhost"].includes(upstream.hostname);
  if ((!localHttp && upstream.protocol !== "https:") || upstream.username || upstream.password) {
    throw new Error("semantic upstream must be an HTTPS origin");
  }
  if (!upstream.pathname.endsWith("/")) upstream.pathname += "/";
  upstream.search = "";
  upstream.hash = "";
  const keys = JSON.parse(rawKeys) as unknown;
  if (!keys || Array.isArray(keys) || typeof keys !== "object") throw new Error("semantic HMAC keys are invalid");
  const activeSecret = (keys as Record<string, unknown>)[activeKeyId];
  if (typeof activeSecret !== "string" || new TextEncoder().encode(activeSecret).byteLength < 32) {
    throw new Error("active semantic HMAC key is unavailable");
  }
  return { upstream, activeKeyId, activeSecret };
}
