from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .db import connect, utcnow


QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_QUERY_LENGTH = 500
MAX_RESULTS = 50
MAX_BODY_BYTES = 4096


class RequestAuthenticator:
    HEADERS = {
        "key_id": "X-XRadar-Key-Id",
        "timestamp": "X-XRadar-Timestamp",
        "nonce": "X-XRadar-Nonce",
        "user": "X-XRadar-User",
        "signature": "X-XRadar-Signature",
    }

    def __init__(self, keys: dict[str, str], *, local_mode: bool = False, window: int = 60, clock=time.time) -> None:
        if not keys and not local_mode:
            raise RuntimeError("XRADAR_SEMANTIC_HMAC_KEYS is required outside local mode")
        if any(len(secret.encode("utf-8")) < 32 for secret in keys.values()):
            raise RuntimeError("semantic HMAC secrets must be at least 32 bytes")
        self.keys = keys
        self.local_mode = local_mode
        self.window = window
        self.clock = clock
        self._nonces: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> "RequestAuthenticator":
        local_mode = os.environ.get("XRADAR_SEMANTIC_LOCAL_MODE") == "1"
        raw = os.environ.get("XRADAR_SEMANTIC_HMAC_KEYS", "").strip()
        if not raw:
            return cls({}, local_mode=local_mode)
        try:
            keys = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("XRADAR_SEMANTIC_HMAC_KEYS must be a JSON object") from exc
        if not isinstance(keys, dict) or not all(
            isinstance(key, str) and key and isinstance(value, str) and value for key, value in keys.items()
        ):
            raise RuntimeError("XRADAR_SEMANTIC_HMAC_KEYS must map key IDs to secrets")
        return cls(keys, local_mode=local_mode)

    @staticmethod
    def canonical(method: str, target: str, timestamp: str, nonce: str, body: bytes, user: str) -> bytes:
        return "\n".join((
            method.upper(), target, timestamp, nonce, hashlib.sha256(body).hexdigest(), user.strip().lower(),
        )).encode("utf-8")

    def verify(self, method: str, target: str, headers: Any, body: bytes) -> tuple[bool, str]:
        if self.local_mode and not self.keys:
            return True, "local"
        values = {name: (headers.get(header) or "").strip() for name, header in self.HEADERS.items()}
        if not all(values.values()):
            return False, "missing signature headers"
        secret = self.keys.get(values["key_id"])
        if secret is None:
            return False, "unknown key ID"
        try:
            stamp = int(values["timestamp"])
        except ValueError:
            return False, "invalid timestamp"
        now = self.clock()
        if abs(now - stamp) > self.window:
            return False, "stale timestamp"
        if not re.fullmatch(r"[0-9a-fA-F]{64}", values["signature"]):
            return False, "invalid signature"
        expected = hmac.new(
            secret.encode("utf-8"),
            self.canonical(method, target, values["timestamp"], values["nonce"], body, values["user"]),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, values["signature"].lower()):
            return False, "invalid signature"
        nonce_key = (values["key_id"], values["nonce"])
        with self._lock:
            self._nonces = {key: expiry for key, expiry in self._nonces.items() if expiry >= now}
            if nonce_key in self._nonces:
                return False, "replayed nonce"
            self._nonces[nonce_key] = now + self.window
        return True, values["user"].strip().lower()


class SearchLimiter:
    def __init__(self, rate=10 / 60, burst=5, concurrent=1, queued=4, clock=time.monotonic) -> None:
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(concurrent + queued)
        self._workers = threading.BoundedSemaphore(concurrent)

    def allow(self, user: str) -> bool:
        now = self.clock()
        with self._lock:
            tokens, updated = self._buckets.get(user, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + max(0, now - updated) * self.rate)
            if tokens < 1:
                self._buckets[user] = (tokens, now)
                return False
            self._buckets[user] = (tokens - 1, now)
            return True

    def enter(self) -> bool:
        if not self._capacity.acquire(blocking=False):
            return False
        self._workers.acquire()
        return True

    def leave(self) -> None:
        self._workers.release()
        self._capacity.release()


