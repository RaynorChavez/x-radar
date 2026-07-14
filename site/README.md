# X Radar dashboard

The private Sites dashboard mirrors collector data in D1. It provides the daily briefing, signal archive, saved reading desk, complete observation ledger, lexical/account search, directed account-scan queue, post feedback, account controls, and collector status.

```bash
npm install
cp .openai/hosting.example.json .openai/hosting.json
npm run dev
npm test
```

The D1 binding must be named `DB`. Configure `XRADAR_INGEST_TOKEN` through Sites and use the same value on the collector. The real project binding, runtime secrets, generated output, and captured data are ignored by Git.
