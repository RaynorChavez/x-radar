import { index, integer, real, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const posts = sqliteTable("posts", {
  postId: text("post_id").primaryKey(),
  url: text("url").notNull().unique(),
  handle: text("handle").notNull(),
  author: text("author"),
  profileImageUrl: text("profile_image_url"),
  text: text("text").notNull(),
  postedAt: text("posted_at"),
  capturedAt: text("captured_at").notNull(),
  firstSeenAt: text("first_seen_at"),
  lastSeenAt: text("last_seen_at"),
  isAd: integer("is_ad", { mode: "boolean" }).notNull().default(false),
  isReply: integer("is_reply", { mode: "boolean" }).notNull().default(false),
  isQuote: integer("is_quote", { mode: "boolean" }).notNull().default(false),
  sourceUrl: text("source_url"),
  xcancelUrl: text("xcancel_url"),
  externalLinksJson: text("external_links_json").notNull().default("[]"),
  mediaJson: text("media_json").notNull().default("[]"),
  articleJson: text("article_json"),
  engagementJson: text("engagement_json").notNull().default("{}"),
  score: real("score").notNull().default(0),
  decision: text("decision").notNull().default("candidate"),
  reasonsJson: text("reasons_json").notNull().default("[]"),
}, (table) => [
  index("posts_captured_idx").on(table.capturedAt),
  index("posts_decision_idx").on(table.decision),
  index("posts_handle_idx").on(table.handle),
]);

export const postObservations = sqliteTable("post_observations", {
  id: text("id").primaryKey(),
  scanId: text("scan_id").notNull().default("legacy"),
  postId: text("post_id").notNull(),
  capturedAt: text("captured_at").notNull(),
  observedIndex: integer("observed_index").notNull().default(0),
  score: real("score").notNull().default(0),
  decision: text("decision").notNull().default("candidate"),
  isAd: integer("is_ad", { mode: "boolean" }).notNull().default(false),
  isReply: integer("is_reply", { mode: "boolean" }).notNull().default(false),
  isQuote: integer("is_quote", { mode: "boolean" }).notNull().default(false),
  preferenceVersion: integer("preference_version").notNull().default(0),
  topicMatchesJson: text("topic_matches_json").notNull().default("[]"),
}, (table) => [
  index("observations_captured_idx").on(table.capturedAt, table.observedIndex),
  index("observations_post_idx").on(table.postId),
]);

export const accountReputation = sqliteTable("account_reputation", {
  handle: text("handle").primaryKey(),
  disposition: text("disposition").notNull().default("normal"),
  strikePoints: integer("strike_points").notNull().default(0),
  confidence: real("confidence").notNull().default(0),
  reasonsJson: text("reasons_json").notNull().default("[]"),
  notes: text("notes"),
  operatorOverride: integer("operator_override", { mode: "boolean" }).notNull().default(false),
  updatedAt: text("updated_at").notNull(),
});

export const runs = sqliteTable("runs", {
  scanId: text("scan_id").primaryKey(),
  host: text("host").notNull(),
  source: text("source").notNull(),
  target: text("target"),
  requestId: text("request_id"),
  capturedAt: text("captured_at").notNull(),
  postsSeen: integer("posts_seen").notNull().default(0),
  postsKept: integer("posts_kept").notNull().default(0),
  postsAdded: integer("posts_added").notNull().default(0),
  duplicates: integer("duplicates").notNull().default(0),
  candidates: integer("candidates").notNull().default(0),
  discarded: integer("discarded").notNull().default(0),
  signalsCount: integer("signals_count").notNull().default(0),
  mediaCount: integer("media_count").notNull().default(0),
  linksCount: integer("links_count").notNull().default(0),
  durationSeconds: integer("duration_seconds").notNull().default(0),
  status: text("status").notNull().default("complete"),
  schemaVersion: integer("schema_version").notNull().default(1),
  periodId: text("period_id"),
  preferenceVersion: integer("preference_version").notNull().default(0),
  targetUnique: integer("target_unique").notNull().default(100),
  ingestedAt: text("ingested_at").notNull(),
});

export const collectorState = sqliteTable("collector_state", {
  id: integer("id").primaryKey(),
  phase: text("phase").notNull().default("idle"),
  target: text("target"),
  requestId: text("request_id"),
  scanId: text("scan_id"),
  observed: integer("observed").notNull().default(0),
  targetUnique: integer("target_unique").notNull().default(100),
  preferenceVersion: integer("preference_version").notNull().default(0),
  sourceProgressJson: text("source_progress_json").notNull().default("{}"),
  pendingSync: integer("pending_sync").notNull().default(0),
  failedAttempts: integer("failed_attempts").notNull().default(0),
  authStatus: text("auth_status").notNull().default("ok"),
  lastError: text("last_error"),
  startedAt: text("started_at"),
  updatedAt: text("updated_at").notNull(),
  lastSyncAt: text("last_sync_at"),
  lastBackupAt: text("last_backup_at"),
});

export const curatorPreferences = sqliteTable("curator_preferences", {
  id: integer("id").primaryKey(),
  instructions: text("instructions").notNull().default(""),
  topicsJson: text("topics_json").notNull().default("[]"),
  preferenceVersion: integer("preference_version").notNull().default(0),
  updatedAt: text("updated_at").notNull(),
});

export const userPostState = sqliteTable("user_post_state", {
  postId: text("post_id").primaryKey(),
  savedAt: text("saved_at"),
  pinnedAt: text("pinned_at"),
  dismissedAt: text("dismissed_at"),
  updatedAt: text("updated_at").notNull(),
});

export const mutations = sqliteTable("mutations", {
  seq: integer("seq").primaryKey({ autoIncrement: true }),
  kind: text("kind").notNull(),
  entityId: text("entity_id").notNull(),
  payloadJson: text("payload_json").notNull(),
  createdAt: text("created_at").notNull(),
});

export const fetchRequests = sqliteTable("fetch_requests", {
  id: text("id").primaryKey(),
  handle: text("handle").notNull(),
  includeReplies: integer("include_replies", { mode: "boolean" }).notNull().default(true),
  kind: text("kind").notNull().default("account"),
  preferenceVersion: integer("preference_version").notNull().default(0),
  bootstrapTopicsJson: text("bootstrap_topics_json").notNull().default("[]"),
  status: text("status").notNull().default("queued"),
  requestedAt: text("requested_at").notNull(),
  completedAt: text("completed_at"),
  resultCount: integer("result_count"),
  error: text("error"),
}, (table) => [index("fetch_status_idx").on(table.status, table.requestedAt)]);

export const runAcquisitions = sqliteTable("run_acquisitions", {
  acquisitionId: text("acquisition_id").primaryKey(),
  scanId: text("scan_id").notNull(),
  kind: text("kind").notNull(),
  target: text("target").notNull(),
  topicKey: text("topic_key"),
  topicLabel: text("topic_label"),
  plannedQuota: integer("planned_quota").notNull().default(0),
  observedCount: integer("observed_count").notNull().default(0),
  uniqueCount: integer("unique_count").notNull().default(0),
  status: text("status").notNull().default("complete"),
  error: text("error"),
  durationSeconds: integer("duration_seconds").notNull().default(0),
}, (table) => [index("run_acquisitions_scan_idx").on(table.scanId)]);

export const observationAcquisitions = sqliteTable("observation_acquisitions", {
  id: text("id").primaryKey(),
  observationId: text("observation_id").notNull(),
  acquisitionId: text("acquisition_id").notNull(),
  isPrimary: integer("is_primary", { mode: "boolean" }).notNull().default(false),
}, (table) => [index("observation_acquisitions_observation_idx").on(table.observationId)]);
