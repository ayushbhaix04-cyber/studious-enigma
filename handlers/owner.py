from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from config import get_settings
from database.models import (
    BannedUser, Giveaway, GlobalChannel, LogEvent, Participant, User, Winner, utcnow,
)
from database.session import session_scope
from keyboards.callbacks import GwCb, OwnCb
from keyboards.menus import owner_panel_kb
from scheduler.context import ctx
from services import audit
from services.channels import ChannelError, is_member, resolve
from services.settings import get_setting, log_channel, set_setting
from utils.text import esc, fmt_dt
from utils.tg import safe_edit

router = Router(name="owner")


class IsOwner(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id == get_settings().bot_owner_id


router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


class OwnerSt(StatesGroup):
    add_channel = State()
    prompt_text = State()
    broadcast = State()


def _back() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="⬅️ Panel", callback_data=OwnCb(a="home").pack())]])


@router.message(Command("panel", "owner"))
async def panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("👑 <b>Owner Control Panel</b>", reply_markup=owner_panel_kb())


# ───────────── global channels ─────────────
async def _channel_list_view() -> tuple[str, Kb]:
    async with session_scope() as s:
        chans = (await s.execute(select(GlobalChannel).order_by(GlobalChannel.id))).scalars().all()
        enabled = (await get_setting(s, "force_join_enabled")) == "1"
    lines = [f"🔒 <b>Global Force Join</b>: {'ON ✅' if enabled else 'OFF ⛔'}\n"]
    rows = []
    for c in chans:
        lines.append(f"{'🟢' if c.enabled else '⚪️'} <b>{esc(c.title)}</b> <code>{c.chat_id}</code>"
                     f"{' 📩' if c.join_request else ''}")
        rows.append([Btn(text=f"{'Disable' if c.enabled else 'Enable'} · {c.title[:18]}",
                         callback_data=OwnCb(a="fjtoggle", id=c.id).pack()),
                     Btn(text="🗑 Remove", callback_data=OwnCb(a="fjdel", id=c.id).pack())])
    if not chans:
        lines.append("No channels configured. Use /addchannel.")
    rows.append([Btn(text="➕ Add channel", callback_data=OwnCb(a="fjadd").pack()),
                 Btn(text="🩺 Test", callback_data=OwnCb(a="fjtest").pack())])
    rows.append([Btn(text="⬅️ Panel", callback_data=OwnCb(a="home").pack())])
    return "\n".join(lines), Kb(inline_keyboard=rows)


@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message, state: FSMContext) -> None:
    await state.set_state(OwnerSt.add_channel)
    await message.answer("➕ Send the channel: <code>@name</code>, <code>-100…</code>, or "
                         "<code>-100… https://t.me/+invite</code> [request]. /cancel to abort.")


@router.message(OwnerSt.add_channel)
async def on_add_channel(message: Message, state: FSMContext, bot: Bot) -> None:
    try:
        ref = await resolve(bot, message.text or "")
    except ChannelError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    async with session_scope() as s:
        exists = (await s.execute(select(GlobalChannel).where(GlobalChannel.chat_id == ref.chat_id))).scalar_one_or_none()
        if exists is None:
            s.add(GlobalChannel(chat_id=ref.chat_id, title=ref.title, username=ref.username,
                                invite_link=ref.invite_link, join_request=ref.join_request))
            await audit.record(s, "global_channel_added", message.from_user.id, chat=ref.chat_id)
    await state.clear()
    warn = "" if ref.bot_is_admin else "\n⚠️ The bot is not an admin there — membership checks will fail."
    await message.answer(f"✅ Added <b>{esc(ref.title)}</b>.{warn}")


@router.message(Command("delchannel", "listchannels"))
async def cmd_channels(message: Message) -> None:
    text_, kb = await _channel_list_view()
    await message.answer(text_, reply_markup=kb)


async def _health_report(bot: Bot) -> str:
    async with session_scope() as s:
        chans = (await s.execute(select(GlobalChannel).order_by(GlobalChannel.id))).scalars().all()
    if not chans:
        return "No global channels configured."
    out = []
    for c in chans:
        try:
            chat = await bot.get_chat(c.chat_id)
            me = await bot.get_chat_member(c.chat_id, bot.id)
            count = await bot.get_chat_member_count(c.chat_id)
            admin = me.status in ("administrator", "creator")
            out.append(f"{'✅' if admin else '⚠️'} {esc(chat.title or str(c.chat_id))}: {count} members, "
                       f"{'bot admin' if admin else 'BOT NOT ADMIN'}{'' if c.invite_link else ', no link'}")
        except TelegramAPIError as exc:
            out.append(f"❌ {esc(c.title)} <code>{c.chat_id}</code>: {esc(str(exc))[:80]}")
    return "🩺 <b>Channel health</b>\n\n" + "\n".join(out)


