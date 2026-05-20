"""One-time backfill of front_pronunciation for existing cards.

Generates IPA for every live card that has no pronunciation yet.
Reads OPENAI_API_KEY and DB_PATH from the environment (same as the
bot). Run from the repo root:

    python -m scripts.backfill_pronunciation
    python scripts/backfill_pronunciation.py

A per-card failure is logged and skipped — the run continues. The
write is committed once at the end (a card with a failed lookup keeps
NULL and is simply picked up on a re-run).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python scripts/backfill_pronunciation.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from src.db.engine import init_db, session_scope  # noqa: E402
from src.db.models import Card  # noqa: E402
from src.utils.pronunciation import generate_pronunciation  # noqa: E402


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    # init_db must run before session_scope — it builds the engine and
    # session factory. (The DB itself is migrated to head as a side
    # effect; harmless if already current.)
    db_path = Path(os.environ.get("DB_PATH", "srs.db"))
    init_db(db_path)

    failures = 0
    with session_scope() as s:
        cards = s.scalars(
            select(Card).where(
                Card.deleted_at.is_(None),
                Card.front_pronunciation.is_(None),
            )
        ).all()
        print(f"Found {len(cards)} cards to backfill")

        for i, card in enumerate(cards, 1):
            try:
                ipa = generate_pronunciation(card.front, api_key)
                card.front_pronunciation = ipa
                print(f"[{i}/{len(cards)}] #{card.id}: {card.front} -> {ipa}")
            except Exception as e:  # noqa: BLE001 — log and continue
                failures += 1
                print(f"[{i}/{len(cards)}] #{card.id}: FAILED - {e}", file=sys.stderr)

    print(f"Done! ({failures} failure(s) — re-run to retry those.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
