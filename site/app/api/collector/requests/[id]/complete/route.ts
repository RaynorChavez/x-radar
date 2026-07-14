import { collectorAuthorized, ensureRadarDb } from "../../../../../../lib/d1";

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await context.params;
  const payload = await request.json() as { status?: string; resultCount?: number; error?: string };
  const status = payload.status === "error" ? "error" : "complete";
  const db = await ensureRadarDb();
  await db.prepare(`
    UPDATE fetch_requests SET status=?, completed_at=?, result_count=?, error=? WHERE id=?
  `).bind(status, new Date().toISOString(), payload.resultCount ?? 0, payload.error ?? null, id).run();
  return Response.json({ ok: true });
}