@router.message(Command("testchannels"))
async def cmd_test(message: Message, bot: Bot) -> None:
    await message.answer(await _health_report(bot))


@router.message(Command("channelsettings"))
async def cmd_settings(message: Message) -> None:
    text_, kb = await _settings_view()
    await message.answer(text_, reply_markup=kb)


async def _settings_view() -> tuple[str, Kb]:
    async with session_scope() as s:
        enabled = (await get_setting(s, "force_join_enabled")) == "1"
        prompt = await get_setting(s, "force_join_text")
    kb = Kb(inline_keyboard=[
        [Btn(text=f"Force join: {'ON ✅' if enabled else 'OFF ⛔'} (tap to toggle)", callback_data=OwnCb(a="fjflag").pack())],
        [Btn(text="✏️ Edit prompt text", callback_data=OwnCb(a="fjprompt").pack())],
        [Btn(text="⬅️ Panel", callback_data=OwnCb(a="home").pack())]])
    return f"⚙️ <b>Bot settings</b>\n\n<b>Prompt text:</b>\n{prompt}", kb


@router.message(OwnerSt.prompt_text)
async def on_prompt_text(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text) > 1000:
        await message.answer("⚠️ Send text up to 1000 chars (HTML allowed).")
        return
    async with session_scope() as s:
        await set_setting(s, "force_join_text", message.html_text)
        await audit.record(s, "force_join_text_changed", message.from_user.id)
    await state.clear()
    await message.answer("✅ Prompt updated.")


# ───────────── ban / log / broadcast ─────────────
async def _target_id(ref: str) -> int | None:
    if ref.lstrip("-").isdigit():
        return int(ref)
    async with session_scope() as s:
        return (await s.execute(select(User.id).where(func.lower(User.username) == ref.lstrip("@").lower()))).scalars().first()


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    uid = await _target_id(parts[0]) if parts else None
    if uid is None or uid == get_settings().bot_owner_id:
        await message.answer("Usage: /ban &lt;user_id|@username&gt; [reason]")
        return
    async with session_scope() as s:
        if await s.get(BannedUser, uid) is None:
            s.add(BannedUser(user_id=uid, reason=parts[1] if len(parts) > 1 else None, banned_by=message.from_user.id))
        await audit.record(s, "user_banned", message.from_user.id, target=uid, reason=parts[1] if len(parts) > 1 else "")
    await message.answer(f"🚫 Banned <code>{uid}</code>.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject) -> None:
    uid = await _target_id((command.args or "").strip()) if command.args else None
    if uid is None:
        await message.answer("Usage: /unban &lt;user_id|@username&gt;")
        return
    async with session_scope() as s:
        row = await s.get(BannedUser, uid)
        if row:
            await s.delete(row)
        await audit.record(s, "user_unbanned", message.from_user.id, target=uid)
    await message.answer(f"✅ Unbanned <code>{uid}</code>.")


@router.message(Command("setlog"))
async def cmd_setlog(message: Message, command: CommandObject, bot: Bot) -> None:
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer("Usage: /setlog &lt;chat_id&gt; (the bot must be able to post there)")
        return
    try:
        await bot.send_message(int(arg), "📋 Log channel connected ✅")
    except TelegramAPIError as exc:
        await message.answer(f"⚠️ Cannot post there: {esc(str(exc))[:150]}")
        return
    async with session_scope() as s:
        await set_setting(s, "log_channel_id", arg)
        await audit.record(s, "log_channel_set", message.from_user.id, chat=arg)
    await message.answer("✅ Log channel saved.")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(OwnerSt.broadcast)
    await message.answer("📣 Send the message to broadcast (text, photo, anything). /cancel to abort.")


@router.message(OwnerSt.broadcast)
async def on_broadcast_msg(message: Message, state: FSMContext) -> None:
    await state.update_data(chat=message.chat.id, mid=message.message_id)
    await state.set_state(None)
    await message.answer("Send this to <b>all users</b>?", reply_markup=Kb(inline_keyboard=[[
        Btn(text="✅ Send", callback_data=OwnCb(a="bcgo").pack()), Btn(text="❌ Cancel", callback_data=OwnCb(a="home").pack())]]))


async def _broadcast(bot: Bot, chat: int, mid: int) -> tuple[int, int]:
    async with session_scope() as s:
        ids = list((await s.execute(select(User.id).where(User.id.not_in(select(BannedUser.user_id))))).scalars())
    sent = failed = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, chat, mid)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.copy_message(uid, chat, mid)
                sent += 1
            except TelegramAPIError:
                failed += 1
        except (TelegramForbiddenError, TelegramAPIError):
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msgs/s, well under Telegram's limit
    return sent, failed


