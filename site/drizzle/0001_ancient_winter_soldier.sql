ALTER TABLE `posts` ADD `xcancel_url` text;--> statement-breakpoint
ALTER TABLE `posts` ADD `external_links_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `posts` ADD `media_json` text DEFAULT '[]' NOT NULL;--> statement-breakpoint
ALTER TABLE `posts` ADD `article_json` text;