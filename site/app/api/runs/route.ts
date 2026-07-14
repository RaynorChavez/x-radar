import { ensureRadarDb } from "../../../lib/d1";

export async function GET(request: Request) {
  try {
    const limit = Math.min(100, Math.max(1, Number(new URL(request.url).searchParams.get("limit") ?? 30)));
    const db = await ensureRadarDb();
    const rows = await db.prepare(`SELECT scan_id scanId,host,source,target,request_id requestId,
      captured_at capturedAt,posts_seen postsSeen,posts_kept postsKept,posts_added postsAdded,
      duplicates,candidates,discarded,signals_count signalsCount,media_count mediaCount,
      links_count linksCount,duration_seconds durationSeconds,status,ingested_at ingestedAt
      FROM runs ORDER BY datetime(ingested_at) DESC LIMIT ?`).bind(limit).all();
    return Response.json({ runs: rows.results });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Run history unavailable" }, { status: 500 });
  }
}
