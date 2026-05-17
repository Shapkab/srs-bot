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
  main.py             # entry: Bot + Dispatcher + APScheduler
  config.py           # .env -> Settings
  db/
    models.py         # User, Card, ReviewState, ReviewLog
    engine.py         # SQLAlchemy engine + session_scope()
    crud.py           # small CRUD layer used by handlers
  srs/
    scheduler.py      # the ONLY place that imports `fsrs`
  handlers/
    middleware.py     # OwnerOnlyMiddleware (single-user gate)
    start.py          # /start, /help, /due
    add_card.py       # /add
    review.py         # /review + callback handlers
  keyboards/
    review.py         # inline keyboards
  jobs/
    daily_reminder.py # APScheduler job
    backup.py         # daily online sqlite3.Connection.backup at 03:00
tests/
  test_srs_roundtrip.py
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
real `fsrs` library, no Telegram involved. If anything is wrong about
the assumed `fsrs.Card.to_json/from_json` or `Scheduler.review_card`
shape, you'll see it immediately and locally.

## Verification status

I built this against the py-fsrs README and DeepWiki-mirrored source
docs without being able to install `fsrs` in my sandbox. The tests have
not been executed against the live library. Things to know:

**Confirmed against documentation:**
- Imports `Scheduler, Card, Rating, State, ReviewLog`
- `scheduler.review_card(card, rating)` returns `(card, review_log)`
- `Card()` produces a card due immediately, in Learning state
- `Card.to_json()` / `Card.from_json(s)` is the documented round-trip
- `Rating` and `State` integer values
- py-fsrs uses UTC only

**Not yet exercised in a real run:**
- That the full pytest passes end-to-end
- That `fsrs_log.review_datetime` is the exact attribute name (search
  results show this string in printed examples; assumed correct)
- aiogram v3.28 dependency injection via `dp["settings"] = settings`
  (documented v3 pattern; not verified by running the bot)

If `pytest -q` fails, paste the traceback and I'll fix it. The most
likely failure point is `src/srs/scheduler.py` — that's the only file
that touches `fsrs`.

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

Schema is managed by **Alembic** (`pip install -e ".[dev]"`).
Migrations are not auto-applied at startup — run them by hand:

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
