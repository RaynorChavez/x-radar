# Server-side semantic search

Semantic search runs on the Linux host, not in the browser. X Radar uses the
ONNX export of `bge-small-en-v1.5` and stores compact vectors beside the local
SQLite archive.

Install the exact, revision-pinned model from the repository root:

```bash
. .venv/bin/activate
python3 tools/install_embedding_model.py
```

The installer downloads only from a fixed HTTPS repository revision, verifies
the SHA-256 checksum of every model and tokenizer file, and safely resumes by
leaving already-valid files in place. The model is about 127 MiB.

Configure the installed directory:

```env
XRADAR_EMBEDDING_MODEL_DIR=/home/example/x-radar/var/models/bge-small-en-v1.5-onnx
XRADAR_SEMANTIC_PYTHON=/home/example/x-radar/.venv/bin/python
```

Verify it and create the first index:

```bash
python3 tools/install_embedding_model.py --check
bin/embed-pending
```

`bin/install-semantic-services` adds incremental indexing and a loopback-only
search service. In split mode, expose only the authenticated semantic route
through Tailscale Funnel. Never expose SQLite, the model directory, or the raw
semantic service directly. Configure `XRADAR_SEMANTIC_HMAC_KEYS` with a secret
different from the ingest token. In single-machine mode,
`XRADAR_SEMANTIC_LOCAL_MODE=1` is permitted only while the service stays on
loopback.
