from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from xradar.db import connect
from xradar.enrichment import next_batch, submit_batch as submit_enrichment_batch
from xradar.luna import LunaResult
from xradar.pipeline import _enrich, _refresh_query_packs
from xradar.planner import apply_preferences, topic_key


def components():
    return {
        name: {"score": .7, "rationale": f"Specific {name} assessment."}
        for name in ("novelty", "evidence", "relevance", "density", "importance")
    } | {"penalties": []}


class StatelessPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "var" / "inbox").mkdir(parents=True)
        self.conn = connect(self.root / "var" / "x-radar.sqlite")
        apply_preferences(self.conn, instructions="Prefer primary work", topics=["orbital manufacturing"], version=4)
        self.key = topic_key("orbital manufacturing")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_invalid_query_expansion_falls_back_to_validated_local_pack(self):
        bad = LunaResult({"queries": [
            {"kind": "canonical", "query": "orbital manufacturing latest research news"},
            {"kind": "technical", "query": "https://bad.example"},
            {"kind": "adjacent", "query": "foo:bar"},
        ]}, "inv", {}, 1)
        with patch("xradar.pipeline.expand_topic_queries", return_value=bad):
            result = _refresh_query_packs(self.conn, self.root)
        self.assertEqual("fallback:validation", result[0]["source"])
        self.assertEqual(3, len(result[0]["queries"]))
        row = self.conn.execute("SELECT query_pack_revision FROM topic_state WHERE topic_key=?", (self.key,)).fetchone()
        self.assertEqual(4, row["query_pack_revision"])

    def test_each_retry_receives_only_unaccepted_items(self):
        raw = self.root / "var" / "inbox" / "raw.json"
        output = self.root / "var" / "inbox" / "capture.json"
        raw.write_text(json.dumps({
            "scan_id": "scan", "captured_at": "2026-07-15T00:00:00Z", "source": "x-home",
            "posts": [{
                "post_id": str(index), "url": f"https://x.com/lab/status/{index}",
                "handle": "@lab", "text": f"Post {index}",
            } for index in range(3)],
        }))
        seen: list[list[str]] = []

        def fake_rank(conn, batch, root):
            ids = [post["post_id"] for post in batch["posts"]]
            seen.append(ids)
            results = [{
                "post_id": post_id, "topic_matches": [], "score_components": components(),
                "reasons": ["Concrete information."], "account_signals": [],
            } for post_id in ids]
            if len(seen) == 1:
                results = results[:1]
            return LunaResult({"batchId": batch["batchId"], "results": results}, "inv", {}, 1)

        with patch.dict("os.environ", {
            "XRADAR_LUNA_BATCH_ITEMS": "2", "XRADAR_LUNA_BATCH_CHARS": "20000",
            "XRADAR_LUNA_CONCURRENCY": "1",
        }), \
             patch("xradar.pipeline.rank_batch", side_effect=fake_rank):
            result = _enrich(self.conn, self.root, raw, output)
        self.assertEqual(["0", "1"], seen[0])
        self.assertEqual(["1", "2"], seen[1])
        self.assertNotIn("0", seen[1])
        self.assertEqual(3, result["posts"])
        self.assertTrue(output.exists())

    def test_two_batches_rank_concurrently(self):
        raw = self.root / "var" / "inbox" / "raw-concurrent.json"
        output = self.root / "var" / "inbox" / "capture-concurrent.json"
        raw.write_text(json.dumps({
            "scan_id": "concurrent", "captured_at": "2026-07-15T00:00:00Z", "source": "x-home",
            "posts": [{
                "post_id": str(index), "url": f"https://x.com/lab/status/{index}",
                "handle": "@lab", "text": f"Post {index}",
            } for index in range(4)],
        }))
        barrier = threading.Barrier(2, timeout=3)
        thread_names: set[str] = set()

        def fake_rank(conn, batch, root):
            thread_names.add(threading.current_thread().name)
            barrier.wait()
            return LunaResult({
                "batchId": batch["batchId"],
                "results": [{
                    "post_id": post["post_id"], "topic_matches": [],
                    "score_components": components(), "reasons": ["Concrete information."],
                    "account_signals": [],
                } for post in batch["posts"]],
            }, "inv", {}, 1)

        with patch.dict("os.environ", {
            "XRADAR_LUNA_BATCH_ITEMS": "2", "XRADAR_LUNA_BATCH_CHARS": "20000",
            "XRADAR_LUNA_CONCURRENCY": "2",
        }), patch("xradar.pipeline.rank_batch", side_effect=fake_rank):
            result = _enrich(self.conn, self.root, raw, output)
        self.assertEqual(4, result["posts"])
        self.assertEqual(2, len(thread_names))

    def test_faster_parallel_result_is_checkpointed_before_slower_lease(self):
        raw = self.root / "var" / "inbox" / "raw-completion-order.json"
        output = self.root / "var" / "inbox" / "capture-completion-order.json"
        raw.write_text(json.dumps({
            "scan_id": "completion-order", "captured_at": "2026-07-15T00:00:00Z",
            "source": "x-home", "posts": [{
                "post_id": str(index), "url": f"https://x.com/lab/status/{index}",
                "handle": "@lab", "text": f"Post {index}",
            } for index in range(4)],
        }))
        faster_submitted = threading.Event()
        def fake_rank(conn, batch, root):
            ids = [post["post_id"] for post in batch["posts"]]
            if ids[0] == "0" and not faster_submitted.wait(timeout=2):
                raise RuntimeError("faster batch was not checkpointed")
            return LunaResult({
                "batchId": batch["batchId"],
                "results": [{
                    "post_id": post_id, "topic_matches": [], "score_components": components(),
                    "reasons": ["Concrete information."], "account_signals": [],
                } for post_id in ids],
            }, "inv", {}, 1)

        def observing_submit(conn, scan_id, payload):
            result = submit_enrichment_batch(conn, scan_id, payload)
            if any(item["post_id"] == "2" for item in payload["results"]):
                faster_submitted.set()
            return result

        with patch.dict("os.environ", {
            "XRADAR_LUNA_BATCH_ITEMS": "2", "XRADAR_LUNA_BATCH_CHARS": "20000",
            "XRADAR_LUNA_CONCURRENCY": "2",
        }), patch("xradar.pipeline.rank_batch", side_effect=fake_rank), \
             patch("xradar.pipeline.submit_batch", side_effect=observing_submit):
            result = _enrich(self.conn, self.root, raw, output)
        self.assertEqual(4, result["posts"])


if __name__ == "__main__":
    unittest.main()
