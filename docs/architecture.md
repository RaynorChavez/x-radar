# Architecture

## Data ownership

The collector host is the durable source of truth. SQLite stores canonical posts, scan runs, per-run observations, account evidence and overrides, user post state, and an outbox. Sites D1 is a synchronized read/write mirror optimized for the private dashboard.

Every capture carries a UUID `scan_id`. Delivery is idempotent by that ID. A post is unique by `post_id`; seeing it again updates mutable metadata and adds a new `(scan_id, post_id)` observation. This keeps storage growth proportional to new posts plus small observation rows.

## Deployment modes

The product has one data contract and two supported topologies:

- **Split:** collection, ranking, SQLite, archives, and the outbox run on an
  always-on Linux host; a private Sites deployment provides D1 and the dashboard.
- **Single machine:** those same collector services run beside a loopback-only
  Workers runtime and persistent local D1 mirror.

In both modes the collector talks to the same HTTP API using an ingest token.
The Sites bypass header is optional and is sent only in split mode when the
private Sites access gate requires it. Personal deployment orchestration and
credentials remain outside this repository.

## Collection and ranking

Firefox runs headlessly against a persistent, manually authenticated profile. Each pass stops after 100 posts, 30 scrolls, or 25 minutes. The raw envelope is untrusted input. Luna `xhigh` applies the ranking rubric conservatively and may add evidence for observable bait patterns, but it cannot create a hard block.

## Synchronization

Ingesting a local capture commits the run, posts, observations, evidence, and an outbox event in one SQLite transaction. A separate ten-minute timer sends due events. Failed events back off after approximately 1, 5, 15, and 60 minutes, then retry hourly until successful.

Dashboard mutations are append-only records with a numeric cursor. The Pi pulls them before collection and during sync. Manual account overrides are protected from automated reputation updates.

## Dashboard surfaces

- **Today:** first discovered on the selected Melbourne calendar day, keep decision, not an ad, not blocked, not dismissed, ranked by effective signal; maximum 20.
- **Signal:** retained posts across day/week/month/year/all-time lenses.
- **Saved:** saved posts with pinned items first.
- **All seen:** the complete observation ledger, including candidates, discards, replies, quotes, and promoted posts.
- **Search:** D1 FTS5 over post text, author, and handle, with an account filter.

## Reliability target

The stabilization gate is 72 hours with at least 65 successful hourly cycles, no lost local observations, and eventual dashboard synchronization. A Sites outage is never a collection failure.
