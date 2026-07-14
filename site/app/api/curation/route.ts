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
    const row = await db.prepare("SELECT instructions,topics_json topicsJson,updated_at updatedAt FROM curator_preferences WHERE id=1").first<{ instructions: string; topicsJson: string; updatedAt: string }>();
    return Response.json({ instructions: row?.instructions ?? "", topics: JSON.parse(row?.topicsJson ?? "[]"), updatedAt: row?.updatedAt ?? null });
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
    const payload = { kind: "curation", instructions, topics, updated_at: now };
    const db = await ensureRadarDb();
    await db.batch([
      db.prepare(`INSERT INTO curator_preferences(id,instructions,topics_json,updated_at) VALUES(1,?,?,?)
        ON CONFLICT(id) DO UPDATE SET instructions=excluded.instructions,topics_json=excluded.topics_json,updated_at=excluded.updated_at`)
        .bind(instructions, JSON.stringify(topics), now),
      db.prepare("INSERT INTO mutations(kind,entity_id,payload_json,created_at) VALUES('curation','curator',?,?)")
        .bind(JSON.stringify(payload), now),
    ]);
    return Response.json({ instructions, topics, updatedAt: now });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Curator brief update failed" }, { status: 500 });
  }
}
