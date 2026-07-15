import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from xradar.db import connect
from xradar.enrichment import (
    active_batches,
    claim_batch,
    fail_active_batch,
    finalize_job,
    next_batch,
    start_job,
    submit_batch,
)
from xradar.planner import apply_preferences, topic_key


def score_components(relevance: float = .8):
    return {
        "novelty": {"score": .8, "rationale": "Reports a concrete new result."},
        "evidence": {"score": .7, "rationale": "Provides a primary artifact or attributable source."},
        "relevance": {"score": relevance, "rationale": "Directly matches the active topic."},
        "density": {"score": .6, "rationale": "Includes useful method and result details."},
        "importance": {"score": .7, "rationale": "Could affect future research decisions."},
        "penalties": [],
    }


class EnrichmentJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "radar.sqlite")
        apply_preferences(self.conn, instructions="Prefer primary work", topics=["robotics"], version=3)
        self.key = topic_key("robotics")
        self.raw = self.root / "raw-period.json"
        self.output = self.root / "capture-period.json"
        self.raw.write_text(json.dumps({
            "schema_version": 2,
            "scan_id": "period",
            "period_id": "period",
            "preference_version": 3,
            "source": "x-mixed",
            "targets": [{
                "id": "search", "kind": "topic_search", "url": "https://x.com/search?q=robotics",
                "quota": 3, "topic_key": self.key, "topic": "robotics",
            }],
            "backfill_targets": [],
            "posts": [{
                "post_id": str(index), "url": f"https://x.com/lab/status/{index}",
                "handle": "@lab", "text": f"Original post {index}",
                "discovery_sources": [{"acquisition_id": "search"}],
            } for index in range(3)],
        }))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def result(self, post_id: str):
        return {
            "post_id": post_id,
            "topic_matches": [{"topic_key": self.key, "confidence": .9}],
            "score_components": score_components(.5),
            "reasons": ["Substantive active-topic result."],
            "account_signals": [],
        }

    def test_valid_items_checkpoint_and_only_failed_or_pending_items_retry(self):
        start = start_job(self.conn, self.raw, output=self.output, max_items=2, max_chars=20_000)
        self.assertEqual(3, start["total"])
        first = next_batch(self.conn, "period")
        self.assertEqual(["0", "1"], [post["post_id"] for post in first["posts"]])
        partial = submit_batch(self.conn, "period", {
            "batchId": first["batchId"], "results": [self.result("0")],
        })
        self.assertEqual(1, partial["accepted"])
        self.assertEqual(1, partial["failed"])
        second = next_batch(self.conn, "period")
        self.assertEqual(["1"], [post["post_id"] for post in second["posts"]])
        self.assertNotIn("0", [post["post_id"] for post in second["posts"]])
        accepted = submit_batch(self.conn, "period", {
            "batchId": second["batchId"], "results": [self.result("1")],
        })
        self.assertEqual(2, accepted["accepted"])
        third = next_batch(self.conn, "period")
        self.assertEqual(["2"], [post["post_id"] for post in third["posts"]])
        submit_batch(self.conn, "period", {
            "batchId": third["batchId"], "results": [self.result("2")],
        })
        self.assertTrue(next_batch(self.conn, "period")["ready"])

        final = finalize_job(self.conn, "period")
        self.assertEqual(3, final["posts"])
        capture = json.loads(self.output.read_text())
        self.assertEqual(["Original post 0", "Original post 1", "Original post 2"], [p["text"] for p in capture["posts"]])
        self.assertEqual(["keep", "keep", "keep"], [p["decision"] for p in capture["posts"]])
        self.assertTrue(all(p["score_components"]["relevance"]["score"] == .9 for p in capture["posts"]))

    def test_unknown_topic_key_is_rejected_without_losing_other_results(self):
        start_job(self.conn, self.raw, output=self.output, max_items=2, max_chars=20_000)
        batch = next_batch(self.conn, "period")
        invalid = self.result("1")
        invalid["topic_matches"] = [{"topic_key": "invented", "confidence": .9}]
        result = submit_batch(self.conn, "period", {
            "batchId": batch["batchId"], "results": [self.result("0"), invalid],
        })
        self.assertEqual(1, result["acceptedThisBatch"])
        self.assertEqual("1", result["errors"][0]["post_id"])
        self.assertIn("unknown topic_key", result["errors"][0]["error"])

    def test_duplicate_and_unexpected_ids_are_reconciled_exactly_once(self):
        start_job(self.conn, self.raw, output=self.output, max_items=2, max_chars=20_000)
        batch = next_batch(self.conn, "period")
        result = submit_batch(self.conn, "period", {
            "batchId": batch["batchId"],
            "results": [self.result("0"), self.result("0"), self.result("not-requested")],
        })
        self.assertEqual(0, result["acceptedThisBatch"])
        self.assertEqual({"0", "1"}, {item["post_id"] for item in result["errors"]})
        self.assertEqual(1, result["warningCount"])
        retry = next_batch(self.conn, "period")
        self.assertEqual(["0"], [post["post_id"] for post in retry["posts"]])

    def test_invalid_optional_signal_is_dropped_without_rejecting_ranking(self):
        start_job(self.conn, self.raw, output=self.output, max_items=1, max_chars=20_000)
        batch = next_batch(self.conn, "period")
        ranked = self.result("0")
        ranked["account_signals"] = [{
            "handle": "@someone_else", "reason": "not-supported", "confidence": 2,
        }]
        result = submit_batch(self.conn, "period", {
            "batchId": batch["batchId"], "results": [ranked],
        })
        self.assertEqual(1, result["acceptedThisBatch"])
        self.assertEqual(1, result["warningCount"])
        row = self.conn.execute(
            "SELECT enrichment_json,warnings_json FROM enrichment_items WHERE scan_id='period' AND post_id='0'"
        ).fetchone()
        self.assertEqual([], json.loads(row["enrichment_json"])["account_signals"])
        self.assertIn("dropped optional", json.loads(row["warnings_json"])[0])

    def test_retry_batches_shrink_to_single_items_after_repeated_timeouts(self):
        start_job(
            self.conn, self.raw, output=self.output, max_items=3, max_chars=20_000,
            max_attempts=3,
        )
        first = next_batch(self.conn, "period")
        self.assertEqual(3, len(first["posts"]))
        fail_active_batch(self.conn, "period", "timeout", batch_id=first["batchId"])
        second = next_batch(self.conn, "period")
        self.assertEqual(2, len(second["posts"]))
        fail_active_batch(self.conn, "period", "timeout", batch_id=second["batchId"])
        third = next_batch(self.conn, "period")
        self.assertEqual(1, len(third["posts"]))

    def test_permanent_single_item_failure_finalizes_partial_without_data_loss(self):
        start_job(
            self.conn, self.raw, output=self.output, max_items=3, max_chars=20_000,
            max_attempts=1,
        )
        batch = next_batch(self.conn, "period")
        submit_batch(self.conn, "period", {
            "batchId": batch["batchId"], "results": [self.result("0"), self.result("1")],
        })
        ready = next_batch(self.conn, "period")
        self.assertEqual("partial_ready", ready["status"])
        final = finalize_job(self.conn, "period")
        self.assertEqual("partial", final["status"])
        self.assertEqual(1, final["failedPosts"])
        capture = json.loads(self.output.read_text())
        self.assertEqual(["0", "1", "2"], [post["post_id"] for post in capture["posts"]])
        self.assertEqual("failed", capture["posts"][2]["enrichment_status"])
        self.assertEqual("partial", capture["enrichment"]["status"])

    def test_active_lease_is_resumable_after_reopening_database(self):
        start_job(self.conn, self.raw, output=self.output, max_items=1, max_chars=20_000)
        batch = next_batch(self.conn, "period")
        self.conn.close()
        self.conn = connect(self.root / "radar.sqlite")
        resumed = active_batches(self.conn, "period")
        self.assertEqual([batch["batchId"]], [item["batchId"] for item in resumed])

    def test_legacy_job_tables_migrate_without_rewriting_existing_rows(self):
        legacy_path = self.root / "legacy.sqlite"
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript("""
            CREATE TABLE enrichment_jobs (
              scan_id TEXT PRIMARY KEY,raw_path TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
              output_path TEXT NOT NULL,ranking_version TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','running','ready','complete')),
              total_items INTEGER NOT NULL,batch_max_items INTEGER NOT NULL,
              batch_max_chars INTEGER NOT NULL,preference_json TEXT NOT NULL,
              allowed_topics_json TEXT NOT NULL,last_error TEXT,created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,completed_at TEXT
            );
            CREATE TABLE enrichment_items (
              scan_id TEXT NOT NULL REFERENCES enrichment_jobs(scan_id),post_id TEXT NOT NULL,
              observed_index INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','in_progress','failed','accepted')),
              batch_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,enrichment_json TEXT,
              last_error TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(scan_id,post_id),
              UNIQUE(scan_id,observed_index)
            );
            INSERT INTO enrichment_jobs VALUES(
              'legacy','raw.json','digest','capture.json','v1','running',1,24,60000,
              '{}','{}','timeout','2026-07-15','2026-07-15',NULL
            );
            INSERT INTO enrichment_items VALUES(
              'legacy','42',0,'failed',NULL,2,NULL,'timeout','2026-07-15'
            );
        """)
        legacy.commit()
        legacy.close()
        migrated = connect(legacy_path)
        try:
            job = migrated.execute("SELECT state,max_attempts FROM enrichment_jobs").fetchone()
            item = migrated.execute("SELECT state,attempts,last_error FROM enrichment_items").fetchone()
            self.assertEqual("running", job["state"])
            self.assertEqual(3, job["max_attempts"])
            self.assertEqual(("retry", 2, "timeout"), tuple(item))
        finally:
            migrated.close()

    def test_start_is_idempotent_but_rejects_changed_raw_capture(self):
        first = start_job(self.conn, self.raw, output=self.output)
        second = start_job(self.conn, self.raw, output=self.output)
        self.assertEqual(first["scanId"], second["scanId"])
        payload = json.loads(self.raw.read_text())
        payload["posts"][0]["text"] = "changed"
        self.raw.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "different raw capture"):
            start_job(self.conn, self.raw, output=self.output)

    def test_parallel_leases_submit_and_fail_independently(self):
        start_job(self.conn, self.raw, output=self.output, max_items=1, max_chars=20_000)
        first = claim_batch(self.conn, "period")
        second = claim_batch(self.conn, "period")
        self.assertNotEqual(first["batchId"], second["batchId"])
        self.assertEqual(2, len(active_batches(self.conn, "period")))

        submit_batch(self.conn, "period", {
            "batchId": first["batchId"], "results": [self.result(first["posts"][0]["post_id"])],
        })
        active = active_batches(self.conn, "period")
        self.assertEqual([second["batchId"]], [batch["batchId"] for batch in active])

        failed = fail_active_batch(
            self.conn, "period", "one Luna call timed out", batch_id=second["batchId"],
        )
        self.assertEqual(1, failed["accepted"])
        self.assertEqual(1, failed["failed"])
        self.assertEqual(0, failed["inProgress"])


if __name__ == "__main__":
    unittest.main()
