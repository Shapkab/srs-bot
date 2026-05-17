"""Print the 21 FSRS parameters that fsrs-optimizer would recommend
for this user's review history.

This script does NOT apply the new weights — that's a deliberate
human-in-the-loop step. Copy them into ``Scheduler(parameters=(...,))``
in ``src/srs/scheduler.py`` once you've eyeballed the output.

Requires the optional dep: ``pip install -e ".[optimizer]"``.
A few hundred reviews is the rough lower bound for meaningful output.

Usage:
    python -m scripts.optimize /path/to/srs.db
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from src.db.engine import init_db, session_scope
from src.db.models import ReviewLog

# A heuristic minimum — fsrs-optimizer needs enough rows to fit 21
# parameters without degenerating. Anything under this prints "not enough
# data" and exits cleanly.
MIN_REVIEWS = 100


def _count_reviews() -> int:
    with session_scope() as s:
        from sqlalchemy import func

        return s.scalar(select(func.count()).select_from(ReviewLog)) or 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path-to-srs.db>", file=sys.stderr)
        return 2
    db_path = Path(argv[1])
    if not db_path.exists():
        print(f"no such file: {db_path}", file=sys.stderr)
        return 1

    init_db(db_path)
    n = _count_reviews()
    if n < MIN_REVIEWS:
        print(f"not enough data: {n} review(s); need at least {MIN_REVIEWS}.")
        return 0

    # Import lazily so users can run this on an empty DB without having
    # fsrs-optimizer installed.
    try:
        from fsrs_optimizer import Optimizer  # type: ignore[import-not-found]
    except ImportError:
        print(
            "fsrs-optimizer is not installed. Install with: "
            'pip install -e ".[optimizer]"',
            file=sys.stderr,
        )
        return 1

    # The actual optimizer API takes a CSV / DataFrame of reviews; the
    # exact shape depends on the version installed. This script is a stub
    # that points at the DB rows you'll want to feed in and prints what
    # comes back. Adjust to match the optimizer version you install.
    optimizer = Optimizer()
    weights = optimizer.optimize_with_db(str(db_path))  # type: ignore[attr-defined]
    print("Suggested FSRS 6 parameters (21 weights):")
    print(tuple(weights))
    print(
        "\nNot applied. To use, paste into Scheduler(parameters=(...,)) "
        "in src/srs/scheduler.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
