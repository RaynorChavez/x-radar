import { ensureRadarDb } from "../../../../lib/d1";
import { publicPost } from "../../../../lib/posts";

export async function GET(_request: Request, context: { params: Promise<{ scanId: string }> }) {
  try {
    const { scanId } = await context.params;
    const db = await ensureRadarDb();
    const run = await db.prepare(`SELECT scan_id scanId,host,source,target,request_id requestId,
      captured_at capturedAt,posts_seen postsSeen,posts_kept postsKept,posts_added postsAdded,
      duplicates,candidates,discarded,signals_count signalsCount,media_count mediaCount,
      links_count linksCount,duration_seconds durationSeconds,status,schema_version schemaVersion,
      period_id periodId,preference_version preferenceVersion,target_unique targetUnique,ingested_at ingestedAt
      FROM runs WHERE scan_id=?`).bind(scanId).first();
    if (!run) return Response.json({ error: "Run not found" }, { status: 404 });
    const observations = await db.prepare(`SELECT p.*,COALESCE(a.disposition,'normal') account_disposition,
      s.saved_at,s.pinned_at,s.dismissed_at,o.captured_at observation_captured_at,
      o.observed_index,o.score observation_score,o.decision observation_decision,
      o.is_ad observation_is_ad,o.is_reply observation_is_reply,o.is_quote observation_is_quote
      FROM post_observations o JOIN posts p ON p.post_id=o.post_id
      LEFT JOIN account_reputation a ON a.handle=p.handle LEFT JOIN user_post_state s ON s.post_id=p.post_id
      WHERE o.scan_id=? ORDER BY o.observed_index`).bind(scanId).all<Record<string, unknown>>();
    const acquisitions = await db.prepare(`SELECT acquisition_id acquisitionId,kind,target,
      topic_key topicKey,topic_label topicLabel,planned_quota plannedQuota,
      observed_count observedCount,unique_count uniqueCount,status,error,
      duration_seconds durationSeconds FROM run_acquisitions WHERE scan_id=?
      ORDER BY rowid`).bind(scanId).all();
    return Response.json({ run, acquisitions: acquisitions.results, observations: observations.results.map(publicPost) });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Run detail unavailable" }, { status: 500 });
  }
}
