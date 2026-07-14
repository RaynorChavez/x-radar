CREATE TABLE `post_observations` (
	`id` text PRIMARY KEY NOT NULL,
	`post_id` text NOT NULL,
	`captured_at` text NOT NULL,
	`observed_index` integer DEFAULT 0 NOT NULL,
	`score` real DEFAULT 0 NOT NULL,
	`decision` text DEFAULT 'candidate' NOT NULL,
	`is_ad` integer DEFAULT false NOT NULL,
	`is_reply` integer DEFAULT false NOT NULL,
	`is_quote` integer DEFAULT false NOT NULL
);
--> statement-breakpoint
CREATE INDEX `observations_captured_idx` ON `post_observations` (`captured_at`,`observed_index`);--> statement-breakpoint
CREATE INDEX `observations_post_idx` ON `post_observations` (`post_id`);--> statement-breakpoint
INSERT INTO `post_observations` (
	`id`, `post_id`, `captured_at`, `observed_index`, `score`, `decision`, `is_ad`, `is_reply`, `is_quote`
)
SELECT
	`captured_at` || ':' || `post_id`, `post_id`, `captured_at`, 0, `score`, `decision`, `is_ad`, `is_reply`, `is_quote`
FROM `posts`;
