import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collector.firefox_collect import (
    EXTRACT_POST, _XCancelArticleParser, collect_target, hydrate_post_details, legacy_kind, load_plan, parse_args,
    validate_post_detail_target, validate_target, validate_xcancel_article_target,
)


class FakeDriver:
    def __init__(self, pages):
        self.pages = pages
        self.current_url = ""

    def get(self, url):
        self.current_url = url

    def find_elements(self, _by, selector):
        if selector == 'article[data-testid="tweet"]':
            return self.pages.get(self.current_url, [])
        return [object()]

    def execute_script(self, script, *args):
        if args and isinstance(args[0], dict):
            return dict(args[0])
        return None


class DelayedTimelineDriver(FakeDriver):
    def __init__(self, pages, empty_checks=2):
        super().__init__(pages)
        self.empty_checks = empty_checks

    def find_elements(self, _by, selector):
        if selector == 'article[data-testid="tweet"]' and self.empty_checks:
            self.empty_checks -= 1
            return []
        return super().find_elements(_by, selector)


class CollectorContractTests(unittest.TestCase):
    def test_browser_binary_paths_can_be_configured_for_a_service_user(self):
        with patch.dict(
            "os.environ",
            {"XRADAR_GECKODRIVER": "/opt/x-radar/geckodriver", "XRADAR_FIREFOX": "/opt/firefox"},
        ), patch("sys.argv", ["firefox_collect.py", "--output", "capture.json"]):
            arguments = parse_args()
        self.assertEqual("/opt/x-radar/geckodriver", arguments.geckodriver)
        self.assertEqual("/opt/firefox", arguments.firefox)

    def test_loads_valid_mixed_plan_and_rejects_wrong_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            base = {
                "schema_version": 2, "mode": "mixed", "target_unique": 150,
                "targets": [
                    {"id": "home", "kind": "home", "url": "https://x.com/home", "quota": 60},
                    {"id": "search", "kind": "topic_search", "url": "https://x.com/search?q=robotics&f=live", "quota": 90},
                ],
                "backfill_targets": [],
            }
            path.write_text(json.dumps(base))
            self.assertEqual(150, load_plan(str(path))["target_unique"])
            path.write_text(json.dumps({**base, "targets": [{**base["targets"][0], "quota": 149}]}))
            with self.assertRaises(ValueError):
                load_plan(str(path))

    def test_rejects_write_or_external_surfaces(self):
        for url in ("https://example.org/home", "https://x.com/settings/account", "http://x.com/home"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_target(url)

    def test_account_root_remains_a_focused_account_source(self):
        self.assertEqual("account", legacy_kind("https://x.com/researcher"))
        self.assertEqual("home", legacy_kind("https://x.com/home"))

    def test_supports_x_longform_article_markup(self):
        self.assertIn('twitterArticleReadView', EXTRACT_POST)
        self.assertIn('twitter-article-title', EXTRACT_POST)
        self.assertIn('twitterArticleRichTextView', EXTRACT_POST)
        self.assertIn('/article/${articleId}', EXTRACT_POST)
        self.assertIn('content: articleContent.slice(0, 12000)', EXTRACT_POST)
        self.assertIn('.filter(Boolean).join("\\n\\n")', EXTRACT_POST)
        self.assertIn('article-cover-image', EXTRACT_POST)
        self.assertIn('tweet-text-show-more-link', EXTRACT_POST)
        self.assertIn('_needs_text_hydration: needsTextHydration', EXTRACT_POST)
        self.assertIn('`https://xcancel.com/i/article/${articleId}`', EXTRACT_POST)

    def test_article_hydration_url_must_match_a_captured_post(self):
        expected = "https://x.com/demishassabis/status/2076957440109625718"
        self.assertEqual(expected, validate_post_detail_target(expected, "2076957440109625718"))
        for url in (
            "https://example.org/demishassabis/status/2076957440109625718",
            "https://x.com/demishassabis/status/999",
            "https://x.com/settings/account",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_post_detail_target(url, "2076957440109625718")

    def test_show_more_preview_is_replaced_by_longer_detail_text(self):
        url = "https://x.com/researcher/status/42"
        post = {
            "post_id": "42", "url": url, "handle": "@researcher", "text": "Short preview",
            "_needs_text_hydration": True, "discovery_sources": [{"acquisition_id": "home"}],
        }
        driver = FakeDriver({url: [{**post, "text": "Complete detail text with the missing conclusion."}]})
        posts = {"42": post}
        self.assertEqual(1, hydrate_post_details(driver, posts, "2026-07-14T00:00:00Z", time.monotonic() + 5))
        self.assertEqual("Complete detail text with the missing conclusion.", posts["42"]["text"])
        self.assertEqual([{"acquisition_id": "home"}], posts["42"]["discovery_sources"])

    def test_xcancel_fallback_is_fixed_to_numeric_article_page_and_extracts_only_article_blocks(self):
        self.assertEqual(
            "https://xcancel.com/i/article/2076957440109625718",
            validate_xcancel_article_target("2076957440109625718"),
        )
        with self.assertRaises(ValueError):
            validate_xcancel_article_target("../../settings")
        parser = _XCancelArticleParser()
        parser.feed("""
          <nav><p>untrusted navigation</p></nav>
          <article class="article-body">
            <h1>Frontier AI</h1><div class="article-author"><p>ignored metadata</p></div>
            <p>First substantive paragraph.</p><h2><strong>Framework</strong></h2>
            <p>Second substantive paragraph.</p>
          </article><footer><p>untrusted footer</p></footer>
        """)
        self.assertEqual("Frontier AI", parser.title)
        self.assertEqual(
            ["First substantive paragraph.", "Framework", "Second substantive paragraph."],
            parser.blocks,
        )

    def test_global_post_id_dedup_preserves_both_discovery_sources(self):
        shared = {"post_id": "1", "url": "https://x.com/a/status/1", "handle": "@a", "text": "result"}
        pages = {
            "https://x.com/home": [shared],
            "https://x.com/search?q=result": [shared, {"post_id": "2", "url": "https://x.com/b/status/2", "handle": "@b", "text": "paper"}],
        }
        driver = FakeDriver(pages)
        posts = {}
        first = {"id": "home", "kind": "home", "url": "https://x.com/home", "quota": 1}
        second = {"id": "search", "kind": "topic_search", "url": "https://x.com/search?q=result", "quota": 1}
        collect_target(driver, first, posts, "2026-07-14T00:00:00Z", time.monotonic() + 5, 10, 0)
        collect_target(driver, second, posts, "2026-07-14T00:00:00Z", time.monotonic() + 5, 10, 0)
        self.assertEqual({"1", "2"}, set(posts))
        self.assertEqual(["home", "search"], [item["acquisition_id"] for item in posts["1"]["discovery_sources"]])

    def test_waits_for_timeline_hydration_after_authenticated_shell_appears(self):
        post = {"post_id": "1", "url": "https://x.com/a/status/1", "handle": "@a", "text": "result"}
        driver = DelayedTimelineDriver({"https://x.com/home": [post]})
        posts = {}
        result = collect_target(
            driver,
            {"id": "home", "kind": "home", "url": "https://x.com/home", "quota": 1},
            posts,
            "2026-07-14T00:00:00Z",
            time.monotonic() + 5,
            10,
            0,
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(1, result["unique"])
        self.assertEqual({"1"}, set(posts))


if __name__ == "__main__":
    unittest.main()
