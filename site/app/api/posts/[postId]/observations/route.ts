import { ensureRadarDb } from "../../../../../lib/d1";

export async function GET(_request: Request, context: { params: Promise<{ postId: string }> }) {
  try {
    const { postId } = await context.params;
    const db = await ensureRadarDb();
    const rows = await db.prepare(`SELECT o.scan_id scanId,o.captured_at capturedAt,
      o.score,o.decision,o.score_components_json scoreComponentsJson,r.source,r.target FROM post_observations o
      LEFT JOIN runs r ON r.scan_id=o.scan_id WHERE o.post_id=?
      ORDER BY datetime(o.captured_at) DESC LIMIT 100`).bind(postId).all();
    const observations = rows.results.map((row) => ({
      ...row,
      scoreComponents: row.scoreComponentsJson ? JSON.parse(String(row.scoreComponentsJson)) : null,
      scoreComponentsJson: undefined,
    }));
    return Response.json({ observations, count: observations.length });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Observations unavailable" }, { status: 500 });
  }
}
