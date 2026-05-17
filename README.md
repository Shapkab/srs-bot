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
- Daily reminder at a configured time
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

## Migrations

v1 uses `Base.metadata.create_all()` for fresh installs. Two one-shot
migration scripts are bundled for existing DBs that predate Phase 3:

```
python -m scripts.migrate_001_card_json_before /path/to/srs.db
python -m scripts.migrate_002_card_deleted_at  /path/to/srs.db
```

Both are idempotent (no-op if the column is already present). They will
be re-expressed as Alembic revisions in a later iteration.

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