# ───────────── panel callbacks ─────────────
@router.callback_query(OwnCb.filter())
async def on_panel(cb: CallbackQuery, callback_data: OwnCb, bot: Bot, redis: Redis, state: FSMContext) -> None:
    a = callback_data.a
    await cb.answer()
    if a == "home":
        await state.set_state(None)
        await safe_edit(cb, "👑 <b>Owner Control Panel</b>", owner_panel_kb())
    elif a == "stats":
        async with session_scope() as s:
            def cnt(m, *w):
                return s.scalar(select(func.count()).select_from(m).where(*w))
            users, banned, parts, wins = await cnt(User), await cnt(BannedUser), await cnt(Participant), await cnt(Winner)
            by = {st: await cnt(Giveaway, Giveaway.status == st) for st in ("active", "paused", "completed", "cancelled")}
            chans = await cnt(GlobalChannel)
        await safe_edit(cb, (f"📊 <b>Bot Statistics</b>\n\n👥 Users: {users} (🚫 {banned})\n🎁 Giveaways: "
                             + " · ".join(f"{k} {v}" for k, v in by.items())
                             + f"\n🎟 Entries: {parts}\n🏆 Winners drawn: {wins}\n🔒 Global channels: {chans}"), _back())
    elif a == "users":
        async with session_scope() as s:
            rows = (await s.execute(select(User).order_by(User.created_at.desc()).limit(10))).scalars().all()
        lines = ["👥 <b>Latest users</b>\n"] + [f"• {esc(u.full_name)} {('@' + u.username) if u.username else ''} <code>{u.id}</code>" for u in rows]
        lines.append("\nCommands: /ban &lt;id|@user&gt; [reason] · /unban &lt;id|@user&gt;")
        await safe_edit(cb, "\n".join(lines), _back())
    elif a == "ban":
        async with session_scope() as s:
            rows = (await s.execute(select(BannedUser).order_by(BannedUser.banned_at.desc()).limit(15))).scalars().all()
        lines = ["🚫 <b>Banned users</b>\n"] + ([f"• <code>{b.user_id}</code> {esc(b.reason or '')}" for b in rows] or ["None."])
        lines.append("\n/ban &lt;id|@user&gt; [reason] · /unban &lt;id|@user&gt;")
        await safe_edit(cb, "\n".join(lines), _back())
    elif a == "gws":
        async with session_scope() as s:
            rows = (await s.execute(select(Giveaway).order_by(Giveaway.id.desc()).limit(10))).scalars().all()
        kb = [[Btn(text=f"{g.code} · {g.status} · {g.title[:20]}", callback_data=GwCb(a="menu", id=g.id).pack())] for g in rows]
        kb.append([Btn(text="⬅️ Panel", callback_data=OwnCb(a="home").pack())])
        await safe_edit(cb, "🎁 <b>Giveaways</b> — tap one to manage (force end, cancel, reroll…):", Kb(inline_keyboard=kb))
    elif a == "fj":
        t, kb = await _channel_list_view()
        await safe_edit(cb, t, kb)
    elif a == "fjadd":
        await state.set_state(OwnerSt.add_channel)
        await cb.message.answer("➕ Send the channel (<code>@name</code>, <code>-100…</code> or "
                                "<code>-100… https://t.me/+invite</code> [request]).")
    elif a in ("fjtoggle", "fjdel"):
        async with session_scope() as s:
            c = await s.get(GlobalChannel, callback_data.id)
            if c:
                if a == "fjdel":
                    await s.delete(c)
                else:
                    c.enabled = not c.enabled
                await audit.record(s, f"global_channel_{'removed' if a == 'fjdel' else 'toggled'}", cb.from_user.id, chat=c.chat_id)
        t, kb = await _channel_list_view()
        await safe_edit(cb, t, kb)
    elif a == "fjtest":
        await safe_edit(cb, await _health_report(bot), _back())
    elif a == "settings":
        t, kb = await _settings_view()
        await safe_edit(cb, t, kb)
    elif a == "fjflag":
        async with session_scope() as s:
            cur = (await get_setting(s, "force_join_enabled")) == "1"
            await set_setting(s, "force_join_enabled", "0" if cur else "1")
            await audit.record(s, "force_join_toggled", cb.from_user.id, enabled=not cur)
        t, kb = await _settings_view()
        await safe_edit(cb, t, kb)
    elif a == "fjprompt":
        await state.set_state(OwnerSt.prompt_text)
        await cb.message.answer("✏️ Send the new force-join prompt (HTML formatting allowed).")
    elif a == "log":
        async with session_scope() as s:
            chat = await log_channel(s)
            pending = await s.scalar(select(func.count()).select_from(LogEvent).where(LogEvent.delivered.is_(False)))
        await safe_edit(cb, f"📋 <b>Log channel</b>: <code>{chat or 'not set'}</code>\nQueued events: {pending}\n\n"
                            "Set with /setlog &lt;chat_id&gt;", _back())
    elif a == "bc":
        await state.set_state(OwnerSt.broadcast)
        await cb.message.answer("📣 Send the message to broadcast. /cancel to abort.")
    elif a == "bcgo":
        data = await state.get_data()
        if "chat" not in data:
            return
        await safe_edit(cb, "📣 Broadcasting…")
        sent, failed = await _broadcast(bot, data["chat"], data["mid"])
        async with session_scope() as s:
            await audit.record(s, "broadcast", cb.from_user.id, sent=sent, failed=failed)
        await state.clear()
        await cb.message.answer(f"✅ Broadcast done: {sent} delivered, {failed} failed.", reply_markup=_back())
    elif a == "ana":
        now = utcnow()
        async with session_scope() as s:
            lines = []
            for label, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7))):
                since = now - delta
                u = await s.scalar(select(func.count()).select_from(User).where(User.created_at >= since))
                g = await s.scalar(select(func.count()).select_from(Giveaway).where(Giveaway.created_at >= since))
                p = await s.scalar(select(func.count()).select_from(Participant).where(Participant.joined_at >= since))
                lines.append(f"<b>{label}</b>: +{u} users · +{g} giveaways · +{p} entries")
        await safe_edit(cb, "📈 <b>System Analytics</b>\n\n" + "\n".join(lines), _back())
    elif a == "audit":
        async with session_scope() as s:
            rows = (await s.execute(select(LogEvent).order_by(LogEvent.id.desc()).limit(15))).scalars().all()
        lines = ["🧾 <b>Audit log</b>\n"] + [f"{e.created_at:%m-%d %H:%M} · {esc(e.event)}"
                                          f"{f' · GW-{10000 + e.giveaway_id}' if e.giveaway_id else ''}"
                                          f"{f' · {e.actor_id}' if e.actor_id else ''}" for e in rows]
        await safe_edit(cb, "\n".join(lines), _back())
    elif a == "health":
        report = []
        t0 = time.perf_counter()
        try:
            async with session_scope() as s:
                await s.execute(text("SELECT 1"))
            report.append(f"✅ Database {1000 * (time.perf_counter() - t0):.0f} ms")
        except Exception as exc:  # noqa: BLE001
            report.append(f"❌ Database: {esc(str(exc))[:80]}")
        t0 = time.perf_counter()
        try:
            await redis.ping()
            report.append(f"✅ Redis {1000 * (time.perf_counter() - t0):.0f} ms")
        except Exception as exc:  # noqa: BLE001
            report.append(f"❌ Redis: {esc(str(exc))[:80]}")
        try:
            me = await bot.get_me()
            report.append(f"✅ Telegram API (@{me.username})")
        except TelegramAPIError as exc:
            report.append(f"❌ Telegram: {esc(str(exc))[:80]}")
        sch = ctx.scheduler
        report.append(f"{'✅' if sch and sch.running else '❌'} Scheduler: {len(sch.get_jobs()) if sch else 0} jobs")
        await safe_edit(cb, "🩺 <b>System Health</b>\n\n" + "\n".join(report), _back())
