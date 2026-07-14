import { ensureRadarDb } from "../../../../../lib/d1";

export async function GET(_request: Request, context: { params: Promise<{ postId: string }> }) {
  try {
    const { postId } = await context.params;
    const db = await ensureRadarDb();
    const rows = await db.prepare(`SELECT o.scan_id scanId,o.captured_at capturedAt,
      o.score,o.decision,r.source,r.target FROM post_observations o
      LEFT JOIN runs r ON r.scan_id=o.scan_id WHERE o.post_id=?
      ORDER BY datetime(o.captured_at) DESC LIMIT 100`).bind(postId).all();
    return Response.json({ observations: rows.results, count: rows.results.length });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Observations unavailable" }, { status: 500 });
  }
}
