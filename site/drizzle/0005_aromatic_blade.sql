CREATE TABLE `collector_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`phase` text DEFAULT 'idle' NOT NULL,
	`target` text,
	`request_id` text,
	`scan_id` text,
	`observed` integer DEFAULT 0 NOT NULL,
	`pending_sync` integer DEFAULT 0 NOT NULL,
	`failed_attempts` integer DEFAULT 0 NOT NULL,
	`auth_status` text DEFAULT 'ok' NOT NULL,
	`last_error` text,
	`started_at` text,
	`updated_at` text NOT NULL,
	`last_sync_at` text,
	`last_backup_at` text
);
--> statement-breakpoint
ALTER TABLE `runs` ADD `posts_added` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `duplicates` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `candidates` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `discarded` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `signals_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `media_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `links_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `duration_seconds` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `runs` ADD `status` text DEFAULT 'complete' NOT NULL;
