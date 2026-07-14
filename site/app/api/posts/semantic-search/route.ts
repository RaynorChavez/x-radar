import { getChatGPTUser } from "../../../chatgpt-auth";
import { semanticProxyConfig } from "../../../semantic-config";
import { canonicalRequest, hmacHex, sha256Hex } from "../../../semantic-signing.mjs";
import { ensureRadarDb } from "../../../../lib/d1";
import { publicPost } from "../../../../lib/posts";

export const dynamic = "force-dynamic";

function error(status: number, message: string) {
  return Response.json({ error: message }, { status, headers: { "cache-control": "no-store" } });
}

export async function GET(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return error(401, "ChatGPT sign-in is required");
  const url = new URL(request.url);
  const query = (url.searchParams.get("q") ?? "").trim();
  if (!query || query.length > 500) return error(400, "Search must be 1-500 characters");
  const handle = (url.searchParams.get("handle") ?? "").trim();
  const decision = url.searchParams.get("decision") ?? "";
  const limit = Math.min(50, Math.max(1, Number(url.searchParams.get("limit") ?? 40)));
  let config: ReturnType<typeof semanticProxyConfig>;
  try { config = semanticProxyConfig(); } catch { return error(503, "Semantic search is not configured"); }
  const body = new TextEncoder().encode(JSON.stringify({ query, handle, decision, limit }));
  const path = "/api/search";
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID();
  const email = user.email.trim().toLowerCase();
  const bodyHash = await sha256Hex(body);
  const signature = await hmacHex(config.activeSecret, canonicalRequest("POST", path, timestamp, nonce, bodyHash, email));
  let response: Response;
  try {
    response = await fetch(new URL("api/search", config.upstream), {
      method: "POST", body, cache: "no-store", redirect: "manual",
      headers: {
        "content-type": "application/json", "x-xradar-key-id": config.activeKeyId,
        "x-xradar-timestamp": timestamp, "x-xradar-nonce": nonce,
        "x-xradar-user": email, "x-xradar-signature": signature,
      },
    });
  } catch { return error(502, "The private semantic index is unavailable"); }
  if (!response.ok) return error(response.status, response.status === 429 ? "Semantic search rate limit reached" : "Semantic search failed");
  const payload = await response.json() as { results?: Array<{ postId: string; semanticScore: number }> };
  const ranked = (payload.results ?? []).slice(0, limit);
  if (!ranked.length) return Response.json({ posts: [], mode: "semantic" });
  const db = await ensureRadarDb();
  const ids = ranked.map((item) => item.postId);
  const rows = await db.prepare(`SELECT p.*,COALESCE(a.disposition,'normal') account_disposition,
    s.saved_at,s.pinned_at,s.dismissed_at FROM posts p
    LEFT JOIN account_reputation a ON a.handle=p.handle
    LEFT JOIN user_post_state s ON s.post_id=p.post_id
    WHERE p.post_id IN (${ids.map(() => "?").join(",")})`).bind(...ids).all<Record<string, unknown>>();
  const byId = new Map(rows.results.map((row) => [String(row.post_id), publicPost(row)]));
  return Response.json({
    mode: "semantic",
    posts: ranked.flatMap((item) => {
      const post = byId.get(item.postId);
      return post ? [{ ...post, semanticScore: item.semanticScore }] : [];
    }),
  });
}
