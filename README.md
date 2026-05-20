# srs-bot

A Telegram vocabulary SRS bot using FSRS 6. Single-user v1.

## Stack (verified 2026-05-17)

- **Python** 3.11+ (aiogram requires 3.10+; we pin to 3.11 for typing).
- **aiogram** 3.28+ — modern async Telegram framework, v3 syntax only.
- **py-fsrs** 6.x — official FSRS 6 implementation. Latest at the time
  of writing was 6.3.x.
- **SQLAlchemy** 2.x with SQLite (WAL mode, FK enforcement on).
- **APScheduler** 3.x for the daily reminder.

## What this v1 does

- `/start`, `/help`, `/due`
- `/add front | back` — add a card (one-shot)
- `/addm` — add a card step-by-step (FSM with front → back → optional tags)
- `/review` — review due cards, rate Again/Hard/Good/Easy, FSRS reschedules
- `/undo` — roll back the most recent rating (within 10 minutes)
- `/cards [page]` — list your cards, 20 per page
- `/edit <id> front | back` — edit a card without resetting FSRS state
- `/delete <id>` — soft-delete a card (history preserved for fsrs-optimizer)
- `/export` — download all of your cards, review states, and review logs
  as a single JSONL file
- `/stats` — total cards, due now, learning/review/relearning split,
  reviews in last 7 days, retention rate over last 30 days
- `/repair` — sweep all live cards and soft-delete any whose FSRS state
  no longer parses (paired with the `cb_rate` corrupt-card guard)
- `/addimage` — add a card whose front and/or back is a photo (with an
  optional caption); see "Image cards" below
- `/remind` — smart reminders: `on` / `off` / `threshold N`. An hourly
  job nudges you when the due backlog reaches the threshold, at most
  once per 24h. Off by default; coexists with the fixed-time daily
  reminder below.
- Daily reminder at a configured time (with **catch-up** if the bot was
  offline at REMINDER_TIME — it fires once at next startup)
- New-card rate-limited by `User.daily_new_limit` (default 10 per UTC day)
- Auto-suspend ("leech") after 8 lapses on the same card
- Single-instance enforcement via POSIX flock on `<DB_PATH>.lock`
- Daily online DB backup at 03:00 in your configured timezone (see "Backups" below)

Out of scope for v1 (deliberately): LLM-generated cards, audio/TTS, web UI,
shared decks, Anki import/export. The schema supports multi-user from day
one (separate `card` and `review_state` tables), so adding those later is
a non-breaking extension.

## Project layout

```
src/
  main.py             # entry: Bot + Dispatcher + APScheduler + instance lock
  config.py           # .env -> Settings (reminder_time parsed to datetime.time)
  logging_setup.py    # JsonFormatter + configure_logging
  instance_lock.py    # POSIX flock-based single-instance guard
  db/
    models.py         # User, Card, ReviewState, ReviewLog, KV
    engine.py         # SQLAlchemy engine + session_scope() + init_db (alembic)
    crud.py           # CRUD layer; persist_review with optimistic-concurrency guard
  srs/
    scheduler.py      # the ONLY place that imports `fsrs`
  handlers/
    middleware.py     # OwnerOnlyMiddleware (single-user gate)
    start.py          # /start, /help, /due
    add_card.py       # /add, /addm (FSM)
    review.py         # /review + callback handlers
    cards.py          # /cards, /edit, /delete
    undo.py           # /undo
    export.py         # /export (JSONL)
    stats.py          # /stats
    repair.py         # /repair (corrupt-card sweep)
  keyboards/
    review.py         # inline keyboards
  jobs/
    daily_reminder.py # APScheduler job + catch-up + KV last-fired
    backup.py         # daily online sqlite3.Connection.backup at 03:00
migrations/           # Alembic chain (0001 baseline → 0004 head)
scripts/
  optimize.py         # fsrs-optimizer driver (prints; never applies)
tests/
  conftest.py         # autouse fresh_db fixture (init_db → alembic upgrade head)
  ...
```

## FSRS persistence: `card_json`

`ReviewState` stores the full FSRS card state as a single `card_json`
TEXT column, produced and consumed via `fsrs.Card.to_json()` and
`fsrs.Card.from_json()`. This is the persistence mechanism py-fsrs
itself documents for database storage.

Three projections are denormalized for query/index:

| Column        | Why duplicated                                  |
|---------------|-------------------------------------------------|
| `due`         | Indexed; primary query is "find next due card"  |
| `last_review` | Used for elapsed-days bookkeeping               |
| `state`       | Used in `ReviewLog.state_before` and analytics  |

These three fields are written together with `card_json` by
`crud.persist_review()`, so they stay in sync.

This trades a richer column-level queryability of FSRS internals
(stability, difficulty) for a guaranteed-correct round-trip via the
library's own API. We don't query stability or difficulty in the review
flow, so the trade is favorable.

## Setup

```bash
# 1. Create a venv (Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install (editable + dev tools)
pip install -e ".[dev]"

# 3. Configure
cp .env.example .env
# Fill in:
#   - BOT_TOKEN: from @BotFather
#   - OWNER_TELEGRAM_ID: your Telegram user ID (from @userinfobot)
#   - TZ, REMINDER_TIME: your preference

# 4. Run tests (no Telegram needed)
pytest -q

# 5. Run the bot
python -m src.main
```

**Run `pytest -q` before anything else.** It exercises
`add_card -> next_due_card -> apply_review -> persist_review` with the
real `fsrs` library, no Telegram involved.

## Logging

