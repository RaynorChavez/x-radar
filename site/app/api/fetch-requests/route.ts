import { ensureRadarDb } from "../../../lib/d1";

export async function GET() {
  try {
    const db = await ensureRadarDb();
    const rows = await db.prepare(`
      SELECT id, handle, include_replies AS includeReplies, status, requested_at AS requestedAt,
        CASE WHEN handle='@home' THEN 'home' ELSE 'account' END AS targetKind,
        result_count AS resultCount, error
      FROM fetch_requests ORDER BY datetime(requested_at) DESC LIMIT 20
    `).all();
    return Response.json({ requests: rows.results.map((row: Record<string, unknown>) => ({ ...row, includeReplies: Boolean(row.includeReplies) })) });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Queue unavailable" }, { status: 500 });
  }
}
export async function POST(request: Request) {
  try {
    const payload = await request.json() as { handle?: string; includeReplies?: boolean; kind?: "home" | "account" };
    const isHome = payload.kind === "home";
    const bare = (payload.handle ?? "").trim().replace(/^@/, "");
    if (!isHome && !/^[A-Za-z0-9_]{1,15}$/.test(bare)) return Response.json({ error: "Enter a valid X handle" }, { status: 400 });
    const item = { id: crypto.randomUUID(), handle: isHome ? "@home" : `@${bare.toLowerCase()}`, targetKind: isHome ? "home" : "account", includeReplies: !isHome && payload.includeReplies !== false, status: "queued", requestedAt: new Date().toISOString() };
    const db = await ensureRadarDb();
    await db.prepare(`INSERT INTO fetch_requests (id, handle, include_replies, status, requested_at) VALUES (?, ?, ?, ?, ?)`)
      .bind(item.id, item.handle, item.includeReplies ? 1 : 0, item.status, item.requestedAt).run();
    return Response.json({ request: item }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not queue scan" }, { status: 500 });
  }
}
