import { ensureRadarDb } from "../../../lib/d1";

const rangeDays: Record<string, number | null> = { day: 1, week: 7, month: 31, year: 366, all: null };

function parseJson<T>(value: unknown, fallback: T): T {
  try { return JSON.parse(String(value ?? "")) as T; } catch { return fallback; }
}

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const range = url.searchParams.get("range") ?? "week";
    const decision = url.searchParams.get("decision") === "all" ? "all" : "keep";
    const sort = url.searchParams.get("sort") === "newest" ? "newest" : "signal";
    const view = url.searchParams.get("view") === "history" ? "history" : "signal";
    const requestedLimit = Number(url.searchParams.get("limit") ?? (view === "history" ? 100 : 300));
    const limit = Math.min(300, Math.max(1, Number.isFinite(requestedLimit) ? requestedLimit : 100));
    const requestedOffset = Number(url.searchParams.get("offset") ?? 0);
    const offset = Math.max(0, Number.isFinite(requestedOffset) ? requestedOffset : 0);
    const days = rangeDays[range] ?? 7;
    const db = await ensureRadarDb();
    const bindings: unknown[] = [];
    let rows;
    if (view === "history") {
      const filters: string[] = [];
      if (days !== null) { filters.push("datetime(o.captured_at) >= datetime('now', ?)"); bindings.push(`-${days} days`); }
      rows = await db.prepare(`
        SELECT p.*, COALESCE(a.disposition, 'normal') AS account_disposition,
          s.saved_at,s.pinned_at,s.dismissed_at,
          o.captured_at AS observation_captured_at,
          o.observed_index AS observation_index,
          o.score AS observation_score,
          o.decision AS observation_decision,
          o.score_components_json AS observation_score_components_json,
          o.is_ad AS observation_is_ad,
          o.is_reply AS observation_is_reply,
          o.is_quote AS observation_is_quote
        FROM post_observations o
        JOIN posts p ON p.post_id=o.post_id
        LEFT JOIN account_reputation a ON a.handle=p.handle
        LEFT JOIN user_post_state s ON s.post_id=p.post_id
        WHERE ${filters.length ? filters.join(" AND ") : "1=1"}
        ORDER BY datetime(o.captured_at) DESC, o.observed_index ASC
        LIMIT ? OFFSET ?
      `).bind(...bindings, limit + 1, offset).all<Record<string, unknown>>();
    } else {
      const filters = ["p.is_ad=0"];
      if (decision === "keep") filters.push("p.decision='keep'");
      else filters.push("p.decision IN ('keep','candidate')");
      filters.push("COALESCE(a.disposition, 'normal') != 'blocked'");
      if (days !== null) { filters.push("datetime(p.captured_at) >= datetime('now', ?)"); bindings.push(`-${days} days`); }
      rows = await db.prepare(`
        SELECT p.*, COALESCE(a.disposition, 'normal') AS account_disposition,
          s.saved_at,s.pinned_at,s.dismissed_at
        FROM posts p LEFT JOIN account_reputation a ON a.handle=p.handle
        LEFT JOIN user_post_state s ON s.post_id=p.post_id
        WHERE ${filters.join(" AND ")}
        ORDER BY ${sort === "signal" ? `p.score + CASE COALESCE(a.disposition,'normal') WHEN 'allow' THEN .05 WHEN 'watch' THEN -.15 WHEN 'downrank' THEN -.40 ELSE 0 END DESC, datetime(COALESCE(p.first_seen_at,p.captured_at)) DESC` : "datetime(COALESCE(p.first_seen_at,p.captured_at)) DESC, p.score DESC"}
        LIMIT ? OFFSET ?
      `).bind(...bindings, limit + 1, offset).all<Record<string, unknown>>();
    }

    const hasMore = rows.results.length > limit;
    const pageRows = rows.results.slice(0, limit);

    const statFilters = days === null ? "1=1" : "datetime(captured_at) >= datetime('now', ?)";
    const statBind = days === null ? [] : [`-${days} days`];
    const stats = await db.prepare(`
      SELECT COUNT(*) AS scanned,
        SUM(CASE WHEN decision='keep' THEN 1 ELSE 0 END) AS kept,
        SUM(CASE WHEN decision='candidate' THEN 1 ELSE 0 END) AS candidates,
        SUM(CASE WHEN decision='discard' THEN 1 ELSE 0 END) AS discarded,
        COUNT(DISTINCT handle) AS authors
      FROM posts WHERE ${statFilters}
    `).bind(...statBind).first<Record<string, number>>();
    const observationStats = await db.prepare(`SELECT COUNT(*) observations FROM post_observations WHERE ${statFilters}`)
      .bind(...statBind).first<{ observations: number }>();
    const reputation = await db.prepare(`
      SELECT handle, disposition, strike_points AS strikePoints, confidence, notes
      FROM account_reputation WHERE disposition != 'normal'
      ORDER BY CASE disposition WHEN 'blocked' THEN 0 WHEN 'downrank' THEN 1 ELSE 2 END, handle
    `).all();

    const posts = pageRows.map((row) => ({
      postId: row.post_id, url: row.url, handle: row.handle, author: row.author,
      profileImageUrl: row.profile_image_url, text: row.text,
      postedAt: row.posted_at, capturedAt: row.observation_captured_at ?? row.captured_at,
      score: row.observation_score ?? row.score, decision: row.observation_decision ?? row.decision,
      scoreComponents: parseJson(row.observation_score_components_json ?? row.score_components_json, null),
      isAd: Boolean(row.observation_is_ad ?? row.is_ad),
      isReply: Boolean(row.observation_is_reply ?? row.is_reply),
      isQuote: Boolean(row.observation_is_quote ?? row.is_quote),
      reasons: parseJson(row.reasons_json, []),
      engagement: parseJson(row.engagement_json, {}),
      xcancelUrl: row.xcancel_url ?? `https://xcancel.com/${String(row.handle).replace(/^@/, "")}/status/${row.post_id}`,
      externalLinks: parseJson(row.external_links_json, []),
      media: parseJson(row.media_json, []),
      article: parseJson(row.article_json, null),
      accountDisposition: row.account_disposition,
      saved: Boolean(row.saved_at), pinned: Boolean(row.pinned_at), dismissed: Boolean(row.dismissed_at),
    }));
    return Response.json({
      posts, stats: { ...stats, observations: observationStats?.observations ?? 0 }, reputation: reputation.results,
      nextOffset: hasMore ? offset + limit : null,
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Feed unavailable" }, { status: 500 });
  }
}
