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
    normalize_score_components,
    purge_account,
    search_posts,
    seed_blocklist,
    set_account_disposition,
    set_post_state,
    upsert_post,
)
from xradar.cli import main as cli_main
from xradar.planner import apply_preferences, topic_key


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

    def test_score_breakdown_is_validated_recalculated_and_stored_per_observation(self):
        components = {
            "novelty": {"score": .9, "rationale": "New result."},
            "evidence": {"score": .8, "rationale": "Links the paper."},
            "relevance": {"score": .7, "rationale": "Matches robotics."},
            "density": {"score": .6, "rationale": "Includes method and result."},
            "importance": {"score": .5, "rationale": "May affect deployment."},
            "penalties": [{"kind": "unsupported_certainty", "amount": .05, "rationale": "One extrapolation is unsupported."}],
        }
        normalized, score = normalize_score_components(components)
        self.assertAlmostEqual(.70, score)
        self.assertEqual(.30, normalized["novelty"]["weight"])
        ingest_capture(self.conn, {
            "scan_id": "scored", "captured_at": "2026-07-15T00:00:00Z", "host": "test",
            "posts": [{
                "post_id": "scored-post", "url": "https://x.com/lab/status/scored-post",
                "handle": "lab", "text": "A result", "score": .99, "decision": "keep",
                "score_components": components,
            }],
        })
        post = self.conn.execute("SELECT score,score_components_json FROM posts WHERE post_id='scored-post'").fetchone()
        observation = self.conn.execute("SELECT score,score_components_json FROM post_observations").fetchone()
        self.assertAlmostEqual(.70, post["score"])
        self.assertAlmostEqual(.70, observation["score"])
        self.assertEqual("New result.", json.loads(observation["score_components_json"])["novelty"]["rationale"])
        queued = json.loads(self.conn.execute("SELECT payload_json FROM sync_outbox").fetchone()["payload_json"])
        self.assertAlmostEqual(.70, queued["posts"][0]["score"])

    def test_incomplete_score_breakdown_is_rejected_but_legacy_capture_remains_valid(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            normalize_score_components({"novelty": {"score": .5, "rationale": "New."}})
        ingest_capture(self.conn, {
            "scan_id": "legacy-score", "posts": [{
                "post_id": "legacy-score", "url": "https://x.com/a/status/legacy-score",
                "handle": "a", "text": "legacy", "score": .4,
            }],
        })
        self.assertIsNone(self.conn.execute("SELECT score_components_json FROM posts").fetchone()[0])

    def test_direct_topic_match_calibrates_only_the_relevance_component(self):
        components = {
            "novelty": {"score": .6, "rationale": "New policy proposal."},
            "evidence": {"score": .7, "rationale": "Primary source for the author's position."},
            "relevance": {"score": .4, "rationale": "Discusses AI."},
            "density": {"score": .7, "rationale": "Contains a concrete framework."},
            "importance": {"score": .8, "rationale": "Could influence frontier AI governance."},
            "penalties": [],
        }
        ingest_capture(self.conn, {
            "scan_id": "topic-calibration", "captured_at": "2026-07-15T00:00:00Z", "host": "test",
            "posts": [{
                "post_id": "topic-post", "url": "https://x.com/lab/status/topic-post",
                "handle": "lab", "text": "A frontier AI standards framework", "decision": "keep",
                "topic_matches": [{"topic_key": "ai", "topic": "AI research", "confidence": .95}],
                "score_components": components,
            }],
        })
        row = self.conn.execute(
            "SELECT score,score_components_json FROM posts WHERE post_id='topic-post'"
        ).fetchone()
        stored = json.loads(row["score_components_json"])
        self.assertAlmostEqual(.95, stored["relevance"]["score"])
        self.assertIn("Direct active-topic match", stored["relevance"]["rationale"])
        self.assertAlmostEqual(.73, row["score"])

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
            "xcancel_url": "https://xcancel.com/researcher/status/2076422557180608888",
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

    def test_purge_account_removes_posts_observations_memory_and_queues_sync(self):
        apply_preferences(self.conn, instructions="", topics=["ai"], version=1)
        key = topic_key("ai")
        ingest_capture(self.conn, {
            "scan_id": "purge-scan", "captured_at": "2026-07-14T00:00:00Z", "host": "test",
            "posts": [{"post_id": "purge-me", "url": "https://x.com/alice/status/purge-me", "handle": "alice", "text": "article"}],
        })
        self.conn.execute(
            "INSERT INTO topic_account_memory(topic_key,handle,last_observed_at) VALUES(?,?,?)",
            (key, "@alice", "2026-07-14T00:00:00Z"),
        )
        before = self.conn.execute("SELECT count(*) FROM sync_outbox").fetchone()[0]
        result = purge_account(self.conn, "Alice")
        self.assertEqual(1, result["posts_purged"])
        self.assertEqual(1, result["observations_purged"])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM posts WHERE handle='@alice'").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM post_observations").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM topic_account_memory WHERE handle='@alice'").fetchone()[0])
        self.assertEqual(before + 1, self.conn.execute("SELECT count(*) FROM sync_outbox").fetchone()[0])

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
        result = json.loads(output.getvalue())
        self.assertEqual(preferences["instructions"], result["instructions"])
        self.assertEqual(preferences["topics"], result["topics"])
        self.assertEqual(0, result["version"])

    def test_v2_capture_preserves_preference_and_multi_source_provenance(self):
        apply_preferences(self.conn, instructions="", topics=["robotics"], version=7)
        key = topic_key("robotics")
        payload = {
            "schema_version": 2, "scan_id": "period-7", "period_id": "period-7",
            "preference_version": 7, "target_unique": 150, "captured_at": "2026-07-14T03:00:00Z",
            "host": "test", "source": "x-mixed",
            "acquisitions": [
                {"id": "home-1", "kind": "home", "url": "https://x.com/home", "quota": 60, "observed": 1, "unique": 1},
                {"id": "search-1", "kind": "topic_search", "url": "https://x.com/search?q=robotics", "topic_key": key, "topic": "robotics", "quota": 45, "observed": 1, "unique": 0},
            ],
            "posts": [{
                "post_id": "v2", "url": "https://x.com/lab/status/v2", "handle": "lab",
                "text": "robotics result", "score": .8, "decision": "keep",
                "topic_matches": [{"topic_key": key, "topic": "robotics", "confidence": .9}],
                "discovery_sources": [{"acquisition_id": "home-1"}, {"acquisition_id": "search-1"}],
            }],
        }
        ingest_capture(self.conn, payload)
        run = self.conn.execute("SELECT source,preference_version,target_unique FROM runs WHERE scan_id='period-7'").fetchone()
        self.assertEqual(("x-mixed", 7, 150), tuple(run))
        self.assertEqual(2, self.conn.execute("SELECT count(*) FROM run_acquisitions").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT count(*) FROM observation_acquisitions").fetchone()[0])
        memory = self.conn.execute("SELECT relevant_observations,kept_posts,status FROM topic_account_memory WHERE topic_key=? AND handle='@lab'", (key,)).fetchone()
        self.assertEqual((1, 1, "candidate"), tuple(memory))

    def test_repeated_topic_observations_promote_account_without_recurating_history(self):
        apply_preferences(self.conn, instructions="", topics=["cooking"], version=1)
        key = topic_key("cooking")
        for index, score in enumerate((.7, .68, .67), 1):
            ingest_capture(self.conn, {
                "schema_version": 2, "scan_id": f"cook-{index}", "preference_version": 1,
                "captured_at": f"2026-07-14T0{index}:00:00Z", "source": "x-mixed",
                "posts": [{"post_id": f"recipe-{index}", "url": f"https://x.com/chef/status/{index}",
                  "handle": "chef", "text": "technique", "score": score, "decision": "keep",
                  "topic_matches": [{"topic_key": key, "confidence": .9}]}],
            })
        row = self.conn.execute("SELECT relevant_observations,kept_posts,status FROM topic_account_memory WHERE topic_key=? AND handle='@chef'", (key,)).fetchone()
        self.assertEqual((3, 3, "known"), tuple(row))
        scores = [row[0] for row in self.conn.execute("SELECT score FROM post_observations ORDER BY id")]
        self.assertEqual([.7, .68, .67], scores)



if __name__ == "__main__":
    unittest.main()
