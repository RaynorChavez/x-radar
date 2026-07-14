import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from xradar.db import connect, upsert_post
from xradar.semantic import RequestAuthenticator, SearchLimiter, index_pending


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeVector:
    def __init__(self, value: bytes = b"12345678"):
        self.value = value

    def astype(self, _kind):
        return self

    def tobytes(self):
        return self.value

    def __len__(self):
        return len(self.value) // 2


class FakeEngine:
    model_id = "test-model"

    def __init__(self):
        self.documents = []

    def encode(self, documents, *, query=False):
        self.documents.extend(documents)
        return [FakeVector() for _ in documents]


class SemanticSecurityTests(unittest.TestCase):
    def test_signed_body_is_bound_to_user_path_and_nonce(self):
        now = 1_700_000_000
        secret = "0123456789abcdef0123456789abcdef"
        auth = RequestAuthenticator({"primary": secret}, clock=lambda: now)
        body = json.dumps({"query": "orbital manufacturing"}).encode()
        stamp = str(now)
        canonical = auth.canonical("POST", "/api/search", stamp, "nonce-1", body, "User@Example.com")
        signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
        headers = Headers({
            "x-xradar-key-id": "primary", "x-xradar-timestamp": stamp,
            "x-xradar-nonce": "nonce-1", "x-xradar-user": "User@Example.com",
            "x-xradar-signature": signature,
        })
        self.assertEqual((True, "user@example.com"), auth.verify("POST", "/api/search", headers, body))
        self.assertEqual((False, "replayed nonce"), auth.verify("POST", "/api/search", headers, body))

    def test_missing_keys_fail_closed_outside_explicit_local_mode(self):
        with self.assertRaisesRegex(RuntimeError, "required"):
            RequestAuthenticator({})
        self.assertEqual((True, "local"), RequestAuthenticator({}, local_mode=True).verify("GET", "/api/health", Headers(), b""))

    def test_rate_and_queue_are_bounded(self):
        now = [0.0]
        limiter = SearchLimiter(rate=1, burst=1, concurrent=1, queued=0, clock=lambda: now[0])
        self.assertTrue(limiter.allow("user"))
        self.assertFalse(limiter.allow("user"))
        self.assertTrue(limiter.enter())
        self.assertFalse(limiter.enter())
        limiter.leave()


class SemanticIndexTests(unittest.TestCase):
    def test_index_is_incremental_and_includes_structured_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radar.sqlite"
            conn = connect(path)
            upsert_post(conn, {
                "post_id": "1", "url": "https://x.com/lab/status/1", "handle": "lab",
                "author": "Research Lab", "text": "New orbital process", "decision": "keep",
                "article": {"title": "Manufacturing paper", "description": "A vacuum deposition method"},
            })
            conn.commit()
            conn.close()
            engine = FakeEngine()
            self.assertEqual({"indexed": 1, "skipped": 0, "total": 1}, index_pending(path, engine))
            self.assertIn("Manufacturing paper", engine.documents[0])
            self.assertEqual({"indexed": 0, "skipped": 1, "total": 1}, index_pending(path, engine))
            conn = connect(path)
            upsert_post(conn, {
                "post_id": "1", "url": "https://x.com/lab/status/1", "handle": "lab",
                "author": "Research Lab", "text": "Updated orbital process", "decision": "keep",
            })
            conn.commit()
            conn.close()
            self.assertEqual({"indexed": 1, "skipped": 0, "total": 1}, index_pending(path, engine))


if __name__ == "__main__":
    unittest.main()
