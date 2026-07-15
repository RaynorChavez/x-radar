import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from xradar.db import connect
from xradar.cli import main as cli_main
from xradar.planner import (
    MIXED_TARGET, SOURCE_BUDGETS, apply_preferences, build_period_plan,
    safe_query, search_url, set_query_pack, validate_x_url,
)


class PeriodPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "radar.sqlite")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_no_topics_collects_150_from_home(self):
        plan = build_period_plan(self.conn)
        self.assertEqual(MIXED_TARGET, plan["target_unique"])
        self.assertEqual([("home", 150)], [(item["kind"], item["quota"]) for item in plan["targets"]])

    def test_topic_plan_uses_60_75_15_when_known_pool_is_empty(self):
        apply_preferences(self.conn, instructions="", topics=["orbital manufacturing"], version=1)
        plan = build_period_plan(self.conn)
        totals = {}
        for target in plan["targets"]:
            totals[target["kind"]] = totals.get(target["kind"], 0) + target["quota"]
        self.assertEqual(60, totals["home"])
        self.assertEqual(75, totals["topic_search"])
        self.assertEqual(15, sum(value for key, value in totals.items() if key.startswith("explore_")))
        self.assertEqual(MIXED_TARGET, sum(item["quota"] for item in plan["targets"]))

    def test_known_accounts_restore_normal_split_and_exclude_watch(self):
        result = apply_preferences(self.conn, instructions="", topics=["robotics"], version=2)
        key = self.conn.execute("SELECT topic_key FROM topic_state WHERE label='robotics'").fetchone()[0]
        for handle, disposition in (("@good", "normal"), ("@watch", "watch")):
            now = "2026-07-14T00:00:00Z"
            self.conn.execute("INSERT INTO account_reputation(handle,disposition,first_seen_at,last_seen_at) VALUES(?,?,?,?)", (handle, disposition, now, now))
            self.conn.execute("INSERT INTO topic_account_memory(topic_key,handle,relevant_observations,kept_posts,score_sum,confidence,status,last_observed_at) VALUES(?,?,?,?,?,?,?,?)", (key, handle, 3, 2, 2.1, .8, "known", now))
        plan = build_period_plan(self.conn)
        known = [item for item in plan["targets"] if item["kind"] == "known_account"]
        self.assertEqual(["@good"], [item["handle"] for item in known])
        self.assertEqual(SOURCE_BUDGETS["known_account"], sum(item["quota"] for item in known))
        self.assertEqual(result["added"], ["robotics"])

    def test_query_pack_is_validated_and_cached(self):
        apply_preferences(self.conn, instructions="", topics=["category theory"], version=1)
        values = set_query_pack(self.conn, "category theory", [
            '"category theory"',
            '("category theory" OR "higher categories") (paper OR result)',
        ], 1)
        self.assertEqual([
            {"kind": "canonical", "query": '"category theory"'},
            {"kind": "technical", "query": '("category theory" OR "higher categories") (paper OR result)'},
        ], values)
        self.assertIn("f=live", search_url(values[0]["query"]))
        with self.assertRaises(ValueError):
            safe_query("https://example.com")

    def test_query_contract_accepts_or_groups_and_rejects_keyword_bags(self):
        self.assertEqual(
            '(AI OR AGI OR ASI) (research OR paper OR benchmark)',
            safe_query('(AI OR AGI OR ASI) (research OR paper OR benchmark)'),
        )
        for invalid in (
            "AI scaling laws mechanistic interpretability",
            "AI or AGI",
            "(AI OR AGI",
            '"artificial intelligence',
            "AI OR",
            "from:demishassabis AI",
        ):
            with self.subTest(query=invalid), self.assertRaises(ValueError):
                safe_query(invalid)

    def test_fallback_pack_compiles_slash_topics_to_x_native_queries(self):
        apply_preferences(self.conn, instructions="", topics=["AI / AGI / ASI research"], version=1)
        row = self.conn.execute("SELECT query_pack_json FROM topic_state WHERE active=1").fetchone()
        self.assertEqual([
            {"kind": "canonical", "query": "(AI OR AGI OR ASI)"},
            {"kind": "technical", "query": "(AI OR AGI OR ASI) (research OR paper OR benchmark)"},
            {"kind": "adjacent", "query": "(AI OR AGI OR ASI) (findings OR analysis OR result)"},
        ], json.loads(row["query_pack_json"]))

    def test_query_pack_cli_accepts_structured_file_without_shell_interpolation(self):
        apply_preferences(self.conn, instructions="", topics=["robotics"], version=4)
        pack = Path(self.temp.name) / "pack.json"
        pack.write_text(json.dumps({"queries": [
            {"kind": "canonical", "query": "robotics"},
            {"kind": "technical", "query": "robotics (manipulation OR benchmark)"},
            {"kind": "adjacent", "query": "robotics (embodied OR experiment)"},
        ]}))
        self.conn.commit()
        self.conn.close()
        output = io.StringIO()
        with redirect_stdout(output):
            cli_main([
                "--db", str(Path(self.temp.name) / "radar.sqlite"), "topic-queries-set",
                "robotics", "--revision", "4", "--pack-file", str(pack),
            ])
        self.conn = connect(Path(self.temp.name) / "radar.sqlite")
        result = json.loads(output.getvalue())
        self.assertEqual(["canonical", "technical", "adjacent"], [item["kind"] for item in result["queries"]])

    def test_query_refresh_cli_marks_active_packs_stale(self):
        apply_preferences(self.conn, instructions="", topics=["robotics", "cooking"], version=4)
        self.conn.execute("UPDATE topic_state SET query_pack_revision=4")
        self.conn.commit()
        self.conn.close()
        output = io.StringIO()
        with redirect_stdout(output):
            cli_main([
                "--db", str(Path(self.temp.name) / "radar.sqlite"), "topic-queries-refresh",
                "--topic", "robotics",
            ])
        self.conn = connect(Path(self.temp.name) / "radar.sqlite")
        self.assertEqual(1, json.loads(output.getvalue())["refreshed"])
        revisions = {
            row["label"]: row["query_pack_revision"]
            for row in self.conn.execute("SELECT label,query_pack_revision FROM topic_state")
        }
        self.assertEqual({"robotics": -1, "cooking": 4}, revisions)

    def test_structured_queries_are_distinct_across_primary_exploration_and_backfill(self):
        apply_preferences(self.conn, instructions="", topics=["AI / AGI / ASI research"], version=1)
        set_query_pack(self.conn, "AI / AGI / ASI research", [
            {"kind": "canonical", "query": "(AI OR AGI OR ASI)"},
            {"kind": "technical", "query": "(AGI OR ASI) (evals OR benchmark)"},
            {"kind": "adjacent", "query": '("frontier model" OR AGI) (capabilities OR paper)'},
        ], 1)
        plan = build_period_plan(self.conn)
        searches = [item for item in [*plan["targets"], *plan["backfill_targets"]] if item.get("query")]
        queries = [item["query"] for item in searches]
        self.assertEqual(len(queries), len(set(queries)))
        self.assertNotIn("AI / AGI / ASI research", queries)

    def test_failed_query_is_cooled_and_not_repeated_in_next_plan(self):
        apply_preferences(self.conn, instructions="", topics=["robotics"], version=1)
        key = self.conn.execute("SELECT topic_key FROM topic_state WHERE label='robotics'").fetchone()[0]
        set_query_pack(self.conn, "robotics", [
            {"kind": "canonical", "query": "robotics"},
            {"kind": "technical", "query": "robotics (manipulation OR benchmark)"},
            {"kind": "adjacent", "query": "robotics (embodied OR experiment)"},
        ], 1)
        from xradar.db import ingest_capture
        ingest_capture(self.conn, {
            "scan_id": "empty-query", "captured_at": "2026-07-15T00:00:00Z", "source": "x-mixed",
            "acquisitions": [{
                "id": "failed", "kind": "topic_search", "url": "https://x.com/search?q=robotics",
                "topic_key": key, "topic": "robotics", "query_kind": "canonical",
                "query": "robotics", "status": "error", "observed": 0, "unique": 0,
            }],
            "posts": [],
        })
        plan = build_period_plan(self.conn)
        self.assertNotIn(
            "robotics",
            [item.get("query") for item in [*plan["targets"], *plan["backfill_targets"]]],
        )
        memory = self.conn.execute(
            "SELECT attempts,consecutive_empty,cooldown_until FROM topic_query_memory WHERE topic_key=?",
            (key,),
        ).fetchone()
        self.assertEqual((1, 1), (memory["attempts"], memory["consecutive_empty"]))
        self.assertIsNotNone(memory["cooldown_until"])

    def test_only_read_only_x_targets_are_accepted(self):
        self.assertEqual("https://x.com/home", validate_x_url("https://x.com/home"))
        self.assertEqual("https://x.com/user/with_replies", validate_x_url("https://x.com/user/with_replies"))
        with self.assertRaises(ValueError):
            validate_x_url("https://example.com/home")
        with self.assertRaises(ValueError):
            validate_x_url("https://x.com/settings/account")

    def test_preference_revisions_preserve_removed_topic_memory(self):
        apply_preferences(self.conn, instructions="first", topics=["cooking"], version=1)
        result = apply_preferences(self.conn, instructions="second", topics=[], version=2)
        row = self.conn.execute("SELECT active FROM topic_state WHERE label='cooking'").fetchone()
        self.assertEqual(0, row["active"])
        self.assertTrue(result["removed"])
        snapshot = json.loads(self.conn.execute("SELECT value FROM site_state WHERE key='curator_preferences'").fetchone()[0])
        self.assertEqual(2, snapshot["version"])

    def test_topic_rotation_prevents_starvation_after_six_topics(self):
        topics = [f"topic {index}" for index in range(7)]
        apply_preferences(self.conn, instructions="", topics=topics, version=1)
        first = build_period_plan(self.conn)
        second = build_period_plan(self.conn)
        first_topics = {item.get("topic") for item in first["targets"] if item.get("topic")}
        second_topics = {item.get("topic") for item in second["targets"] if item.get("topic")}
        self.assertEqual(6, len(first_topics))
        self.assertIn((set(topics) - first_topics).pop(), second_topics)

    def test_bootstrap_reserves_topical_budget_for_new_topics(self):
        apply_preferences(self.conn, instructions="", topics=["technology", "cooking"], version=2)
        plan = build_period_plan(self.conn, bootstrap_topics=["cooking"])
        topical = [item for item in plan["targets"] if item["kind"] != "home"]
        self.assertEqual({"cooking"}, {item.get("topic") for item in topical})
        self.assertEqual(90, sum(item["quota"] for item in topical))


if __name__ == "__main__":
    unittest.main()
