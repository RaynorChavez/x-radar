import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from xradar.db import (
    add_evidence,
    connect,
    export_feed,
    ingest_capture,
    search_posts,
    seed_blocklist,
    set_account_disposition,
    set_post_state,
    upsert_post,
)
from xradar.cli import main as cli_main


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite"
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_ingest_is_idempotent(self):
        post = {
            "post_id": "1",
            "url": "https://x.com/researcher/status/1",
            "handle": "researcher",
            "text": "A new result with a linked paper.",
            "score": 0.9,
        }
        self.assertTrue(upsert_post(self.conn, post))
        self.assertFalse(upsert_post(self.conn, post))

    def test_duplicate_capture_can_refresh_profile_image(self):
        post = {
            "post_id": "avatar-1", "url": "https://x.com/alice/status/avatar-1",
            "handle": "alice", "text": "hello", "decision": "keep",
        }
        self.assertTrue(upsert_post(self.conn, post))
        self.assertFalse(upsert_post(self.conn, {
            **post, "profile_image_url": "https://pbs.twimg.com/profile_images/alice.jpg",
        }))
        self.assertEqual(
            "https://pbs.twimg.com/profile_images/alice.jpg",
            export_feed(self.conn, 10)[0]["profile_image_url"],
        )

    def test_post_gets_xcancel_url_and_structured_content(self):
        post = {
            "post_id": "2076422557180608888",
            "url": "https://x.com/researcher/status/2076422557180608888",
            "handle": "researcher",
            "text": "A long-form research note.",
            "score": 0.9,
            "decision": "keep",
            "external_links": [{"url": "https://example.org/paper", "title": "Paper"}],
            "media": [{"type": "image", "url": "https://images.example/figure.png"}],
            "article": {"id": "2076422557180608888", "title": "Research note"},
        }
        self.assertTrue(upsert_post(self.conn, post))
        feed = export_feed(self.conn, 10)
        self.assertEqual(
            "https://xcancel.com/i/article/2076422557180608888",
            feed[0]["xcancel_url"],
        )
        self.assertEqual("Paper", feed[0]["external_links"][0]["title"])
        self.assertEqual("image", feed[0]["media"][0]["type"])
        self.assertEqual("Research note", feed[0]["article"]["title"])

    def test_existing_posts_are_backfilled_with_status_mirror(self):
        upsert_post(self.conn, {
            "post_id": "99", "url": "https://x.com/alice/status/99",
            "handle": "@Alice", "text": "hello", "decision": "keep",
        })
        row = self.conn.execute("SELECT xcancel_url FROM posts WHERE post_id='99'").fetchone()
        self.assertEqual("https://xcancel.com/alice/status/99", row["xcancel_url"])

    def test_primary_source_becomes_a_renderable_external_link(self):
        upsert_post(self.conn, {
            "post_id": "100", "url": "https://x.com/alice/status/100",
            "handle": "alice", "text": "paper", "decision": "keep",
            "source_url": "https://example.org/paper",
        })
        self.conn.commit()
        self.conn.close()
        self.conn = connect(Path(self.temp.name) / "test.sqlite")
        self.assertEqual(
            "https://example.org/paper",
            export_feed(self.conn, 10)[0]["external_links"][0]["url"],
        )

    def test_single_signal_watches_but_does_not_block(self):
        add_evidence(
            self.conn,
            handle="bait",
            post_url="https://x.com/bait/status/1",
            reason="engagement_bait",
            confidence=0.9,
        )
        row = self.conn.execute(
            "SELECT disposition, strike_points FROM account_reputation WHERE handle='@bait'"
        ).fetchone()
        self.assertEqual("watch", row["disposition"])
        self.assertEqual(2, row["strike_points"])

    def test_repeated_evidence_downranks(self):
        for index in (1, 2):
            add_evidence(
                self.conn,
                handle="bait",
                post_url=f"https://x.com/bait/status/{index}",
                reason="engagement_bait",
                confidence=0.9,
            )
        row = self.conn.execute(
            "SELECT disposition FROM account_reputation WHERE handle='@bait'"
        ).fetchone()
        self.assertEqual("downrank", row["disposition"])

    def test_blocked_account_is_not_exported(self):
        seed_blocklist(
            self.conn,
            [{"handle": "@spam", "disposition": "blocked", "operator_override": True}],
        )
        upsert_post(
            self.conn,
            {
                "post_id": "2",
                "url": "https://x.com/spam/status/2",
                "handle": "spam",
                "text": "spam",
                "score": 1,
                "decision": "keep",
            },
        )
        self.assertEqual([], export_feed(self.conn, 10))

    def test_capture_records_posts_and_account_signals(self):
        result = ingest_capture(
            self.conn,
            {
                "posts": [
                    {
                        "post_id": "3",
                        "url": "https://x.com/noise/status/3",
                        "handle": "noise",
                        "text": "You need to see this NOW",
                        "score": 0.1,
                        "decision": "discard",
                    }
                ],
                "account_signals": [
                    {
                        "handle": "noise",
                        "post_id": "3",
                        "post_url": "https://x.com/noise/status/3",
                        "reason": "engagement_bait",
                        "confidence": 0.9,
                    }
                ],
            },
        )
        self.assertEqual(1, result["posts_added"])
        disposition = self.conn.execute(
            "SELECT disposition FROM account_reputation WHERE handle='@noise'"
        ).fetchone()["disposition"]
        self.assertEqual("watch", disposition)

    def test_watchlist_penalty_changes_feed_order(self):
        for post_id, handle, score in (("4", "watched", 0.9), ("5", "normal", 0.8)):
            upsert_post(
                self.conn,
                {
                    "post_id": post_id,
                    "url": f"https://x.com/{handle}/status/{post_id}",
                    "handle": handle,
                    "text": "substantive",
                    "score": score,
                    "decision": "keep",
                },
            )
        set_account_disposition(self.conn, "watched", "watch")
        feed = export_feed(self.conn, 10)
        self.assertEqual("@normal", feed[0]["handle"])

    def test_same_post_has_one_record_and_many_scan_observations(self):
        post = {"post_id": "seen", "url": "https://x.com/alice/status/seen", "handle": "alice", "text": "a reusable result", "decision": "keep"}
        for scan_id, captured_at in (("scan-a", "2026-07-14T00:00:00Z"), ("scan-b", "2026-07-14T01:00:00Z")):
            ingest_capture(self.conn, {"scan_id": scan_id, "captured_at": captured_at, "host": "test", "source": "x-home", "posts": [post]})
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM posts").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT count(*) FROM runs").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT count(*) FROM post_observations").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT count(*) FROM sync_outbox").fetchone()[0])

    def test_reingesting_same_scan_is_idempotent(self):
        payload = {"scan_id": "one-scan", "captured_at": "2026-07-14T00:00:00Z", "host": "test", "posts": [
            {"post_id": "idem", "url": "https://x.com/a/status/idem", "handle": "a", "text": "idempotent"}
        ]}
        ingest_capture(self.conn, payload)
        ingest_capture(self.conn, payload)
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM runs").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM post_observations").fetchone()[0])
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM sync_outbox").fetchone()[0])

    def test_legacy_collector_metadata_is_normalized(self):
        result = ingest_capture(self.conn, {"captured_at": "2026-07-14T02:00:00Z", "host": "pi", "source": {"target": "https://x.com/home", "scrolls": 4}, "posts": []})
        run = self.conn.execute("SELECT source,target FROM runs WHERE scan_id=?", (result["scan_id"],)).fetchone()
        self.assertEqual("x-home", run["source"])
        self.assertEqual("https://x.com/home", run["target"])

    def test_pin_implies_save_and_search_uses_local_fts(self):
        upsert_post(self.conn, {"post_id": "paper", "url": "https://x.com/robot/status/paper", "handle": "robot", "author": "Robotics Lab", "text": "orbital manufacturing paper", "decision": "keep"})
        set_post_state(self.conn, "paper", pinned=True)
        state = self.conn.execute("SELECT saved_at,pinned_at FROM user_post_state WHERE post_id='paper'").fetchone()
        self.assertIsNotNone(state["saved_at"])
        self.assertIsNotNone(state["pinned_at"])
        self.assertEqual("paper", search_posts(self.conn, "manufacturing")[0]["post_id"])

    def test_curator_preferences_are_available_to_luna(self):
        preferences = {"instructions": "Prefer primary robotics papers", "topics": ["robotics", "orbital manufacturing"]}
        self.conn.execute("INSERT INTO site_state(key,value) VALUES('curator_preferences',?)", (json.dumps(preferences),))
        self.conn.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, cli_main(["--db", str(self.db_path), "curation"]))
        self.assertEqual(preferences, json.loads(output.getvalue()))



if __name__ == "__main__":
    unittest.main()
