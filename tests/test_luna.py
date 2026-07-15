from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xradar.db import connect
from xradar.luna import RANKING_SCHEMA, invoke


class LunaRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "radar.sqlite")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_fresh_read_only_schema_constrained_call_records_usage(self):
        def fake_run(command, **kwargs):
            response = Path(command[command.index("-o") + 1])
            response.write_text(json.dumps({"batchId": "batch", "results": []}))
            self.assertEqual('{"batchId": "batch", "posts": []}', kwargs["input"])
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({"type": "turn.completed", "usage": {
                    "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50,
                }}) + "\n",
                stderr="",
            )

        with patch("xradar.luna.subprocess.run", side_effect=fake_run) as run:
            result = invoke(
                self.conn, {"batchId": "batch", "posts": []}, schema=RANKING_SCHEMA,
                prompt="rank", purpose="ranking_batch", runtime_root=self.root,
                scan_id="scan", batch_id="batch",
            )
        command = run.call_args.args[0]
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertEqual("gpt-5.6-luna", command[command.index("-m") + 1])
        self.assertIn("model_reasoning_effort='medium'", command)
        self.assertEqual(250, result.usage["billable_tokens"])
        row = self.conn.execute("SELECT * FROM luna_invocations").fetchone()
        self.assertEqual((1000, 800, 50, 250, "complete"), (
            row["input_tokens"], row["cached_input_tokens"], row["output_tokens"],
            row["billable_tokens"], row["status"],
        ))
        self.assertEqual("medium", row["reasoning_effort"])
        self.assertEqual([], list((self.root / "var" / "luna").iterdir()))

    def test_timeout_is_durable_and_does_not_leave_batch_files(self):
        with patch("xradar.luna.subprocess.run", side_effect=subprocess.TimeoutExpired(["codex"], 1)):
            with self.assertRaisesRegex(RuntimeError, "exceeded"):
                invoke(
                    self.conn, {}, schema=RANKING_SCHEMA, prompt="rank", purpose="ranking_batch",
                    runtime_root=self.root, scan_id="scan", batch_id="batch", timeout_seconds=1,
                )
        row = self.conn.execute("SELECT status,error FROM luna_invocations").fetchone()
        self.assertEqual("timeout", row["status"])
        self.assertEqual([], list((self.root / "var" / "luna").iterdir()))


if __name__ == "__main__":
    unittest.main()
