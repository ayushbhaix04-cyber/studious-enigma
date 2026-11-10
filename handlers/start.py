from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis
from sqlalchemy import func, select

from database.models import Giveaway, Participant, User, Winner
from database.session import session_scope
from handlers import participate
from keyboards.menus import home_kb, main_menu_kb
from services.channels import list_global, missing
from utils.text import esc, fmt_dt
from utils.tg import safe_edit

router = Router(name="start")
PAYLOAD = re.compile(r"^GW_(\d+)(?:_ref(\d+))?$")

WELCOME = (
    "🎁 <b>WELCOME TO GIVEAWAY HOSTER!</b> ✨\n\n"
    "The ultimate platform to create, host, and participate in verified Telegram giveaways! 🚀\n\n"
    "✨ <b>Create &amp; Host:</b> Launch your own giveaways with optional channels &amp; CAPTCHA security 🛡️\n"
    "🏆 <b>Win Prizes:</b> Participate in exclusive community rewards 🎉\n"
    "🎨 <b>Stunning Posters:</b> Generate eye-catching promotional cards 🖼️\n"
    "📊 <b>Track Performance:</b> Real-time analytics and participant metrics 📈"
)
HELP = (
    "ℹ️ <b>Help &amp; Support</b>\n\n"
    "🎁 /create — launch a giveaway\n📊 /dashboard — manage your giveaways\n"
    "🔍 Use <b>Explore</b> to browse public giveaways\n❌ /cancel — abort the current action\n\n"
    "To enter a giveaway, open its link, join the required channels, and solve the quick math check 🧮."
)
PUBLIC_COMMANDS = [("start", "Open the main menu"), ("create", "Create a giveaway"),
                   ("dashboard", "Manage your giveaways"), ("help", "Help & support"), ("cancel", "Cancel current action")]


async def launch(bot: Bot, redis: Redis, user_id: int, payload: str | None) -> None:
    m = PAYLOAD.match(payload or "")
    if m:
        n = int(m.group(1)) - 10000
        if n > 0:
            await participate.begin(bot, redis, user_id, n, int(m.group(2) or 0))
            return
    await bot.send_message(user_id, WELCOME, reply_markup=main_menu_kb())


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject, bot: Bot, redis: Redis, state: FSMContext) -> None:
    await state.clear()
    await launch(bot, redis, message.from_user.id, command.args)


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=home_kb())


@router.message(Command("cancel"))
async def on_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✅ Cancelled.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "verify:global")
async def verify_global(cb: CallbackQuery, bot: Bot, redis: Redis) -> None:
    async with session_scope() as s:
        chans = await list_global(s)
    if await missing(bot, cb.from_user.id, chans):
        await cb.answer("❌ You haven't joined all the channels yet.", show_alert=True)
        return
    await redis.set(f"fjok:{cb.from_user.id}", "1", ex=60)
    await cb.answer("✅ Verified!")
    if cb.message:
        try:
            await cb.message.delete()
        except Exception:  # noqa: BLE001 - message may be too old to delete
            pass
    pending = await redis.getdel(f"pending:{cb.from_user.id}")
    await launch(bot, redis, cb.from_user.id, pending)


@router.callback_query(F.data == "menu:home")
async def menu_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await safe_edit(cb, WELCOME, main_menu_kb())


@router.callback_query(F.data == "menu:help")
async def menu_help(cb: CallbackQuery) -> None:
    await cb.answer()
    await safe_edit(cb, HELP, home_kb())


@router.callback_query(F.data == "menu:profile")
async def menu_profile(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    async with session_scope() as s:
        u = await s.get(User, uid)
        entered = (await s.execute(select(func.count()).select_from(Participant).where(Participant.user_id == uid))).scalar_one()
        hosted = (await s.execute(select(func.count()).select_from(Giveaway).where(Giveaway.host_id == uid))).scalar_one()
    await cb.answer()
    await safe_edit(cb, (f"👤 <b>My Profile</b>\n\n🆔 <code>{uid}</code>\n📛 {esc(u.full_name)}\n"
                         f"🎁 Hosted: {hosted}\n🎟 Entered: {entered}\n🏆 Wins: {u.wins_count}\n"
                         f"📅 Joined: {fmt_dt(u.created_at)}"), home_kb())


@router.callback_query(F.data == "menu:wins")
async def menu_wins(cb: CallbackQuery) -> None:
    async with session_scope() as s:
        rows = (await s.execute(select(Giveaway, Winner).join(Winner, Winner.giveaway_id == Giveaway.id).where(
            Winner.user_id == cb.from_user.id, Winner.is_active.is_(True)).order_by(Winner.id.desc()).limit(15))).all()
    await cb.answer()
    if not rows:
        await safe_edit(cb, "🏆 <b>My Wins</b>\n\nNo wins yet — enter some giveaways! 🍀", home_kb())
        return
    lines = [f"🏆 <b>My Wins</b>\n"] + [f"• {g.code} — {esc(g.prize)} ({esc(g.title)})" for g, _ in rows]
    await safe_edit(cb, "\n".join(lines), home_kb())