All log records are emitted as single-line JSON via the stdlib `logging`
module (no external dep). Each record has `ts`, `level`, `logger`,
`msg`; structured fields like `event`, `user_id`, `card_id` are added
when handlers pass them via `extra={...}`. Non-owner Telegram updates
are dropped silently to the sender but logged at INFO with the
offending `user_id` so probing is visible in `journalctl` / your log
aggregator.

## Instance lock

To prevent two bot processes from polling the same Telegram bot against
the same DB, `src/main.py` acquires an exclusive POSIX `flock` on
`<DB_PATH>.lock` at startup. If another instance already holds it, the
second instance logs `another instance is already running` and exits
with status 1. The lock is kernel-managed: if the holder process dies
ungracefully, the lock is reclaimed immediately. **macOS / Linux only —
Windows is not supported in v1.**

## Backups

`src/jobs/backup.py` runs once a day at **03:00** in `settings.timezone`. It
uses SQLite's online backup API (`sqlite3.Connection.backup`) to take a
consistent snapshot without stopping the bot, and writes it to:

```
<DB_PATH>.parent/backups/srs-YYYYMMDD.db
```

Any `-wal` / `-shm` siblings of the live DB are copied alongside as a
defensive measure (the snapshot is consistent on its own; the siblings
are belt-and-suspenders).

Retention: the seven most recent backups are kept; older files are
pruned each run. The backup time (03:00) is intentionally not
configurable in v1.

## Migrations (Alembic)

Schema is managed by **Alembic** (a regular runtime dep since Phase 7.2:
`init_db()` runs `alembic upgrade head` in-process at startup). You can
still drive it manually:

```bash
# Fresh DB or legacy DB — same command, the env.py handoff figures it out.
DB_PATH=./srs.db alembic upgrade head

# Inspect history / current revision:
alembic history
DB_PATH=./srs.db alembic current
```

The four revisions in `migrations/versions/` are:

| Rev    | What it does                                                  |
|--------|---------------------------------------------------------------|
| `0001` | Baseline: original four tables (pre-Phase-3 schema)           |
| `0002` | `review_log.card_json_before` — pre-review snapshot for `/undo` |
| `0003` | `card.deleted_at` — soft-delete tombstone                     |
| `0004` | `review_state.suspended_at` + `kv` table                      |

`migrations/env.py` inspects `PRAGMA user_version` before running:
`user_version == 1` means the DB was built by the (retired) one-shot
`scripts/migrate_001-003.py` scripts, so the baseline schema is already
in place. The env script stamps `alembic_version` at revision `0001`
without re-running `CREATE TABLE`, then lets `0002 → 0004` apply
normally. Fresh DBs (`user_version == 0`) run the whole chain.

## Image cards

`/addimage` opens a two-step prompt (front, then back). Each side
accepts either:

- a **photo** (with an optional caption used as the text), or
- a **plain text** message.

So you can have text-front + photo-back, photo-front + text-back,
photo-front + photo-back, or all text (which behaves identically to
`/add`). Send `/cancel` at any point to back out.

When a card has any image attachment, `/review` renders that side as a
Telegram photo with the text as its caption. Pure-text cards keep the
existing edit-in-place flow.

### Storage

Each image is downloaded once and written to
`<IMAGE_DIR>/<sha256>.jpg` (set `IMAGE_DIR` in `.env`; default
`./images`, the Fly deployment uses `/data/images` on the persistent
volume). Identical bytes dedupe automatically — the filename is the
content hash, so uploading the same photo to two cards costs one file
on disk.

On every card row, `front_image_sha256` / `back_image_sha256` are the
content-addressed identifiers, and `front_image_file_id` /
`back_image_file_id` are the matching Telegram-issued handles that the
bot uses to re-send the photo cheaply.

### Token rotation

Telegram **invalidates every `file_id` when the bot token changes**.
After a token rotation, run:

```bash
python -m scripts.reupload_images           # do it
python -m scripts.reupload_images --dry-run # list first
```

The script reads each image from `IMAGE_DIR`, re-uploads it through
the new token, captures the fresh `file_id`, and writes it back. One
DB transaction per image so a crash mid-run loses at most one entry.

## FSRS parameter optimization

After a few hundred reviews, `scripts/optimize.py` can ask
[`fsrs-optimizer`](https://github.com/open-spaced-repetition/fsrs-optimizer)
for personalized FSRS 6 weights:

```bash
pip install -e ".[optimizer]"
python -m scripts.optimize ./srs.db
```

The script **prints** suggested weights and does not write anywhere —
you copy them into `Scheduler(parameters=(...,))` in
[src/srs/scheduler.py](src/srs/scheduler.py) when you're satisfied.

## Scale path (notes, not promises)

| Concern | v1 | When to change |
|---|---|---|
| Hosting | local / VPS, polling | Webhooks before public deploy |
| DB | SQLite | Switch URL to Postgres when adding a second user |
| Scheduler | APScheduler in-process | Separate worker (Arq/Celery + Redis) at multi-user scale |
| Auth | OwnerOnlyMiddleware | Replace with per-user enable list / open signup |
| FSRS params | defaults | Run `fsrs-optimizer` against `review_log` after a few hundred reviews |
| Card content | manual `/add` | LLM ingestion of articles into cards |

## Known limitations

- `OwnerOnlyMiddleware` silently drops non-owner updates. If you want a
  "this bot is private" reply, change the middleware return.
- No rate-limiting on `/add`. Fine for one user; add a limiter before
  opening signup.
- No Anki import. Out of v1 scope.
