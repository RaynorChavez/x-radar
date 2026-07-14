import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

type CapturePost = {
  post_id: string; url: string; handle: string; author?: string | null;
  profile_image_url?: string | null; text: string;
  posted_at?: string | null; captured_at: string; is_ad?: boolean; is_reply?: boolean; is_quote?: boolean;
  source_url?: string | null; engagement?: Record<string, number>; score?: number; decision?: string; reasons?: string[];
  xcancel_url?: string; external_links?: Array<Record<string, unknown>>; media?: Array<Record<string, unknown>>;
  article?: { id?: string; url?: string; title?: string; [key: string]: unknown } | null;
};

function xcancelFor(post: CapturePost) {
  if (post.xcancel_url) return post.xcancel_url;
  if (post.article?.id) return `https://xcancel.com/i/article/${post.article.id}`;
  return `https://xcancel.com/${post.handle.replace(/^@/, "").toLowerCase()}/status/${post.post_id}`;
}

export async function POST(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const payload = await request.json() as {
    scan_id?: string; captured_at?: string; host?: string; source?: string;
    target?: string; request_id?: string;
    posts?: CapturePost[];
    account_signals?: Array<{ handle: string; confidence: number }>;
    account_reputation?: Array<{
      handle: string; disposition: string; strikePoints: number;
      confidence: number; reasonsJson?: string; notes?: string | null; operatorOverride?: number;
    }>;
  };
  const posts = payload.posts ?? [];
  if (posts.length > 250) return Response.json({ error: "Capture too large" }, { status: 400 });
  const db = await ensureRadarDb();
  const capturedAt = payload.captured_at ?? posts[0]?.captured_at ?? new Date().toISOString();
  const scanId = payload.scan_id ?? await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(`${payload.host ?? "unknown"}|${capturedAt}|${payload.source ?? "x-home"}|${payload.target ?? ""}`),
  ).then((digest) => [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""));
  const existingRun = await db.prepare("SELECT scan_id FROM runs WHERE scan_id=?").bind(scanId).first();
  if (existingRun) return Response.json({ scanId, duplicate: true, postsReceived: 0 });
  const existingPosts = posts.length
    ? await db.prepare(`SELECT COUNT(*) count FROM posts WHERE post_id IN (${posts.map(() => "?").join(",")})`)
      .bind(...posts.map((post) => post.post_id)).first<{ count: number }>()
    : { count: 0 };
  if (posts.length) await db.batch(posts.map((post) => db.prepare(`
    INSERT INTO posts (
      post_id, url, handle, author, profile_image_url, text, posted_at, captured_at,
      first_seen_at, last_seen_at, is_ad, is_reply, is_quote,
      source_url, xcancel_url, external_links_json, media_json, article_json,
      engagement_json, score, decision, reasons_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(post_id) DO UPDATE SET
      text=excluded.text, profile_image_url=COALESCE(excluded.profile_image_url, posts.profile_image_url),
      xcancel_url=excluded.xcancel_url,
      external_links_json=excluded.external_links_json, media_json=excluded.media_json,
      article_json=excluded.article_json, engagement_json=excluded.engagement_json,
      score=excluded.score, decision=excluded.decision, reasons_json=excluded.reasons_json,
      captured_at=excluded.captured_at, last_seen_at=excluded.last_seen_at
  `).bind(
    post.post_id, post.url, post.handle.toLowerCase(), post.author ?? null,
    post.profile_image_url ?? null, post.text,
    post.posted_at ?? null, post.captured_at ?? capturedAt, post.captured_at ?? capturedAt,
    post.captured_at ?? capturedAt, post.is_ad ? 1 : 0, post.is_reply ? 1 : 0,
    post.is_quote ? 1 : 0, post.source_url ?? null, xcancelFor(post),
    JSON.stringify(post.external_links ?? []), JSON.stringify(post.media ?? []),
    post.article ? JSON.stringify(post.article) : null, JSON.stringify(post.engagement ?? {}),
    post.score ?? 0, post.decision ?? "candidate", JSON.stringify(post.reasons ?? []),
  )));
  if (posts.length) await db.batch(posts.map((post, observedIndex) => db.prepare(`
    INSERT INTO post_observations (
      id, scan_id, post_id, captured_at, observed_index, score, decision, is_ad, is_reply, is_quote
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      observed_index=excluded.observed_index,
      score=excluded.score,
      decision=excluded.decision,
      is_ad=excluded.is_ad,
      is_reply=excluded.is_reply,
      is_quote=excluded.is_quote
  `).bind(
    `${scanId}:${post.post_id}`, scanId, post.post_id, post.captured_at ?? capturedAt, observedIndex,
    post.score ?? 0, post.decision ?? "candidate", post.is_ad ? 1 : 0,
    post.is_reply ? 1 : 0, post.is_quote ? 1 : 0,
  )));
  const reputation = payload.account_reputation ?? [];
  if (reputation.length) await db.batch(reputation.map((account) => db.prepare(`
    INSERT INTO account_reputation
      (handle, disposition, strike_points, confidence, reasons_json, notes, operator_override, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(handle) DO UPDATE SET
      disposition=CASE WHEN account_reputation.operator_override=1 THEN account_reputation.disposition ELSE excluded.disposition END,
      strike_points=excluded.strike_points,
      confidence=excluded.confidence,
      reasons_json=excluded.reasons_json, notes=COALESCE(account_reputation.notes, excluded.notes),
      operator_override=MAX(account_reputation.operator_override, excluded.operator_override),
      updated_at=excluded.updated_at
  `).bind(
    account.handle.toLowerCase(), account.disposition, account.strikePoints,
    account.confidence, account.reasonsJson ?? "[]", account.notes ?? null,
    account.operatorOverride ?? 0, new Date().toISOString(),
  )));
  const ingestedAt = new Date().toISOString();
  const durationSeconds = Math.max(0, Math.round((Date.parse(ingestedAt) - Date.parse(capturedAt)) / 1000) || 0);
  const postsAdded = posts.length - (existingPosts?.count ?? 0);
  await db.prepare(`INSERT INTO runs(
    scan_id,host,source,target,request_id,captured_at,posts_seen,posts_kept,posts_added,
    duplicates,candidates,discarded,signals_count,media_count,links_count,duration_seconds,status,ingested_at
  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    scanId, payload.host ?? "unknown", payload.source ?? "x-home", payload.target ?? null,
    payload.request_id ?? null, capturedAt, posts.length,
    posts.filter((post) => post.decision === "keep").length, postsAdded, posts.length - postsAdded,
    posts.filter((post) => post.decision === "candidate").length,
    posts.filter((post) => post.decision === "discard").length,
    payload.account_signals?.length ?? 0,
    posts.reduce((count, post) => count + (post.media?.length ?? 0), 0),
    posts.reduce((count, post) => count + (post.external_links?.length ?? 0), 0),
    durationSeconds, "complete", ingestedAt,
  ).run();
  await db.prepare(`INSERT INTO collector_state(id,phase,target,request_id,scan_id,observed,auth_status,updated_at)
    VALUES(1,'complete',?,?,?,?,'ok',?) ON CONFLICT(id) DO UPDATE SET phase='complete',
    target=excluded.target,request_id=excluded.request_id,scan_id=excluded.scan_id,
    observed=excluded.observed,auth_status='ok',last_error=NULL,updated_at=excluded.updated_at`)
    .bind(payload.target ?? null, payload.request_id ?? null, scanId, posts.length, ingestedAt).run();
  return Response.json({
    scanId,
    postsReceived: posts.length,
    signalsReceived: payload.account_signals?.length ?? 0,
    accountsReceived: reputation.length,
  });
}
