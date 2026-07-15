from __future__ import annotations

import gzip
import hashlib
import json
import math
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DISPOSITIONS = {"allow", "normal", "watch", "downrank", "blocked"}
SCORE_WEIGHTS = {
    "novelty": 0.30,
    "evidence": 0.25,
    "relevance": 0.20,
    "density": 0.15,
    "importance": 0.10,
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    handle TEXT NOT NULL,
    author TEXT,
    profile_image_url TEXT,
    text TEXT NOT NULL,
    posted_at TEXT,
    captured_at TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    is_ad INTEGER NOT NULL DEFAULT 0,
    is_reply INTEGER NOT NULL DEFAULT 0,
    is_quote INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    xcancel_url TEXT,
    external_links_json TEXT NOT NULL DEFAULT '[]',
    media_json TEXT NOT NULL DEFAULT '[]',
    article_json TEXT,
    engagement_json TEXT NOT NULL DEFAULT '{}',
    score REAL NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'candidate',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    score_components_json TEXT
);

CREATE TABLE IF NOT EXISTS account_reputation (
    handle TEXT PRIMARY KEY,
    disposition TEXT NOT NULL CHECK (
        disposition IN ('allow', 'normal', 'watch', 'downrank', 'blocked')
    ),
    strike_points INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    operator_override INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS account_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL REFERENCES account_reputation(handle),
    post_id TEXT,
    post_url TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(handle, post_url, reason)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT UNIQUE,
    host TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'x-home',
    target TEXT,
    request_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    posts_seen INTEGER NOT NULL DEFAULT 0,
    posts_kept INTEGER NOT NULL DEFAULT 0,
    posts_added INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT
);

