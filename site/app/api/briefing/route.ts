import { ensureRadarDb } from "../../../lib/d1";
import { dateInZone, publicPost } from "../../../lib/posts";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const timeZone = url.searchParams.get("timezone") ?? "Australia/Melbourne";
    const sort = url.searchParams.get("sort") === "newest" ? "newest" : "signal";
    const orderBy = sort === "newest"
      ? "datetime(COALESCE(p.posted_at,p.first_seen_at,p.captured_at)) DESC,effective_score DESC"
      : "effective_score DESC,datetime(COALESCE(p.first_seen_at,p.captured_at)) DESC";
    let date = url.searchParams.get("date");
    try { date = date ?? dateInZone(new Date().toISOString(), timeZone); }
    catch { return Response.json({ error: "Invalid timezone" }, { status: 400 }); }
    const db = await ensureRadarDb();
    const rows = await db.prepare(`
      SELECT p.*,COALESCE(a.disposition,'normal') account_disposition,
        s.saved_at,s.pinned_at,s.dismissed_at,
        p.score + CASE COALESCE(a.disposition,'normal')
          WHEN 'allow' THEN .05 WHEN 'watch' THEN -.15 WHEN 'downrank' THEN -.40 ELSE 0 END effective_score
      FROM posts p LEFT JOIN account_reputation a ON a.handle=p.handle
      LEFT JOIN user_post_state s ON s.post_id=p.post_id
      WHERE p.decision='keep' AND p.is_ad=0 AND COALESCE(a.disposition,'normal')!='blocked'
        AND s.dismissed_at IS NULL
      ORDER BY ${orderBy} LIMIT 500
    `).all<Record<string, unknown>>();
    const selected = rows.results.filter((row) => {
      try { return dateInZone(String(row.first_seen_at ?? row.captured_at), timeZone) === date; }
      catch { return false; }
    }).slice(0, 20).map(publicPost);
    const stats = await db.prepare(`SELECT COUNT(*) scanned,
      SUM(CASE WHEN decision='keep' THEN 1 ELSE 0 END) kept,
      SUM(CASE WHEN decision='candidate' THEN 1 ELSE 0 END) candidates,
      SUM(CASE WHEN decision='discard' THEN 1 ELSE 0 END) discarded,
      COUNT(DISTINCT handle) authors,
      (SELECT COUNT(*) FROM post_observations) observations FROM posts`).first();
    const reputation = await db.prepare(`SELECT handle,disposition,strike_points strikePoints,confidence,notes
      FROM account_reputation WHERE disposition!='normal'
      ORDER BY CASE disposition WHEN 'blocked' THEN 0 WHEN 'downrank' THEN 1 ELSE 2 END,handle`).all();
    return Response.json({ date, timezone: timeZone, sort, posts: selected, count: selected.length, stats, reputation: reputation.results });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Briefing unavailable" }, { status: 500 });
  }
}
