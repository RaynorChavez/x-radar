# X Radar

X Radar turns an endless X timeline into a finite briefing and a searchable,
private archive. An always-on collector reads visible posts, retains every
observation locally, uses Codex/Luna to rank signal, and synchronizes a reading
dashboard without making the dashboard the source of truth.

## Highlights

- Daily top-signal briefing and a longer curated feed.
- Complete **All seen** history with one post record and many scan timestamps.
- Directed scans for an account's posts and replies.
- Lexical and account search, save, pin, dismiss, and observation history.
- Evidence-backed account controls for bait and non-additive commentary.
- Rich articles, media, external links, profile images, X, and XCancel links.
- Durable SQLite storage, compressed archives, backups, and retrying sync.
- Split collector + Sites hosting and supported single-machine Linux mode.

X Radar is read-only on X. It never likes, reposts, replies, follows,
bookmarks, sends messages, or changes account settings.

## Architecture

```text
X → Firefox collector → SQLite + compressed archive
                           ├── Luna xhigh ranking
                           ├── account evidence and user overrides
                           └── durable outbox → dashboard database → web UI
```

The dashboard database can be private Sites D1 or persistent local D1 on the
collector machine. Collection continues when the dashboard is unavailable.
See [the architecture](docs/architecture.md).

## Choose an installation

- [Raspberry Pi/Linux collector + private Sites dashboard](docs/install-pi.md)
- [One Linux machine for collector, database, API, and dashboard](docs/install-single-machine.md)

Both modes begin with:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[collector]'
cp .env.example .env
```

Review [configuration](docs/configuration.md), authenticate an isolated Firefox
profile manually, and run `bin/doctor` before enabling services.

## Repository and data boundaries

This repository contains product code, schema migrations, generic service
definitions, safe examples, tests, and documentation. It must not contain an
operator's `.env`, Sites binding, browser profile, SQLite files, captures,
archives, blocklist, saved-post state, diagnostic screenshots, or deployment
hostnames.

Curator topics, custom instructions, account controls, saved posts, and scan
history live in each installation's databases. Two people using the same source
repository receive completely separate data and credentials.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 tools/check_public_tree.py
cd site
cp -n .openai/hosting.example.json .openai/hosting.json
npm ci
npm run lint
npm test
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[upgrade guide](docs/upgrading.md). X Radar is available under the MIT License.