CREATE TABLE IF NOT EXISTS post_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    post_id TEXT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    observed_index INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'candidate',
    score_components_json TEXT,
    UNIQUE(run_id, post_id)
);
CREATE INDEX IF NOT EXISTS observations_post_idx
    ON post_observations(post_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS sync_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due_idx
    ON sync_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS user_post_state (
    post_id TEXT PRIMARY KEY REFERENCES posts(post_id) ON DELETE CASCADE,
    saved_at TEXT,
    pinned_at TEXT,
    dismissed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preference_versions (
    version INTEGER PRIMARY KEY,
    instructions TEXT NOT NULL DEFAULT '',
    topics_json TEXT NOT NULL DEFAULT '[]',
    effective_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_state (
    topic_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    normalized_label TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    last_planned_at TEXT,
    last_searched_at TEXT,
    query_pack_json TEXT NOT NULL DEFAULT '[]',
    query_pack_revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topic_query_memory (
    topic_key TEXT NOT NULL REFERENCES topic_state(topic_key),
    query_key TEXT NOT NULL,
    query_kind TEXT NOT NULL,
    query TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    observed_total INTEGER NOT NULL DEFAULT 0,
    unique_total INTEGER NOT NULL DEFAULT 0,
    kept_total INTEGER NOT NULL DEFAULT 0,
    consecutive_empty INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    cooldown_until TEXT,
    PRIMARY KEY(topic_key,query_key)
);
CREATE INDEX IF NOT EXISTS topic_query_memory_rotation_idx
    ON topic_query_memory(topic_key,cooldown_until,last_attempted_at);

CREATE TABLE IF NOT EXISTS topic_account_memory (
    topic_key TEXT NOT NULL REFERENCES topic_state(topic_key),
    handle TEXT NOT NULL,
    relevant_observations INTEGER NOT NULL DEFAULT 0,
    kept_posts INTEGER NOT NULL DEFAULT 0,
    score_sum REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','known')),
    last_observed_at TEXT NOT NULL,
    last_scanned_at TEXT,
    PRIMARY KEY(topic_key,handle)
);

CREATE TABLE IF NOT EXISTS run_acquisitions (
    acquisition_id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    topic_key TEXT,
    topic_label TEXT,
    query_kind TEXT,
    query TEXT,
    planned_quota INTEGER NOT NULL DEFAULT 0,
    observed_count INTEGER NOT NULL DEFAULT 0,
    unique_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'complete',
    error TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observation_acquisitions (
    observation_id INTEGER NOT NULL REFERENCES post_observations(id) ON DELETE CASCADE,
    acquisition_id TEXT NOT NULL REFERENCES run_acquisitions(acquisition_id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(observation_id,acquisition_id)
);

CREATE TABLE IF NOT EXISTS post_embeddings (
    post_id TEXT PRIMARY KEY REFERENCES posts(post_id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    embedded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS post_embeddings_model_idx
    ON post_embeddings(model_id, embedded_at);

CREATE TABLE IF NOT EXISTS enrichment_jobs (
    scan_id TEXT PRIMARY KEY,
    raw_path TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    output_path TEXT NOT NULL,
    ranking_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','ready','complete')),
    total_items INTEGER NOT NULL,
    batch_max_items INTEGER NOT NULL,
    batch_max_chars INTEGER NOT NULL,
    preference_json TEXT NOT NULL,
    allowed_topics_json TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS enrichment_items (
    scan_id TEXT NOT NULL REFERENCES enrichment_jobs(scan_id) ON DELETE CASCADE,
    post_id TEXT NOT NULL,
    observed_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','in_progress','failed','accepted')),
    batch_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    enrichment_json TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scan_id,post_id),
    UNIQUE(scan_id,observed_index)
);
CREATE INDEX IF NOT EXISTS enrichment_items_next_idx
    ON enrichment_items(scan_id,status,observed_index);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn: sqlite3.Connection, table: str, additions: dict[str, str]) -> None:
    columns = _columns(conn, table)
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "posts", {
        "xcancel_url": "TEXT", "external_links_json": "TEXT NOT NULL DEFAULT '[]'",
        "media_json": "TEXT NOT NULL DEFAULT '[]'", "article_json": "TEXT",
        "profile_image_url": "TEXT", "first_seen_at": "TEXT", "last_seen_at": "TEXT",
        "score_components_json": "TEXT",
    })
    _add_columns(conn, "runs", {
        "scan_id": "TEXT", "source": "TEXT NOT NULL DEFAULT 'x-home'",
        "target": "TEXT", "request_id": "TEXT", "posts_added": "INTEGER NOT NULL DEFAULT 0",
        "error": "TEXT", "schema_version": "INTEGER NOT NULL DEFAULT 1",
        "period_id": "TEXT", "preference_version": "INTEGER NOT NULL DEFAULT 0",
        "target_unique": "INTEGER NOT NULL DEFAULT 100",
    })
    _add_columns(conn, "post_observations", {
        "preference_version": "INTEGER NOT NULL DEFAULT 0",
        "topic_matches_json": "TEXT NOT NULL DEFAULT '[]'",
        "score_components_json": "TEXT",
    })
    _add_columns(conn, "run_acquisitions", {
        "query_kind": "TEXT", "query": "TEXT",
    })
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_scan_id_idx ON runs(scan_id)")
    conn.execute("""
        UPDATE posts SET xcancel_url =
          'https://xcancel.com/' || ltrim(handle, '@') || '/status/' || post_id
        WHERE xcancel_url IS NULL OR xcancel_url = ''
    """)
    conn.execute("""
        UPDATE posts SET external_links_json = json_array(json_object('url', source_url))
        WHERE source_url IS NOT NULL AND source_url != ''
          AND (external_links_json IS NULL OR external_links_json = '[]')
    """)
    conn.execute("""
        UPDATE posts SET first_seen_at=COALESCE(first_seen_at, captured_at),
                         last_seen_at=COALESCE(last_seen_at, captured_at)
    """)
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                post_id UNINDEXED, text, author, handle, tokenize='porter unicode61'
            )
        """)
        conn.execute("""
            INSERT INTO posts_fts(post_id, text, author, handle)
            SELECT p.post_id, p.text, COALESCE(p.author, ''), p.handle FROM posts p
            WHERE NOT EXISTS (SELECT 1 FROM posts_fts f WHERE f.post_id=p.post_id)
        """)
    except sqlite3.OperationalError:
        pass
    if conn.execute("SELECT count(*) FROM topic_state").fetchone()[0] == 0:
        preference = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
        if preference:
            try:
                snapshot = json.loads(preference["value"])
                from .planner import apply_preferences
                apply_preferences(
                    conn, instructions=snapshot.get("instructions", ""), topics=snapshot.get("topics", []),
                    version=int(snapshot.get("version") or snapshot.get("preferenceVersion") or 0),
                    updated_at=snapshot.get("updated_at") or snapshot.get("updatedAt"),
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass


def normalize_handle(handle: str) -> str:
    value = handle.strip().lower()
    return value if value.startswith("@") else f"@{value}"


def xcancel_url(handle: str, post_id: str, article_id: str | None = None) -> str:
    if article_id:
        return f"https://xcancel.com/i/article/{article_id}"
    return f"https://xcancel.com/{normalize_handle(handle).lstrip('@')}/status/{post_id}"


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, sort_keys=True)


def normalize_score_components(value: Any) -> tuple[dict[str, Any] | None, float | None]:
    """Validate Luna's auditable rubric and deterministically calculate its final score."""
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise ValueError("score_components must be an object")
    normalized: dict[str, Any] = {}
    weighted_total = 0.0
    for name, weight in SCORE_WEIGHTS.items():
        component = value.get(name)
        if not isinstance(component, dict):
            raise ValueError(f"score_components.{name} must be an object")
        raw_score = component.get("score")
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError(f"score_components.{name}.score must be a number")
        score = float(raw_score)
        if not math.isfinite(score) or score < 0 or score > 1:
            raise ValueError(f"score_components.{name}.score must be between 0 and 1")
        rationale = str(component.get("rationale") or "").strip()
        if not rationale or len(rationale) > 280:
            raise ValueError(f"score_components.{name}.rationale must be 1-280 characters")
        normalized[name] = {"score": round(score, 4), "weight": weight, "rationale": rationale}
        weighted_total += score * weight
    penalties: list[dict[str, Any]] = []
    penalty_total = 0.0
    raw_penalties = value.get("penalties", [])
    if not isinstance(raw_penalties, list) or len(raw_penalties) > 8:
        raise ValueError("score_components.penalties must be an array of at most 8 items")
    for penalty in raw_penalties:
        if not isinstance(penalty, dict):
            raise ValueError("each score penalty must be an object")
        kind = str(penalty.get("kind") or "").strip()
        rationale = str(penalty.get("rationale") or "").strip()
        raw_amount = penalty.get("amount")
        if not kind or len(kind) > 64 or not rationale or len(rationale) > 280:
            raise ValueError("score penalty kind and rationale are required")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
            raise ValueError("score penalty amount must be a number")
        amount = float(raw_amount)
        if not math.isfinite(amount) or amount < 0 or amount > 1:
            raise ValueError("score penalty amount must be between 0 and 1")
        penalties.append({"kind": kind, "amount": round(amount, 4), "rationale": rationale})
        penalty_total += amount
    final_score = max(0.0, min(1.0, weighted_total - penalty_total))
    normalized.update({
        "penalties": penalties,
        "weighted_total": round(weighted_total, 4),
        "penalty_total": round(penalty_total, 4),
        "final_score": round(final_score, 4),
    })
    return normalized, round(final_score, 4)


def normalize_ranked_post(post: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(post)
    raw_components = post.get("score_components")
    topic_matches = post.get("topic_matches")
    if isinstance(raw_components, dict) and isinstance(topic_matches, list):
        confidences = [
            float(match["confidence"])
            for match in topic_matches
            if isinstance(match, dict)
            and isinstance(match.get("confidence"), (int, float))
            and not isinstance(match.get("confidence"), bool)
            and math.isfinite(float(match["confidence"]))
            and 0 <= float(match["confidence"]) <= 1
        ]
        relevance = raw_components.get("relevance")
        if confidences and isinstance(relevance, dict):
            strongest = max(confidences)
            current = relevance.get("score")
            if isinstance(current, (int, float)) and not isinstance(current, bool) and strongest > float(current):
                raw_components = dict(raw_components)
                relevance = dict(relevance)
                relevance["score"] = round(strongest, 4)
                rationale = str(relevance.get("rationale") or "").strip()
                relevance["rationale"] = (
                    f"{rationale} Direct active-topic match calibrates relevance to {strongest:.2f}."
                ).strip()[:280]
                raw_components["relevance"] = relevance
    components, final_score = normalize_score_components(raw_components)
    if components is not None:
        normalized["score_components"] = components
        normalized["score"] = final_score
    return normalized


def canonical_content_tier(post: dict[str, Any]) -> int:
    """Return how much rankable content an observation actually captured.

    Article bodies outrank post text, which outranks link/media-only cards.
    This deliberately measures capture completeness, not signal quality.
    """
    article = post.get("article") if isinstance(post.get("article"), dict) else {}
    if str(article.get("content") or "").strip():
        return 3
    if str(post.get("text") or "").strip():
        return 2
    if post.get("external_links") or post.get("media"):
        return 1
    return 0


def upsert_post(conn: sqlite3.Connection, post: dict[str, Any]) -> bool:
    post = normalize_ranked_post(post)
    post_id = str(post["post_id"])
    existing = conn.execute("""
        SELECT text,external_links_json,media_json,article_json FROM posts WHERE post_id=?
    """, (post_id,)).fetchone()
    existed = existing is not None
    existing_tier = canonical_content_tier({
        "text": existing["text"] if existing else "",
        "external_links": json.loads(existing["external_links_json"] or "[]") if existing else [],
        "media": json.loads(existing["media_json"] or "[]") if existing else [],
        "article": json.loads(existing["article_json"] or "null") if existing else None,
    })
    promote_canonical = not existed or canonical_content_tier(post) >= existing_tier
    captured_at = post.get("captured_at") or utcnow()
    article = post.get("article")
    article_id = str(article["id"]) if isinstance(article, dict) and article.get("id") else None
    mirror_url = (
        xcancel_url(post["handle"], post_id, article_id)
        if article_id else post.get("xcancel_url") or xcancel_url(post["handle"], post_id)
    )
    conn.execute("""
        INSERT INTO posts (
            post_id, url, handle, author, profile_image_url, text, posted_at, captured_at,
            first_seen_at, last_seen_at, is_ad, is_reply, is_quote, source_url, xcancel_url,
            external_links_json, media_json, article_json, engagement_json,
            score, decision, reasons_json, score_components_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            url=excluded.url, handle=excluded.handle,
            author=COALESCE(excluded.author, posts.author),
            profile_image_url=COALESCE(excluded.profile_image_url, posts.profile_image_url),
            text=CASE WHEN excluded.text != '' THEN excluded.text ELSE posts.text END,
            posted_at=COALESCE(excluded.posted_at, posts.posted_at),
            captured_at=CASE WHEN ? THEN excluded.captured_at ELSE posts.captured_at END,
            last_seen_at=excluded.last_seen_at,
            is_ad=excluded.is_ad, is_reply=excluded.is_reply, is_quote=excluded.is_quote,
            source_url=COALESCE(excluded.source_url, posts.source_url),
            xcancel_url=COALESCE(excluded.xcancel_url, posts.xcancel_url),
            external_links_json=CASE WHEN excluded.external_links_json != '[]'
                THEN excluded.external_links_json ELSE posts.external_links_json END,
            media_json=CASE WHEN excluded.media_json != '[]'
                THEN excluded.media_json ELSE posts.media_json END,
            article_json=COALESCE(excluded.article_json, posts.article_json),
            engagement_json=excluded.engagement_json,
            score=CASE WHEN ? THEN excluded.score ELSE posts.score END,
            decision=CASE WHEN ? THEN excluded.decision ELSE posts.decision END,
            reasons_json=CASE WHEN ? THEN excluded.reasons_json ELSE posts.reasons_json END,
            score_components_json=CASE WHEN ? THEN COALESCE(excluded.score_components_json, posts.score_components_json)
                ELSE posts.score_components_json END
    """, (
        post_id, post["url"], normalize_handle(post["handle"]), post.get("author"),
        post.get("profile_image_url"), post.get("text", ""), post.get("posted_at"), captured_at,
        captured_at, captured_at, int(bool(post.get("is_ad"))), int(bool(post.get("is_reply"))),
        int(bool(post.get("is_quote"))), post.get("source_url"), mirror_url,
        _json(post.get("external_links"), []), _json(post.get("media"), []),
        _json(article, {}) if article else None, _json(post.get("engagement"), {}),
        float(post.get("score", 0)), post.get("decision", "candidate"),
        _json(post.get("reasons"), []),
        _json(post.get("score_components"), {}) if post.get("score_components") else None,
        int(promote_canonical), int(promote_canonical), int(promote_canonical),
        int(promote_canonical), int(promote_canonical),
    ))
    try:
        canonical = conn.execute("SELECT text,author,handle FROM posts WHERE post_id=?", (post_id,)).fetchone()
        conn.execute("DELETE FROM posts_fts WHERE post_id=?", (post_id,))
        conn.execute("INSERT INTO posts_fts(post_id,text,author,handle) VALUES(?,?,?,?)", (
            post_id, canonical["text"], canonical["author"] or "", canonical["handle"],
        ))
    except sqlite3.OperationalError:
        pass
    return not existed


def _scan_id(payload: dict[str, Any]) -> str:
    if payload.get("scan_id"):
        return str(payload["scan_id"])
    stable = "|".join(str(payload.get(k, "")) for k in ("host", "captured_at", "source", "target"))
    if not stable.strip("|"):
        return str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"x-radar:{stable}"))


def enqueue(conn: sqlite3.Connection, event_key: str, kind: str, payload: dict[str, Any]) -> None:
    now = utcnow()
    conn.execute("""
        INSERT OR IGNORE INTO sync_outbox(
            event_key, kind, payload_json, next_attempt_at, created_at
        ) VALUES(?,?,?,?,?)
    """, (event_key, kind, json.dumps(payload, sort_keys=True), now, now))


def ingest_capture(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    scan_id = _scan_id(payload)
    captured_at = payload.get("captured_at") or utcnow()
    source_value = payload.get("source", "x-home")
    legacy_target = source_value.get("target") if isinstance(source_value, dict) else None
    target = payload.get("target") or legacy_target
    source_name = (
        "x-account" if target and "/with_replies" in str(target) else "x-home"
    ) if isinstance(source_value, dict) else str(source_value)
    posts = [normalize_ranked_post(post) for post in payload.get("posts", [])]
    signals = payload.get("account_signals", [])
    run = conn.execute("SELECT id FROM runs WHERE scan_id=?", (scan_id,)).fetchone()
    if run:
        run_id = int(run["id"])
        added = 0
    else:
        schema_version = int(payload.get("schema_version") or 1)
        preference_version = int(payload.get("preference_version") or 0)
        period_id = str(payload.get("period_id") or scan_id)
        target_unique = int(payload.get("target_unique") or (150 if source_name == "x-mixed" else 100))
        run_id = conn.execute("""
            INSERT INTO runs(
                scan_id,host,source,target,request_id,started_at,
                schema_version,period_id,preference_version,target_unique
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_id, payload.get("host", "unknown"), source_name,
            target, payload.get("request_id"), captured_at,
            schema_version, period_id, preference_version, target_unique,
        )).lastrowid
        acquisitions = list(payload.get("acquisitions") or [])
        acquisition_ids = {
            str(item.get("id") or item.get("acquisition_id") or "")
            for item in acquisitions
        }
        # Version-1 single-target captures did not have an envelope-level
        # acquisitions array. Newer collectors may still annotate their posts
        # with discovery provenance, so synthesize the missing parent records
        # rather than rejecting an otherwise valid legacy capture.
        for post in posts:
            for discovery in post.get("discovery_sources", []):
                acquisition_id = str(discovery.get("acquisition_id") or discovery.get("id") or "")
                if not acquisition_id or acquisition_id in acquisition_ids:
                    continue
                acquisitions.append({
                    "id": acquisition_id,
                    "kind": discovery.get("kind") or ("account" if source_name == "x-account" else "home"),
                    "url": discovery.get("url") or discovery.get("target") or target or "",
                    "quota": payload.get("collector", {}).get("limit") or len(posts),
                    "observed": len(posts),
                    "unique": len(posts),
                    "status": "complete",
                })
                acquisition_ids.add(acquisition_id)
        for acquisition in acquisitions:
            conn.execute("""
                INSERT OR IGNORE INTO run_acquisitions(
                    acquisition_id,run_id,kind,target,topic_key,topic_label,query_kind,query,
                    planned_quota,observed_count,unique_count,status,error,duration_seconds
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(acquisition["id"]), run_id, acquisition.get("kind", "unknown"),
                acquisition.get("url") or acquisition.get("target") or "",
                acquisition.get("topic_key"), acquisition.get("topic"),
                acquisition.get("query_kind"), acquisition.get("query"),
                int(acquisition.get("quota") or acquisition.get("planned_quota") or 0),
                int(acquisition.get("observed") or acquisition.get("observed_count") or 0),
                int(acquisition.get("unique") or acquisition.get("unique_count") or 0),
                acquisition.get("status", "complete"), acquisition.get("error"),
                int(acquisition.get("duration_seconds") or 0),
            ))
        added = 0
        for index, post in enumerate(posts):
            enriched = {**post, "captured_at": post.get("captured_at") or captured_at}
            added += int(upsert_post(conn, enriched))
            observation_id = conn.execute("""
                INSERT OR IGNORE INTO post_observations(
                    run_id,post_id,captured_at,observed_index,score,decision,
                    preference_version,topic_matches_json,score_components_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                run_id, str(post["post_id"]), captured_at, index,
                float(post.get("score", 0)), post.get("decision", "candidate"),
                preference_version, _json(post.get("topic_matches"), []),
                _json(post.get("score_components"), {}) if post.get("score_components") else None,
            )).lastrowid
            if not observation_id:
                row = conn.execute("SELECT id FROM post_observations WHERE run_id=? AND post_id=?", (run_id, str(post["post_id"]))).fetchone()
                observation_id = int(row["id"]) if row else None
            for source_index, discovery in enumerate(post.get("discovery_sources", [])):
                acquisition_id = str(discovery.get("acquisition_id") or discovery.get("id") or "")
                if observation_id and acquisition_id:
                    conn.execute("""
                        INSERT OR IGNORE INTO observation_acquisitions(observation_id,acquisition_id,is_primary)
                        VALUES(?,?,?)
                    """, (observation_id, acquisition_id, int(source_index == 0)))
            _update_topic_account_memory(conn, post, captured_at)
        _record_query_yields(conn, acquisitions, posts, captured_at)
        for signal in signals:
            add_evidence(
                conn, handle=signal["handle"],
                post_id=str(signal["post_id"]) if signal.get("post_id") else None,
                post_url=signal["post_url"], reason=signal["reason"],
                confidence=float(signal["confidence"]),
            )
        kept = sum(post.get("decision") == "keep" for post in posts)
        conn.execute("""
            UPDATE runs SET finished_at=?,posts_seen=?,posts_kept=?,posts_added=?,status='complete'
            WHERE id=?
        """, (utcnow(), len(posts), kept, added, run_id))
        event = {**payload, "scan_id": scan_id, "captured_at": captured_at,
                 "source": source_name, "target": target, "posts": posts}
        enqueue(conn, f"capture:{scan_id}", "capture", event)
    return {
        "scan_id": scan_id, "posts_seen": len(posts), "posts_added": added,
        "duplicates": len(posts) - added, "signals_seen": len(signals),
    }


def _record_query_yields(
    conn: sqlite3.Connection,
    acquisitions: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    observed_at: str,
) -> None:
    now = utcnow()
    for acquisition in acquisitions:
        if acquisition.get("kind") not in {"topic_search", "explore_query"}:
            continue
        topic_key = str(acquisition.get("topic_key") or "").strip()
        query = " ".join(str(acquisition.get("query") or "").split()).strip()
        if not topic_key or not query:
            continue
        acquisition_id = str(acquisition.get("id") or acquisition.get("acquisition_id") or "")
        kept = 0
        for post in posts:
            sources = post.get("discovery_sources", [])
            if post.get("decision") == "keep" and any(
                str(source.get("acquisition_id") or source.get("id") or "") == acquisition_id
                for source in sources if isinstance(source, dict)
            ):
                kept += 1
        unique = int(acquisition.get("unique") or acquisition.get("unique_count") or 0)
        observed = int(acquisition.get("observed") or acquisition.get("observed_count") or 0)
        empty = unique == 0 or acquisition.get("status") == "error"
        cooldown = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat() if empty else None
        key = hashlib.sha256(" ".join(query.casefold().split()).encode()).hexdigest()[:16]
        conn.execute(
            """
            INSERT INTO topic_query_memory(
              topic_key,query_key,query_kind,query,attempts,observed_total,unique_total,
              kept_total,consecutive_empty,last_attempted_at,cooldown_until
            ) VALUES(?,?,?,?,1,?,?,?,?,?,?)
            ON CONFLICT(topic_key,query_key) DO UPDATE SET
              query_kind=excluded.query_kind,query=excluded.query,
              attempts=topic_query_memory.attempts+1,
              observed_total=topic_query_memory.observed_total+excluded.observed_total,
              unique_total=topic_query_memory.unique_total+excluded.unique_total,
              kept_total=topic_query_memory.kept_total+excluded.kept_total,
              consecutive_empty=CASE WHEN excluded.consecutive_empty=1
                THEN topic_query_memory.consecutive_empty+1 ELSE 0 END,
              last_attempted_at=excluded.last_attempted_at,
              cooldown_until=excluded.cooldown_until
            """,
            (
                topic_key, key, acquisition.get("query_kind") or "canonical", query,
                observed, unique, kept, int(empty), observed_at or now, cooldown,
            ),
        )


def _update_topic_account_memory(conn: sqlite3.Connection, post: dict[str, Any], observed_at: str) -> None:
    handle = normalize_handle(post.get("handle", ""))
    if handle == "@":
        return
    score = float(post.get("score", 0))
    kept = int(post.get("decision") == "keep")
    for match in post.get("topic_matches", []):
        if not isinstance(match, dict) or float(match.get("confidence", 0)) < .65:
            continue
        key = str(match.get("topic_key") or "")
        if not key or not conn.execute("SELECT 1 FROM topic_state WHERE topic_key=?", (key,)).fetchone():
            continue
        conn.execute("""
            INSERT INTO topic_account_memory(
                topic_key,handle,relevant_observations,kept_posts,score_sum,confidence,status,last_observed_at
            ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(topic_key,handle) DO UPDATE SET
              relevant_observations=topic_account_memory.relevant_observations+1,
              kept_posts=topic_account_memory.kept_posts+excluded.kept_posts,
              score_sum=topic_account_memory.score_sum+excluded.score_sum,
              confidence=MAX(topic_account_memory.confidence,excluded.confidence),
              last_observed_at=excluded.last_observed_at
        """, (key, handle, 1, kept, score, float(match.get("confidence", 0)), "candidate", observed_at))
        row = conn.execute("""
            SELECT relevant_observations,kept_posts,score_sum FROM topic_account_memory
            WHERE topic_key=? AND handle=?
        """, (key, handle)).fetchone()
        average = float(row["score_sum"]) / max(1, int(row["relevant_observations"]))
        if int(row["relevant_observations"]) >= 3 and (int(row["kept_posts"]) >= 2 or average >= .65):
            conn.execute("UPDATE topic_account_memory SET status='known' WHERE topic_key=? AND handle=?", (key, handle))


def ensure_account(conn: sqlite3.Connection, handle: str, disposition: str = "normal", *,
                   operator_override: bool = False, notes: str | None = None) -> None:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"invalid disposition: {disposition}")
    now = utcnow()
    handle = normalize_handle(handle)
    conn.execute("""
        INSERT INTO account_reputation(
            handle,disposition,first_seen_at,last_seen_at,operator_override,notes
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(handle) DO UPDATE SET
            last_seen_at=excluded.last_seen_at,
            disposition=CASE WHEN account_reputation.operator_override=1
                AND excluded.operator_override=0 THEN account_reputation.disposition
                ELSE excluded.disposition END,
            operator_override=MAX(account_reputation.operator_override,excluded.operator_override),
            notes=COALESCE(excluded.notes,account_reputation.notes)
    """, (handle, disposition, now, now, int(operator_override), notes))


def add_evidence(conn: sqlite3.Connection, *, handle: str, post_url: str, reason: str,
                 confidence: float, post_id: str | None = None) -> None:
    handle = normalize_handle(handle)
    ensure_account(conn, handle)
    inserted = conn.execute("""
        INSERT OR IGNORE INTO account_evidence(
            handle,post_id,post_url,reason,confidence,observed_at
        ) VALUES(?,?,?,?,?,?)
    """, (handle, post_id, post_url, reason, confidence, utcnow())).rowcount
    if not inserted:
        return
    points = 2 if confidence >= .8 else 1
    row = conn.execute("SELECT strike_points FROM account_reputation WHERE handle=?", (handle,)).fetchone()
    strikes = int(row["strike_points"]) + points
    disposition = "downrank" if strikes >= 4 else "watch"
    conn.execute("""
        UPDATE account_reputation SET strike_points=?, confidence=MAX(confidence,?),
            disposition=CASE WHEN operator_override=1 THEN disposition ELSE ? END,
            reasons_json=(SELECT json_group_array(DISTINCT reason) FROM account_evidence WHERE handle=?),
            last_seen_at=? WHERE handle=?
    """, (strikes, confidence, disposition, handle, utcnow(), handle))


def set_account_disposition(conn: sqlite3.Connection, handle: str, disposition: str,
                            notes: str | None = None, *, enqueue_change: bool = True) -> None:
    ensure_account(conn, handle, disposition, operator_override=True, notes=notes)
    if enqueue_change:
        normalized = normalize_handle(handle)
        enqueue(conn, f"account:{normalized}:{uuid.uuid4()}", "account", {
            "handle": normalized, "disposition": disposition, "notes": notes, "updated_at": utcnow(),
        })


def purge_account(conn: sqlite3.Connection, handle: str, *, enqueue_change: bool = True) -> dict[str, Any]:
    normalized = normalize_handle(handle)
    post_rows = list(conn.execute("SELECT post_id FROM posts WHERE lower(handle)=?", (normalized,)))
    post_ids = [str(row["post_id"]) for row in post_rows]
    observation_rows = list(conn.execute(
        "SELECT po.id FROM post_observations po JOIN posts p ON p.post_id=po.post_id WHERE lower(p.handle)=?",
        (normalized,),
    ))
    observation_ids = [int(row["id"]) for row in observation_rows]
    if observation_ids:
        conn.executemany("DELETE FROM observation_acquisitions WHERE observation_id=?", ((item,) for item in observation_ids))
    if post_ids:
        conn.executemany("DELETE FROM posts_fts WHERE post_id=?", ((item,) for item in post_ids))
    conn.execute("DELETE FROM posts WHERE lower(handle)=?", (normalized,))
    conn.execute("DELETE FROM account_evidence WHERE lower(handle)=?", (normalized,))
    conn.execute("DELETE FROM topic_account_memory WHERE lower(handle)=?", (normalized,))
    conn.execute("DELETE FROM account_reputation WHERE lower(handle)=?", (normalized,))
    if enqueue_change:
        enqueue(conn, f"account-purge:{normalized}:{uuid.uuid4()}", "account_purge", {
            "handle": normalized, "updated_at": utcnow(),
        })
    return {"handle": normalized, "posts_purged": len(post_ids), "observations_purged": len(observation_ids)}


def set_post_state(conn: sqlite3.Connection, post_id: str, *, saved: bool | None = None,
                   pinned: bool | None = None, dismissed: bool | None = None,
                   enqueue_change: bool = True) -> None:
    now = utcnow()
    current = conn.execute("SELECT * FROM user_post_state WHERE post_id=?", (post_id,)).fetchone()
    values = dict(current) if current else {"saved_at": None, "pinned_at": None, "dismissed_at": None}
    if saved is not None:
        values["saved_at"] = now if saved else None
    if pinned is not None:
        values["pinned_at"] = now if pinned else None
        if pinned:
            values["saved_at"] = values["saved_at"] or now
    if dismissed is not None:
        values["dismissed_at"] = now if dismissed else None
    conn.execute("""
        INSERT INTO user_post_state(post_id,saved_at,pinned_at,dismissed_at,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET
        saved_at=excluded.saved_at,pinned_at=excluded.pinned_at,
        dismissed_at=excluded.dismissed_at,updated_at=excluded.updated_at
    """, (post_id, values["saved_at"], values["pinned_at"], values["dismissed_at"], now))
    if enqueue_change:
        enqueue(conn, f"post-state:{post_id}:{uuid.uuid4()}", "post_state", {
            "post_id": post_id, **values, "updated_at": now,
        })


def due_outbox(conn: sqlite3.Connection, limit: int = 25) -> list[sqlite3.Row]:
    return list(conn.execute("""
        SELECT * FROM sync_outbox WHERE status='pending' AND next_attempt_at<=?
        ORDER BY id LIMIT ?
    """, (utcnow(), limit)))


def mark_outbox_sent(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("UPDATE sync_outbox SET status='sent',sent_at=?,last_error=NULL WHERE id=?", (utcnow(), row_id))


def mark_outbox_failed(conn: sqlite3.Connection, row_id: int, attempts: int, error: str) -> None:
    delays = (1, 5, 15, 60)
    delay = delays[min(attempts, len(delays) - 1)]
    next_at = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
    conn.execute("""
        UPDATE sync_outbox SET attempt_count=?,next_attempt_at=?,last_error=? WHERE id=?
    """, (attempts + 1, next_at, error[-1000:], row_id))


def search_posts(conn: sqlite3.Connection, query: str = "", *, handle: str | None = None,
                 decision: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = []
    joins = ""
    clauses = ["1=1"]
    if query:
        joins = "JOIN posts_fts f ON f.post_id=p.post_id"
        clauses.append("posts_fts MATCH ?")
        params.append(query)
    if handle:
        clauses.append("p.handle=?")
        params.append(normalize_handle(handle))
    if decision:
        clauses.append("p.decision=?")
        params.append(decision)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT p.*,s.saved_at,s.pinned_at,s.dismissed_at FROM posts p {joins}
        LEFT JOIN user_post_state s ON s.post_id=p.post_id
        WHERE {' AND '.join(clauses)} ORDER BY p.last_seen_at DESC LIMIT ?
    """, params)
    return [_decode_post(row) for row in rows]


def _decode_post(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key, target, default in (
        ("engagement_json", "engagement", {}), ("reasons_json", "reasons", []),
        ("external_links_json", "external_links", []), ("media_json", "media", []),
        ("score_components_json", "score_components", None),
    ):
        if key in item:
            raw = item.pop(key)
            item[target] = json.loads(raw) if raw else default
    if "article_json" in item:
        raw = item.pop("article_json")
        item["article"] = json.loads(raw) if raw else None
    return item


def export_feed(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT p.*,COALESCE(a.disposition,'normal') account_disposition,
          p.score + CASE COALESCE(a.disposition,'normal')
            WHEN 'allow' THEN .05 WHEN 'watch' THEN -.15 WHEN 'downrank' THEN -.40 ELSE 0 END effective_score
        FROM posts p LEFT JOIN account_reputation a ON a.handle=p.handle
        LEFT JOIN user_post_state s ON s.post_id=p.post_id
        WHERE p.is_ad=0 AND p.decision='keep' AND COALESCE(a.disposition,'normal')!='blocked'
          AND s.dismissed_at IS NULL
        ORDER BY effective_score DESC,p.last_seen_at DESC LIMIT ?
    """, (limit,))
    return [_decode_post(row) for row in rows]


def seed_blocklist(conn: sqlite3.Connection, entries: Iterable[dict[str, Any]]) -> None:
    for entry in entries:
        ensure_account(conn, entry["handle"], entry.get("disposition", "watch"),
                       operator_override=bool(entry.get("operator_override", False)), notes=entry.get("notes"))
        for evidence in entry.get("evidence", []):
            add_evidence(conn, handle=entry["handle"], post_url=evidence["url"],
                         reason=evidence["reason"], confidence=float(evidence.get("confidence", .7)))


def archive_capture(source: str | Path, archive_root: str | Path, kind: str = "enriched") -> Path:
    source_path = Path(source)
    payload = json.loads(source_path.read_text())
    captured = str(payload.get("captured_at") or utcnow()).replace(":", "").replace("+00:00", "Z")
    day = captured[:10]
    destination = Path(archive_root) / day / f"{captured}-{kind}.json.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as src, gzip.open(destination, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    return destination


def backup_database(db_path: str | Path, backup_root: str | Path, keep_days: int = 30) -> Path:
    source = sqlite3.connect(str(db_path))
    root = Path(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"x-radar-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite"
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    cutoff = datetime.now().timestamp() - keep_days * 86400
    for candidate in root.glob("x-radar-*.sqlite"):
        if candidate.stat().st_mtime < cutoff:
            candidate.unlink()
    return destination


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    scalar = lambda sql: conn.execute(sql).fetchone()[0]
    last = conn.execute("SELECT scan_id,finished_at,status,posts_seen,error FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "posts": scalar("SELECT count(*) FROM posts"),
        "observations": scalar("SELECT count(*) FROM post_observations"),
        "runs": scalar("SELECT count(*) FROM runs"),
        "pending_sync": scalar("SELECT count(*) FROM sync_outbox WHERE status='pending'"),
        "failed_attempts": scalar("SELECT COALESCE(sum(attempt_count),0) FROM sync_outbox WHERE status='pending'"),
        "last_run": dict(last) if last else None,
    }
