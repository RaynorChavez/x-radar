# Contributing

Contributions should preserve four invariants:

1. X collection remains read-only.
2. The collector host remains authoritative and continues through dashboard
   outages.
3. Every seen post is retained locally while the default UI remains curated.
4. No installation-specific identifier, secret, browser profile, or captured
   post enters Git history.

Before opening a pull request, run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 tools/check_public_tree.py
cd site
cp -n .openai/hosting.example.json .openai/hosting.json
npm ci
npm run lint
npm test
```

Use synthetic fixtures in tests and examples. Keep deployment-specific
automation in the operator's private infrastructure rather than the product
repository.
