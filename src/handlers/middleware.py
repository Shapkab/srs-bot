"""Owner-only middleware.

v1 is single-user. This middleware drops any update whose effective
user is not the configured OWNER_TELEGRAM_ID. When multi-user arrives,
remove this and gate features in handlers instead.

Dropped updates are logged at INFO with the offending ``user_id`` so
probing attempts are visible without leaking responses back to senders.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

log = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id != self.owner_id:
            log.info(
                "non-owner update dropped",
                extra={
                    "event": "non_owner_drop",
                    "user_id": user.id if user is not None else None,
                },
            )
            # No response to the sender — don't leak the bot's existence.
            return None
        return await handler(event, data)
