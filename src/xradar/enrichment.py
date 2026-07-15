from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .db import normalize_ranked_post, utcnow


DEFAULT_BATCH_ITEMS = 24
DEFAULT_BATCH_CHARS = 60_000
RANKING_VERSION = "luna-batched-v1"
SIGNAL_REASONS = {
    "ragebait",
    "engagement_bait",
    "engagement_bait_non_additive_quote_wrapper",
    "unsupported_scientific_certainty",
    "sensational_marketing_claim",
    "source_obscuring_aggregator",
    "repeated_non_additive_commentary",
    "promotional_saturation",
}


def _load_json(path: str | Path) -> tuple[Path, bytes, dict[str, Any]]:
    resolved = Path(path).resolve()
    payload = resolved.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("capture must be a JSON object")
    return resolved, payload, value


def _capture_id(payload: dict[str, Any]) -> str:
    scan_id = str(payload.get("scan_id") or payload.get("period_id") or "").strip()
    if not scan_id:
        raise ValueError("raw capture requires scan_id")
    return scan_id


def _default_output(raw_path: Path) -> Path:
    name = raw_path.name
    if name.startswith("raw-"):
        name = f"capture-{name[4:]}"
    else:
        name = f"capture-{name}"
    return raw_path.with_name(name)


def _allowed_topics(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for target in [*payload.get("targets", []), *payload.get("backfill_targets", [])]:
        if not isinstance(target, dict):
            continue
        key = str(target.get("topic_key") or "").strip()
        label = str(target.get("topic") or "").strip()
        if key and label:
            result[key] = label
    return result


def _job_row(conn: sqlite3.Connection, scan_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM enrichment_jobs WHERE scan_id=?", (scan_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown enrichment job: {scan_id}")
    return row


def _job_summary(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    job = _job_row(conn, scan_id)
    counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status,count(*) count FROM enrichment_items WHERE scan_id=? GROUP BY status",
            (scan_id,),
        )
    }
    return {
        "scanId": scan_id,
        "status": job["status"],
        "rankingVersion": job["ranking_version"],
        "total": int(job["total_items"]),
        "pending": counts.get("pending", 0),
        "inProgress": counts.get("in_progress", 0),
        "failed": counts.get("failed", 0),
        "accepted": counts.get("accepted", 0),
        "output": job["output_path"],
    }


