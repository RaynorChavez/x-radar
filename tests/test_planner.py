import json
import tempfile
import unittest
from pathlib import Path

from xradar.db import connect
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
        values = set_query_pack(self.conn, "category theory", ["category theory", "higher categories"], 1)
        self.assertEqual(["category theory", "higher categories"], values)
        self.assertIn("f=live", search_url(values[0]))
        with self.assertRaises(ValueError):
            safe_query("https://example.com")

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
