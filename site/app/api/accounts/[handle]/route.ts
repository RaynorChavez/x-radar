import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

const allowed = new Set(["allow", "normal", "watch", "downrank", "blocked"]);

export async function PUT(request: Request, context: { params: Promise<{ handle: string }> }) {
  try {
    const params = await context.params;
    const handle = `@${params.handle.replace(/^@/, "").toLowerCase()}`;
    const body = await request.json() as { disposition?: string; notes?: string | null; updated_at?: string };
    if (!body.disposition || !allowed.has(body.disposition)) return Response.json({ error: "Invalid disposition" }, { status: 400 });
    const db = await ensureRadarDb();
    const now = body.updated_at ?? new Date().toISOString();
    const payload = { kind: "account", handle, disposition: body.disposition, notes: body.notes ?? null, updated_at: now };
    await db.batch([
      db.prepare(`INSERT INTO account_reputation(handle,disposition,strike_points,confidence,reasons_json,notes,operator_override,updated_at)
        VALUES(?,?,0,0,'[]',?,1,?) ON CONFLICT(handle) DO UPDATE SET disposition=excluded.disposition,
        notes=excluded.notes,operator_override=1,updated_at=excluded.updated_at`).bind(handle, body.disposition, body.notes ?? null, now),
      db.prepare("INSERT INTO mutations(kind,entity_id,payload_json,created_at) VALUES('account',?,?,?)")
        .bind(handle, JSON.stringify(payload), now),
    ]);
    return Response.json({ account: payload });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Account update failed" }, { status: 500 });
  }
}

export async function DELETE(request: Request, context: { params: Promise<{ handle: string }> }) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const params = await context.params;
    const handle = `@${params.handle.replace(/^@/, "").toLowerCase()}`;
    const db = await ensureRadarDb();
    const row = await db.prepare("SELECT COUNT(*) count FROM posts WHERE lower(handle)=?")
      .bind(handle).first<{ count: number }>();
    await db.batch([
      db.prepare(`DELETE FROM observation_acquisitions WHERE observation_id IN (
        SELECT id FROM post_observations WHERE post_id IN (SELECT post_id FROM posts WHERE lower(handle)=?)
      )`).bind(handle),
      db.prepare("DELETE FROM post_observations WHERE post_id IN (SELECT post_id FROM posts WHERE lower(handle)=?)").bind(handle),
      db.prepare("DELETE FROM user_post_state WHERE post_id IN (SELECT post_id FROM posts WHERE lower(handle)=?)").bind(handle),
      db.prepare("DELETE FROM posts WHERE lower(handle)=?").bind(handle),
      db.prepare("DELETE FROM account_reputation WHERE lower(handle)=?").bind(handle),
    ]);
    return Response.json({ handle, postsPurged: row?.count ?? 0 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Account purge failed" }, { status: 500 });
  }
}
