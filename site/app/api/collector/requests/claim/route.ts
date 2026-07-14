import { collectorAuthorized, ensureRadarDb } from "../../../../../lib/d1";

export async function POST(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const db = await ensureRadarDb();
  const item = await db.prepare(`SELECT id,handle,include_replies AS includeReplies,requested_at AS requestedAt,
    CASE WHEN kind='mixed' OR handle IN ('@home','@mixed') THEN 'mixed' ELSE 'account' END AS targetKind,
    preference_version AS preferenceVersion,bootstrap_topics_json AS bootstrapTopicsJson
    FROM fetch_requests WHERE status='queued' ORDER BY datetime(requested_at) LIMIT 1`).first<Record<string, unknown>>();
  if (!item) return Response.json({ request: null });
  const updated = await db.prepare("UPDATE fetch_requests SET status='claimed' WHERE id=? AND status='queued'").bind(item.id).run();
  if (!updated.meta.changes) return Response.json({ request: null, raced: true }, { status: 409 });
  return Response.json({ request: { ...item, includeReplies: Boolean(item.includeReplies),
    bootstrapTopics: JSON.parse(String(item.bootstrapTopicsJson ?? "[]")), bootstrapTopicsJson: undefined, status: "claimed" } });
}
