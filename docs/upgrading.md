# Upgrading an installation

The local SQLite database, archives, browser profile, `.env`, Sites binding,
and local D1 state are ignored runtime data. An upgrade must never replace or
delete them.

Before upgrading:

```bash
cd ~/x-radar
bin/backup
systemctl --user stop x-radar.service
```

Update the source, reinstall Python dependencies if `pyproject.toml` changed,
and reinstall the relevant services:

```bash
. .venv/bin/activate
pip install -e '.[collector]'
bin/install-user-services
```

For single-machine mode, run `bin/install-single-machine` instead. It performs a
clean dashboard dependency install and production build before restarting the
local dashboard. Schema initialization is additive and idempotent.

After upgrading, run `bin/doctor`, inspect service status, and confirm that the
post, observation, and run counts have not decreased.

The topic-discovery upgrade is additive. Keep
`XRADAR_ACQUISITION_MODE=legacy` during initial deployment if you want to test
the database and dashboard migrations before enabling 150-post mixed periods;
switch it to `mixed` and restart the timer after the manual verification scan.
