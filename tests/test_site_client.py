import json
import os
import unittest
from unittest.mock import patch

from xradar import site_client


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"ok": True}).encode()


class SiteClientTests(unittest.TestCase):
    def test_local_dashboard_does_not_require_sites_bypass_token(self):
        env = {
            "XRADAR_SITE_URL": "http://127.0.0.1:8787",
            "XRADAR_INGEST_TOKEN": "test-secret",
        }
        captured = []

        def open_request(request, **_kwargs):
            captured.append(request)
            return _Response()

        with patch.dict(os.environ, env, clear=True), patch.object(site_client, "load_runtime_env"), patch.object(site_client.urllib.request, "urlopen", side_effect=open_request):
            self.assertEqual({"ok": True}, site_client.pending_requests())

        headers = dict(captured[0].header_items())
        self.assertEqual("Bearer test-secret", headers["Authorization"])
        self.assertNotIn("Oai-sites-authorization", headers)

    def test_sites_bypass_token_is_forwarded_when_configured(self):
        env = {
            "XRADAR_SITE_URL": "https://private.example",
            "XRADAR_INGEST_TOKEN": "test-secret",
            "XRADAR_SITE_BYPASS_TOKEN": "test-bypass",
        }
        captured = []

        def open_request(request, **_kwargs):
            captured.append(request)
            return _Response()

        with patch.dict(os.environ, env, clear=True), patch.object(site_client, "load_runtime_env"), patch.object(site_client.urllib.request, "urlopen", side_effect=open_request):
            site_client.pending_requests()

        self.assertEqual("Bearer test-bypass", dict(captured[0].header_items())["Oai-sites-authorization"])
