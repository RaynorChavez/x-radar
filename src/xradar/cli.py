from __future__ import annotations

import argparse
import json
import platform
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
from .planner import apply_preferences, build_period_plan, set_query_pack


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
    queries.add_argument("--query", action="append", required=True)
    queries.add_argument("--revision", type=int, required=True)

    commands.add_parser("topic-memory")

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
        values = set_query_pack(conn, args.topic, args.query, args.revision)
        conn.commit()
        print(json.dumps({"topic": args.topic, "queries": values, "revision": args.revision}))
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
        print(json.dumps({
            "preference": json.loads(preference["value"]) if preference else {"version": 0, "topics": []},
            "topics": [{**dict(row), "queryPack": json.loads(row["queryPackJson"]), "queryPackJson": None} for row in topic_rows],
            "accounts": [dict(row) for row in rows],
        }, indent=2))
    return 0
