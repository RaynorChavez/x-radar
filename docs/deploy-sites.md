# Deploy the private Sites dashboard

Split mode uses an OpenAI Sites project as a private web dashboard and D1
mirror. The collector host remains authoritative.

## Prerequisites

- An OpenAI account or workspace with Sites available in Codex.
- The repository cloned locally and opened as the current Codex workspace.
- A cryptographically random ingest token saved privately.

## Deploy

Prepare the local binding template:

```bash
cd site
cp .openai/hosting.example.json .openai/hosting.json
npm ci
npm test
```

Then ask Codex to deploy the existing `site/` project privately with:

- a D1 binding named `DB`;
- `XRADAR_INGEST_TOKEN` set to the same secret used by the collector; and
- owner-only access, unless you intentionally choose a broader policy.

After deployment, ask Codex to generate the private Sites bypass token used by
headless collector requests. Put the resulting values only in the collector's
ignored `.env`:

```env
XRADAR_MODE=split
XRADAR_SITE_URL=https://your-private-site.example
XRADAR_INGEST_TOKEN=replace-with-the-shared-random-secret
XRADAR_SITE_BYPASS_TOKEN=replace-with-the-private-bypass-token
```

Do not add the live project ID to
`site/.openai/hosting.example.json`. The generated
`site/.openai/hosting.json` is installation-specific and ignored.

## Verify

Open the deployed URL while signed in and confirm that the dashboard loads.
Then run on the collector host:

```bash
PYTHONPATH=src python3 -m xradar --db var/x-radar.sqlite site-requests
PYTHONPATH=src python3 -m xradar --db var/x-radar.sqlite sync
```

The first command should return a JSON request list and the second should report
zero failed events. A Sites outage does not delete local captures; the outbox
retries later.
