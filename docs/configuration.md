# Configuration

X Radar has one codebase and two runtime modes.

| Setting | Split mode | Single-machine mode |
| --- | --- | --- |
| `XRADAR_MODE` | `split` | `single` |
| `XRADAR_SITE_URL` | private Sites URL | `http://127.0.0.1:8787` |
| `XRADAR_INGEST_TOKEN` | same random value in Sites and `.env` | random local value |
| `XRADAR_SITE_BYPASS_TOKEN` | private Sites bypass token | empty |
| `XRADAR_HOST` | collector label, such as `pi` | machine label |
| `XRADAR_ACQUISITION_MODE` | `mixed` normally; `legacy` for rollback | same |
| `XRADAR_GECKODRIVER` | optional absolute geckodriver path for a dedicated service user | usually omit |
| `XRADAR_FIREFOX` | optional absolute Firefox binary path | usually omit |
| `XRADAR_CODEX_BIN` | optional absolute Codex CLI path | same |
| `XRADAR_CODEX_UNSANDBOXED` | keep `0`; use `1` only inside an independent host sandbox | same |
| `XRADAR_SEMANTIC_PYTHON` | Python from the isolated semantic environment | same environment Python |
| `XRADAR_EMBEDDING_MODEL_DIR` | private host model directory | local model directory |
| `XRADAR_SEMANTIC_HMAC_KEYS` | JSON key-id to secret map; required for Funnel | omit only with explicit local mode |
| `XRADAR_SEMANTIC_LOCAL_MODE` | unset | `1` when the service is loopback-only |

Generate an ingest token with a cryptographically secure password generator.
Never store the real value in Git. In split mode, add it to the Sites runtime
and the collector `.env`. In single-machine mode, Wrangler reads the root
`.env` when the local dashboard service starts.

Personal curator topics, custom instructions, account controls, saved posts,
and scan requests are runtime database records. They are not configuration
files and should not be copied into the public repository.

The ignored `site/.openai/hosting.json` identifies one Sites installation. The
tracked `.openai/hosting.example.json` is the portable template.

Mixed acquisition targets 150 unique posts per period across Home, active-topic
search, known topic accounts, and bounded exploration. Setting
`XRADAR_ACQUISITION_MODE=legacy` restores the earlier single-target 100-post
Home behavior without changing or deleting topic memory.

The Sites runtime additionally holds `XRADAR_SEMANTIC_UPSTREAM_URL`,
`XRADAR_SEMANTIC_HMAC_ACTIVE_KEY_ID`, and the matching
`XRADAR_SEMANTIC_HMAC_KEYS`. These are server-only values. Use a different HMAC
secret from the ingest token and rotate by temporarily retaining both key IDs.

The curator model is intentionally fixed to `gpt-5.6-luna` with xhigh reasoning
for collection runs. `bin/doctor --live-model-check` verifies that the installed
Codex CLI is logged in and that the subscription can access that model.
