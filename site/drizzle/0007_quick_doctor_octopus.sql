CREATE TABLE `observation_acquisitions` (
	`id` text PRIMARY KEY NOT NULL,
	`observation_id` text NOT NULL,
	`acquisition_id` text NOT NULL,
	`is_primary` integer DEFAULT false NOT NULL
);
--> statement-breakpoint
CREATE INDEX `observation_acquisitions_observation_idx` ON `observation_acquisitions` (`observation_id`);--> statement-breakpoint
CREATE TABLE `run_acquisitions` (
	`acquisition_id` text PRIMARY KEY NOT NULL,
	`scan_id` text NOT NULL,
	`kind` text NOT NULL,
	`target` text NOT NULL,
	`topic_key` text,
	`topic_label` text,
	`planned_quota` integer DEFAULT 0 NOT NULL,
	`observed_count` integer DEFAULT 0 NOT NULL,
	`unique_count` integer DEFAULT 0 NOT NULL,
	`status` text DEFAULT 'complete' NOT NULL,
	`error` text,
	`duration_seconds` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE INDEX `run_acquisitions_scan_idx` ON `run_acquisitions` (`scan_id`);--> statement-breakpoint
ALTER TABLE `collector_state` ADD `target_unique` integer DEFAULT 100 NOT NULL;--> statement-breakpoint
ALTER TABLE `collector_state` ADD `preference_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `collector_state` ADD `source_progress_json` text DEFAULT '{}' NOT NULL;--> statement-breakpoint
ALTER TABLE `curator_preferences` ADD `preference_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `fetch_requests` ADD `kind` text DEFAULT 'account' NOT NULL;--> statement-breakpoint
ALTER TABLE `fetch_requests` ADD `preference_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `fetch_requests` ADD `bootstrap_topics_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `post_observations` ADD `preference_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `post_observations` ADD `topic_matches_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `schema_version` integer DEFAULT 1 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `period_id` text;--> statement-breakpoint
ALTER TABLE `runs` ADD `preference_version` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `target_unique` integer DEFAULT 100 NOT NULL;