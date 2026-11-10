"""Hidden owner-only /winner command.

Never registered with BotFather (see bot.py), never in /help or menus. Anyone who is not
BOT_OWNER_ID gets *no* response at all, so the command cannot be discovered by probing.
"""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from redis.asyncio import Redis
from sqlalchemy import func, select

from config import get_settings
from database.models import User
from database.session import session_scope
from services import winners
from utils.text import esc, parse_giveaway_ref

router = Router(name="secret")

MESSAGES = {
    "ok": "🎯 Winner set.",
    "no_giveaway": "❌ Giveaway not found.",
    "no_user": "❌ That user has never started the bot.",
    "already": "ℹ️ Already a winner.",
    "bad_status": "❌ Giveaway is cancelled.",
    "full": "❌ All winner slots are taken; wait until it completes, then override to replace.",
}


async def _resolve_user(ref: str) -> int | None:
    async with session_scope() as s:
        if ref.lstrip("-").isdigit():
            u = await s.get(User, int(ref))
            return u.id if u else None
        name = ref.lstrip("@").lower()
        return (await s.execute(select(User.id).where(func.lower(User.username) == name))).scalars().first()


@router.message(Command("winner"))
async def winner_cmd(message: Message, command: CommandObject, bot: Bot, redis: Redis) -> None:
    if message.from_user is None or message.from_user.id != get_settings().bot_owner_id:
        return  # silent drop
    parts = (command.args or "").split()
    gid = parse_giveaway_ref(parts[0]) if len(parts) == 2 else None
    if gid is None:
        await message.answer("Usage: /winner &lt;giveaway_id&gt; &lt;username_or_user_id&gt;")
        return
    target = await _resolve_user(parts[1])
    if target is None:
        await message.answer(MESSAGES["no_user"] + f" ({esc(parts[1])})")
        return
    result = await winners.owner_override(bot, gid, target, message.from_user.id)
    await message.answer(MESSAGES[result])
