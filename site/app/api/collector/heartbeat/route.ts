import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

export async function POST(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await request.json() as { pendingSync?: number; failedAttempts?: number; lastBackupAt?: string | null };
    const now = new Date().toISOString();
    const db = await ensureRadarDb();
    await db.prepare(`INSERT INTO collector_state(
      id,phase,pending_sync,failed_attempts,auth_status,updated_at,last_sync_at,last_backup_at
    ) VALUES(1,'idle',?,?, 'ok',?,?,?) ON CONFLICT(id) DO UPDATE SET
      pending_sync=excluded.pending_sync,failed_attempts=excluded.failed_attempts,
      last_sync_at=excluded.last_sync_at,last_backup_at=COALESCE(excluded.last_backup_at,collector_state.last_backup_at),
      updated_at=excluded.updated_at,
      phase=CASE WHEN collector_state.phase IN ('complete','error') THEN 'idle' ELSE collector_state.phase END`)
      .bind(body.pendingSync ?? 0, body.failedAttempts ?? 0, now, now, body.lastBackupAt ?? null).run();
    return Response.json({ ok: true, lastSyncAt: now });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Heartbeat failed" }, { status: 500 });
  }
}
