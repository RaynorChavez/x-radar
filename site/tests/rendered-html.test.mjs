import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

test("ships the X Radar product surface and durable capabilities", async () => {
  const [page, dashboard, css, hosting, migration, historyMigration, verificationMigration, curationMigration, schema, ingest, feed, briefing, search, state, account, claim, status, progress, heartbeat, runs, observations, exported, curation] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/radar-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0000_tan_blue_shield.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0003_red_kylun.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0005_aromatic_blade.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0006_known_mother_askani.sql", import.meta.url), "utf8"),
    readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/collector/ingest/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/feed/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/briefing/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/posts/search/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/posts/[postId]/state/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/accounts/[handle]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/collector/requests/claim/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/status/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/collector/progress/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/collector/heartbeat/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/runs/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/posts/[postId]/observations/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/export/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/curation/route.ts", import.meta.url), "utf8"),
  ]);

  assert.match(page, /X Radar — Signal without the scroll/);
  assert.match(dashboard, /Signal, ranked/);
  assert.match(dashboard, /Fetch an account/);
  assert.match(dashboard, /Include replies and threads/);
  assert.match(dashboard, /Read on XCancel/);
  assert.match(dashboard, /article-card/);
  assert.match(dashboard, /article\?\.xcancelUrl/);
  assert.match(dashboard, /article-reader/);
  assert.match(dashboard, /Read captured article/);
  assert.match(dashboard, /ExpandablePostText/);
  assert.match(dashboard, /See full post/);
  assert.match(dashboard, /Show less/);
  assert.match(dashboard, /articleBlocks/);
  assert.match(dashboard, /block\.kind === "heading"/);
  assert.match(dashboard, /xcancel\.com\/i\/article/);
  assert.match(dashboard, /media-grid/);
  assert.match(dashboard, /Referenced websites/);
  assert.match(dashboard, /ProfileAvatar/);
  assert.match(dashboard, /x-radar-mark\.png/);
  assert.match(css, /\.brand-mark img/);
  assert.match(dashboard, /profileImageUrl/);
  assert.match(dashboard, /publishedLabel/);
  assert.match(dashboard, /fetchedLabel/);
  assert.doesNotMatch(dashboard, /relativeTime\(post\.capturedAt\)/);
  assert.match(dashboard, /All seen/);
  assert.match(dashboard, /Today’s strongest signals/);
  assert.match(dashboard, /Saved for deeper reading/);
  assert.match(dashboard, /Search archive/);
  assert.match(dashboard, /Dismiss/);
  assert.match(dashboard, /AccountControl/);
  assert.match(dashboard, /Complete scan history/);
  assert.match(dashboard, /Load 100 more observations/);
  assert.match(dashboard, /24-HOUR VERIFICATION/);
  assert.match(dashboard, /Run discovery now/);
  assert.match(dashboard, /150-post discovery period/);
  assert.match(dashboard, /preferenceVersion/);
  assert.match(dashboard, /Run account now/);
  assert.match(dashboard, /Pi checks the queue every minute/);
  assert.doesNotMatch(dashboard, /Queue account scan/);
  assert.match(dashboard, /Sort posts/);
  assert.match(dashboard, /surface === "briefing" \|\| surface === "signal"/);
  assert.match(briefing, /sort.*newest/);
  assert.match(briefing, /posted_at.*first_seen_at.*captured_at/);
  assert.match(dashboard, /Signal, ranked/);
  assert.match(dashboard, /Newest signal first/);
  assert.match(dashboard, /submittedSearch/);
  assert.match(dashboard, /effectiveScore/);
  assert.match(dashboard, /api\/export\?format=markdown/);
  assert.match(dashboard, /Undo/);
  assert.match(dashboard, /Seen history/);
  assert.match(dashboard, /LUNA CURATOR/);
  assert.match(dashboard, /Shape the signal/);
  assert.match(dashboard, /Save brief/);
  assert.match(css, /profile-avatar/);
  assert.match(dashboard, /day.*week.*month.*year.*all/s);
  assert.match(css, /--azure:\s*#2f6f98/);
  assert.doesNotMatch(css, /#c9ff57|--acid/);
  assert.match(dashboard, /mobile-nav/);
  assert.match(dashboard, /Operations desk/);
  assert.match(dashboard, /Open curator settings and topics/);
  assert.match(dashboard, /Settings & topics/);
  assert.doesNotMatch(dashboard, /Desk sections/);
  assert.doesNotMatch(dashboard, /sidecar-shortcuts/);
  assert.match(dashboard, /Open collector configuration/);
  assert.match(dashboard, /desk-backdrop/);
  assert.match(dashboard, /desk-open/);
  assert.match(dashboard, /deskOpen && <>/);
  assert.doesNotMatch(dashboard, /scrollIntoView/);
  assert.doesNotMatch(dashboard, /panel\.scrollTo/);
  assert.match(dashboard, /deskSection.*config.*settings/s);
  assert.match(dashboard, /desk-scroll-region/);
  assert.match(css, /\.sidecar\.desk-open/);
  assert.match(css, /\.sidecar\.desk-open \.desk-scroll-region/);
  assert.match(css, /\.sidecar \{ position: fixed/);
  assert.match(css, /visibility:\s*hidden/);
  assert.doesNotMatch(css, /\.sidecar \{ position: sticky/);
  assert.doesNotMatch(css, /grid-template-columns:\s*minmax\(0,\s*900px\)\s*330px/);
  assert.match(css, /border-radius:\s*var\(--radius\)/);
  assert.match(hosting, /"d1":\s*"DB"/);
  assert.match(migration, /CREATE TABLE `fetch_requests`/);
  assert.match(schema, /xcancel_url/);
  assert.match(schema, /external_links_json/);
  assert.match(schema, /media_json/);
  assert.match(schema, /article_json/);
  assert.match(schema, /profile_image_url/);
  assert.match(schema, /postObservations/);
  assert.match(historyMigration, /CREATE TABLE `post_observations`/);
  assert.match(historyMigration, /INSERT INTO `post_observations`/);
  assert.match(verificationMigration, /CREATE TABLE `collector_state`/);
  assert.match(verificationMigration, /ALTER TABLE `runs` ADD `duration_seconds`/);
  assert.doesNotMatch(verificationMigration, /DROP TABLE `post_observations`/);
  assert.match(curationMigration, /CREATE TABLE `curator_preferences`/);
  const discoveryMigration = await readFile(new URL("../drizzle/0007_quick_doctor_octopus.sql", import.meta.url), "utf8");
  assert.match(discoveryMigration, /CREATE TABLE `run_acquisitions`/);
  assert.match(discoveryMigration, /`preference_version`/);
  assert.match(discoveryMigration, /`bootstrap_topics_json`/);
  assert.match(ingest, /xcancel\.com\/i\/article/);
  assert.match(ingest, /INSERT INTO post_observations/);
  assert.match(ingest, /batchInChunks/);
  assert.match(ingest, /batchSize = 50/);
  assert.match(ingest, /countExistingPosts/);
  assert.match(ingest, /Capture ingest failed/);
  assert.match(feed, /view === "history"/);
  assert.match(feed, /sort === "signal"/);
  assert.match(feed, /downrank.*-.40/s);
  assert.match(feed, /nextOffset/);
  assert.match(briefing, /slice\(0, 20\)/);
  assert.match(search, /posts_fts MATCH/);
  assert.match(state, /pinnedAt.*savedAt/s);
  assert.match(account, /operator_override/);
  assert.match(account, /export async function DELETE/);
  assert.match(account, /collectorAuthorized/);
  assert.match(account, /observation_acquisitions/);
  assert.match(claim, /status='queued'/);
  assert.match(status, /reliability/);
  assert.match(status, /stale_collection/);
  assert.match(progress, /collecting.*ranking.*syncing.*complete.*error/s);
  assert.match(progress, /current\?\.targetUnique/);
  assert.match(heartbeat, /last_backup_at/);
  assert.match(runs, /durationSeconds/);
  assert.match(observations, /capturedAt/);
  assert.match(exported, /text\/csv/);
  assert.match(curation, /kind: "curation"/);
  assert.match(curation, /maxTopics = 24/);
  assert.doesNotMatch(page + dashboard, /codex-preview|react-loading-skeleton/);
  await access(new URL("../dist/server/index.js", import.meta.url));
});
