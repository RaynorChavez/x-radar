import json
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
        self.assertEqual(["1", "2"], [post["post_id"] for post in second["posts"]])
        self.assertNotIn("0", [post["post_id"] for post in second["posts"]])
        accepted = submit_batch(self.conn, "period", {
            "batchId": second["batchId"], "results": [self.result("1"), self.result("2")],
        })
        self.assertEqual(3, accepted["accepted"])
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