class EmbeddingEngine:
    def __init__(self) -> None:
        self.model_id = os.environ.get("XRADAR_EMBEDDING_MODEL_ID", "bge-small-en-v1.5-onnx")
        model_root = Path(os.environ.get("XRADAR_EMBEDDING_MODEL_DIR", "var/models/bge-small-en-v1.5-onnx"))
        self.model_path = Path(os.environ.get("XRADAR_EMBEDDING_MODEL", model_root / "model.onnx"))
        self.tokenizer_path = Path(os.environ.get("XRADAR_EMBEDDING_TOKENIZER", model_root / "tokenizer"))
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            if not self.model_path.is_file() or not self.tokenizer_path.is_dir():
                raise RuntimeError("embedding model or tokenizer is unavailable")
            import onnxruntime as ort
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path), local_files_only=True)
            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])

    def encode(self, texts: list[str], *, query: bool = False):
        self.load()
        import numpy as np

        values = [QUERY_PREFIX + text if query else text for text in texts]
        encoded = self._tokenizer(values, padding=True, truncation=True, max_length=512, return_tensors="np")
        input_names = {item.name for item in self._session.get_inputs()}
        feeds = {key: value.astype(np.int64) for key, value in encoded.items() if key in input_names}
        with self._lock:
            hidden = self._session.run(None, feeds)[0]
        mask = encoded["attention_mask"][..., None].astype(np.float32)
        vectors = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return vectors.astype(np.float32)


def post_document(row: sqlite3.Row) -> str:
    parts = [str(row["author"] or ""), str(row["handle"] or ""), str(row["text"] or "")]
    for column in ("article_json", "external_links_json"):
        try:
            value = json.loads(row[column] or "null")
        except json.JSONDecodeError:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                parts.extend(str(item.get(key) or "") for key in ("title", "description", "content", "publisher", "domain"))
    return "\n".join(part.strip() for part in parts if part.strip())[:12000]


def index_pending(db_path: str | Path, engine: EmbeddingEngine, *, batch_size: int = 16) -> dict[str, int]:
    conn = connect(db_path)
    indexed = skipped = 0
    try:
        rows = list(conn.execute("""
            SELECT p.post_id,p.author,p.handle,p.text,p.article_json,p.external_links_json,
                   e.model_id,e.content_hash
            FROM posts p LEFT JOIN post_embeddings e ON e.post_id=p.post_id
            ORDER BY p.last_seen_at,p.post_id
        """))
        pending: list[tuple[sqlite3.Row, str, str]] = []
        for row in rows:
            document = post_document(row)
            digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
            if row["model_id"] == engine.model_id and row["content_hash"] == digest:
                skipped += 1
            else:
                pending.append((row, document, digest))
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            vectors = engine.encode([item[1] for item in batch])
            for (row, _document, digest), vector in zip(batch, vectors, strict=True):
                conn.execute("""
                    INSERT INTO post_embeddings(post_id,model_id,content_hash,vector,dimensions,embedded_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET
                      model_id=excluded.model_id,content_hash=excluded.content_hash,
                      vector=excluded.vector,dimensions=excluded.dimensions,embedded_at=excluded.embedded_at
                """, (row["post_id"], engine.model_id, digest, vector.astype("float16").tobytes(), len(vector), utcnow()))
            conn.commit()
            indexed += len(batch)
        return {"indexed": indexed, "skipped": skipped, "total": len(rows)}
    finally:
        conn.close()


