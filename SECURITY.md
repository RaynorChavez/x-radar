# Security

## Sensitive local state

An X Radar installation contains an authenticated browser profile, API tokens,
captured posts, archives, account controls, and user reading state. Never commit
or upload `.env`, `var/`, `public/feed.json`, `site/.openai/hosting.json`, browser
profiles, database files, backups, or diagnostic screenshots.

The collector is intentionally read-only on X. Changes that add likes, reposts,
replies, follows, bookmarks, direct messages, or account-setting mutations are
outside the project safety model.

## Network exposure

Single-machine mode listens on `127.0.0.1:8787` by default. Keep it loopback-only
unless an authenticated reverse proxy, Tailscale Serve, or an equivalent access
layer is in front of it. Do not expose the local dashboard directly to the
public internet.

## Reporting a vulnerability

Open a private security advisory in the GitHub repository. Do not include live
tokens, cookies, captured private data, or database exports in the report.
