import { ensureRadarDb } from "../../../../../lib/d1";

export async function PUT(request: Request, context: { params: Promise<{ postId: string }> }) {
  try {
    const { postId } = await context.params;
    const body = await request.json() as { saved?: boolean; pinned?: boolean; dismissed?: boolean };
    const db = await ensureRadarDb();
    const current = await db.prepare("SELECT * FROM user_post_state WHERE post_id=?").bind(postId).first<Record<string, string | null>>();
    const now = new Date().toISOString();
    let savedAt = current?.saved_at ?? null;
    let pinnedAt = current?.pinned_at ?? null;
    let dismissedAt = current?.dismissed_at ?? null;
    if (body.saved !== undefined) savedAt = body.saved ? now : null;
    if (body.pinned !== undefined) { pinnedAt = body.pinned ? now : null; if (body.pinned) savedAt = savedAt ?? now; }
    if (body.dismissed !== undefined) dismissedAt = body.dismissed ? now : null;
    const payload = { kind: "post_state", post_id: postId, saved: Boolean(savedAt), pinned: Boolean(pinnedAt), dismissed: Boolean(dismissedAt), updated_at: now };
    await db.batch([
      db.prepare(`INSERT INTO user_post_state(post_id,saved_at,pinned_at,dismissed_at,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET saved_at=excluded.saved_at,
        pinned_at=excluded.pinned_at,dismissed_at=excluded.dismissed_at,updated_at=excluded.updated_at`)
        .bind(postId, savedAt, pinnedAt, dismissedAt, now),
      db.prepare("INSERT INTO mutations(kind,entity_id,payload_json,created_at) VALUES('post_state',?,?,?)")
        .bind(postId, JSON.stringify(payload), now),
    ]);
    return Response.json({ state: payload });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "State update failed" }, { status: 500 });
  }
}
