# Single-machine installation

Single-machine mode runs the collector, SQLite archive, local D1 mirror, API,
and dashboard on one Linux machine. It uses the same application code and data
contracts as split Pi + Sites mode.

## 1. Install and configure

Clone the repository as `~/x-radar` and install Python dependencies:

```bash
cd ~/x-radar
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[collector]'
cp .env.example .env
```

Set:

```env
XRADAR_MODE=single
XRADAR_SITE_URL=http://127.0.0.1:8787
XRADAR_INGEST_TOKEN=replace-with-a-random-secret
XRADAR_SITE_BYPASS_TOKEN=
XRADAR_HOST=single-machine
XRADAR_ACQUISITION_MODE=mixed
```

Install Node.js, Firefox, geckodriver, and the Codex CLI, then follow the
[manual X authentication guide](authenticate-x.md).

## 2. Install services

```bash
bin/doctor
bin/install-single-machine
sudo loginctl enable-linger "$USER"
```

The installer builds the dashboard, starts a loopback-only Workers runtime with
persistent local D1 state under `var/single-machine`, and enables the same
collector, dispatcher, sync, and backup timers used in split mode.

Open `http://127.0.0.1:8787` on the host. For remote access, put Tailscale Serve
or another authenticated reverse proxy in front of the loopback listener. Do
not bind the dashboard directly to a public interface.

## 3. Verify

```bash
curl -fsS http://127.0.0.1:8787/api/status
systemctl --user status x-radar-dashboard.service
systemctl --user list-timers 'x-radar*'
```
