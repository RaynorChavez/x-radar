import { ensureRadarDb } from "../../../lib/d1";

export async function GET() {
  try {
    const db = await ensureRadarDb();
    const rows = await db.prepare(`
      SELECT id, handle, include_replies AS includeReplies, status, requested_at AS requestedAt,
        CASE WHEN kind='mixed' OR handle='@home' OR handle='@mixed' THEN 'mixed' ELSE 'account' END AS targetKind,
        preference_version AS preferenceVersion,bootstrap_topics_json AS bootstrapTopicsJson,
        result_count AS resultCount, error
      FROM fetch_requests ORDER BY datetime(requested_at) DESC LIMIT 20
    `).all();
    return Response.json({ requests: rows.results.map((row: Record<string, unknown>) => ({
      ...row, includeReplies: Boolean(row.includeReplies),
      bootstrapTopics: JSON.parse(String(row.bootstrapTopicsJson ?? "[]")), bootstrapTopicsJson: undefined,
    })) });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Queue unavailable" }, { status: 500 });
  }
}
export async function POST(request: Request) {
  try {
    const payload = await request.json() as { handle?: string; includeReplies?: boolean; kind?: "home" | "mixed" | "account" };
    const isMixed = payload.kind === "home" || payload.kind === "mixed";
    const bare = (payload.handle ?? "").trim().replace(/^@/, "");
    if (!isMixed && !/^[A-Za-z0-9_]{1,15}$/.test(bare)) return Response.json({ error: "Enter a valid X handle" }, { status: 400 });
    const db = await ensureRadarDb();
    const preferences = await db.prepare("SELECT preference_version preferenceVersion FROM curator_preferences WHERE id=1").first<{ preferenceVersion: number }>();
    const item = { id: crypto.randomUUID(), handle: isMixed ? "@mixed" : `@${bare.toLowerCase()}`, targetKind: isMixed ? "mixed" : "account", includeReplies: !isMixed && payload.includeReplies !== false, preferenceVersion: preferences?.preferenceVersion ?? 0, bootstrapTopics: [], status: "queued", requestedAt: new Date().toISOString() };
    await db.prepare(`INSERT INTO fetch_requests (
      id,handle,include_replies,kind,preference_version,bootstrap_topics_json,status,requested_at
    ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?)`)
      .bind(item.id, item.handle, item.includeReplies ? 1 : 0, item.targetKind, item.preferenceVersion, item.status, item.requestedAt).run();
    return Response.json({ request: item }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not queue scan" }, { status: 500 });
  }
}
