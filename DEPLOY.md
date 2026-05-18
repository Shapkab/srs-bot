# Deploying srs-bot to Fly.io

This repo deploys to Fly.io as a single always-on machine running the
polling bot. SQLite is persisted on a Fly volume mounted at `/data`;
the DB file lives at `/data/srs.db`. No public HTTP surface, no scale
to zero.

The four deployment artifacts are:

| File              | What it does                                              |
|-------------------|-----------------------------------------------------------|
| `Dockerfile`      | Single-stage `python:3.12-slim` image; `pip install .`.   |
| `.dockerignore`   | Keeps secrets, `.venv`, tests, caches out of the image.   |
| `fly.toml`        | App + region + volume mount + env (non-secret) + VM size. |
| `DEPLOY.md`       | This file.                                                |

## Prerequisites

- A [Fly.io](https://fly.io/) account with billing enabled (a single
  `shared-cpu-1x` 256 MB machine + 1 GB volume currently costs roughly
  a couple of dollars per month — verify pricing on Fly's site).
- The `fly` CLI installed locally. macOS:

  ```bash
  brew install flyctl
  ```

- Your Telegram `BOT_TOKEN` (from @BotFather) and your numeric
  `OWNER_TELEGRAM_ID` (from @userinfobot).

## One-time setup

Run these commands once, in order. Replace `<app-name>` with the value
from the `app = ...` line in `fly.toml`, and `<region>` with the value
from `primary_region`.

```bash
# 1. Authenticate the local CLI.
fly auth login

# 2. Create the app. Name must match fly.toml and be globally unique.
fly apps create <app-name>

# 3. Create the persistent volume in the same region as the app.
#    Size 1 = 1 GB; ample for years of single-user review history.
fly volumes create srs_data --region <region> --size 1

# 4. Set runtime secrets. These never enter fly.toml or the image.
fly secrets set BOT_TOKEN=<your-bot-token> OWNER_TELEGRAM_ID=<your-telegram-id>

# 5. Build the image, push it, and start the machine.
fly deploy

# 6. Pin to exactly one always-on machine in the chosen region.
#    Run this only on the first deploy; subsequent `fly deploy` reuses
#    the existing machine.
fly scale count 1 --region <region>
```

Verify the bot is alive with `fly logs` — you should see structured
JSON records for "DB ready at /data/srs.db" and "Scheduler started;
daily reminder at 09:00 ...".

## Regular deploys

After a code change, rebuild and roll the running machine with:

```bash
fly deploy
```

## Logs

```bash
fly logs
```

Each line is a single JSON object emitted by `JsonFormatter` in
`src/logging_setup.py`. Common things to grep for:

- `"event":"non_owner_drop"` — someone other than you messaged the bot.
- `"event":"repair_soft_delete"` — `/repair` removed a corrupt card.
- `"msg":"backup written"` — the daily 03:00 snapshot job ran.

## SSH into the machine

```bash
fly ssh console
```

You land in `/app` inside the container. From there:

```bash
# Inspect the live DB.
sqlite3 /data/srs.db ".tables"

# Re-run migrations by hand (init_db already does this at startup).
DB_PATH=/data/srs.db alembic current
DB_PATH=/data/srs.db alembic upgrade head

# Try the FSRS optimizer (requires the optimizer extra to be installed
# inside the container; not part of the deployed image by default).
python -m scripts.optimize /data/srs.db
```

## Back up the database

The bot writes its own daily snapshots to `/data/backups/srs-YYYYMMDD.db`
inside the volume (see `src/jobs/backup.py`). To copy one to your
laptop:

```bash
fly ssh sftp shell
> ls /data/backups/
> get /data/backups/srs-<YYYYMMDD>.db ./srs-<YYYYMMDD>.db.bak
> quit
```

To copy the live DB directly (SQLite's online backup keeps it
consistent):

```bash
fly ssh sftp shell
> get /data/srs.db ./srs.db.bak
> quit
```

**Don't forget `IMAGE_DIR`.** Image-card bytes live at `/data/images/`
on the same Fly volume and are referenced by SHA-256 from the DB; a
backup that only includes `srs.db` will resolve to cards whose photo
data is gone. Pull the whole directory alongside the DB snapshot —
`fly ssh sftp shell` then `get -r /data/images ./images.bak`.

## Adjusting non-secret config

`TZ`, `REMINDER_TIME`, `LOG_LEVEL`, and `DB_PATH` live in `fly.toml`
under `[env]`. The defaults carried over from `.env.example` are
`TZ=Europe/Sofia`, `REMINDER_TIME=09:00`, `LOG_LEVEL=INFO`. If you're
not in Europe/Sofia, edit `fly.toml` and `fly deploy` to update.

To change a secret (rotate the bot token, change owner):

```bash
fly secrets set BOT_TOKEN=<new-token>
```

Fly redeploys the machine automatically when secrets change.

## Tear down

> **WARNING:** this permanently deletes the Fly volume and every review
> log + card you've ever created in this bot. If there's any chance you
> want the data later, copy `/data/srs.db` off the machine first (see
> "Back up the database" above).

```bash
fly apps destroy <app-name>
```

This removes the app, its volumes, machines, and secrets.
