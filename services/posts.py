from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import Giveaway, PublishedMessage, User
from database.session import session_scope
from posters.generator import generate_poster
from services.giveaways import channels_of, count_participants
from utils.text import esc, fmt_dt, truncate


def join_kb(username: str, gw: Giveaway) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🎁 JOIN GIVEAWAY", url=f"https://t.me/{username}?start={gw.payload}")]])


def caption(gw: Giveaway, participants: int, n_channels: int) -> str:
    lines = [f"🎁 <b>{esc(gw.title)}</b>", "", f"🏆 <b>Prize:</b> {esc(gw.prize)}",
             f"👥 <b>Winners:</b> {gw.winners_count}", f"⏱ <b>Ends:</b> {fmt_dt(gw.ends_at)}",
             f"🆔 <b>{gw.code}</b>", "", esc(truncate(gw.description, 350))]
    if gw.rules:
        lines += ["", f"📜 <b>Rules:</b> {esc(truncate(gw.rules, 120))}"]
    if n_channels:
        lines.append(f"📢 Required channels: {n_channels}")
    if gw.max_participants:
        lines.append(f"🎟 Entries: {participants}/{gw.max_participants}")
    elif participants:
        lines.append(f"🎟 Entries: {participants}")
    return "\n".join(lines)


async def send_post(bot: Bot, chat_id: int, gw: Giveaway, record: bool = False) -> Message:
    """Send the poster + caption + JOIN button; caches the poster file_id after first upload."""
    me = await bot.me()
    async with session_scope() as s:
        n = await count_participants(s, gw.id)
        chans = await channels_of(s, gw.id, mandatory_only=True)
        host = await s.get(User, gw.host_id)
    text = caption(gw, n, len(chans))
    kb = join_kb(me.username or "", gw)
    photo: str | BufferedInputFile
    fresh = not gw.poster_file_id
    if fresh:
        png = await asyncio.to_thread(
            generate_poster, gw.title, gw.prize, gw.winners_count, fmt_dt(gw.ends_at),
            (host.full_name if host and host.full_name else "Host"), gw.code, gw.poster_theme)
        photo = BufferedInputFile(png, filename=f"{gw.code}.png")
    else:
        photo = gw.poster_file_id  # type: ignore[assignment]
    if len(text) <= 1024:
        msg = await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
    else:
        msg = await bot.send_photo(chat_id, photo)
        msg = await bot.send_message(chat_id, text, reply_markup=kb)
    async with session_scope() as s:
        if fresh and msg.photo:
            g = await s.get(Giveaway, gw.id)
            g.poster_file_id = msg.photo[-1].file_id
            gw.poster_file_id = g.poster_file_id
        if record:
            s.add(PublishedMessage(giveaway_id=gw.id, chat_id=msg.chat.id, message_id=msg.message_id))
    return msg
