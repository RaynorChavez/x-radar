import { collectorAuthorized, ensureRadarDb } from "../../../../lib/d1";

const phases = new Set(["collecting", "ranking", "syncing", "complete", "error"]);

export async function POST(request: Request) {
  if (!collectorAuthorized(request)) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await request.json() as {
      phase?: string; target?: string | null; requestId?: string | null; scanId?: string | null;
      observed?: number; error?: string | null; errorCode?: string | null; startedAt?: string | null;
      targetUnique?: number; preferenceVersion?: number; sourceProgress?: Record<string, unknown>;
    };
    if (!body.phase || !phases.has(body.phase)) return Response.json({ error: "Invalid phase" }, { status: 400 });
    const now = new Date().toISOString();
    const authStatus = body.errorCode === "AUTH_REQUIRED" ? "required" : "ok";
    const db = await ensureRadarDb();
    const current = await db.prepare("SELECT target_unique targetUnique,preference_version preferenceVersion FROM collector_state WHERE id=1")
      .first<{ targetUnique: number; preferenceVersion: number }>();
    const targetUnique = body.targetUnique ?? current?.targetUnique ?? 100;
    const preferenceVersion = body.preferenceVersion ?? current?.preferenceVersion ?? 0;
    await db.prepare(`INSERT INTO collector_state(
      id,phase,target,request_id,scan_id,observed,target_unique,preference_version,source_progress_json,
      auth_status,last_error,started_at,updated_at
    ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      phase=excluded.phase,target=COALESCE(excluded.target,collector_state.target),
      request_id=COALESCE(excluded.request_id,collector_state.request_id),
      scan_id=COALESCE(excluded.scan_id,collector_state.scan_id),observed=excluded.observed,
      target_unique=COALESCE(excluded.target_unique,collector_state.target_unique),
      preference_version=COALESCE(excluded.preference_version,collector_state.preference_version),
      source_progress_json=CASE WHEN excluded.source_progress_json='{}' THEN collector_state.source_progress_json ELSE excluded.source_progress_json END,
      auth_status=excluded.auth_status,last_error=excluded.last_error,
      started_at=CASE WHEN excluded.phase='collecting' THEN excluded.started_at ELSE collector_state.started_at END,
      updated_at=excluded.updated_at`).bind(
        body.phase, body.target ?? null, body.requestId ?? null, body.scanId ?? null,
        body.observed ?? 0, targetUnique, preferenceVersion,
        JSON.stringify(body.sourceProgress ?? {}), authStatus, body.error ?? null, body.startedAt ?? now, now,
      ).run();
    if (body.requestId) {
      await db.prepare("UPDATE fetch_requests SET status=?,error=? WHERE id=?")
        .bind(body.phase, body.error ?? null, body.requestId).run();
    }
    return Response.json({ ok: true, phase: body.phase, updatedAt: now });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Progress update failed" }, { status: 500 });
  }
}