def start_job(
    conn: sqlite3.Connection,
    raw_capture: str | Path,
    *,
    output: str | Path | None = None,
    max_items: int = DEFAULT_BATCH_ITEMS,
    max_chars: int = DEFAULT_BATCH_CHARS,
    ranking_version: str = RANKING_VERSION,
) -> dict[str, Any]:
    if not 1 <= int(max_items) <= 50:
        raise ValueError("max_items must be between 1 and 50")
    if not 4_000 <= int(max_chars) <= 200_000:
        raise ValueError("max_chars must be between 4000 and 200000")
    raw_path, raw_bytes, payload = _load_json(raw_capture)
    scan_id = _capture_id(payload)
    posts = payload.get("posts")
    if not isinstance(posts, list):
        raise ValueError("raw capture posts must be an array")
    post_ids = [str(post.get("post_id") or "").strip() for post in posts if isinstance(post, dict)]
    if len(post_ids) != len(posts) or any(not post_id for post_id in post_ids):
        raise ValueError("every raw post requires post_id")
    if len(set(post_ids)) != len(post_ids):
        raise ValueError("raw capture contains duplicate post_id values")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    output_path = Path(output).resolve() if output else _default_output(raw_path).resolve()
    preference = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
    preference_json = preference["value"] if preference else json.dumps({"version": 0, "topics": [], "instructions": ""})
    topics_json = json.dumps(_allowed_topics(payload), sort_keys=True)
    existing = conn.execute("SELECT raw_sha256 FROM enrichment_jobs WHERE scan_id=?", (scan_id,)).fetchone()
    if existing:
        if existing["raw_sha256"] != digest:
            raise ValueError("scan_id already belongs to a different raw capture")
        return _job_summary(conn, scan_id)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO enrichment_jobs(
          scan_id,raw_path,raw_sha256,output_path,ranking_version,status,total_items,
          batch_max_items,batch_max_chars,preference_json,allowed_topics_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?,?)
        """,
        (
            scan_id, str(raw_path), digest, str(output_path), ranking_version, len(posts),
            int(max_items), int(max_chars), preference_json, topics_json, now, now,
        ),
    )
    conn.executemany(
        """
        INSERT INTO enrichment_items(scan_id,post_id,observed_index,status,attempts,updated_at)
        VALUES(?,?,?,'pending',0,?)
        """,
        ((scan_id, post_id, index, now) for index, post_id in enumerate(post_ids)),
    )
    conn.commit()
    return _job_summary(conn, scan_id)


def _verified_raw(job: sqlite3.Row) -> dict[str, Any]:
    path, payload, value = _load_json(job["raw_path"])
    if hashlib.sha256(payload).hexdigest() != job["raw_sha256"]:
        raise ValueError(f"raw capture changed after enrichment started: {path}")
    return value


def _ranking_input(post: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "post_id", "url", "handle", "author", "text", "posted_at", "is_reply", "is_quote",
        "source_url", "external_links", "media", "article", "engagement", "discovery_sources",
    )
    return {field: post.get(field) for field in fields if field in post}


def next_batch(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    job = _job_row(conn, scan_id)
    if job["status"] == "complete":
        return {**_job_summary(conn, scan_id), "complete": True, "posts": []}
    payload = _verified_raw(job)
    posts = payload["posts"]
    rows = list(conn.execute(
        """
        SELECT * FROM enrichment_items WHERE scan_id=? AND status='in_progress'
        ORDER BY observed_index
        """,
        (scan_id,),
    ))
    if not rows:
        candidates = list(conn.execute(
            """
            SELECT * FROM enrichment_items WHERE scan_id=? AND status IN ('failed','pending')
            ORDER BY CASE status WHEN 'failed' THEN 0 ELSE 1 END,observed_index
            """,
            (scan_id,),
        ))
        selected: list[sqlite3.Row] = []
        chars = 0
        for row in candidates:
            item = _ranking_input(posts[int(row["observed_index"])])
            item_chars = len(json.dumps(item, ensure_ascii=False))
            if selected and (len(selected) >= int(job["batch_max_items"]) or chars + item_chars > int(job["batch_max_chars"])):
                break
            selected.append(row)
            chars += item_chars
        rows = selected
        if not rows:
            conn.execute("UPDATE enrichment_jobs SET status='ready',updated_at=? WHERE scan_id=?", (utcnow(), scan_id))
            conn.commit()
            return {**_job_summary(conn, scan_id), "complete": False, "ready": True, "posts": []}
        batch_id = str(uuid.uuid4())
        conn.executemany(
            "UPDATE enrichment_items SET status='in_progress',batch_id=?,updated_at=? WHERE scan_id=? AND post_id=?",
            ((batch_id, utcnow(), scan_id, row["post_id"]) for row in rows),
        )
        conn.execute("UPDATE enrichment_jobs SET status='running',updated_at=? WHERE scan_id=?", (utcnow(), scan_id))
        conn.commit()
        rows = list(conn.execute(
            "SELECT * FROM enrichment_items WHERE scan_id=? AND batch_id=? ORDER BY observed_index",
            (scan_id, batch_id),
        ))
    batch_id = str(rows[0]["batch_id"])
    return {
        "schemaVersion": 1,
        "scanId": scan_id,
        "batchId": batch_id,
        "rankingVersion": job["ranking_version"],
        "preference": json.loads(job["preference_json"]),
        "allowedTopics": json.loads(job["allowed_topics_json"]),
        "posts": [_ranking_input(posts[int(row["observed_index"])]) for row in rows],
        "remainingAfterBatch": _job_summary(conn, scan_id)["pending"] + _job_summary(conn, scan_id)["failed"],
    }


def _clean_reasons(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("reasons must be an array of 1-8 strings")
    result = []
    for item in value:
        reason = " ".join(str(item).split()).strip()
        if not reason or len(reason) > 280:
            raise ValueError("each reason must be 1-280 characters")
        result.append(reason)
    return result


def _clean_topic_matches(value: Any, allowed: dict[str, str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("topic_matches must be an array of at most 24 items")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each topic match must be an object")
        key = str(item.get("topic_key") or "").strip()
        confidence = item.get("confidence")
        if key not in allowed:
            raise ValueError(f"unknown topic_key: {key}")
        if key in seen:
            raise ValueError(f"duplicate topic_key: {key}")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
            raise ValueError(f"invalid topic confidence for {key}")
        seen.add(key)
        result.append({"topic_key": key, "topic": allowed[key], "confidence": round(float(confidence), 4)})
    return result


def _clean_signals(value: Any, raw_post: dict[str, Any]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("account_signals must be an array of at most 8 items")
    result = []
    expected_handle = str(raw_post.get("handle") or "").strip().casefold().lstrip("@")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each account signal must be an object")
        reason = str(item.get("reason") or "").strip()
        handle = str(item.get("handle") or raw_post.get("handle") or "").strip()
        confidence = item.get("confidence")
        if reason not in SIGNAL_REASONS:
            raise ValueError(f"unsupported account signal reason: {reason}")
        if handle.casefold().lstrip("@") != expected_handle:
            raise ValueError("account signal handle must match the ranked post")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("account signal confidence must be between 0 and 1")
        result.append({
            "handle": handle,
            "post_id": str(raw_post["post_id"]),
            "post_url": str(raw_post["url"]),
            "reason": reason,
            "confidence": round(float(confidence), 4),
        })
    return result


def _validate_result(result: Any, raw_post: dict[str, Any], allowed: dict[str, str]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    if str(result.get("post_id") or "") != str(raw_post["post_id"]):
        raise ValueError("post_id does not match the batch item")
    topic_matches = _clean_topic_matches(result.get("topic_matches", []), allowed)
    ranked = normalize_ranked_post({
        "topic_matches": topic_matches,
        "score_components": result.get("score_components"),
    })
    score = ranked.get("score")
    if score is None:
        raise ValueError("score_components are required")
    decision = "keep" if score >= .62 else "candidate" if score >= .45 else "discard"
    return {
        "topic_matches": topic_matches,
        "score_components": ranked["score_components"],
        "score": score,
        "decision": decision,
        "reasons": _clean_reasons(result.get("reasons")),
        "account_signals": _clean_signals(result.get("account_signals", []), raw_post),
    }


def fail_active_batch(conn: sqlite3.Connection, scan_id: str, error: str) -> dict[str, Any]:
    rows = list(conn.execute(
        "SELECT post_id FROM enrichment_items WHERE scan_id=? AND status='in_progress'",
        (scan_id,),
    ))
    if not rows:
        raise ValueError("enrichment job has no active batch")
    message = " ".join(str(error).split())[:1000]
    conn.executemany(
        """
        UPDATE enrichment_items SET status='failed',attempts=attempts+1,last_error=?,batch_id=NULL,updated_at=?
        WHERE scan_id=? AND post_id=?
        """,
        ((message, utcnow(), scan_id, row["post_id"]) for row in rows),
    )
    conn.execute("UPDATE enrichment_jobs SET status='running',last_error=?,updated_at=? WHERE scan_id=?", (message, utcnow(), scan_id))
    conn.commit()
    return {**_job_summary(conn, scan_id), "batchError": message}


def submit_batch(conn: sqlite3.Connection, scan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = _job_row(conn, scan_id)
    if not isinstance(payload, dict):
        return fail_active_batch(conn, scan_id, "submission must be a JSON object")
    batch_id = str(payload.get("batchId") or payload.get("batch_id") or "").strip()
    rows = list(conn.execute(
        "SELECT * FROM enrichment_items WHERE scan_id=? AND status='in_progress' ORDER BY observed_index",
        (scan_id,),
    ))
    if not rows:
        raise ValueError("enrichment job has no active batch")
    if not batch_id or any(str(row["batch_id"]) != batch_id for row in rows):
        raise ValueError("batchId does not match the active batch")
    results = payload.get("results")
    if not isinstance(results, list):
        return fail_active_batch(conn, scan_id, "results must be an array")
    mapped: dict[str, Any] = {}
    duplicate_ids: set[str] = set()
    for result in results:
        post_id = str(result.get("post_id") or "") if isinstance(result, dict) else ""
        if post_id in mapped:
            duplicate_ids.add(post_id)
        mapped[post_id] = result
    raw = _verified_raw(job)
    allowed = json.loads(job["allowed_topics_json"])
    accepted = 0
    errors: list[dict[str, str]] = []
    now = utcnow()
    for row in rows:
        post_id = str(row["post_id"])
        try:
            if post_id in duplicate_ids:
                raise ValueError("duplicate result for post_id")
            if post_id not in mapped:
                raise ValueError("missing result for batch item")
            enrichment = _validate_result(mapped[post_id], raw["posts"][int(row["observed_index"])], allowed)
            conn.execute(
                """
                UPDATE enrichment_items SET status='accepted',attempts=attempts+1,enrichment_json=?,
                  last_error=NULL,batch_id=NULL,updated_at=? WHERE scan_id=? AND post_id=?
                """,
                (json.dumps(enrichment, sort_keys=True), now, scan_id, post_id),
            )
            accepted += 1
        except (TypeError, ValueError) as error:
            message = " ".join(str(error).split())[:1000]
            conn.execute(
                """
                UPDATE enrichment_items SET status='failed',attempts=attempts+1,last_error=?,
                  batch_id=NULL,updated_at=? WHERE scan_id=? AND post_id=?
                """,
                (message, now, scan_id, post_id),
            )
            errors.append({"post_id": post_id, "error": message})
    remaining = conn.execute(
        "SELECT count(*) FROM enrichment_items WHERE scan_id=? AND status!='accepted'", (scan_id,)
    ).fetchone()[0]
    status = "ready" if remaining == 0 else "running"
    conn.execute(
        "UPDATE enrichment_jobs SET status=?,last_error=?,updated_at=? WHERE scan_id=?",
        (status, errors[0]["error"] if errors else None, now, scan_id),
    )
    conn.commit()
    return {**_job_summary(conn, scan_id), "batchId": batch_id, "acceptedThisBatch": accepted, "errors": errors}


def finalize_job(conn: sqlite3.Connection, scan_id: str, *, output: str | Path | None = None) -> dict[str, Any]:
    job = _job_row(conn, scan_id)
    incomplete = conn.execute(
        "SELECT count(*) FROM enrichment_items WHERE scan_id=? AND status!='accepted'", (scan_id,)
    ).fetchone()[0]
    if incomplete:
        raise ValueError(f"cannot finalize: {incomplete} posts are not accepted")
    raw = _verified_raw(job)
    signals: list[dict[str, Any]] = []
    decisions = {"keep": 0, "candidate": 0, "discard": 0}
    rows = list(conn.execute(
        "SELECT * FROM enrichment_items WHERE scan_id=? ORDER BY observed_index", (scan_id,)
    ))
    for row in rows:
        enrichment = json.loads(row["enrichment_json"])
        signals.extend(enrichment.pop("account_signals", []))
        post = raw["posts"][int(row["observed_index"])]
        post.update(enrichment)
        decisions[post["decision"]] += 1
    raw["account_signals"] = signals
    destination = Path(output).resolve() if output else Path(job["output_path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, destination)
    now = utcnow()
    conn.execute(
        "UPDATE enrichment_jobs SET status='complete',output_path=?,completed_at=?,updated_at=?,last_error=NULL WHERE scan_id=?",
        (str(destination), now, now, scan_id),
    )
    conn.commit()
    return {
        **_job_summary(conn, scan_id),
        "posts": len(rows),
        "decisions": decisions,
        "accountSignals": len(signals),
    }


def job_status(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    result = _job_summary(conn, scan_id)
    failed = [dict(row) for row in conn.execute(
        """
        SELECT post_id,attempts,last_error FROM enrichment_items
        WHERE scan_id=? AND status='failed' ORDER BY observed_index LIMIT 50
        """,
        (scan_id,),
    )]
    return {**result, "errors": failed}
