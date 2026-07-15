from __future__ import annotations

import argparse
import json
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

from .db import (
    add_evidence,
    archive_capture,
    backup_database,
    connect,
    due_outbox,
    export_feed,
    ingest_capture,
    mark_outbox_failed,
    mark_outbox_sent,
    purge_account,
    search_posts,
    seed_blocklist,
    set_account_disposition,
    set_post_state,
    status,
    upsert_post,
)
from .site_client import (
    claim_request, complete_request, ingest_capture as ingest_site_capture,
    mutations, pending_requests, report_progress, send_event, send_heartbeat,
)
from .planner import apply_preferences, build_period_plan, set_query_pack, topic_key
from .enrichment import (
    fail_active_batch,
    finalize_job,
    job_status,
    next_batch,
    start_job,
    submit_batch,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="x-radar")
    root.add_argument("--db", default="var/x-radar.sqlite")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    ingest = commands.add_parser("ingest")
    ingest.add_argument("input")

    capture = commands.add_parser("ingest-capture")
    capture.add_argument("input")

    signal = commands.add_parser("signal")
    signal.add_argument("handle")
    signal.add_argument("post_url")
    signal.add_argument("reason")
    signal.add_argument("confidence", type=float)

    account = commands.add_parser("account-set")
    account.add_argument("handle")
    account.add_argument("disposition", choices=["allow", "normal", "watch", "downrank", "blocked"])
    account.add_argument("--notes")

    purge = commands.add_parser("purge-account")
    purge.add_argument("handle")
    purge.add_argument("--confirm", required=True)

    seed = commands.add_parser("seed-blocklist")
    seed.add_argument("input")

    export = commands.add_parser("export")
    export.add_argument("output")
    export.add_argument("--limit", type=int, default=100)

    commands.add_parser("blocklist")

    commands.add_parser("site-requests")
    commands.add_parser("site-claim")

    sync = commands.add_parser("sync")
    sync.add_argument("--limit", type=int, default=25)

    progress = commands.add_parser("site-progress")
    progress.add_argument("phase", choices=["collecting", "ranking", "syncing", "complete", "error"])
    progress.add_argument("--target")
    progress.add_argument("--request-id")
    progress.add_argument("--scan-id")
    progress.add_argument("--observed", type=int, default=0)
    progress.add_argument("--target-unique", type=int)
    progress.add_argument("--preference-version", type=int)
    progress.add_argument("--source-progress")
    progress.add_argument("--error")
    progress.add_argument("--error-code")

    pull = commands.add_parser("pull-site-mutations")
    pull.add_argument("--after", type=int)

    archive = commands.add_parser("archive")
    archive.add_argument("capture")
    archive.add_argument("--root", default="var/archive")
    archive.add_argument("--kind", default="enriched")

    backup = commands.add_parser("backup")
    backup.add_argument("--root", default="var/backups")
    backup.add_argument("--keep-days", type=int, default=30)

    post_state = commands.add_parser("post-state")
    post_state.add_argument("post_id")
    post_state.add_argument("action", choices=["save", "unsave", "pin", "unpin", "dismiss", "restore"])

    search = commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--handle")
    search.add_argument("--decision")
    search.add_argument("--limit", type=int, default=50)

    commands.add_parser("status")
    commands.add_parser("curation")

    period = commands.add_parser("plan-period")
    period.add_argument("--output", required=True)
    period.add_argument("--request-id")
    period.add_argument("--bootstrap-topics", default="[]")

    queries = commands.add_parser("topic-queries-set")
    queries.add_argument("topic")
    queries.add_argument("--query", action="append", default=[])
    queries.add_argument("--canonical")
    queries.add_argument("--technical")
    queries.add_argument("--adjacent")
    queries.add_argument("--pack-file")
    queries.add_argument("--revision", type=int, required=True)

    refresh_queries = commands.add_parser("topic-queries-refresh")
    refresh_queries.add_argument("--topic")

    commands.add_parser("topic-memory")

    enrichment_start = commands.add_parser("enrichment-start")
    enrichment_start.add_argument("capture")
    enrichment_start.add_argument("--output")
    enrichment_start.add_argument("--batch-items", type=int, default=24)
    enrichment_start.add_argument("--batch-chars", type=int, default=60_000)

    enrichment_next = commands.add_parser("enrichment-next")
    enrichment_next.add_argument("scan_id")
    enrichment_next.add_argument("--output")

    enrichment_submit = commands.add_parser("enrichment-submit")
    enrichment_submit.add_argument("scan_id")
    enrichment_submit.add_argument("input")

    enrichment_status = commands.add_parser("enrichment-status")
    enrichment_status.add_argument("scan_id")

    enrichment_finalize = commands.add_parser("enrichment-finalize")
    enrichment_finalize.add_argument("scan_id")
    enrichment_finalize.add_argument("--output")

    luna_usage = commands.add_parser("luna-usage")
    luna_usage.add_argument("--scan-id")
    luna_usage.add_argument("--limit", type=int, default=25)

    site_ingest = commands.add_parser("site-ingest")
    site_ingest.add_argument("capture")

    site_complete = commands.add_parser("site-complete")
    site_complete.add_argument("request_id")
    site_complete.add_argument("--count", type=int, default=0)
    site_complete.add_argument("--error")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    conn = connect(args.db)
    if args.command == "init":
        print(f"initialized {args.db} on {platform.node()}")
    elif args.command == "ingest":
        posts = json.loads(Path(args.input).read_text())
        added = sum(upsert_post(conn, post) for post in posts)
        conn.commit()
        print(f"ingested {added} new posts ({len(posts) - added} duplicates)")
    elif args.command == "ingest-capture":
        result = ingest_capture(conn, json.loads(Path(args.input).read_text()))
        conn.commit()
        print(json.dumps(result, sort_keys=True))
    elif args.command == "signal":
        add_evidence(
            conn,
            handle=args.handle,
            post_url=args.post_url,
            reason=args.reason,
            confidence=args.confidence,
        )
        conn.commit()
        print("signal recorded")
    elif args.command == "account-set":
        set_account_disposition(conn, args.handle, args.disposition, args.notes)
        conn.commit()
        print(f"{args.handle} set to {args.disposition}")
    elif args.command == "purge-account":
        if args.confirm.strip().lower().lstrip("@") != args.handle.strip().lower().lstrip("@"):
            raise SystemExit("--confirm must match the account handle")
        result = purge_account(conn, args.handle)
        conn.commit()
        print(json.dumps(result, sort_keys=True))
    elif args.command == "seed-blocklist":
        seed_blocklist(conn, json.loads(Path(args.input).read_text()))
        conn.commit()
        print("blocklist seeded")
    elif args.command == "export":
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(export_feed(conn, args.limit), indent=2) + "\n")
        print(f"exported {output}")
    elif args.command == "blocklist":
        rows = conn.execute(
            """
            SELECT handle, disposition, strike_points, confidence, notes
            FROM account_reputation
            WHERE disposition != 'normal'
            ORDER BY
                CASE disposition
                    WHEN 'blocked' THEN 0 WHEN 'downrank' THEN 1
                    WHEN 'watch' THEN 2 ELSE 3
                END,
                handle
            """
        )
        print(json.dumps([dict(row) for row in rows], indent=2))
    elif args.command == "site-requests":
        print(json.dumps(pending_requests(), indent=2))
    elif args.command == "site-claim":
        print(json.dumps(claim_request(), indent=2))
    elif args.command == "site-ingest":
        reputation = [
            dict(row)
            for row in conn.execute(
                """
                SELECT handle, disposition, strike_points AS strikePoints,
                       confidence, reasons_json AS reasonsJson
                FROM account_reputation
                """
            )
        ]
        print(json.dumps(ingest_site_capture(args.capture, reputation), indent=2))
    elif args.command == "site-complete":
        print(json.dumps(complete_request(
            args.request_id,
            result_count=args.count,
            error=args.error,
        ), indent=2))
    elif args.command == "sync":
        sent = failed = 0
        for row in due_outbox(conn, args.limit):
            try:
                payload = json.loads(row["payload_json"])
                if row["kind"] == "capture":
                    payload["account_reputation"] = [dict(item) for item in conn.execute(
                        "SELECT handle,disposition,strike_points AS strikePoints,confidence,reasons_json AS reasonsJson,notes,operator_override AS operatorOverride FROM account_reputation"
                    )]
                send_event(row["kind"], payload)
                mark_outbox_sent(conn, row["id"])
                sent += 1
            except Exception as error:  # one failed event must not block the rest
                mark_outbox_failed(conn, row["id"], row["attempt_count"], str(error))
                failed += 1
        conn.commit()
        local_status = status(conn)
        backups = sorted((Path(args.db).parent / "backups").glob("x-radar-*.sqlite"), key=lambda item: item.stat().st_mtime)
        last_backup = datetime.fromtimestamp(backups[-1].stat().st_mtime, timezone.utc).isoformat() if backups else None
        heartbeat_ok = True
        try:
            send_heartbeat({
                "pendingSync": local_status["pending_sync"],
                "failedAttempts": local_status["failed_attempts"],
                "lastBackupAt": last_backup,
            })
        except Exception:
            heartbeat_ok = False
        print(json.dumps({"sent": sent, "failed": failed, "pending": local_status["pending_sync"], "heartbeat": heartbeat_ok}))
    elif args.command == "site-progress":
        payload = {
            "phase": args.phase, "target": args.target, "requestId": args.request_id,
            "scanId": args.scan_id, "observed": args.observed,
            "error": args.error, "errorCode": args.error_code,
            "targetUnique": args.target_unique, "preferenceVersion": args.preference_version,
        }
        if args.source_progress:
            payload["sourceProgress"] = json.loads(args.source_progress)
        print(json.dumps(report_progress({key: value for key, value in payload.items() if value is not None})))
    elif args.command == "pull-site-mutations":
        cursor = args.after
        if cursor is None:
            row = conn.execute("SELECT value FROM site_state WHERE key='mutation_cursor'").fetchone()
            cursor = int(row["value"]) if row else 0
        result = mutations(cursor)
        for item in result.get("mutations", []):
            if item["kind"] == "account":
                set_account_disposition(conn, item["handle"], item["disposition"], item.get("notes"), enqueue_change=False)
            elif item["kind"] == "post_state":
                set_post_state(conn, item["post_id"], saved=item.get("saved"), pinned=item.get("pinned"), dismissed=item.get("dismissed"), enqueue_change=False)
            elif item["kind"] == "curation":
                apply_preferences(
                    conn, instructions=item.get("instructions", ""), topics=item.get("topics", []),
                    version=int(item.get("preferenceVersion") or item.get("version") or 0),
                    updated_at=item.get("updated_at") or item.get("updatedAt"),
                )
        next_cursor = int(result.get("cursor", cursor))
        conn.execute("INSERT INTO site_state(key,value) VALUES('mutation_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(next_cursor),))
        conn.commit()
        print(json.dumps({"applied": len(result.get("mutations", [])), "cursor": next_cursor}))
    elif args.command == "archive":
        print(archive_capture(args.capture, args.root, args.kind))
    elif args.command == "backup":
        conn.commit()
        conn.close()
        print(backup_database(args.db, args.root, args.keep_days))
        return 0
    elif args.command == "post-state":
        mapping = {
            "save": {"saved": True}, "unsave": {"saved": False},
            "pin": {"pinned": True}, "unpin": {"pinned": False},
            "dismiss": {"dismissed": True}, "restore": {"dismissed": False},
        }
        set_post_state(conn, args.post_id, **mapping[args.action])
        conn.commit()
        print(f"{args.post_id}: {args.action}")
    elif args.command == "search":
        print(json.dumps(search_posts(conn, args.query, handle=args.handle, decision=args.decision, limit=args.limit), indent=2))
    elif args.command == "status":
        print(json.dumps(status(conn), indent=2))
    elif args.command == "curation":
        row = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
        default = {"instructions": "", "topics": []}
        print(json.dumps(json.loads(row["value"]) if row else default, indent=2))
    elif args.command == "plan-period":
        bootstrap = json.loads(args.bootstrap_topics)
        if not isinstance(bootstrap, list):
            raise ValueError("--bootstrap-topics must be a JSON array")
        plan = build_period_plan(conn, request_id=args.request_id, bootstrap_topics=bootstrap)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2) + "\n")
        conn.commit()
        print(json.dumps({"output": str(output), "periodId": plan["period_id"], "targetUnique": plan["target_unique"], "targets": len(plan["targets"])}))
    elif args.command == "topic-queries-set":
        structured = [
            {"kind": kind, "query": value}
            for kind, value in (
                ("canonical", args.canonical),
                ("technical", args.technical),
                ("adjacent", args.adjacent),
            )
            if value
        ]
        supplied = structured or args.query
        if args.pack_file:
            pack_payload = json.loads(Path(args.pack_file).read_text())
            supplied = pack_payload.get("queries") if isinstance(pack_payload, dict) else pack_payload
            if not isinstance(supplied, list):
                raise ValueError("query pack file must contain an array or {\"queries\": [...]} object")
        values = set_query_pack(conn, args.topic, supplied, args.revision)
        conn.commit()
        print(json.dumps({"topic": args.topic, "queries": values, "revision": args.revision}))
    elif args.command == "topic-queries-refresh":
        if args.topic:
            key = args.topic if re.fullmatch(r"[0-9a-f]{16}", args.topic) else topic_key(args.topic)
            updated = conn.execute(
                "UPDATE topic_state SET query_pack_revision=-1 WHERE active=1 AND topic_key=?",
                (key,),
            ).rowcount
            if not updated:
                raise ValueError(f"unknown active topic: {args.topic}")
        else:
            updated = conn.execute("UPDATE topic_state SET query_pack_revision=-1 WHERE active=1").rowcount
        conn.commit()
        print(json.dumps({"refreshed": updated, "topic": args.topic}))
    elif args.command == "topic-memory":
        rows = conn.execute("""
            SELECT t.label topic,m.handle,m.relevant_observations,m.kept_posts,
              CASE WHEN m.relevant_observations=0 THEN 0 ELSE m.score_sum/m.relevant_observations END average_score,
              m.confidence,m.status,m.last_observed_at,m.last_scanned_at
            FROM topic_account_memory m JOIN topic_state t ON t.topic_key=m.topic_key
            ORDER BY t.label,m.status DESC,m.confidence DESC
        """)
        topic_rows = conn.execute("""
            SELECT topic_key AS topicKey,label,active,query_pack_json AS queryPackJson,
              query_pack_revision AS queryPackRevision,last_planned_at AS lastPlannedAt,
              last_searched_at AS lastSearchedAt FROM topic_state ORDER BY active DESC,label
        """)
        preference = conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()
        query_rows = conn.execute("""
            SELECT topic_key AS topicKey,query_kind AS kind,query,attempts,
              observed_total AS observedTotal,unique_total AS uniqueTotal,
              kept_total AS keptTotal,consecutive_empty AS consecutiveEmpty,
              last_attempted_at AS lastAttemptedAt,cooldown_until AS cooldownUntil
            FROM topic_query_memory ORDER BY topic_key,last_attempted_at
        """)
        preference_value = json.loads(preference["value"]) if preference else {"version": 0, "topics": []}
        topics = []
        for row in topic_rows:
            value = {**dict(row), "queryPack": json.loads(row["queryPackJson"]), "queryPackJson": None}
            pack = value["queryPack"] if isinstance(value["queryPack"], list) else []
            kinds = {
                item.get("kind")
                for item in pack
                if isinstance(item, dict) and isinstance(item.get("query"), str)
            }
            value["queryPackNeedsRefresh"] = (
                value["active"] == 1
                and (not {"canonical", "technical", "adjacent"}.issubset(kinds)
                     or int(value["queryPackRevision"] or 0) < int(preference_value.get("version") or 0))
            )
            topics.append(value)
        print(json.dumps({
            "preference": preference_value,
            "topics": topics,
            "accounts": [dict(row) for row in rows],
            "queryMemory": [dict(row) for row in query_rows],
        }, indent=2))
    elif args.command == "enrichment-start":
        print(json.dumps(start_job(
            conn, args.capture, output=args.output,
            max_items=args.batch_items, max_chars=args.batch_chars,
        ), indent=2))
    elif args.command == "enrichment-next":
        batch = next_batch(conn, args.scan_id)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps({
                "output": str(output), "scanId": batch["scanId"],
                "batchId": batch.get("batchId"), "posts": len(batch.get("posts", [])),
                "ready": batch.get("ready", False), "complete": batch.get("complete", False),
            }))
        else:
            print(json.dumps(batch, indent=2, ensure_ascii=False))
    elif args.command == "enrichment-submit":
        try:
            submission = json.loads(Path(args.input).read_text())
        except (OSError, json.JSONDecodeError) as error:
            print(json.dumps(fail_active_batch(conn, args.scan_id, f"invalid submission JSON: {error}"), indent=2))
        else:
            print(json.dumps(submit_batch(conn, args.scan_id, submission), indent=2))
    elif args.command == "enrichment-status":
        print(json.dumps(job_status(conn, args.scan_id), indent=2))
    elif args.command == "enrichment-finalize":
        print(json.dumps(finalize_job(conn, args.scan_id, output=args.output), indent=2))
    elif args.command == "luna-usage":
        where = "WHERE scan_id=?" if args.scan_id else ""
        parameters = (args.scan_id, args.limit) if args.scan_id else (args.limit,)
        rows = conn.execute(
            f"""
            SELECT invocation_id AS invocationId,scan_id AS scanId,batch_id AS batchId,
              purpose,model,reasoning_effort AS reasoningEffort,status,
              input_tokens AS inputTokens,cached_input_tokens AS cachedInputTokens,
              output_tokens AS outputTokens,billable_tokens AS billableTokens,
              duration_ms AS durationMs,error,created_at AS createdAt
            FROM luna_invocations {where} ORDER BY id DESC LIMIT ?
            """,
            parameters,
        )
        print(json.dumps([dict(row) for row in rows], indent=2))
    return 0
