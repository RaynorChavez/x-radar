from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote_plus, urlparse


MIXED_TARGET = 150
SOURCE_BUDGETS = {"home": 60, "topic_search": 45, "known_account": 30, "exploration": 15}
MAX_ACTIVE_TOPICS = 6
MAX_QUERY_LENGTH = 128
_HANDLE = re.compile(r"^@[A-Za-z0-9_]{1,15}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def topic_key(label: str) -> str:
    normalized = " ".join(label.casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def safe_query(value: str) -> str:
    query = " ".join(str(value).replace("\x00", " ").split()).strip()
    if not query or len(query) > MAX_QUERY_LENGTH or "http://" in query.casefold() or "https://" in query.casefold():
        raise ValueError("query must be 1-128 characters and must not contain a URL")
    if any(ord(character) < 32 for character in query):
        raise ValueError("query contains a control character")
    return query


def search_url(query: str) -> str:
    return f"https://x.com/search?q={quote_plus(safe_query(query))}&src=typed_query&f=live"


def validate_x_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"x.com", "www.x.com"}:
        raise ValueError("collector targets must use https://x.com")
    path = parsed.path.rstrip("/") or "/"
    allowed = path == "/home" or path == "/search"
    parts = [part for part in path.split("/") if part]
    if len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_]{1,15}", parts[0] or ""):
        allowed = True
    if len(parts) == 2 and parts[1] == "with_replies" and re.fullmatch(r"[A-Za-z0-9_]{1,15}", parts[0] or ""):
        allowed = True
    if not allowed:
        raise ValueError(f"unsupported X collection target: {value}")
    return value


def split_quota(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _target(kind: str, url: str, quota: int, *, topic: sqlite3.Row | None = None,
            handle: str | None = None, query: str | None = None, backfill: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()), "kind": kind, "url": validate_x_url(url),
        "quota": max(0, int(quota)), "backfill": backfill,
    }
    if topic is not None:
        item.update({"topic_key": topic["topic_key"], "topic": topic["label"]})
    if handle:
        item["handle"] = handle
    if query:
        item["query"] = query
    return item


def _query_pack(topic: sqlite3.Row) -> list[str]:
    try:
        values = json.loads(topic["query_pack_json"] or "[]")
    except json.JSONDecodeError:
        values = []
    result: list[str] = []
    for value in values:
        try:
            cleaned = safe_query(value)
        except ValueError:
            continue
        if cleaned not in result:
            result.append(cleaned)
    return result[:3] or [safe_query(topic["label"])]


def _eligible_accounts(conn: sqlite3.Connection, topic_keys: Iterable[str], status: str) -> list[sqlite3.Row]:
    keys = list(topic_keys)
    if not keys:
        return []
    placeholders = ",".join("?" for _ in keys)
    return list(conn.execute(f"""
        SELECT m.*, t.label FROM topic_account_memory m
        JOIN topic_state t ON t.topic_key=m.topic_key
        LEFT JOIN account_reputation r ON r.handle=m.handle
        WHERE m.topic_key IN ({placeholders}) AND m.status=?
          AND COALESCE(r.disposition,'normal') NOT IN ('watch','downrank','blocked')
        ORDER BY m.confidence DESC,
          CASE WHEN m.last_scanned_at IS NULL THEN 0 ELSE 1 END,
          datetime(m.last_scanned_at), datetime(m.last_observed_at) DESC
    """, (*keys, status)))


def _selected_topics(conn: sqlite3.Connection, bootstrap_labels: list[str]) -> list[sqlite3.Row]:
    if bootstrap_labels:
        keys = [topic_key(label) for label in bootstrap_labels]
        placeholders = ",".join("?" for _ in keys)
        rows = list(conn.execute(f"""
            SELECT * FROM topic_state WHERE active=1 AND topic_key IN ({placeholders})
            ORDER BY datetime(added_at), label LIMIT ?
        """, (*keys, MAX_ACTIVE_TOPICS)))
        if rows:
            return rows
    return list(conn.execute("""
        SELECT * FROM topic_state WHERE active=1
        ORDER BY CASE WHEN last_planned_at IS NULL THEN 0 ELSE 1 END,
          datetime(last_planned_at), label LIMIT ?
    """, (MAX_ACTIVE_TOPICS,)))


