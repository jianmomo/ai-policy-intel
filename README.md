# AI Policy Intel

AI Policy Intel is a lightweight MVP for collecting AI and China policy signals, ranking them, and generating daily/weekly digests for manual review.

## Features

- YAML-based source registry
- RSS, HTML policy, GitHub search, and arXiv API collectors
- AI news and China policy classification with keyword tags
- Normalization, deduplication, classification, and scoring pipeline
- SQLite storage with run logs
- Daily and weekly Markdown digests
- Telegram split delivery for AI and policy briefs with Chinese translation
- OSS radar output for open source alternatives and complements
- FastAPI health and summary endpoints
- Docker and Python `venv` deployment paths for an HK VPS

## Project Layout

```text
app/
configs/
data/
deploy/
scripts/
tests/
```

## Local Run

1. Copy `.env.example` to `.env`
2. Create a virtual environment
3. Install requirements
4. Run the API and jobs

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/run_daily.py
python3 scripts/run_weekly.py
python3 scripts/run_policy_refresh.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Outputs land in `data/digests/`.

## Docker

```bash
docker compose -f deploy/docker-compose.yml up --build -d
docker compose -f deploy/docker-compose.yml exec app python scripts/run_daily.py
docker compose -f deploy/docker-compose.yml exec app python scripts/run_weekly.py
```

## HK VPS Deployment

1. Install Docker and Docker Compose
2. Upload the project to the VPS
3. Copy `.env.example` to `.env` and fill mail settings if needed
4. Build and start containers

```bash
cd /data/ai-policy-intel
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
```

Optional reverse proxy:

- Put Caddy or Nginx in front of `:8000`
- Keep `APP_BASE_URL` aligned with the public URL

If the HK VPS does not have Docker, the supported fallback is Python `venv`:

```bash
cd /data/ai-policy-intel
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/run_daily.py
python scripts/run_weekly.py
```

## Adding Sources

Edit `configs/sources.yaml` and add an entry with:

- `id`
- `name`
- `category`
- `region`
- `type`
- `url`
- `enabled`
- `priority`
- `tags`

Supported `type` values in the MVP:

- `rss`
- `html`
- `github`
- `arxiv`

Optional source fields:

- `query`
- `max_results`
- `extra`

These are used by the GitHub and arXiv collectors to build real API requests.

## Running Digests

Daily:

```bash
python3 scripts/run_daily.py
```

Weekly:

```bash
python3 scripts/run_weekly.py
python3 scripts/run_policy_refresh.py
```

Backup:

```bash
python3 scripts/run_backup.py
python3 scripts/restore_backup.py data/backups/<backup-file>.tar.gz
```

## Delivery

The MVP now supports direct delivery hooks for:

- SMTP email
- Telegram bot

Set these values in `.env` when you are ready:

- `DELIVERY_EMAIL_ENABLED=true`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TO`
- `SMTP_STARTTLS=true`

Telegram:

- `DELIVERY_TELEGRAM_ENABLED=true`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_AI_CHAT_ID` optional
- `TELEGRAM_POLICY_CHAT_ID` optional
- `TELEGRAM_OPS_CHAT_ID` optional, falls back to `TELEGRAM_CHAT_ID`
- `DELIVERY_HEALTH_ALERTS_ENABLED=true`
- `COLLECTOR_STALE_DAYS=3`
- `TELEGRAM_AI_LIMIT`
- `TELEGRAM_POLICY_LIMIT`

When separate chat IDs are not set, both AI and policy digests are sent to `TELEGRAM_CHAT_ID` as two separate messages.

## Manual Resend And Logs

Resend Telegram brief without recollecting:

```bash
python scripts/send_telegram_brief.py
python scripts/send_telegram_brief.py --weekly
```

Inspect recent run logs:

```bash
python scripts/print_latest_runs.py
```

## systemd Automation

The repo includes ready-to-install systemd units in `deploy/systemd/`.

Install them on the HK VPS:

```bash
cd /data/ai-policy-intel
bash deploy/systemd/install_systemd.sh /data/ai-policy-intel
```

Useful commands:

```bash
systemctl status ai-policy-intel-api.service
systemctl status ai-policy-intel-daily.timer
systemctl status ai-policy-intel-weekly.timer
systemctl status ai-policy-intel-backup.timer
systemctl status ai-policy-intel-policy-refresh.timer
systemctl list-timers | grep ai-policy-intel
journalctl -u ai-policy-intel-daily.service -n 100 --no-pager
```

Recommended on the HK VPS:

```bash
timedatectl set-timezone Asia/Hong_Kong
timedatectl
```

## Notes

- Network-heavy collectors intentionally degrade gracefully.
- GitHub and arXiv collectors support mock fallback to keep the MVP runnable.
- Telegram delivery sends AI and policy as separate messages and can route them to different chat IDs.
- Topic snapshot history is refreshed by the daily, weekly, and policy-refresh jobs so the topic pages can show trend changes over time.
- Python 3.10 works for the current MVP, while the container image uses Python 3.11.
