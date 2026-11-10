from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LogEvent
from database.session import session_scope
from services.settings import log_channel

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 20


async def record(s: AsyncSession, event: str, actor_id: int | None = None,
                 giveaway_id: int | None = None, **payload: Any) -> None:
    """Persist an audit event in the caller's transaction (atomic with the change itself)."""
    clean = {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v)) for k, v in payload.items()}
    s.add(LogEvent(event=event, actor_id=actor_id, giveaway_id=giveaway_id, payload=clean))
    log.info("audit %s actor=%s giveaway=%s %s", event, actor_id, giveaway_id, clean)


def format_event(e: LogEvent) -> str:
    lines = [f"📋 <b>{html.escape(e.event)}</b>"]
    if e.giveaway_id:
        lines.append(f"🆔 GW-{10000 + e.giveaway_id}")
    if e.actor_id:
        lines.append(f"👤 <code>{e.actor_id}</code>")
    for k, v in (e.payload or {}).items():
        lines.append(f"• {html.escape(str(k))}: {html.escape(str(v))[:200]}")
    lines.append(f"🕒 {e.created_at:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)


async def flush(bot: Bot, limit: int = 50) -> int:
    """Deliver undelivered events to the log channel; failures stay queued and are retried."""
    sent = 0
    async with session_scope() as s:
        chat = await log_channel(s)
        if chat is None:
            return 0
        rows = (await s.execute(
            select(LogEvent).where(LogEvent.delivered.is_(False)).order_by(LogEvent.id)
            .limit(limit).with_for_update(skip_locked=True)
        )).scalars().all()
        for row in rows:
            try:
                await bot.send_message(chat, format_event(row))
                row.delivered = True
                sent += 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(min(exc.retry_after, 30))
                break
            except TelegramAPIError as exc:
                row.attempts += 1
                if row.attempts >= MAX_ATTEMPTS:
                    row.delivered = True
                log.warning("log dispatch failed (%s): %s", row.id, exc)
    return sent
