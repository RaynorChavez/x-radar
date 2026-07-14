from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def load_runtime_env(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    load_runtime_env()
    base = os.environ.get("XRADAR_SITE_URL", "").rstrip("/")
    token = os.environ.get("XRADAR_INGEST_TOKEN", "")
    bypass = os.environ.get("XRADAR_SITE_BYPASS_TOKEN", "")
    if not base or not token:
        raise RuntimeError("XRADAR_SITE_URL and XRADAR_INGEST_TOKEN are required")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            **({"OAI-Sites-Authorization": f"Bearer {bypass}"} if bypass else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Site API returned {error.code}: {detail}") from error


def pending_requests() -> dict[str, Any]:
    return _request("GET", "/api/collector/requests")


def claim_request() -> dict[str, Any]:
    return _request("POST", "/api/collector/requests/claim", {})


def mutations(after: int = 0) -> dict[str, Any]:
    return _request("GET", f"/api/collector/mutations?after={after}")


def send_event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "capture":
        return _request("POST", "/api/collector/ingest", payload)
    if kind == "account":
        return _request("PUT", f"/api/accounts/{payload['handle'].lstrip('@')}", payload)
    if kind == "post_state":
        return _request("PUT", f"/api/posts/{payload['post_id']}/state", payload)
    raise ValueError(f"unknown outbox event kind: {kind}")


def report_progress(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/api/collector/progress", payload)


def send_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/api/collector/heartbeat", payload)


def ingest_capture(capture_path: str | Path, reputation: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(Path(capture_path).read_text())
    payload["account_reputation"] = reputation
    return _request("POST", "/api/collector/ingest", payload)


def complete_request(
    request_id: str,
    *,
    result_count: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error" if error else "complete",
        "resultCount": result_count,
    }
    if error:
        payload["error"] = error
    return _request("POST", f"/api/collector/requests/{request_id}/complete", payload)
