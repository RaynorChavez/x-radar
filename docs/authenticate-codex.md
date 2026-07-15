# Authenticate Codex for unattended curation

X Radar uses the Codex CLI and intentionally fixes collection runs to
`gpt-5.6-luna` with xhigh reasoning. Install the CLI through OpenAI's official
channel, then authenticate it as the same Linux user that will own the X Radar
services:

```bash
codex login --device-auth
codex login status
```

If `codex` is not on the service user's normal `PATH`, set its absolute path in
`.env`:

```env
XRADAR_CODEX_BIN=/home/example/.local/bin/codex
```

Before enabling timers, verify both the login and one bounded live model call:

```bash
bin/doctor --live-model-check
```

The doctor expects the exact `XRADAR_LUNA_OK` response and does not grant write
or network access for that check. A successful browser login in another user
account does not authenticate the service user.

Normal collection runs use a workspace-write Codex sandbox with network access
because the read-only Firefox collector must reach X. Keep
`XRADAR_CODEX_UNSANDBOXED=0`. The value `1` is reserved for operators who run a
dedicated account inside an independently hardened host sandbox; it is not a
general troubleshooting switch.

If login expires, collection reports an authentication failure while the local
database and existing dashboard data remain intact. Re-run device authentication
as the service user and repeat the live doctor check.
