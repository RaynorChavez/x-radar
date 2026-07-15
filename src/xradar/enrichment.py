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
        row["state"]: int(row["count"])
        for row in conn.execute(
            "SELECT state,count(*) count FROM enrichment_items WHERE scan_id=? GROUP BY state",
            (scan_id,),
        )
    }
    warnings = json.loads(job["warnings_json"] or "[]")
    return {
        "scanId": scan_id,
        "status": job["state"],
        "rankingVersion": job["ranking_version"],
        "total": int(job["total_items"]),
        "pending": counts.get("pending", 0),
        "inProgress": counts.get("in_progress", 0),
        "retry": counts.get("retry", 0),
        "failed": counts.get("retry", 0) + counts.get("exhausted", 0),
        "exhausted": counts.get("exhausted", 0),
        "accepted": counts.get("accepted", 0),
        "warningCount": len(warnings),
        "lastError": job["last_error"],
        "output": job["output_path"],
    }


def _legacy_job_status(state: str) -> str:
    if state in {"complete", "partial", "failed"}:
        return "complete" if state != "failed" else "ready"
    if state in {"ready", "partial_ready"}:
        return "ready"
    if state == "pending":
        return "pending"
    return "running"


def _set_job_state(
    conn: sqlite3.Connection,
    scan_id: str,
    state: str,
    *,
    error: str | None = None,
    finished: bool = False,
) -> None:
    now = utcnow()
    conn.execute(
        """
        UPDATE enrichment_jobs SET state=?,status=?,last_error=?,updated_at=?,
          finished_at=CASE WHEN ? THEN ? ELSE finished_at END
        WHERE scan_id=?
        """,
        (state, _legacy_job_status(state), error, now, int(finished), now, scan_id),
    )


def _append_job_warnings(conn: sqlite3.Connection, scan_id: str, warnings: list[dict[str, Any]]) -> None:
    if not warnings:
        return
    row = _job_row(conn, scan_id)
    existing = json.loads(row["warnings_json"] or "[]")
    existing.extend(warnings)
    conn.execute(
        "UPDATE enrichment_jobs SET warnings_json=?,updated_at=? WHERE scan_id=?",
        (json.dumps(existing[-500:], sort_keys=True), utcnow(), scan_id),
    )


def _reconcile_job_state(conn: sqlite3.Connection, scan_id: str) -> str:
    counts = {
        row["state"]: int(row["count"])
        for row in conn.execute(
            "SELECT state,count(*) count FROM enrichment_items WHERE scan_id=? GROUP BY state",
            (scan_id,),
        )
    }
    last = conn.execute(
        """
        SELECT last_error FROM enrichment_items
        WHERE scan_id=? AND last_error IS NOT NULL
        ORDER BY updated_at DESC LIMIT 1
        """,
        (scan_id,),
    ).fetchone()
    error = last["last_error"] if last else None
    if counts.get("in_progress", 0):
        state = "running"
    elif counts.get("pending", 0) or counts.get("retry", 0):
        state = "resumable" if counts.get("retry", 0) else "pending"
    elif counts.get("exhausted", 0):
        state = "partial_ready" if counts.get("accepted", 0) else "failed"
    else:
        state = "ready"
        error = None
    _set_job_state(conn, scan_id, state, error=error, finished=state == "failed")
    return state


