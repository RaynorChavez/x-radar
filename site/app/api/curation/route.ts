import { ensureRadarDb } from "../../../lib/d1";

const maxTopics = 24;

function parseTopics(value: unknown) {
  if (!Array.isArray(value)) return null;
  const topics = [...new Set(value.map((item) => String(item).trim()).filter(Boolean))];
  if (topics.length > maxTopics || topics.some((topic) => topic.length > 60)) return null;
  return topics;
}

export async function GET() {
  try {
    const db = await ensureRadarDb();
    const row = await db.prepare("SELECT instructions,topics_json topicsJson,preference_version preferenceVersion,updated_at updatedAt FROM curator_preferences WHERE id=1").first<{ instructions: string; topicsJson: string; preferenceVersion: number; updatedAt: string }>();
    return Response.json({ instructions: row?.instructions ?? "", topics: JSON.parse(row?.topicsJson ?? "[]"), preferenceVersion: row?.preferenceVersion ?? 0, updatedAt: row?.updatedAt ?? null });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Curator brief unavailable" }, { status: 500 });
  }
}

export async function PUT(request: Request) {
  try {
    const body = await request.json() as { instructions?: unknown; topics?: unknown };
    const instructions = String(body.instructions ?? "").trim();
    const topics = parseTopics(body.topics);
    if (instructions.length > 4000) return Response.json({ error: "Instructions are limited to 4,000 characters" }, { status: 400 });
    if (!topics) return Response.json({ error: `Use at most ${maxTopics} topics of 60 characters each` }, { status: 400 });
    const now = new Date().toISOString();
    const db = await ensureRadarDb();
    const current = await db.prepare("SELECT topics_json topicsJson,preference_version preferenceVersion FROM curator_preferences WHERE id=1")
      .first<{ topicsJson: string; preferenceVersion: number }>();
    const oldTopics = JSON.parse(current?.topicsJson ?? "[]") as string[];
    const oldKeys = new Set(oldTopics.map((topic) => topic.toLocaleLowerCase()));
    const addedTopics = topics.filter((topic) => !oldKeys.has(topic.toLocaleLowerCase()));
    const preferenceVersion = Number(current?.preferenceVersion ?? 0) + 1;
    const payload = { kind: "curation", instructions, topics, preferenceVersion, updated_at: now };
    const statements = [
      db.prepare(`INSERT INTO curator_preferences(id,instructions,topics_json,preference_version,updated_at) VALUES(1,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET instructions=excluded.instructions,topics_json=excluded.topics_json,
        preference_version=excluded.preference_version,updated_at=excluded.updated_at`)
        .bind(instructions, JSON.stringify(topics), preferenceVersion, now),
      db.prepare("INSERT INTO mutations(kind,entity_id,payload_json,created_at) VALUES('curation','curator',?,?)")
        .bind(JSON.stringify(payload), now),
    ];
    let bootstrapQueued = false;
    if (addedTopics.length) {
      const pending = await db.prepare("SELECT id,bootstrap_topics_json bootstrapTopicsJson FROM fetch_requests WHERE status='queued' AND (kind='mixed' OR handle='@home') ORDER BY datetime(requested_at) LIMIT 1")
        .first<{ id: string; bootstrapTopicsJson: string }>();
      if (pending) {
        const combined = [...new Set([...(JSON.parse(pending.bootstrapTopicsJson ?? "[]") as string[]), ...addedTopics])];
        statements.push(db.prepare("UPDATE fetch_requests SET kind='mixed',handle='@mixed',preference_version=?,bootstrap_topics_json=?,requested_at=? WHERE id=?")
          .bind(preferenceVersion, JSON.stringify(combined), now, pending.id));
      } else {
        statements.push(db.prepare(`INSERT INTO fetch_requests(
          id,handle,include_replies,kind,preference_version,bootstrap_topics_json,status,requested_at
        ) VALUES(?,?,0,'mixed',?,?,'queued',?)`).bind(
          crypto.randomUUID(), "@mixed", preferenceVersion, JSON.stringify(addedTopics), now,
        ));
      }
      bootstrapQueued = true;
    }
    await db.batch(statements);
    return Response.json({ instructions, topics, preferenceVersion, addedTopics, bootstrapQueued, updatedAt: now });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Curator brief update failed" }, { status: 500 });
  }
}
