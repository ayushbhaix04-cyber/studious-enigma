from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis

from config import get_settings
from database.models import BannedUser
from database.session import session_scope
from keyboards.menus import force_join_kb
from services.channels import list_global, missing
from services.giveaways import upsert_user
from services.settings import get_setting

log = logging.getLogger(__name__)
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    """Registers the user and silently drops everything from banned users."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)
        async with session_scope() as s:
            await upsert_user(s, user)
            banned = user.id != get_settings().bot_owner_id and await s.get(BannedUser, user.id) is not None
        if banned:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 You are banned from this bot.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🚫 You are banned from this bot.")
            return None
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, limit: int = 25, window: int = 10) -> None:
        self.limit, self.window = limit, window

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user, redis = data.get("event_from_user"), data.get("redis")
        if user is None or redis is None:
            return await handler(event, data)
        key = f"rl:{user.id}"
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, self.window)
        if n > self.limit:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Slow down a little.")
            return None
        return await handler(event, data)


class ForceJoinMiddleware(BaseMiddleware):
    """Global gatekeeper: every message, command, callback and deep link passes through here."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot or user.id == get_settings().bot_owner_id:
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "verify:global":
            return await handler(event, data)
        if isinstance(event, Message) and event.chat.type != "private":
            return await handler(event, data)
        bot: Bot = data["bot"]
        redis: Redis = data["redis"]
        if await redis.exists(f"fjok:{user.id}"):
            return await handler(event, data)
        async with session_scope() as s:
            enabled = (await get_setting(s, "force_join_enabled")) == "1"
            chans = await list_global(s)
            text = await get_setting(s, "force_join_text")
        if not enabled or not chans:
            return await handler(event, data)
        miss = await missing(bot, user.id, chans)
        if not miss:
            await redis.set(f"fjok:{user.id}", "1", ex=60)
            return await handler(event, data)
        if isinstance(event, Message) and event.text and event.text.startswith("/start "):
            await redis.set(f"pending:{user.id}", event.text.split(maxsplit=1)[1].strip(), ex=3600)
        target = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer("🔒 Join the required channels first.")
        if target is not None:
            await target.answer(text, reply_markup=force_join_kb(miss))
        return None
