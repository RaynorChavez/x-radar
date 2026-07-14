import { ensureRadarDb } from "../../../lib/d1";

function csvCell(value: unknown) { return `"${String(value ?? "").replaceAll('"', '""')}"`; }

export async function GET(request: Request) {
  try {
    const format = new URL(request.url).searchParams.get("format") ?? "json";
    if (!["json", "csv", "markdown"].includes(format)) return Response.json({ error: "Invalid format" }, { status: 400 });
    const db = await ensureRadarDb();
    const rows = await db.prepare(`SELECT p.post_id postId,p.author,p.handle,p.text,p.url,p.xcancel_url xcancelUrl,
      p.posted_at postedAt,s.saved_at savedAt,s.pinned_at pinnedAt FROM user_post_state s
      JOIN posts p ON p.post_id=s.post_id WHERE s.saved_at IS NOT NULL
      ORDER BY CASE WHEN s.pinned_at IS NOT NULL THEN 0 ELSE 1 END,datetime(s.saved_at) DESC`).all<Record<string, unknown>>();
    const stamp = new Date().toISOString().slice(0, 10);
    if (format === "csv") {
      const headers = ["postId", "author", "handle", "text", "url", "xcancelUrl", "postedAt", "savedAt", "pinnedAt"];
      const body = [headers.join(","), ...rows.results.map((row) => headers.map((key) => csvCell(row[key])).join(","))].join("\n");
      return new Response(body, { headers: { "content-type": "text/csv; charset=utf-8", "content-disposition": `attachment; filename="x-radar-saved-${stamp}.csv"` } });
    }
    if (format === "markdown") {
      const body = [`# X Radar saved — ${stamp}`, "", ...rows.results.flatMap((row) => [
        `## ${row.author ?? row.handle} (${row.handle})`, "", String(row.text), "",
        `[XCancel](${row.xcancelUrl}) · [Original](${row.url})`, "",
      ])].join("\n");
      return new Response(body, { headers: { "content-type": "text/markdown; charset=utf-8", "content-disposition": `attachment; filename="x-radar-saved-${stamp}.md"` } });
    }
    return new Response(JSON.stringify(rows.results, null, 2), { headers: { "content-type": "application/json", "content-disposition": `attachment; filename="x-radar-saved-${stamp}.json"` } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Export unavailable" }, { status: 500 });
  }
}
