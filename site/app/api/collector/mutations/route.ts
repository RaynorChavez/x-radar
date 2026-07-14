import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

export async function GET(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const after = Math.max(0, Number(new URL(request.url).searchParams.get("after") ?? 0));
  const db = await ensureRadarDb();
  const rows = await db.prepare("SELECT seq,payload_json FROM mutations WHERE seq>? ORDER BY seq LIMIT 500").bind(after).all<{ seq: number; payload_json: string }>();
  return Response.json({ mutations: rows.results.map((row) => JSON.parse(row.payload_json)), cursor: rows.results.at(-1)?.seq ?? after });
}
