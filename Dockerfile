# srs-bot — single-stage runtime image for Fly.io.
#
# Long-polling Telegram bot; no inbound HTTP. The bot reads its
# configuration from environment variables (see src/config.py); the
# SQLite DB path is overridden in fly.toml to point at the mounted
# Fly volume.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# COPY before pip install: setuptools needs src/ at build time to
# package it (see [tool.setuptools.packages.find] in pyproject.toml).
# alembic.ini and migrations/ also need to be present at /app so that
# init_db() can locate them at startup
# (src/db/engine.py:_ALEMBIC_INI = parents[2] / "alembic.ini").
COPY . .

RUN pip install --no-cache-dir .

CMD ["python", "-m", "src.main"]
