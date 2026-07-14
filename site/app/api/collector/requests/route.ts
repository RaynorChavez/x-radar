import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

export async function GET(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const db = await ensureRadarDb();
  const rows = await db.prepare(`
    SELECT id, handle, include_replies AS includeReplies, status, requested_at AS requestedAt,
      CASE WHEN kind='mixed' OR handle IN ('@home','@mixed') THEN 'mixed' ELSE 'account' END AS targetKind,
      preference_version AS preferenceVersion,bootstrap_topics_json AS bootstrapTopicsJson
    FROM fetch_requests WHERE status='queued' ORDER BY datetime(requested_at) ASC LIMIT 10
  `).all();
  return Response.json({ requests: rows.results.map((item: Record<string, unknown>) => ({
    ...item, includeReplies: Boolean(item.includeReplies),
    bootstrapTopics: JSON.parse(String(item.bootstrapTopicsJson ?? "[]")), bootstrapTopicsJson: undefined,
  })) });
}
