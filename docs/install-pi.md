# Raspberry Pi collector with a Sites dashboard

This mode keeps collection, SQLite, archives, and ranking on an always-on Linux
machine while the private dashboard runs on OpenAI Sites.

## 1. Prepare the collector

Clone the repository as `~/x-radar`, then install Python dependencies:

```bash
cd ~/x-radar
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[collector]'
cp .env.example .env
```

Set `XRADAR_MODE=split`, the private Sites URL, a random ingest token, the Sites
bypass token, and a non-identifying host label in `.env`.

Install Firefox, geckodriver, and the Codex CLI through their official
installation channels. Run `bin/doctor` before installing services.

## 2. Authenticate X manually

Create `var/firefox-profile`, launch Firefox with that profile, and sign in to X
yourself. Never transfer the profile to a public or shared location. A successful
profile contains `var/firefox-profile/cookies.sqlite`.

## 3. Configure the dashboard

Create a private Sites project from `site/`, retain the D1 binding name `DB`,
and configure the same `XRADAR_INGEST_TOKEN` in its runtime. Generate a private
Sites bypass token for the collector.

## 4. Enable collection

```bash
cd ~/x-radar
bin/install-user-services
sudo loginctl enable-linger "$USER"
systemctl --user list-timers 'x-radar*'
```

The hourly timer collects the home feed. The dispatcher checks directed scan
requests every minute, synchronization retries every ten minutes, and backups
run daily. `flock` prevents overlapping collection cycles.

## 5. Verify

```bash
PYTHONPATH=src python3 -m xradar --db var/x-radar.sqlite status
systemctl --user status x-radar.service
systemctl --user status x-radar-sync.service
```
