from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb
from redis.asyncio import Redis

from config import get_settings
from database.models import Giveaway, Participant
from database.session import session_scope
from keyboards.callbacks import CapCb, JoinCb
from services import audit, captcha
from services.channels import link_for, missing
from services.giveaways import channels_of, referral_count, register_participant
from sqlalchemy import select
from utils.text import as_utc, esc, fmt_remaining
from database.models import utcnow

router = Router(name="participate")

CLOSED_TEXT = {
    "completed": "🏁 This giveaway has already ended.",
    "cancelled": "🚫 This giveaway was cancelled.",
    "paused": "⏸ This giveaway is paused right now.",
    "ending": "⏳ This giveaway is being drawn right now.",
}


async def begin(bot: Bot, redis: Redis, user_id: int, gid: int, referrer: int = 0) -> None:
    """Steps 2–4 of the participation flow: giveaway channels -> math CAPTCHA."""
    async with session_scope() as s:
        gw = await s.get(Giveaway, gid)
        channels: list = []
        already = False
        if gw is not None:
            channels = await channels_of(s, gid, mandatory_only=True)
            already = (await s.execute(select(Participant.id).where(
                Participant.giveaway_id == gid, Participant.user_id == user_id))).first() is not None
    if gw is None:
        await bot.send_message(user_id, "❌ Giveaway not found.")
        return
    if gw.status != "active" or as_utc(gw.ends_at) <= utcnow():
        await bot.send_message(user_id, CLOSED_TEXT.get(gw.status, "⏰ This giveaway is closed."))
        return
    if gw.host_id == user_id:
        await bot.send_message(user_id, "ℹ️ Hosts can't enter their own giveaway.")
        return
    if already:
        await bot.send_message(user_id, f"🎟 You're already entered in <b>{esc(gw.title)}</b>. Good luck! 🍀")
        return
    miss = await missing(bot, user_id, channels)
    if miss:
        rows = [[Btn(text=f"📢 Join {c.title or 'Channel'}"[:60], url=link_for(c))] for c in miss]
        rows.append([Btn(text="✅ VERIFY MEMBERSHIP", callback_data=JoinCb(g=gid, r=referrer).pack())])
        await bot.send_message(
            user_id, f"📢 <b>{esc(gw.title)}</b> ({gw.code})\n\nJoin the required channels to enter, then verify:",
            reply_markup=Kb(inline_keyboard=rows))
        return
    if await captcha.is_locked(redis, user_id):
        await bot.send_message(user_id, "🛑 Too many wrong answers. Please try again in 10 minutes.")
        return
    question, options = await captcha.issue(redis, user_id, gid, get_settings().captcha_ttl)
    kb = Kb(inline_keyboard=[[Btn(text=str(o), callback_data=CapCb(g=gid, v=o, r=referrer).pack()) for o in options]])
    await bot.send_message(
        user_id, f"🛡️ <b>Anti-bot check</b>\n\nSolve this to enter <b>{esc(gw.title)}</b>:\n\n🧮 <b>{question}</b>\n\n"
                 "<i>Expires in 5 minutes.</i>", reply_markup=kb)


@router.callback_query(JoinCb.filter())
async def on_join(cb: CallbackQuery, callback_data: JoinCb, bot: Bot, redis: Redis) -> None:
    await cb.answer()
    await begin(bot, redis, cb.from_user.id, callback_data.g, callback_data.r)


@router.callback_query(CapCb.filter())
async def on_captcha(cb: CallbackQuery, callback_data: CapCb, bot: Bot, redis: Redis) -> None:
    uid, gid = cb.from_user.id, callback_data.g
    result = await captcha.verify(redis, uid, gid, callback_data.v)
    if result == "locked":
        await cb.answer("🛑 Too many wrong answers. Try again in 10 minutes.", show_alert=True)
        return
    if result == "expired":
        await cb.answer("⌛ This check expired — generating a new one.")
        await begin(bot, redis, uid, gid, callback_data.r)
        return
    if result == "wrong":
        await cb.answer("❌ Wrong answer — here's a new sum.")
        await begin(bot, redis, uid, gid, callback_data.r)
        return
    ref = callback_data.r or None
    async with session_scope() as s:
        outcome = await register_participant(s, gid, uid, ref)
        gw = await s.get(Giveaway, gid)
        n_refs = await referral_count(s, gid, uid)
        if outcome == "ok":
            await audit.record(s, "participant_joined", uid, gid, referrer=ref or 0)
    texts = {
        "full": "😕 Sorry, this giveaway just reached its participant limit.",
        "closed": "⏰ This giveaway just closed.",
        "duplicate": "🎟 You're already entered. Good luck! 🍀",
        "own": "ℹ️ Hosts can't enter their own giveaway.",
    }
    if outcome != "ok":
        await cb.answer()
        if cb.message:
            await cb.message.edit_text(texts[outcome])
        return
    me = await bot.me()
    text = (gw.confirm_message or "🎉 You're in! Good luck!")
    text = (f"🎟️ <b>Participation confirmed!</b>\n\n{esc(text)}\n\n🆔 {gw.code} · ⏱ ends in {fmt_remaining(gw.ends_at)}\n\n"
            f"🔗 Invite friends (tracked): https://t.me/{me.username}?start={gw.payload}_ref{uid}\n"
            f"👥 Your referrals: {n_refs}")
    await cb.answer("✅ Verified!")
    if cb.message:
        await cb.message.edit_text(text)
