# Raspberry Pi collector with a Sites dashboard

This mode keeps collection, SQLite, archives, and ranking on an always-on Linux
machine while the private dashboard runs on OpenAI Sites.

## 1. Prepare the collector

Clone the repository as `~/x-radar`, then install Python dependencies:

```bash
cd ~/x-radar
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[collector,semantic]'
cp .env.example .env
```

Set `XRADAR_MODE=split`, `XRADAR_ACQUISITION_MODE=mixed`, the private Sites URL, a random ingest token, the Sites
bypass token, and a non-identifying host label in `.env`.

Install Firefox, geckodriver, and the Codex CLI through their official
installation channels. [Authenticate Codex](authenticate-codex.md), then run
`bin/doctor --live-model-check` before installing services.

To enable semantic search, install the pinned BGE model and put this path in
`.env`:

```bash
python3 tools/install_embedding_model.py
```

```env
XRADAR_EMBEDDING_MODEL_DIR=/home/example/x-radar/var/models/bge-small-en-v1.5-onnx
```

Run `bin/embed-pending` to create the incremental local index. For
public remote search, run `bin/semantic-server` on loopback and expose only its
path through Tailscale Funnel; configure a 32-byte-or-longer HMAC secret on both
the service and Sites. Never expose SQLite or the model directory.
User-service installations can enable the indexer and server with
`bin/install-semantic-services` after those settings exist.
See [semantic search](semantic-search.md) for verification and threat boundaries.

## 2. Authenticate X manually

Follow the [manual X authentication guide](authenticate-x.md). Never transfer
the resulting profile to a public or shared location.

## 3. Configure the dashboard

Follow the [private Sites deployment guide](deploy-sites.md), retain the D1
binding name `DB`, and configure the same `XRADAR_INGEST_TOKEN` in both places.

## 4. Enable collection

```bash
cd ~/x-radar
bin/install-user-services
sudo loginctl enable-linger "$USER"
systemctl --user list-timers 'x-radar*'
```

The hourly timer runs a topic-aware 150-post discovery period. The dispatcher checks directed scan
requests every minute, synchronization retries every ten minutes, and backups
run daily. `flock` prevents overlapping collection cycles.

## 5. Verify

```bash
PYTHONPATH=src python3 -m xradar --db var/x-radar.sqlite status
systemctl --user status x-radar.service
systemctl --user status x-radar-sync.service
```