def start_job(
    conn: sqlite3.Connection,
    raw_capture: str | Path,
    *,
    output: str | Path | None = None,
    max_items: int = DEFAULT_BATCH_ITEMS,
    max_chars: int = DEFAULT_BATCH_CHARS,
    ranking_version: str = RANKING_VERSION,
    max_attempts: int = 3,
) -> dict[str, Any]:
    if not 1 <= int(max_items) <= 50:
        raise ValueError("max_items must be between 1 and 50")
    if not 4_000 <= int(max_chars) <= 200_000:
        raise ValueError("max_chars must be between 4000 and 200000")
    if not 1 <= int(max_attempts) <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
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
          batch_max_items,batch_max_chars,preference_json,allowed_topics_json,created_at,updated_at,
          state,max_attempts
        ) VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?,?,'pending',?)
        """,
        (
            scan_id, str(raw_path), digest, str(output_path), ranking_version, len(posts),
            int(max_items), int(max_chars), preference_json, topics_json, now, now,
            int(max_attempts),
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


def _batch_payload(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    raw: dict[str, Any],
    rows: list[sqlite3.Row],
) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot build an empty enrichment batch")
    batch_id = str(rows[0]["batch_id"] or "")
    if not batch_id or any(str(row["batch_id"] or "") != batch_id for row in rows):
        raise ValueError("enrichment rows do not share one batch_id")
    summary = _job_summary(conn, str(job["scan_id"]))
    return {
        "schemaVersion": 1,
        "scanId": str(job["scan_id"]),
        "batchId": batch_id,
        "rankingVersion": job["ranking_version"],
        "preference": json.loads(job["preference_json"]),
        "allowedTopics": json.loads(job["allowed_topics_json"]),
        "posts": [_ranking_input(raw["posts"][int(row["observed_index"])]) for row in rows],
        "remainingAfterBatch": summary["pending"] + summary["failed"],
    }


def active_batches(conn: sqlite3.Connection, scan_id: str) -> list[dict[str, Any]]:
    """Return durable leased batches without claiming additional work."""
    job = _job_row(conn, scan_id)
    if job["state"] in {"complete", "partial"}:
        return []
    raw = _verified_raw(job)
    batch_ids = [
        str(row["batch_id"])
        for row in conn.execute(
            """
            SELECT batch_id,MIN(observed_index) first_index
            FROM enrichment_items
            WHERE scan_id=? AND state='in_progress' AND batch_id IS NOT NULL
            GROUP BY batch_id ORDER BY first_index
            """,
            (scan_id,),
        )
    ]
    batches = []
    for batch_id in batch_ids:
        rows = list(conn.execute(
            """
            SELECT * FROM enrichment_items
            WHERE scan_id=? AND state='in_progress' AND batch_id=?
            ORDER BY observed_index
            """,
            (scan_id, batch_id),
        ))
        batches.append(_batch_payload(conn, job, raw, rows))
    return batches


def claim_batch(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    """Lease one new batch even when another batch is already in progress."""
    job = _job_row(conn, scan_id)
    if job["state"] in {"complete", "partial"}:
        return {**_job_summary(conn, scan_id), "complete": True, "posts": []}
    conn.execute(
        """
        UPDATE enrichment_items SET state='exhausted',status='failed',batch_id=NULL,updated_at=?
        WHERE scan_id=? AND state='retry' AND attempts>=?
        """,
        (utcnow(), scan_id, int(job["max_attempts"])),
    )
    raw = _verified_raw(job)
    candidates = list(conn.execute(
        """
        SELECT * FROM enrichment_items WHERE scan_id=? AND state IN ('retry','pending')
        ORDER BY CASE state WHEN 'retry' THEN 0 ELSE 1 END,attempts,observed_index
        """,
        (scan_id,),
    ))
    if candidates:
        first_state = str(candidates[0]["state"])
        first_attempts = int(candidates[0]["attempts"])
        candidates = [
            row for row in candidates
            if row["state"] == first_state and int(row["attempts"]) == first_attempts
        ]
        if first_attempts == 0:
            item_limit = int(job["batch_max_items"])
            char_limit = int(job["batch_max_chars"])
        elif first_attempts == 1 and int(job["max_attempts"]) > 2:
            item_limit = max(1, math.ceil(int(job["batch_max_items"]) / 2))
            char_limit = max(4_000, math.ceil(int(job["batch_max_chars"]) / 2))
        else:
            item_limit = 1
            char_limit = int(job["batch_max_chars"])
    else:
        item_limit = int(job["batch_max_items"])
        char_limit = int(job["batch_max_chars"])
    selected: list[sqlite3.Row] = []
    chars = 0
    for row in candidates:
        item = _ranking_input(raw["posts"][int(row["observed_index"])])
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if selected and (
            len(selected) >= item_limit
            or chars + item_chars > char_limit
        ):
            break
        selected.append(row)
        chars += item_chars
    if not selected:
        waiting = bool(active_batches(conn, scan_id))
        if not waiting:
            _reconcile_job_state(conn, scan_id)
            conn.commit()
        summary = _job_summary(conn, scan_id)
        return {
            **summary, "complete": False,
            "ready": not waiting and summary["status"] in {"ready", "partial_ready", "failed"},
            "waiting": waiting, "posts": [],
        }
    batch_id = str(uuid.uuid4())
    now = utcnow()
    conn.executemany(
        "UPDATE enrichment_items SET status='in_progress',state='in_progress',batch_id=?,updated_at=? WHERE scan_id=? AND post_id=?",
        ((batch_id, now, scan_id, row["post_id"]) for row in selected),
    )
    _set_job_state(conn, scan_id, "running")
    conn.commit()
    rows = list(conn.execute(
        "SELECT * FROM enrichment_items WHERE scan_id=? AND batch_id=? ORDER BY observed_index",
        (scan_id, batch_id),
    ))
    return _batch_payload(conn, job, raw, rows)


def next_batch(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    """Compatibility API: resume the oldest lease, otherwise claim one batch."""
    batches = active_batches(conn, scan_id)
    return batches[0] if batches else claim_batch(conn, scan_id)


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


def _clean_signals(value: Any, raw_post: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list) or len(value) > 8:
        return [], ["dropped invalid optional account_signals container"]
    result: list[dict[str, Any]] = []
    warnings: list[str] = []
    expected_handle = str(raw_post.get("handle") or "").strip().casefold().lstrip("@")
    for index, item in enumerate(value):
        try:
            if not isinstance(item, dict):
                raise ValueError("signal must be an object")
            reason = str(item.get("reason") or "").strip()
            handle = str(item.get("handle") or raw_post.get("handle") or "").strip()
            confidence = item.get("confidence")
            if reason not in SIGNAL_REASONS:
                raise ValueError(f"unsupported reason: {reason or '<empty>'}")
            if handle.casefold().lstrip("@") != expected_handle:
                raise ValueError("handle does not match ranked post")
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(float(confidence))
                or not 0 <= float(confidence) <= 1
            ):
                raise ValueError("confidence must be between 0 and 1")
            result.append({
                "handle": handle,
                "post_id": str(raw_post["post_id"]),
                "post_url": str(raw_post["url"]),
                "reason": reason,
                "confidence": round(float(confidence), 4),
            })
        except (TypeError, ValueError) as error:
            warnings.append(f"dropped optional account_signal[{index}]: {error}")
    return result, warnings


def _validate_result(
    result: Any,
    raw_post: dict[str, Any],
    allowed: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
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
    signals, warnings = _clean_signals(result.get("account_signals", []), raw_post)
    return {
        "topic_matches": topic_matches,
        "score_components": ranked["score_components"],
        "score": score,
        "decision": decision,
        "reasons": _clean_reasons(result.get("reasons")),
        "account_signals": signals,
    }, warnings


def fail_active_batch(
    conn: sqlite3.Connection,
    scan_id: str,
    error: str,
    *,
    batch_id: str | None = None,
) -> dict[str, Any]:
    where = "scan_id=? AND status='in_progress'"
    parameters: tuple[Any, ...] = (scan_id,)
    if batch_id:
        where += " AND batch_id=?"
        parameters += (batch_id,)
    rows = list(conn.execute(
        f"SELECT post_id,attempts FROM enrichment_items WHERE {where.replace('status=', 'state=')}",
        parameters,
    ))
    if not rows:
        raise ValueError("enrichment job has no active batch")
    message = " ".join(str(error).split())[:1000]
    max_attempts = int(_job_row(conn, scan_id)["max_attempts"])
    now = utcnow()
    for row in rows:
        attempts = int(row["attempts"]) + 1
        state = "exhausted" if attempts >= max_attempts else "retry"
        conn.execute(
            """
            UPDATE enrichment_items SET status='failed',state=?,attempts=?,last_error=?,
              batch_id=NULL,updated_at=? WHERE scan_id=? AND post_id=?
            """,
            (state, attempts, message, now, scan_id, row["post_id"]),
        )
    _reconcile_job_state(conn, scan_id)
    conn.commit()
    return {**_job_summary(conn, scan_id), "batchError": message}


def submit_batch(conn: sqlite3.Connection, scan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("submission must be a JSON object")
    batch_id = str(payload.get("batchId") or payload.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("submission requires batchId")
    job = _job_row(conn, scan_id)
    rows = list(conn.execute(
        """
        SELECT * FROM enrichment_items
        WHERE scan_id=? AND state='in_progress' AND batch_id=?
        ORDER BY observed_index
        """,
        (scan_id, batch_id),
    ))
    if not rows:
        raise ValueError("batchId does not match an active batch")
    results = payload.get("results")
    if not isinstance(results, list):
        return fail_active_batch(conn, scan_id, "results must be an array", batch_id=batch_id)
    mapped: dict[str, Any] = {}
    frequencies: dict[str, int] = {}
    for result in results:
        post_id = str(result.get("post_id") or "") if isinstance(result, dict) else ""
        frequencies[post_id] = frequencies.get(post_id, 0) + 1
        mapped[post_id] = result
    requested_ids = {str(row["post_id"]) for row in rows}
    unexpected_ids = sorted(post_id for post_id in frequencies if post_id not in requested_ids)
    raw = _verified_raw(job)
    allowed = json.loads(job["allowed_topics_json"])
    accepted = 0
    errors: list[dict[str, str]] = []
    batch_warnings: list[dict[str, Any]] = [
        {"batchId": batch_id, "kind": "unexpected_post_id", "postId": post_id}
        for post_id in unexpected_ids
    ]
    now = utcnow()
    for row in rows:
        post_id = str(row["post_id"])
        try:
            if frequencies.get(post_id, 0) > 1:
                raise ValueError("duplicate result for post_id")
            if post_id not in mapped:
                raise ValueError("missing result for batch item")
            enrichment, warnings = _validate_result(
                mapped[post_id], raw["posts"][int(row["observed_index"])], allowed,
            )
            conn.execute(
                """
                UPDATE enrichment_items SET status='accepted',state='accepted',attempts=attempts+1,
                  enrichment_json=?,warnings_json=?,last_error=NULL,batch_id=NULL,updated_at=?
                WHERE scan_id=? AND post_id=?
                """,
                (
                    json.dumps(enrichment, sort_keys=True), json.dumps(warnings, sort_keys=True),
                    now, scan_id, post_id,
                ),
            )
            batch_warnings.extend({
                "batchId": batch_id, "kind": "optional_signal_dropped",
                "postId": post_id, "warning": warning,
            } for warning in warnings)
            accepted += 1
        except (TypeError, ValueError) as error:
            message = " ".join(str(error).split())[:1000]
            attempts = int(row["attempts"]) + 1
            state = "exhausted" if attempts >= int(job["max_attempts"]) else "retry"
            conn.execute(
                """
                UPDATE enrichment_items SET status='failed',state=?,attempts=?,last_error=?,
                  batch_id=NULL,updated_at=? WHERE scan_id=? AND post_id=?
                """,
                (state, attempts, message, now, scan_id, post_id),
            )
            errors.append({"post_id": post_id, "error": message})
    _append_job_warnings(conn, scan_id, batch_warnings)
    _reconcile_job_state(conn, scan_id)
    conn.commit()
    return {**_job_summary(conn, scan_id), "batchId": batch_id, "acceptedThisBatch": accepted, "errors": errors}


def finalize_job(conn: sqlite3.Connection, scan_id: str, *, output: str | Path | None = None) -> dict[str, Any]:
    job = _job_row(conn, scan_id)
    incomplete = conn.execute(
        """
        SELECT count(*) FROM enrichment_items
        WHERE scan_id=? AND state NOT IN ('accepted','exhausted')
        """,
        (scan_id,),
    ).fetchone()[0]
    if incomplete:
        raise ValueError(f"cannot finalize: {incomplete} posts are neither accepted nor exhausted")
    raw = _verified_raw(job)
    signals: list[dict[str, Any]] = []
    decisions = {"keep": 0, "candidate": 0, "discard": 0}
    rows = list(conn.execute(
        "SELECT * FROM enrichment_items WHERE scan_id=? ORDER BY observed_index", (scan_id,)
    ))
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = json.loads(job["warnings_json"] or "[]")
    for row in rows:
        post = raw["posts"][int(row["observed_index"])]
        if row["state"] == "accepted":
            enrichment = json.loads(row["enrichment_json"])
            signals.extend(enrichment.pop("account_signals", []))
            post["enrichment_status"] = "accepted"
        else:
            message = str(row["last_error"] or "Luna ranking unavailable")
            enrichment = {
                "topic_matches": [], "score": 0.0, "decision": "discard",
                "reasons": ["Ranking unavailable after bounded retries; retained for audit."],
                "enrichment_status": "failed", "enrichment_error": message,
            }
            failures.append({
                "post_id": str(row["post_id"]), "attempts": int(row["attempts"]),
                "error": message,
            })
        post.update(enrichment)
        decisions[post["decision"]] += 1
    raw["account_signals"] = signals
    raw["enrichment"] = {
        "status": "partial" if failures else "complete",
        "ranking_version": job["ranking_version"],
        "accepted": len(rows) - len(failures), "failed": len(failures),
        "failures": failures, "warnings": warnings,
    }
    destination = Path(output).resolve() if output else Path(job["output_path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, destination)
    now = utcnow()
    state = "partial" if failures else "complete"
    conn.execute(
        """
        UPDATE enrichment_jobs SET status='complete',state=?,output_path=?,completed_at=?,
          finished_at=?,updated_at=?,last_error=? WHERE scan_id=?
        """,
        (
            state, str(destination), now, now, now,
            failures[0]["error"] if failures else None, scan_id,
        ),
    )
    conn.commit()
    return {
        **_job_summary(conn, scan_id),
        "posts": len(rows),
        "decisions": decisions,
        "accountSignals": len(signals),
        "failedPosts": len(failures),
        "warnings": len(warnings),
    }


def job_status(conn: sqlite3.Connection, scan_id: str) -> dict[str, Any]:
    result = _job_summary(conn, scan_id)
    failed = [dict(row) for row in conn.execute(
        """
        SELECT post_id,state,attempts,last_error,warnings_json FROM enrichment_items
        WHERE scan_id=? AND state IN ('retry','exhausted') ORDER BY observed_index LIMIT 50
        """,
        (scan_id,),
    )]
    return {**result, "errors": failed}
