"""Structured JSON logging.

One log record → one JSON line. Pulled out of main.py so it can be
configured before any other module emits a record. Stdlib only; no
new dependency.

Structured fields are passed via the standard ``extra=`` kwarg of the
logging API, e.g.:

    log.info("non-owner update dropped",
             extra={"event": "non_owner_drop", "user_id": 12345})

Recognized extras (added to the JSON if present): ``event``, ``user_id``,
``card_id``. Anything else passed via ``extra=`` is ignored to keep the
schema stable.
"""

from __future__ import annotations

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """Emit each LogRecord as a JSON object on a single line."""

    EXTRA_KEYS: tuple[str, ...] = ("event", "user_id", "card_id")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in self.EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Replace the root handlers with a single stdout handler that uses
    JsonFormatter. Safe to call multiple times.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
