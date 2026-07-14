CREATE TABLE `account_reputation` (
	`handle` text PRIMARY KEY NOT NULL,
	`disposition` text DEFAULT 'normal' NOT NULL,
	`strike_points` integer DEFAULT 0 NOT NULL,
	`confidence` real DEFAULT 0 NOT NULL,
	`reasons_json` text DEFAULT '[]' NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `fetch_requests` (
	`id` text PRIMARY KEY NOT NULL,
	`handle` text NOT NULL,
	`include_replies` integer DEFAULT true NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`requested_at` text NOT NULL,
	`completed_at` text,
	`result_count` integer,
	`error` text
);
--> statement-breakpoint
CREATE INDEX `fetch_status_idx` ON `fetch_requests` (`status`,`requested_at`);--> statement-breakpoint
CREATE TABLE `posts` (
	`post_id` text PRIMARY KEY NOT NULL,
	`url` text NOT NULL,
	`handle` text NOT NULL,
	`author` text,
	`text` text NOT NULL,
	`posted_at` text,
	`captured_at` text NOT NULL,
	`is_ad` integer DEFAULT false NOT NULL,
	`is_reply` integer DEFAULT false NOT NULL,
	`is_quote` integer DEFAULT false NOT NULL,
	`source_url` text,
	`engagement_json` text DEFAULT '{}' NOT NULL,
	`score` real DEFAULT 0 NOT NULL,
	`decision` text DEFAULT 'candidate' NOT NULL,
	`reasons_json` text DEFAULT '[]' NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `posts_url_unique` ON `posts` (`url`);--> statement-breakpoint
CREATE INDEX `posts_captured_idx` ON `posts` (`captured_at`);--> statement-breakpoint
CREATE INDEX `posts_decision_idx` ON `posts` (`decision`);--> statement-breakpoint
CREATE INDEX `posts_handle_idx` ON `posts` (`handle`);