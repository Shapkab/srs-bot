"""Smoke test for the fsrs-optimizer entry point.

Per Phase 6.2 of the brief: imports cleanly and runs on an empty DB
without crashing, printing the "not enough data" branch. The actual
optimizer wiring is exercised by hand once enough review history exists.
"""

from __future__ import annotations

from pathlib import Path

from scripts.optimize import main


def test_optimize_main_prints_not_enough_data_on_empty_db(
    fresh_db: Path, capsys
) -> None:
    rc = main([__file__, str(fresh_db)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "not enough data" in captured.out.lower()


def test_optimize_main_errors_on_missing_path(tmp_path: Path, capsys) -> None:
    rc = main([__file__, str(tmp_path / "does-not-exist.db")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "no such file" in captured.err.lower()


def test_optimize_main_usage(capsys) -> None:
    rc = main([__file__])
    assert rc == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()
