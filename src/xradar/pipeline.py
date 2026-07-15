from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import (
    archive_capture,
    due_outbox,
    export_feed,
    ingest_capture,
    mark_outbox_failed,
    mark_outbox_sent,
    set_account_disposition,
    set_post_state,
)
from .enrichment import active_batches, claim_batch, fail_active_batch, finalize_job, start_job, submit_batch
from .luna import expand_topic_queries, rank_batch
from .planner import apply_preferences, build_period_plan, set_query_pack
from .site_client import claim_request, complete_request, mutations, report_progress, send_event


_HANDLE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _best_effort_progress(payload: dict[str, Any]) -> None:
    try:
        report_progress({key: value for key, value in payload.items() if value is not None})
    except Exception:
        pass


def _pull_mutations(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM site_state WHERE key='mutation_cursor'").fetchone()
    cursor = int(row["value"]) if row else 0
    result = mutations(cursor)
    items = result.get("mutations", [])
    for item in items:
        if item["kind"] == "account":
            set_account_disposition(conn, item["handle"], item["disposition"], item.get("notes"), enqueue_change=False)
        elif item["kind"] == "post_state":
            set_post_state(
                conn, item["post_id"], saved=item.get("saved"), pinned=item.get("pinned"),
                dismissed=item.get("dismissed"), enqueue_change=False,
            )
        elif item["kind"] == "curation":
            apply_preferences(
                conn, instructions=item.get("instructions", ""), topics=item.get("topics", []),
                version=int(item.get("preferenceVersion") or item.get("version") or 0),
                updated_at=item.get("updated_at") or item.get("updatedAt"),
            )
    next_cursor = int(result.get("cursor", cursor))
    conn.execute(
        "INSERT INTO site_state(key,value) VALUES('mutation_cursor',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(next_cursor),),
    )
    conn.commit()
    return len(items)


def _refresh_query_packs(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    preference_row = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
    preference = json.loads(preference_row["value"]) if preference_row else {"version": 0, "instructions": ""}
    version = int(preference.get("version") or 0)
    rows = list(conn.execute(
        """
        SELECT topic_key,label,query_pack_json,query_pack_revision FROM topic_state
        WHERE active=1 ORDER BY label
        """
    ))
    refreshed: list[dict[str, Any]] = []
    for row in rows:
        try:
            pack = json.loads(row["query_pack_json"] or "[]")
        except json.JSONDecodeError:
            pack = []
        kinds = {item.get("kind") for item in pack if isinstance(item, dict)}
        stale = not {"canonical", "technical", "adjacent"}.issubset(kinds) or int(row["query_pack_revision"] or 0) < version
        if not stale:
            continue
        source = "luna"
        try:
            result = expand_topic_queries(
                conn, topic_key=row["topic_key"], topic=row["label"],
                instructions=str(preference.get("instructions") or ""),
                preference_version=version, runtime_root=root,
            )
            queries = result.payload["queries"]
            invocation_id = result.invocation_id
        except Exception as error:
            # A locally validated exact-topic fallback keeps discovery available
            # without letting model availability prevent the entire period.
            queries = []
            invocation_id = None
            source = f"fallback:{type(error).__name__}"
        try:
            values = set_query_pack(conn, row["topic_key"], queries, version)
        except (TypeError, ValueError):
            values = set_query_pack(conn, row["topic_key"], [], version)
            source = "fallback:validation"
        refreshed.append({
            "topicKey": row["topic_key"], "topic": row["label"], "source": source,
            "queries": values, "invocationId": invocation_id,
        })
    conn.commit()
    return refreshed


def _collect(
    root: Path,
    *,
    output: Path,
    request: dict[str, Any] | None,
    plan_path: Path | None,
) -> dict[str, Any]:
    command = [sys.executable, str(root / "collector" / "firefox_collect.py"), "--output", str(output)]
    request_id = str(request.get("id")) if request and request.get("id") else None
    if plan_path:
        command.extend(["--plan", str(plan_path)])
    else:
        if request and request.get("targetKind") == "account":
            handle = str(request.get("handle") or "")
            if not _HANDLE.fullmatch(handle):
                raise ValueError("directed account request has an invalid handle")
            suffix = "/with_replies" if request.get("includeReplies", True) else ""
            target = f"https://x.com/{handle.lstrip('@')}{suffix}"
        else:
            target = "https://x.com/home"
        command.extend(["--target", target, "--limit", "100"])
    if request_id:
        command.extend(["--request-id", request_id])
    completed = subprocess.run(
        command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=int(os.environ.get("XRADAR_FIREFOX_TIMEOUT_SECONDS", "1860")), check=False,
    )
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[-1600:]
        raise RuntimeError(detail or f"Firefox collector exited {completed.returncode}")
    payload = json.loads(output.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        raise RuntimeError("Firefox collector did not write a valid raw capture")
    return payload


def _enrich(conn: sqlite3.Connection, root: Path, raw_path: Path, capture_path: Path) -> dict[str, Any]:
    job = start_job(
        conn, raw_path, output=capture_path,
        max_items=int(os.environ.get("XRADAR_LUNA_BATCH_ITEMS", "24")),
        max_chars=int(os.environ.get("XRADAR_LUNA_BATCH_CHARS", "60000")),
        ranking_version="luna-stateless-v2",
    )
    scan_id = job["scanId"]
    max_attempts = int(os.environ.get("XRADAR_LUNA_MAX_ATTEMPTS", "3"))
    concurrency = int(os.environ.get("XRADAR_LUNA_CONCURRENCY", "2"))
    if not 1 <= concurrency <= 4:
        raise ValueError("XRADAR_LUNA_CONCURRENCY must be between 1 and 4")
    database_row = conn.execute("PRAGMA database_list").fetchone()
    database_path = Path(database_row["file"]).resolve()

    def rank_in_worker(batch: dict[str, Any]):
        worker = sqlite3.connect(database_path, timeout=30)
        worker.row_factory = sqlite3.Row
        worker.execute("PRAGMA foreign_keys=ON")
        worker.execute("PRAGMA busy_timeout=30000")
        try:
            return rank_batch(worker, batch, root)
        finally:
            worker.close()

    while True:
        exhausted = conn.execute(
            "SELECT post_id,last_error FROM enrichment_items WHERE scan_id=? AND status='failed' AND attempts>=? LIMIT 1",
            (scan_id, max_attempts),
        ).fetchone()
        if exhausted:
            raise RuntimeError(f"Luna enrichment exhausted retries for post {exhausted['post_id']}: {exhausted['last_error']}")
        batches = active_batches(conn, scan_id)[:concurrency]
        while len(batches) < concurrency:
            batch = claim_batch(conn, scan_id)
            if batch.get("ready") or batch.get("waiting"):
                break
            if not batch.get("posts"):
                raise RuntimeError("enrichment returned neither a batch nor ready state")
            batches.append(batch)
        if not batches:
            break
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="xradar-luna") as executor:
            futures = [(batch, executor.submit(rank_in_worker, batch)) for batch in batches]
            for batch, future in futures:
                try:
                    result = future.result()
                    submit_batch(conn, scan_id, result.payload)
                except Exception as error:
                    # A lease is the retry boundary. Parallel work in other
                    # batches and accepted results from earlier waves survive.
                    active = conn.execute(
                        """
                        SELECT 1 FROM enrichment_items
                        WHERE scan_id=? AND status='in_progress' AND batch_id=? LIMIT 1
                        """,
                        (scan_id, batch["batchId"]),
                    ).fetchone()
                    if active:
                        fail_active_batch(conn, scan_id, str(error), batch_id=batch["batchId"])
    return finalize_job(conn, scan_id, output=capture_path)


def _sync(conn: sqlite3.Connection, limit: int = 25) -> dict[str, int]:
    sent = failed = 0
    for row in due_outbox(conn, limit):
        try:
            payload = json.loads(row["payload_json"])
            if row["kind"] == "capture":
                payload["account_reputation"] = [dict(item) for item in conn.execute(
                    "SELECT handle,disposition,strike_points AS strikePoints,confidence,reasons_json AS reasonsJson,notes,operator_override AS operatorOverride FROM account_reputation"
                )]
            send_event(row["kind"], payload)
            mark_outbox_sent(conn, row["id"])
            sent += 1
        except Exception as error:
            mark_outbox_failed(conn, row["id"], row["attempt_count"], str(error))
            failed += 1
    conn.commit()
    pending = int(conn.execute("SELECT count(*) FROM sync_outbox WHERE status='pending'").fetchone()[0])
    return {"sent": sent, "failed": failed, "pending": pending}


def _persist_capture(
    conn: sqlite3.Connection,
    root: Path,
    *,
    raw_path: Path,
    capture_path: Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    capture = json.loads(capture_path.read_text())
    ingest = ingest_capture(conn, capture)
    conn.commit()
    archive_capture(raw_path, root / "var" / "archive", "raw")
    archive_capture(capture_path, root / "var" / "archive", "enriched")
    feed = root / "public" / "feed.json"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps(export_feed(conn, 100), indent=2) + "\n")
    return ingest, _sync(conn)


def _resume_uningested(conn: sqlite3.Connection, root: Path) -> dict[str, Any] | None:
    max_attempts = int(os.environ.get("XRADAR_LUNA_MAX_ATTEMPTS", "3"))
    job = conn.execute(
        """
        SELECT j.* FROM enrichment_jobs j
        LEFT JOIN runs r ON r.scan_id=j.scan_id
        WHERE r.id IS NULL AND NOT EXISTS (
          SELECT 1 FROM enrichment_items i
          WHERE i.scan_id=j.scan_id AND i.status='failed' AND i.attempts>=?
        )
        ORDER BY j.created_at LIMIT 1
        """,
        (max_attempts,),
    ).fetchone()
    if not job:
        return None
    raw_path = Path(job["raw_path"])
    capture_path = Path(job["output_path"])
    raw = json.loads(raw_path.read_text())
    scan_id = str(job["scan_id"])
    observed = len(raw.get("posts", []))
    target_unique = int(raw.get("target_unique") or 100)
    request_id = str(raw.get("request_id") or "") or None
    _best_effort_progress({
        "phase": "ranking", "target": "mixed" if raw.get("source") == "x-mixed" else raw.get("target"),
        "requestId": request_id, "scanId": scan_id, "observed": observed,
        "targetUnique": target_unique, "preferenceVersion": int(raw.get("preference_version") or 0),
    })
    enrichment = (
        {"scanId": scan_id, "status": "complete", "resumed": True}
        if job["status"] == "complete"
        else _enrich(conn, root, raw_path, capture_path)
    )
    ingest, sync = _persist_capture(conn, root, raw_path=raw_path, capture_path=capture_path)
    if request_id:
        try:
            complete_request(request_id, result_count=observed)
        except Exception:
            pass
    return {
        "scanId": scan_id, "observed": observed, "target": target_unique,
        "resumed": True, "ingest": ingest, "enrichment": enrichment, "sync": sync,
    }


def run_cycle(conn: sqlite3.Connection, runtime_root: str | Path) -> dict[str, Any]:
    root = Path(runtime_root).resolve()
    inbox = root / "var" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    request: dict[str, Any] | None = None
    request_id: str | None = None
    target = "mixed"
    try:
        resumed = _resume_uningested(conn, root)
        if resumed:
            return resumed
        try:
            _pull_mutations(conn)
        except Exception:
            pass
        try:
            claimed = claim_request()
            candidate = claimed.get("request")
            request = candidate if isinstance(candidate, dict) else None
        except Exception:
            request = None
        request_id = str(request.get("id")) if request and request.get("id") else None
        is_account = bool(request and request.get("targetKind") == "account")
        legacy = os.environ.get("XRADAR_ACQUISITION_MODE", "mixed").strip().lower() == "legacy"
        plan_path: Path | None = None
        if not is_account and not legacy:
            refreshed = _refresh_query_packs(conn, root)
            plan = build_period_plan(
                conn, request_id=request_id,
                bootstrap_topics=list(request.get("bootstrapTopics") or []) if request else [],
            )
            plan_path = inbox / f"plan-{_stamp()}.json"
            plan_path.write_text(json.dumps(plan, indent=2) + "\n")
            conn.executemany(
                "UPDATE luna_invocations SET scan_id=? WHERE invocation_id=? AND scan_id IS NULL",
                ((plan["scan_id"], item["invocationId"]) for item in refreshed if item.get("invocationId")),
            )
            conn.commit()
            target_unique = 150
            preference_version = int(plan.get("preference_version") or 0)
        else:
            target = str(request.get("handle")) if is_account else "https://x.com/home"
            target_unique = 100
            preference_version = 0
        _best_effort_progress({
            "phase": "collecting", "target": target, "requestId": request_id,
            "observed": 0, "targetUnique": target_unique,
            "preferenceVersion": preference_version, "sourceProgress": {},
        })
        stamp = _stamp()
        raw_path = inbox / f"raw-{stamp}.json"
        capture_path = inbox / f"capture-{stamp}.json"
        raw = _collect(root, output=raw_path, request=request, plan_path=plan_path)
        scan_id = str(raw.get("scan_id") or raw.get("period_id") or "")
        observed = len(raw["posts"])
        _best_effort_progress({
            "phase": "ranking", "target": target, "requestId": request_id,
            "scanId": scan_id, "observed": observed, "targetUnique": target_unique,
            "preferenceVersion": preference_version,
        })
        enrichment = _enrich(conn, root, raw_path, capture_path)
        _best_effort_progress({
            "phase": "syncing", "target": target, "requestId": request_id,
            "scanId": scan_id, "observed": observed, "targetUnique": target_unique,
            "preferenceVersion": preference_version,
        })
        ingest, sync = _persist_capture(conn, root, raw_path=raw_path, capture_path=capture_path)
        if request_id:
            try:
                complete_request(request_id, result_count=observed)
            except Exception:
                pass
        source_progress = {
            item.get("id", str(index)): {
                "kind": item.get("kind"), "topic": item.get("topic"),
                "planned": item.get("quota", 0), "observed": item.get("observed", 0),
                "unique": item.get("unique", 0), "status": item.get("status", "complete"),
            }
            for index, item in enumerate(raw.get("acquisitions", []))
        }
        _best_effort_progress({
            "phase": "complete", "target": target, "requestId": request_id,
            "scanId": scan_id, "observed": observed, "targetUnique": target_unique,
            "preferenceVersion": preference_version, "sourceProgress": source_progress,
        })
        usage = dict(conn.execute(
            """
            SELECT count(*) calls,COALESCE(sum(input_tokens),0) inputTokens,
              COALESCE(sum(cached_input_tokens),0) cachedInputTokens,
              COALESCE(sum(output_tokens),0) outputTokens,
              COALESCE(sum(billable_tokens),0) billableTokens
            FROM luna_invocations WHERE scan_id=?
            """,
            (scan_id,),
        ).fetchone())
        return {
            "scanId": scan_id, "observed": observed, "target": target_unique,
            "ingest": ingest, "enrichment": enrichment, "sync": sync, "lunaUsage": usage,
        }
    except Exception as error:
        detail = " ".join(str(error).split())[:1000]
        _best_effort_progress({
            "phase": "error", "target": target, "requestId": request_id,
            "errorCode": "AUTH_REQUIRED" if "AUTH_REQUIRED" in detail else "COLLECTOR_ERROR",
            "error": detail,
        })
        if request_id:
            try:
                complete_request(request_id, error=detail)
            except Exception:
                pass
        raise


def main() -> int:
    from .db import connect
    from .site_client import load_runtime_env

    root = Path(os.environ.get("XRADAR_ROOT") or Path.cwd()).resolve()
    load_runtime_env(root / ".env")
    conn = connect(root / "var" / "x-radar.sqlite")
    try:
        print(json.dumps(run_cycle(conn, root), indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
