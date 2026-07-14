CREATE TABLE `curator_preferences` (
	`id` integer PRIMARY KEY NOT NULL,
	`instructions` text DEFAULT '' NOT NULL,
	`topics_json` text DEFAULT '[]' NOT NULL,
	`updated_at` text NOT NULL
);
