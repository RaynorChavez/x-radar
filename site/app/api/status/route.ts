import { ensureRadarDb } from "../../../lib/d1";

export async function GET() {
  try {
    const db = await ensureRadarDb();
    const last = await db.prepare(`SELECT scan_id AS scanId,host,source,captured_at AS capturedAt,
      posts_seen AS postsSeen,posts_kept AS postsKept,ingested_at AS ingestedAt,status FROM runs
      ORDER BY datetime(ingested_at) DESC LIMIT 1`).first();
    const counts = await db.prepare(`SELECT
      (SELECT COUNT(*) FROM posts) uniquePosts,
      (SELECT COUNT(*) FROM post_observations) observations,
      (SELECT COUNT(*) FROM runs) runs`).first();
    const recent = await db.prepare(`SELECT ingested_at ingestedAt,status FROM runs
      WHERE datetime(ingested_at)>=datetime('now','-24 hours') ORDER BY datetime(ingested_at)`).all<{ ingestedAt: string; status: string }>();
    const state = await db.prepare(`SELECT phase,target,request_id requestId,scan_id scanId,observed,
      target_unique targetUnique,preference_version preferenceVersion,source_progress_json sourceProgressJson,
      pending_sync pendingSync,failed_attempts failedAttempts,auth_status authStatus,
      last_error lastError,started_at startedAt,updated_at updatedAt,last_sync_at lastSyncAt,
      last_backup_at lastBackupAt FROM collector_state WHERE id=1`).first<Record<string, unknown>>();
    const successfulSlots = new Set(recent.results.filter((run) => ["complete", "partial"].includes(run.status)).map((run) => run.ingestedAt.slice(0, 13))).size;
    const warnings: Array<{ code: string; level: "warning" | "critical"; message: string }> = [];
    const ageMinutes = (value: unknown) => value ? (Date.now() - Date.parse(String(value))) / 60000 : Infinity;
    if (!last || ageMinutes(last.ingestedAt) > 90) warnings.push({ code: "stale_collection", level: "critical", message: "No completed scan in the past 90 minutes." });
    if (last?.status === "partial") warnings.push({ code: "partial_collection", level: "warning", message: "The latest scan retained one or more posts that could not be ranked." });
    if (state?.authStatus === "required") warnings.push({ code: "auth_required", level: "critical", message: "The X browser session needs a fresh login." });
    if (Number(state?.pendingSync ?? 0) > 0) warnings.push({ code: "pending_sync", level: "warning", message: `${state?.pendingSync} synchronization event(s) are waiting.` });
    if (Number(state?.failedAttempts ?? 0) > 0) warnings.push({ code: "sync_failures", level: "warning", message: "Dashboard synchronization has retry failures." });
    if (state?.lastSyncAt && ageMinutes(state.lastSyncAt) > 20) warnings.push({ code: "stale_sync", level: "warning", message: "The Pi has not checked in for over 20 minutes." });
    if (state?.lastBackupAt && ageMinutes(state.lastBackupAt) > 26 * 60) warnings.push({ code: "stale_backup", level: "warning", message: "The latest database backup is over 26 hours old." });
    if (state && ["collecting", "ranking", "syncing"].includes(String(state.phase)) && ageMinutes(state.updatedAt) > 45) warnings.push({ code: "stuck_cycle", level: "critical", message: `Collector appears stuck in ${state.phase}.` });
    const runTimes = recent.results.map((run) => Date.parse(run.ingestedAt)).sort((a, b) => a - b);
    if (runTimes.some((time, index) => index > 0 && time - runTimes[index - 1] > 100 * 60000)) warnings.push({ code: "schedule_gap", level: "warning", message: "A gap longer than 100 minutes occurred between scans." });
    const activity = state ? { ...state, sourceProgress: JSON.parse(String(state.sourceProgressJson ?? "{}")), sourceProgressJson: undefined } : { phase: "idle" };
    return Response.json({
      lastRun: last ?? null, counts, activity, warnings,
      reliability: { successfulSlots, expectedSlots: 24, percent: Math.round((successfulSlots / 24) * 100) },
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Status unavailable" }, { status: 500 });
  }
}
