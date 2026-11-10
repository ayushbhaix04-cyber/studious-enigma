from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GlobalChannel

MEMBER_STATUSES = {"member", "administrator", "creator"}
_INVITE = re.compile(r"^(?:https?://)?t\.me/(?:\+|joinchat/)([\w-]+)/?$", re.I)
_USER = re.compile(r"^(?:https?://)?(?:t\.me/|@)([A-Za-z][\w]{3,31})/?$")
_ID = re.compile(r"^-\d{5,}$")
_REQUEST_WORDS = {"request", "join_request", "joinrequest"}


class ChannelError(Exception):
    pass


@dataclass
class ChannelRef:
    chat_id: int
    title: str
    username: str | None
    invite_link: str | None
    join_request: bool = False
    bot_is_admin: bool = True


def parse_channel_input(raw: str) -> dict[str, Any] | None:
    """Parse '@name', 't.me/name', '-100123', or '-100123 https://t.me/+abc [request]'."""
    tokens = raw.split()
    join_request = any(t.lower() in _REQUEST_WORDS for t in tokens)
    chat_id: int | None = None
    username: str | None = None
    invite: str | None = None
    for t in tokens:
        if t.lower() in _REQUEST_WORDS:
            continue
        if _ID.match(t):
            chat_id = int(t)
        elif _INVITE.match(t):
            invite = t if t.lower().startswith("http") else "https://" + t
        elif (m := _USER.match(t)):
            username = m.group(1)
    if chat_id is None and username is None:
        return None  # private channels need a numeric id: invite links alone cannot be resolved
    return {"chat_id": chat_id, "username": username, "invite_link": invite, "join_request": join_request}


async def resolve(bot: Bot, raw: str) -> ChannelRef:
    parsed = parse_channel_input(raw)
    if parsed is None:
        raise ChannelError("Use @username, a -100… chat id, or `-100… https://t.me/+invite` for private chats.")
    target: int | str = parsed["chat_id"] if parsed["chat_id"] is not None else "@" + parsed["username"]
    try:
        chat = await bot.get_chat(target)
    except TelegramAPIError as exc:
        raise ChannelError(f"Cannot access that chat ({exc.message if hasattr(exc, 'message') else exc}). "
                           "Add the bot as an admin first.") from exc
    is_admin = False
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_admin = me.status in {"administrator", "creator"}
    except TelegramAPIError:
        pass
    username = chat.username or parsed["username"]
    link = parsed["invite_link"] or (f"https://t.me/{username}" if username else None)
    if link is None:
        raise ChannelError("Private chats need an invite link: `-100… https://t.me/+invite`.")
    return ChannelRef(chat.id, chat.title or str(chat.id), username, link, parsed["join_request"], is_admin)


def link_for(ch: Any) -> str:
    return ch.invite_link or (f"https://t.me/{ch.username}" if ch.username else "https://t.me")


async def is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Strict membership. A pending join request surfaces as 'left', so it is never accepted."""
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    if m.status in MEMBER_STATUSES:
        return True
    if m.status == "restricted":
        return bool(getattr(m, "is_member", False))
    return False


async def missing(bot: Bot, user_id: int, channels: Iterable[Any]) -> list[Any]:
    chans = list(channels)
    if not chans:
        return []
    results = await asyncio.gather(*(is_member(bot, c.chat_id, user_id) for c in chans))
    return [c for c, ok in zip(chans, results) if not ok]


async def list_global(s: AsyncSession, only_enabled: bool = True) -> list[GlobalChannel]:
    q = select(GlobalChannel).order_by(GlobalChannel.id)
    if only_enabled:
        q = q.where(GlobalChannel.enabled.is_(True))
    return list((await s.execute(q)).scalars().all())
