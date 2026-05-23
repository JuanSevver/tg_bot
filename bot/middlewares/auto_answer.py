"""
AutoAnswerMiddleware — answers every CallbackQuery before the handler runs.

Telegram requires callback.answer() within 10 seconds of the button press.
Heavy DB queries can exceed this, causing:
  "Bad Request: query is too old and response timeout expired or query ID is invalid"

Strategy:
  1. Call answer() immediately (removes loading spinner, satisfies Telegram).
  2. Patch event.answer to a no-op so subsequent calls in handlers don't raise errors.
     This means show_alert=True popups become silent (no popup shown), but all
     message edits and other handler logic continues to work correctly.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery


class AutoAnswerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        # 1. Answer immediately — before any DB work.
        try:
            await event.answer()
        except Exception:
            pass  # Already expired or answered — ignore.

        # 2. Replace event.answer with a no-op so handlers can call it freely
        #    without triggering "query already answered" errors.
        async def _noop(*args: Any, **kwargs: Any) -> None:
            pass

        event.answer = _noop  # type: ignore[method-assign]

        return await handler(event, data)
