import { env } from "cloudflare:workers";
export type RadarEnv = typeof env & { DB?: D1Database; XRADAR_INGEST_TOKEN?: string };

export function getD1() {
  const db = (env as RadarEnv).DB;
  if (!db) throw new Error("D1 binding DB is unavailable");
  return db;
}

export async function ensureRadarDb() {
  const db = getD1();
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS posts (
      post_id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, handle TEXT NOT NULL, author TEXT,
      profile_image_url TEXT,
      text TEXT NOT NULL, posted_at TEXT, captured_at TEXT NOT NULL, first_seen_at TEXT, last_seen_at TEXT,
      is_ad INTEGER NOT NULL DEFAULT 0,
      is_reply INTEGER NOT NULL DEFAULT 0, is_quote INTEGER NOT NULL DEFAULT 0, source_url TEXT,
      xcancel_url TEXT, external_links_json TEXT NOT NULL DEFAULT '[]',
      media_json TEXT NOT NULL DEFAULT '[]', article_json TEXT,
      engagement_json TEXT NOT NULL DEFAULT '{}', score REAL NOT NULL DEFAULT 0,
      decision TEXT NOT NULL DEFAULT 'candidate', reasons_json TEXT NOT NULL DEFAULT '[]',
      score_components_json TEXT
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS posts_captured_idx ON posts(captured_at)"),
    db.prepare("CREATE INDEX IF NOT EXISTS posts_decision_idx ON posts(decision)"),
    db.prepare("CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(post_id UNINDEXED, text, author, handle, tokenize='porter unicode61')"),
    db.prepare(`CREATE TRIGGER IF NOT EXISTS posts_fts_insert AFTER INSERT ON posts BEGIN
      INSERT INTO posts_fts(post_id,text,author,handle) VALUES(new.post_id,new.text,COALESCE(new.author,''),new.handle);
    END`),
    db.prepare(`CREATE TRIGGER IF NOT EXISTS posts_fts_delete AFTER DELETE ON posts BEGIN
      DELETE FROM posts_fts WHERE post_id=old.post_id;
    END`),
    db.prepare(`CREATE TRIGGER IF NOT EXISTS posts_fts_update AFTER UPDATE ON posts BEGIN
      DELETE FROM posts_fts WHERE post_id=old.post_id;
      INSERT INTO posts_fts(post_id,text,author,handle) VALUES(new.post_id,new.text,COALESCE(new.author,''),new.handle);
    END`),
    db.prepare(`CREATE TABLE IF NOT EXISTS account_reputation (
      handle TEXT PRIMARY KEY, disposition TEXT NOT NULL DEFAULT 'normal', strike_points INTEGER NOT NULL DEFAULT 0,
      confidence REAL NOT NULL DEFAULT 0, reasons_json TEXT NOT NULL DEFAULT '[]', notes TEXT,
      operator_override INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS runs (
      scan_id TEXT PRIMARY KEY, host TEXT NOT NULL, source TEXT NOT NULL, target TEXT, request_id TEXT,
      captured_at TEXT NOT NULL, posts_seen INTEGER NOT NULL DEFAULT 0,
      posts_kept INTEGER NOT NULL DEFAULT 0, posts_added INTEGER NOT NULL DEFAULT 0,
      duplicates INTEGER NOT NULL DEFAULT 0, candidates INTEGER NOT NULL DEFAULT 0,
      discarded INTEGER NOT NULL DEFAULT 0, signals_count INTEGER NOT NULL DEFAULT 0,
      media_count INTEGER NOT NULL DEFAULT 0, links_count INTEGER NOT NULL DEFAULT 0,
      duration_seconds INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'complete',
      ingested_at TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS collector_state (
      id INTEGER PRIMARY KEY, phase TEXT NOT NULL DEFAULT 'idle', target TEXT, request_id TEXT,
      scan_id TEXT, observed INTEGER NOT NULL DEFAULT 0, pending_sync INTEGER NOT NULL DEFAULT 0,
      failed_attempts INTEGER NOT NULL DEFAULT 0, auth_status TEXT NOT NULL DEFAULT 'ok',
      last_error TEXT, started_at TEXT, updated_at TEXT NOT NULL, last_sync_at TEXT,
      last_backup_at TEXT
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS curator_preferences (
      id INTEGER PRIMARY KEY, instructions TEXT NOT NULL DEFAULT '',
      topics_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS post_observations (
      id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, post_id TEXT NOT NULL, captured_at TEXT NOT NULL,
      observed_index INTEGER NOT NULL DEFAULT 0, score REAL NOT NULL DEFAULT 0,
      decision TEXT NOT NULL DEFAULT 'candidate', is_ad INTEGER NOT NULL DEFAULT 0,
      is_reply INTEGER NOT NULL DEFAULT 0, is_quote INTEGER NOT NULL DEFAULT 0,
      score_components_json TEXT
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS observations_captured_idx ON post_observations(captured_at, observed_index)"),
    db.prepare("CREATE INDEX IF NOT EXISTS observations_post_idx ON post_observations(post_id)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS user_post_state (
      post_id TEXT PRIMARY KEY, saved_at TEXT, pinned_at TEXT, dismissed_at TEXT, updated_at TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS mutations (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS fetch_requests (
      id TEXT PRIMARY KEY, handle TEXT NOT NULL, include_replies INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'queued', requested_at TEXT NOT NULL, completed_at TEXT,
      result_count INTEGER, error TEXT
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS fetch_status_idx ON fetch_requests(status, requested_at)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS run_acquisitions (
      acquisition_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
      topic_key TEXT, topic_label TEXT, planned_quota INTEGER NOT NULL DEFAULT 0,
      observed_count INTEGER NOT NULL DEFAULT 0, unique_count INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'complete', error TEXT, duration_seconds INTEGER NOT NULL DEFAULT 0
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS run_acquisitions_scan_idx ON run_acquisitions(scan_id)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS observation_acquisitions (
      id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, acquisition_id TEXT NOT NULL,
      is_primary INTEGER NOT NULL DEFAULT 0
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS observation_acquisitions_observation_idx ON observation_acquisitions(observation_id)"),
  ]);
  await db.prepare(`INSERT OR IGNORE INTO curator_preferences(id,instructions,topics_json,updated_at)
    VALUES(1,'','[]',?)`)
    .bind(new Date().toISOString()).run();

  const postColumns = await db.prepare("PRAGMA table_info(posts)").all<{ name: string }>();
  const existing = new Set(postColumns.results.map((column) => column.name));
  const additions: Array<[string, string]> = [
    ["xcancel_url", "TEXT"],
    ["external_links_json", "TEXT NOT NULL DEFAULT '[]'"],
    ["media_json", "TEXT NOT NULL DEFAULT '[]'"],
    ["article_json", "TEXT"],
    ["profile_image_url", "TEXT"],
    ["first_seen_at", "TEXT"],
    ["last_seen_at", "TEXT"],
    ["score_components_json", "TEXT"],
  ];
  for (const [name, definition] of additions) {
    if (!existing.has(name)) await db.prepare(`ALTER TABLE posts ADD COLUMN ${name} ${definition}`).run();
  }
  const observationColumns = await db.prepare("PRAGMA table_info(post_observations)").all<{ name: string }>();
  const observationExisting = new Set(observationColumns.results.map((column) => column.name));
  if (!observationExisting.has("scan_id")) {
    await db.prepare("ALTER TABLE post_observations ADD COLUMN scan_id TEXT NOT NULL DEFAULT 'legacy'").run();
  }
  if (!observationExisting.has("preference_version")) await db.prepare("ALTER TABLE post_observations ADD COLUMN preference_version INTEGER NOT NULL DEFAULT 0").run();
  if (!observationExisting.has("topic_matches_json")) await db.prepare("ALTER TABLE post_observations ADD COLUMN topic_matches_json TEXT NOT NULL DEFAULT '[]'").run();
  if (!observationExisting.has("score_components_json")) await db.prepare("ALTER TABLE post_observations ADD COLUMN score_components_json TEXT").run();
  const accountColumns = await db.prepare("PRAGMA table_info(account_reputation)").all<{ name: string }>();
  const accountExisting = new Set(accountColumns.results.map((column) => column.name));
  if (!accountExisting.has("notes")) await db.prepare("ALTER TABLE account_reputation ADD COLUMN notes TEXT").run();
  if (!accountExisting.has("operator_override")) await db.prepare("ALTER TABLE account_reputation ADD COLUMN operator_override INTEGER NOT NULL DEFAULT 0").run();
  const runColumns = await db.prepare("PRAGMA table_info(runs)").all<{ name: string }>();
  const runExisting = new Set(runColumns.results.map((column) => column.name));
  const runAdditions: Array<[string, string]> = [
    ["posts_added", "INTEGER NOT NULL DEFAULT 0"], ["duplicates", "INTEGER NOT NULL DEFAULT 0"],
    ["candidates", "INTEGER NOT NULL DEFAULT 0"], ["discarded", "INTEGER NOT NULL DEFAULT 0"],
    ["signals_count", "INTEGER NOT NULL DEFAULT 0"], ["media_count", "INTEGER NOT NULL DEFAULT 0"],
    ["links_count", "INTEGER NOT NULL DEFAULT 0"], ["duration_seconds", "INTEGER NOT NULL DEFAULT 0"],
    ["status", "TEXT NOT NULL DEFAULT 'complete'"],
    ["schema_version", "INTEGER NOT NULL DEFAULT 1"], ["period_id", "TEXT"],
    ["preference_version", "INTEGER NOT NULL DEFAULT 0"], ["target_unique", "INTEGER NOT NULL DEFAULT 100"],
  ];
  for (const [name, definition] of runAdditions) {
    if (!runExisting.has(name)) await db.prepare(`ALTER TABLE runs ADD COLUMN ${name} ${definition}`).run();
  }
  const stateColumns = await db.prepare("PRAGMA table_info(collector_state)").all<{ name: string }>();
  const stateExisting = new Set(stateColumns.results.map((column) => column.name));
  if (!stateExisting.has("last_backup_at")) {
    await db.prepare("ALTER TABLE collector_state ADD COLUMN last_backup_at TEXT").run();
  }
  if (!stateExisting.has("target_unique")) await db.prepare("ALTER TABLE collector_state ADD COLUMN target_unique INTEGER NOT NULL DEFAULT 100").run();
  if (!stateExisting.has("preference_version")) await db.prepare("ALTER TABLE collector_state ADD COLUMN preference_version INTEGER NOT NULL DEFAULT 0").run();
  if (!stateExisting.has("source_progress_json")) await db.prepare("ALTER TABLE collector_state ADD COLUMN source_progress_json TEXT NOT NULL DEFAULT '{}'").run();
  const curatorColumns = await db.prepare("PRAGMA table_info(curator_preferences)").all<{ name: string }>();
  if (!curatorColumns.results.some((column) => column.name === "preference_version")) await db.prepare("ALTER TABLE curator_preferences ADD COLUMN preference_version INTEGER NOT NULL DEFAULT 0").run();
  const requestColumns = await db.prepare("PRAGMA table_info(fetch_requests)").all<{ name: string }>();
  const requestExisting = new Set(requestColumns.results.map((column) => column.name));
  if (!requestExisting.has("kind")) await db.prepare("ALTER TABLE fetch_requests ADD COLUMN kind TEXT NOT NULL DEFAULT 'account'").run();
  if (!requestExisting.has("preference_version")) await db.prepare("ALTER TABLE fetch_requests ADD COLUMN preference_version INTEGER NOT NULL DEFAULT 0").run();
  if (!requestExisting.has("bootstrap_topics_json")) await db.prepare("ALTER TABLE fetch_requests ADD COLUMN bootstrap_topics_json TEXT NOT NULL DEFAULT '[]'").run();
  await db.prepare(`UPDATE posts SET xcancel_url =
    'https://xcancel.com/' || ltrim(handle, '@') || '/status/' || post_id
    WHERE xcancel_url IS NULL OR xcancel_url = ''`).run();
  await db.prepare(`UPDATE posts SET external_links_json = json_array(json_object('url', source_url))
    WHERE source_url IS NOT NULL AND source_url != ''
      AND (external_links_json IS NULL OR external_links_json = '[]')`).run();
  await db.prepare(`UPDATE posts SET first_seen_at=COALESCE(first_seen_at,captured_at),
    last_seen_at=COALESCE(last_seen_at,captured_at)
    WHERE first_seen_at IS NULL OR last_seen_at IS NULL`).run();
  await db.prepare(`INSERT INTO posts_fts(post_id,text,author,handle)
    SELECT p.post_id,p.text,COALESCE(p.author,''),p.handle FROM posts p
    WHERE NOT EXISTS(SELECT 1 FROM posts_fts f WHERE f.post_id=p.post_id)`).run();

  return db;
}

export function collectorAuthorized(request: Request) {
  const token = (env as RadarEnv).XRADAR_INGEST_TOKEN;
  if (!token) return false;
  return request.headers.get("authorization") === `Bearer ${token}`;
}
