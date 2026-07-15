# Architecture

## Data ownership

The collector host is the durable source of truth. SQLite stores canonical posts, collection periods, per-period observations and acquisition provenance, versioned topic state, topic-account memory, account evidence and overrides, user post state, and an outbox. Sites D1 is a synchronized read/write mirror optimized for the private dashboard.

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

Firefox runs headlessly against a persistent, manually authenticated profile.
Normal periods target 150 unique posts: 60 Home, 45 active-topic search, 30
known topic accounts, and 15 exploration. One browser visits targets
sequentially and stops after 30 minutes. Focused account requests retain their
100-post, 25-minute ceiling. Global post-id deduplication keeps every source as
provenance rather than duplicating the post.

Curator preferences are revisioned. Adding a topic queues a coalesced bootstrap
period; removing one affects only future periods. Luna `xhigh` supplies three
bounded, semantically distinct query variants per topic. The query contract
matches X web search semantics: spaces mean AND, uppercase `OR` groups
alternatives, phrases are quoted, and a query may require at most two concepts.
Local validation rejects natural-language keyword bags, unbalanced expressions,
URLs, and field operators before they reach Firefox. Local query memory
rotates variants, records unique/kept yield, cools empty variants, and prevents
backfill from repeating the query that just failed. The raw envelope is
untrusted input, and Luna may add evidence for observable bait patterns but
cannot create a hard block or navigate an account that was not first observed.

Firefox writes an immutable raw JSON capture before ranking begins. A
deterministic host orchestrator hashes that file, fetches one bounded batch from
SQLite, and pipes only that batch to a fresh ephemeral Luna `xhigh` invocation.
The invocation has a read-only sandbox, no project/user rules or configured MCP
servers, and a strict output schema. It cannot run Firefox, select a different
file, or mutate the database. SQLite checkpoints accepted post results so a
malformed or timed-out response retries only failed items; a process restart
resumes the unfinished enrichment job instead of scrolling X again.

Luna emits an auditable five-part score breakdown. A deterministic finalizer
merges accepted enrichment into the untouched raw capture; Luna never generates
executable transformation code. Per-invocation duration and input, cached-input,
output, and billable token counts are retained locally for operational analysis.
SQLite and D1 store the component scores, rationales, and penalties alongside
the final score. The host recalculates the final value and decision from fixed
weights so a displayed total cannot drift from its breakdown; historical
observations are never rewritten.

## Synchronization

Ingesting a local capture commits the run, posts, observations, evidence, and an outbox event in one SQLite transaction. A separate ten-minute timer sends due events. Failed events back off after approximately 1, 5, 15, and 60 minutes, then retry hourly until successful.

Dashboard mutations are append-only records with a numeric cursor. The Pi pulls them before collection and during sync. Manual account overrides are protected from automated reputation updates.

## Dashboard surfaces

- **Today:** first discovered on the selected Melbourne calendar day, keep decision, not an ad, not blocked, not dismissed, ranked by effective signal; maximum 20.
- **Signal:** retained posts across day/week/month/year/all-time lenses.
- **Saved:** saved posts with pinned items first.
- **All seen:** the complete observation ledger, including candidates, discards, replies, quotes, and promoted posts.
- **Search:** D1 FTS5 provides exact-word search. Optional semantic search is
  served from the collector host: an incremental local BGE index stores one
  normalized vector per unique post, while Sites sends bounded HMAC-signed
  requests through a path-scoped Tailscale Funnel route. The browser never
  receives the model, Funnel URL, or HMAC secret.

## Reliability target

The initial stabilization gate is 24 hours with successful hourly slots, no
lost local observations, bounded runtime and resource use, and eventual
dashboard synchronization. A Sites outage is never a collection failure.