def build_period_plan(conn: sqlite3.Connection, *, request_id: str | None = None,
                      bootstrap_topics: list[str] | None = None) -> dict[str, Any]:
    bootstrap_topics = [str(item).strip() for item in (bootstrap_topics or []) if str(item).strip()]
    preference = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
    snapshot = json.loads(preference["value"]) if preference else {"version": 0, "topics": [], "instructions": ""}
    version = int(snapshot.get("version") or 0)
    topics = _selected_topics(conn, bootstrap_topics)
    now = utcnow()
    period_id = str(uuid.uuid4())
    targets: list[dict[str, Any]] = []
    backfill: list[dict[str, Any]] = []

    if not topics:
        targets.append(_target("home", "https://x.com/home", MIXED_TARGET))
    else:
        targets.append(_target("home", "https://x.com/home", SOURCE_BUDGETS["home"]))
        topic_keys = [row["topic_key"] for row in topics]
        known = _eligible_accounts(conn, topic_keys, "known")
        candidates = _eligible_accounts(conn, topic_keys, "candidate")

        search_budget = SOURCE_BUDGETS["topic_search"]
        known_budget = SOURCE_BUDGETS["known_account"]
        exploration_budget = SOURCE_BUDGETS["exploration"]
        if bootstrap_topics:
            known = [row for row in known if row["topic_key"] in topic_keys]
        if not known:
            search_budget += known_budget
            known_budget = 0

        primary_queries = [(topic, _query_pack(topic)[0]) for topic in topics]
        for quota, (topic, query) in zip(split_quota(search_budget, len(primary_queries)), primary_queries):
            targets.append(_target("topic_search", search_url(query), quota, topic=topic, query=query))

        if known_budget and known:
            selected_known = known[:min(6, len(known))]
            for quota, account in zip(split_quota(known_budget, len(selected_known)), selected_known):
                handle = account["handle"]
                targets.append(_target("known_account", f"https://x.com/{handle.lstrip('@')}", quota,
                                       topic=account, handle=handle))
                conn.execute("UPDATE topic_account_memory SET last_scanned_at=? WHERE topic_key=? AND handle=?", (now, account["topic_key"], handle))

        exploration_items: list[dict[str, Any]] = []
        for account in candidates[:3]:
            handle = account["handle"]
            exploration_items.append(_target("explore_account", f"https://x.com/{handle.lstrip('@')}", 0,
                                                    topic=account, handle=handle))
        for topic in topics:
            pack = _query_pack(topic)
            query = pack[1] if len(pack) > 1 else f'"{topic["label"]}"'
            exploration_items.append(_target("explore_query", search_url(query), 0, topic=topic, query=query))
            if len(exploration_items) >= 6:
                break
        for quota, item in zip(split_quota(exploration_budget, len(exploration_items)), exploration_items):
            item["quota"] = quota
            targets.append(item)
            if item.get("handle"):
                conn.execute("UPDATE topic_account_memory SET last_scanned_at=? WHERE topic_key=? AND handle=?", (now, item["topic_key"], item["handle"]))

        for topic, query in primary_queries:
            backfill.append(_target("topic_search", search_url(query), 0, topic=topic, query=query, backfill=True))
        backfill.append(_target("home", "https://x.com/home", 0, backfill=True))

        conn.executemany("UPDATE topic_state SET last_planned_at=? WHERE topic_key=?", ((now, key) for key in topic_keys))
        conn.executemany("UPDATE topic_state SET last_searched_at=? WHERE topic_key=?", ((now, key) for key in topic_keys))

    return {
        "schema_version": 2, "period_id": period_id, "scan_id": period_id,
        "created_at": now, "request_id": request_id, "mode": "mixed",
        "preference_version": version, "target_unique": MIXED_TARGET,
        "budgets": dict(SOURCE_BUDGETS), "bootstrap_topics": bootstrap_topics,
        "targets": targets, "backfill_targets": backfill,
    }


def set_query_pack(conn: sqlite3.Connection, label: str, queries: list[str], revision: int) -> list[str]:
    key = label if re.fullmatch(r"[0-9a-f]{16}", label) and conn.execute("SELECT 1 FROM topic_state WHERE topic_key=?", (label,)).fetchone() else topic_key(label)
    cleaned: list[str] = []
    for query in queries:
        value = safe_query(query)
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        cleaned = [safe_query(label)]
    updated = conn.execute("""
        UPDATE topic_state SET query_pack_json=?,query_pack_revision=?,last_changed_at=?
        WHERE topic_key=? AND active=1
    """, (json.dumps(cleaned[:3]), int(revision), utcnow(), key)).rowcount
    if not updated:
        raise ValueError(f"unknown active topic: {label}")
    return cleaned[:3]


def apply_preferences(conn: sqlite3.Connection, *, instructions: str, topics: list[str],
                      version: int, updated_at: str | None = None) -> dict[str, Any]:
    now = updated_at or utcnow()
    normalized_topics = list(dict.fromkeys(" ".join(str(topic).split()).strip() for topic in topics if str(topic).strip()))
    active_keys = {topic_key(label) for label in normalized_topics}
    previous = {row["topic_key"]: row["active"] for row in conn.execute("SELECT topic_key,active FROM topic_state")}
    conn.execute("UPDATE topic_state SET active=0,last_changed_at=? WHERE active=1", (now,))
    for label in normalized_topics:
        key = topic_key(label)
        conn.execute("""
            INSERT INTO topic_state(topic_key,label,normalized_label,active,added_at,last_changed_at,query_pack_json)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(topic_key) DO UPDATE SET
              label=excluded.label,normalized_label=excluded.normalized_label,active=1,last_changed_at=excluded.last_changed_at
        """, (key, label, " ".join(label.casefold().split()), 1, now, now, json.dumps([label])))
    conn.execute("""
        INSERT INTO preference_versions(version,instructions,topics_json,effective_at)
        VALUES(?,?,?,?) ON CONFLICT(version) DO NOTHING
    """, (int(version), instructions, json.dumps(normalized_topics), now))
    snapshot = {"instructions": instructions, "topics": normalized_topics, "version": int(version), "updated_at": now}
    conn.execute("""
        INSERT INTO site_state(key,value) VALUES('curator_preferences',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (json.dumps(snapshot),))
    added = [label for label in normalized_topics if not previous.get(topic_key(label), 0)]
    removed = [key for key, was_active in previous.items() if was_active and key not in active_keys]
    return {"added": added, "removed": removed, "version": int(version)}
