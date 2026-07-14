CREATE TABLE `mutations` (
	`seq` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`kind` text NOT NULL,
	`entity_id` text NOT NULL,
	`payload_json` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `runs` (
	`scan_id` text PRIMARY KEY NOT NULL,
	`host` text NOT NULL,
	`source` text NOT NULL,
	`target` text,
	`request_id` text,
	`captured_at` text NOT NULL,
	`posts_seen` integer DEFAULT 0 NOT NULL,
	`posts_kept` integer DEFAULT 0 NOT NULL,
	`ingested_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `user_post_state` (
	`post_id` text PRIMARY KEY NOT NULL,
	`saved_at` text,
	`pinned_at` text,
	`dismissed_at` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
ALTER TABLE `account_reputation` ADD `notes` text;--> statement-breakpoint
ALTER TABLE `account_reputation` ADD `operator_override` integer DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE `post_observations` ADD `scan_id` text DEFAULT 'legacy' NOT NULL;--> statement-breakpoint
ALTER TABLE `posts` ADD `first_seen_at` text;--> statement-breakpoint
ALTER TABLE `posts` ADD `last_seen_at` text;
--> statement-breakpoint
UPDATE `posts` SET `first_seen_at`=COALESCE(`first_seen_at`,`captured_at`), `last_seen_at`=COALESCE(`last_seen_at`,`captured_at`);
--> statement-breakpoint
CREATE VIRTUAL TABLE IF NOT EXISTS `posts_fts` USING fts5(`post_id` UNINDEXED, `text`, `author`, `handle`, tokenize='porter unicode61');
--> statement-breakpoint
INSERT INTO `posts_fts`(`post_id`,`text`,`author`,`handle`) SELECT `post_id`,`text`,COALESCE(`author`,''),`handle` FROM `posts`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `posts_fts_insert` AFTER INSERT ON `posts` BEGIN INSERT INTO `posts_fts`(`post_id`,`text`,`author`,`handle`) VALUES(new.`post_id`,new.`text`,COALESCE(new.`author`,''),new.`handle`); END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `posts_fts_delete` AFTER DELETE ON `posts` BEGIN DELETE FROM `posts_fts` WHERE `post_id`=old.`post_id`; END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `posts_fts_update` AFTER UPDATE ON `posts` BEGIN DELETE FROM `posts_fts` WHERE `post_id`=old.`post_id`; INSERT INTO `posts_fts`(`post_id`,`text`,`author`,`handle`) VALUES(new.`post_id`,new.`text`,COALESCE(new.`author`,''),new.`handle`); END;
