import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

export async function GET(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const db = await ensureRadarDb();
  const rows = await db.prepare(`
    SELECT id, handle, include_replies AS includeReplies, status, requested_at AS requestedAt,
      CASE WHEN handle='@home' THEN 'home' ELSE 'account' END AS targetKind
    FROM fetch_requests WHERE status='queued' ORDER BY datetime(requested_at) ASC LIMIT 10
  `).all();
  return Response.json({ requests: rows.results });
}
