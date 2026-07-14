import { ensureRadarDb } from "../../../../lib/d1";
import { publicPost } from "../../../../lib/posts";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const query = (url.searchParams.get("q") ?? "").trim();
    const handle = (url.searchParams.get("handle") ?? "").trim().toLowerCase();
    const decision = url.searchParams.get("decision") ?? "";
    const surface = url.searchParams.get("surface") ?? "all";
    const sort = url.searchParams.get("sort") === "newest" ? "newest" : "signal";
    const before = url.searchParams.get("before");
    const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 40)));
    const joins = ["LEFT JOIN account_reputation a ON a.handle=p.handle", "LEFT JOIN user_post_state s ON s.post_id=p.post_id"];
    const filters = ["1=1"];
    const bindings: unknown[] = [];
    if (query) {
      const ftsQuery = query.split(/\s+/).map((term) => term.replace(/[^\p{L}\p{N}_-]/gu, "")).filter(Boolean).map((term) => `"${term}"`).join(" AND ");
      if (ftsQuery) { joins.unshift("JOIN posts_fts f ON f.post_id=p.post_id"); filters.push("posts_fts MATCH ?"); bindings.push(ftsQuery); }
    }
    if (handle) { filters.push("p.handle=?"); bindings.push(handle.startsWith("@") ? handle : `@${handle}`); }
    if (["keep", "candidate", "discard"].includes(decision)) { filters.push("p.decision=?"); bindings.push(decision); }
    if (surface === "saved") filters.push("s.saved_at IS NOT NULL");
    if (surface === "briefing") filters.push("p.decision='keep' AND p.is_ad=0 AND COALESCE(a.disposition,'normal')!='blocked' AND s.dismissed_at IS NULL");
    if (before) { filters.push("p.last_seen_at<?"); bindings.push(before); }
    const db = await ensureRadarDb();
    const result = await db.prepare(`
      SELECT p.*,COALESCE(a.disposition,'normal') account_disposition,s.saved_at,s.pinned_at,s.dismissed_at
      FROM posts p ${joins.join(" ")} WHERE ${filters.join(" AND ")}
      ORDER BY ${surface === "saved"
        ? "CASE WHEN s.pinned_at IS NOT NULL THEN 0 ELSE 1 END,datetime(p.last_seen_at) DESC"
        : sort === "signal"
          ? `p.score + CASE COALESCE(a.disposition,'normal') WHEN 'allow' THEN .05 WHEN 'watch' THEN -.15 WHEN 'downrank' THEN -.40 ELSE 0 END DESC,datetime(p.last_seen_at) DESC`
          : "datetime(p.last_seen_at) DESC,p.score DESC"}
      LIMIT ?
    `).bind(...bindings, limit + 1).all<Record<string, unknown>>();
    const page = result.results.slice(0, limit);
    return Response.json({ posts: page.map(publicPost), nextCursor: result.results.length > limit ? page.at(-1)?.last_seen_at : null });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Search unavailable" }, { status: 500 });
  }
}