class SemanticSearch:
    def __init__(self, db_path: str | Path, engine: EmbeddingEngine) -> None:
        self.db_path = Path(db_path)
        self.engine = engine

    def search(self, query: str, *, limit: int, handle: str | None = None, decision: str | None = None) -> list[dict[str, Any]]:
        import numpy as np

        clauses = ["e.model_id=?"]
        values: list[Any] = [self.engine.model_id]
        if handle:
            clauses.append("p.handle=?")
            values.append(handle.lower() if handle.startswith("@") else f"@{handle.lower()}")
        if decision in {"keep", "candidate", "discard"}:
            clauses.append("p.decision=?")
            values.append(decision)
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=15)
        try:
            rows = conn.execute(f"""
                SELECT e.post_id,e.vector,e.dimensions FROM post_embeddings e
                JOIN posts p ON p.post_id=e.post_id WHERE {' AND '.join(clauses)}
            """, values).fetchall()
        finally:
            conn.close()
        if not rows:
            return []
        dimensions = int(rows[0][2])
        if any(int(row[2]) != dimensions for row in rows):
            raise RuntimeError("embedding dimension mismatch")
        matrix = np.frombuffer(b"".join(bytes(row[1]) for row in rows), dtype=np.float16).reshape(-1, dimensions).astype(np.float32)
        vector = self.engine.encode([query], query=True)[0]
        scores = matrix @ vector
        count = min(limit, len(rows))
        selected = np.argpartition(-scores, count - 1)[:count]
        selected = selected[np.argsort(-scores[selected])]
        return [{"postId": str(rows[index][0]), "semanticScore": round(float(scores[index]), 6)} for index in selected]


def handler(auth: RequestAuthenticator, limiter: SearchLimiter, search: SemanticSearch):
    class Handler(BaseHTTPRequestHandler):
        server_version = "XRadarSemantic/1"

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def json_response(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            valid, _user = auth.verify("GET", self.path, self.headers, b"")
            if not valid:
                return self.json_response({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            if self.path != "/api/health":
                return self.json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self.json_response({"ok": True, "model": search.engine.model_id})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/search":
                return self.json_response({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                length = int(self.headers.get("content-length") or "0")
            except ValueError:
                return self.json_response({"error": "invalid content length"}, HTTPStatus.BAD_REQUEST)
            if length <= 0 or length > MAX_BODY_BYTES:
                return self.json_response({"error": "request body is too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            body = self.rfile.read(length)
            valid, user = auth.verify("POST", self.path, self.headers, body)
            if not valid:
                return self.json_response({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            try:
                payload = json.loads(body)
                query = str(payload.get("query") or "").strip()
                limit = max(1, min(MAX_RESULTS, int(payload.get("limit") or 20)))
                handle = str(payload.get("handle") or "").strip() or None
                decision = str(payload.get("decision") or "").strip() or None
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                return self.json_response({"error": "invalid request"}, HTTPStatus.BAD_REQUEST)
            if not query or len(query) > MAX_QUERY_LENGTH:
                return self.json_response({"error": "query must be 1-500 characters"}, HTTPStatus.BAD_REQUEST)
            if not limiter.allow(user):
                return self.json_response({"error": "search rate limit exceeded"}, HTTPStatus.TOO_MANY_REQUESTS)
            if not limiter.enter():
                return self.json_response({"error": "search queue full"}, HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                return self.json_response({"results": search.search(query, limit=limit, handle=handle, decision=decision)})
            finally:
                limiter.leave()

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x-radar-semantic")
    parser.add_argument("--db", default=os.environ.get("XRADAR_DATABASE", "var/x-radar.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("--batch-size", type=int, default=16)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8777)
    args = parser.parse_args(argv)
    engine = EmbeddingEngine()
    if args.command == "index":
        print(json.dumps(index_pending(args.db, engine, batch_size=max(1, min(64, args.batch_size))), sort_keys=True))
        return 0
    auth = RequestAuthenticator.from_environment()
    engine.load()
    server = ThreadingHTTPServer((args.host, args.port), handler(auth, SearchLimiter(), SemanticSearch(args.db, engine)))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
