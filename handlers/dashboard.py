from __future__ import annotations

import csv
import io
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message,
)
from redis.asyncio import Redis
from sqlalchemy import func, select

from config import get_settings
from database.models import (
    Giveaway, GiveawayChannel, Participant, PublishedMessage, User, Winner, utcnow,
)
from database.session import session_scope
from keyboards.callbacks import GwCb, JoinCb, PageCb
from keyboards.menus import home_kb, manage_kb, themes_kb
from posters.generator import THEMES
from services import audit, posts, winners
from services.channels import ChannelError, resolve
from services.giveaways import (
    channels_of, count_participants, lock_giveaway, referral_count,
)
from utils.text import as_utc, esc, fmt_dt, fmt_remaining
from utils.tg import safe_edit
from utils.validators import VALIDATORS

router = Router(name="dashboard")
ICON = {"active": "🟢", "paused": "⏸", "ending": "⏳", "completed": "✅", "cancelled": "🚫"}
PAGE = 8


class EditSt(StatesGroup):
    value = State()


class AddChSt(StatesGroup):
    value = State()


class PubSt(StatesGroup):
    target = State()


async def ack(cb: CallbackQuery, text: str | None = None, **kw) -> None:
    """Answer a callback once; a second answer for the same query is harmlessly swallowed."""
    try:
        await cb.answer(text, **kw)
    except TelegramBadRequest:
        pass


def back_kb(gid: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gid).pack())]])


# ───────────────────────── lists ─────────────────────────
async def render_list(user_id: int, page: int) -> tuple[str, Kb]:
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Giveaway).where(Giveaway.host_id == user_id))).scalar_one()
        rows = (await s.execute(select(Giveaway).where(Giveaway.host_id == user_id)
                                .order_by(Giveaway.id.desc()).offset(page * PAGE).limit(PAGE))).scalars().all()
    if not total:
        return "📊 <b>My Giveaways</b>\n\nYou haven't hosted anything yet. Create your first one! 🎁", home_kb()
    pages = (total + PAGE - 1) // PAGE
    kb = [[Btn(text=f"{ICON.get(g.status, '•')} {g.code} · {g.title[:26]}",
               callback_data=GwCb(a="menu", id=g.id).pack())] for g in rows]
    nav = []
    if page > 0:
        nav.append(Btn(text="◀️ Prev", callback_data=PageCb(kind="mine", p=page - 1).pack()))
    nav.append(Btn(text=f"Page {page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(Btn(text="Next ▶️", callback_data=PageCb(kind="mine", p=page + 1).pack()))
    kb += [nav, [Btn(text="🏠 Main Menu", callback_data="menu:home")]]
    return f"📊 <b>My Giveaways</b> ({total})", Kb(inline_keyboard=kb)


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_list(message.from_user.id, 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:mine")
async def menu_mine(cb: CallbackQuery) -> None:
    await ack(cb)
    text, kb = await render_list(cb.from_user.id, 0)
    await safe_edit(cb, text, kb)


@router.callback_query(PageCb.filter(F.kind == "mine"))
async def page_mine(cb: CallbackQuery, callback_data: PageCb) -> None:
    await ack(cb)
    text, kb = await render_list(cb.from_user.id, callback_data.p)
    await safe_edit(cb, text, kb)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery) -> None:
    await ack(cb)


# ───────────────────────── explorer ─────────────────────────
async def render_explore(page: int) -> tuple[str, Kb]:
    cond = (Giveaway.status == "active", Giveaway.is_public.is_(True), Giveaway.ends_at > utcnow())
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Giveaway).where(*cond))).scalar_one()
        rows = (await s.execute(select(Giveaway).where(*cond).order_by(Giveaway.ends_at)
                                .offset(page * 5).limit(5))).scalars().all()
    if not total:
        return "🔍 <b>Explore</b>\n\nNo public giveaways right now. Check back soon! ✨", home_kb()
    pages = (total + 4) // 5
    kb = [[Btn(text=f"🎁 {g.title[:28]} · {fmt_remaining(g.ends_at)}",
               callback_data=GwCb(a="view", id=g.id).pack())] for g in rows]
    nav = []
    if page > 0:
        nav.append(Btn(text="◀️ Prev", callback_data=PageCb(kind="exp", p=page - 1).pack()))
    nav.append(Btn(text=f"Page {page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(Btn(text="Next ▶️", callback_data=PageCb(kind="exp", p=page + 1).pack()))
    kb += [nav, [Btn(text="🏠 Main Menu", callback_data="menu:home")]]
    return f"🔍 <b>Explore Giveaways</b> ({total} active)", Kb(inline_keyboard=kb)


@router.callback_query(F.data == "menu:explore")
async def menu_explore(cb: CallbackQuery) -> None:
    await ack(cb)
    text, kb = await render_explore(0)
    await safe_edit(cb, text, kb)


@router.callback_query(PageCb.filter(F.kind == "exp"))
async def page_explore(cb: CallbackQuery, callback_data: PageCb) -> None:
    await ack(cb)
    text, kb = await render_explore(callback_data.p)
    await safe_edit(cb, text, kb)


# ───────────────────────── manage ─────────────────────────
async def manage_text(gw: Giveaway) -> str:
    async with session_scope() as s:
        n = await count_participants(s, gw.id)
        ws = await winners.active_winners(s, gw.id)
    left = fmt_remaining(gw.ends_at) if gw.status in ("active",) else gw.status
    return (f"{ICON.get(gw.status, '•')} <b>{esc(gw.title)}</b> · {gw.code}\n\n🎁 {esc(gw.prize)}\n"
            f"👥 Participants: {n}{f'/{gw.max_participants}' if gw.max_participants else ''}\n"
            f"🏆 Winners: {len(ws)}/{gw.winners_count}\n⏱ Ends: {fmt_dt(gw.ends_at)} ({left})\n"
            f"📌 Status: <b>{gw.status}</b>")


async def _act_menu(cb, gw, x, bot, redis, state):
    await state.clear()
    await safe_edit(cb, await manage_text(gw), manage_kb(gw))


async def _act_parts(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        rows = (await s.execute(select(Participant, User).join(User, User.id == Participant.user_id, isouter=True)
                                .where(Participant.giveaway_id == gw.id).order_by(Participant.id).limit(30))).all()
        total = await count_participants(s, gw.id)
    lines = [f"👥 <b>Participants</b> ({total})\n"] + [
        f"{i}. {esc(u.full_name if u else '?')} {('@' + u.username) if u and u.username else ''} <code>{p.user_id}</code>"
        for i, (p, u) in enumerate(rows, 1)]
    if total > 30:
        lines.append(f"\n… and {total - 30} more. Use 📄 Export for the full list.")
    await safe_edit(cb, "\n".join(lines), back_kb(gw.id))


async def _act_stats(cb, gw, x, bot, redis, state):
    since = utcnow() - timedelta(hours=24)
    async with session_scope() as s:
        n = await count_participants(s, gw.id)
        n24 = (await s.execute(select(func.count()).select_from(Participant).where(
            Participant.giveaway_id == gw.id, Participant.joined_at >= since))).scalar_one()
        refs = await referral_count(s, gw.id)
        chans = await channels_of(s, gw.id)
        pubs = (await s.execute(select(func.count()).select_from(PublishedMessage).where(
            PublishedMessage.giveaway_id == gw.id))).scalar_one()
    await safe_edit(cb, (f"📊 <b>Statistics</b> · {gw.code}\n\n👥 Total entries: {n}\n🆕 Last 24h: {n24}\n"
                         f"🔗 Referral joins: {refs}\n📢 Channels: {len(chans)} "
                         f"({sum(c.is_mandatory for c in chans)} mandatory)\n📣 Published posts: {pubs}\n"
                         f"⏱ Remaining: {fmt_remaining(gw.ends_at)}"), back_kb(gw.id))


async def _act_pause(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        g = await lock_giveaway(s, gw.id)
        if g.status == "active":
            g.status, g.paused_at = "paused", utcnow()
            await audit.record(s, "giveaway_paused", cb.from_user.id, g.id)
    await ack(cb, "⏸ Paused")
    await _refresh(cb, gw.id)


async def _act_resume(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        g = await lock_giveaway(s, gw.id)
        if g.status == "paused":
            g.ends_at = as_utc(g.ends_at) + (utcnow() - as_utc(g.paused_at))  # pause time is not lost
            g.status, g.paused_at = "active", None
            await audit.record(s, "giveaway_resumed", cb.from_user.id, g.id)
    await ack(cb, "▶️ Resumed")
    await _refresh(cb, gw.id)


async def _act_extend(cb, gw, x, bot, redis, state):
    deltas = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "1d": timedelta(days=1), "3d": timedelta(days=3)}
    if x not in deltas:
        kb = Kb(inline_keyboard=[[Btn(text=f"+{k}", callback_data=GwCb(a="extend", id=gw.id, x=k).pack())
                                  for k in deltas], [Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gw.id).pack())]])
        await safe_edit(cb, "⏰ <b>Extend deadline</b> by:", kb)
        return
    async with session_scope() as s:
        g = await lock_giveaway(s, gw.id)
        if g.status in ("active", "paused"):
            base = max(as_utc(g.ends_at), utcnow())
            g.ends_at = base + deltas[x]
            await audit.record(s, "giveaway_extended", cb.from_user.id, g.id, by=x)
    await ack(cb, f"⏰ Extended by {x}")
    await _refresh(cb, gw.id)


async def _act_end(cb, gw, x, bot, redis, state):
    if x != "yes":
        kb = Kb(inline_keyboard=[[Btn(text="✅ Yes, end now", callback_data=GwCb(a="end", id=gw.id, x="yes").pack()),
                                  Btn(text="⬅️ No", callback_data=GwCb(a="menu", id=gw.id).pack())]])
        await safe_edit(cb, "🏁 End this giveaway now and draw winners?", kb)
        return
    await ack(cb, "🎲 Drawing winners…")
    await audit_action(cb.from_user.id, "giveaway_ended_manually", gw.id)
    res = await winners.finalize(bot, redis, gw.id)
    if res is None:
        await cb.message.answer("⚠️ This giveaway is already being finalized or is closed.")
    await _refresh(cb, gw.id)


async def audit_action(actor: int, event: str, gid: int, **kw) -> None:
    async with session_scope() as s:
        await audit.record(s, event, actor, gid, **kw)


async def _act_cancel(cb, gw, x, bot, redis, state):
    if x != "yes":
        kb = Kb(inline_keyboard=[[Btn(text="🚨 Yes, cancel", callback_data=GwCb(a="cancel", id=gw.id, x="yes").pack()),
                                  Btn(text="⬅️ No", callback_data=GwCb(a="menu", id=gw.id).pack())]])
        await safe_edit(cb, "🚨 Cancel this giveaway? No winners will be drawn.", kb)
        return
    async with session_scope() as s:
        g = await lock_giveaway(s, gw.id)
        if g.status in ("active", "paused", "ending"):
            g.status, g.ended_at = "cancelled", utcnow()
            await audit.record(s, "giveaway_cancelled", cb.from_user.id, g.id)
        pubs = list((await s.execute(select(PublishedMessage).where(PublishedMessage.giveaway_id == gw.id))).scalars())
    for p in pubs:
        try:
            await bot.edit_message_reply_markup(chat_id=p.chat_id, message_id=p.message_id, reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    await ack(cb, "🚫 Cancelled")
    await _refresh(cb, gw.id)
    await audit.flush(bot)


async def _refresh(cb: CallbackQuery, gid: int) -> None:
    async with session_scope() as s:
        gw = await s.get(Giveaway, gid)
    await safe_edit(cb, await manage_text(gw), manage_kb(gw))


# ── channels ──
async def _act_channels(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        chans = await channels_of(s, gw.id)
    rows = []
    for c in chans:
        rows.append([Btn(text=f"{'🔒 Mandatory' if c.is_mandatory else '➕ Optional'} · {c.title[:22]}",
                         callback_data=GwCb(a="chtoggle", id=gw.id, x=str(c.id)).pack()),
                     Btn(text="🗑", callback_data=GwCb(a="chdel", id=gw.id, x=str(c.id)).pack())])
    rows.append([Btn(text="➕ Add channel", callback_data=GwCb(a="chadd", id=gw.id).pack())])
    rows.append([Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gw.id).pack())])
    await safe_edit(cb, "📢 <b>Giveaway channels</b>\nTap a channel to toggle mandatory ⇄ optional. "
                        "Optional channels are not enforced.", Kb(inline_keyboard=rows))


async def _act_chtoggle(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        c = await s.get(GiveawayChannel, int(x))
        if c and c.giveaway_id == gw.id:
            c.is_mandatory = not c.is_mandatory
            await audit.record(s, "channel_toggled", cb.from_user.id, gw.id, chat=c.chat_id, mandatory=c.is_mandatory)
    await ack(cb, "Updated")
    await _act_channels(cb, gw, "", bot, redis, state)


async def _act_chdel(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        c = await s.get(GiveawayChannel, int(x))
        if c and c.giveaway_id == gw.id:
            await s.delete(c)
            await audit.record(s, "channel_removed", cb.from_user.id, gw.id, chat=c.chat_id)
    await ack(cb, "Removed")
    await _act_channels(cb, gw, "", bot, redis, state)


async def _act_chadd(cb, gw, x, bot, redis, state):
    await ack(cb)
    await state.set_state(AddChSt.value)
    await state.update_data(gid=gw.id)
    await cb.message.answer("📢 Send the channel (<code>@name</code>, <code>-100…</code>, or "
                            "<code>-100… https://t.me/+invite</code> [request]). /cancel to abort.")


@router.message(AddChSt.value)
async def on_add_channel(message: Message, state: FSMContext, bot: Bot) -> None:
    gid = (await state.get_data())["gid"]
    try:
        ref = await resolve(bot, message.text or "")
    except ChannelError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    async with session_scope() as s:
        g = await s.get(Giveaway, gid)
        if g is None or message.from_user.id not in (g.host_id, get_settings().bot_owner_id):
            await state.clear()
            return
        existing = await channels_of(s, gid)
        if not any(c.chat_id == ref.chat_id for c in existing):
            s.add(GiveawayChannel(giveaway_id=gid, chat_id=ref.chat_id, title=ref.title, username=ref.username,
                                  invite_link=ref.invite_link, join_request=ref.join_request, is_mandatory=True))
            await audit.record(s, "channel_added", message.from_user.id, gid, chat=ref.chat_id)
    await state.clear()
    await message.answer(f"✅ Added <b>{esc(ref.title)}</b>.", reply_markup=back_kb(gid))


# ── edit ──
EDIT_FIELDS = {"title": "Title", "prize": "Prize", "description": "Description", "rules": "Rules"}


async def _act_edit(cb, gw, x, bot, redis, state):
    rows = [[Btn(text=f"✏️ {label}", callback_data=GwCb(a="editf", id=gw.id, x=f).pack())] for f, label in EDIT_FIELDS.items()]
    rows.append([Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gw.id).pack())])
    await safe_edit(cb, "✏️ <b>Edit giveaway</b> — choose a field:", Kb(inline_keyboard=rows))


async def _act_editf(cb, gw, x, bot, redis, state):
    if x not in EDIT_FIELDS:
        await ack(cb)
        return
    await ack(cb)
    await state.set_state(EditSt.value)
    await state.update_data(gid=gw.id, field=x)
    await cb.message.answer(f"✏️ Send the new <b>{EDIT_FIELDS[x]}</b>. /cancel to abort.")


async def _act_poster(cb, gw, x, bot, redis, state):
    if x == "upload":
        await ack(cb)
        await state.set_state(EditSt.value)
        await state.update_data(gid=gw.id, field="poster")
        await cb.message.answer("🖼 Send the new poster image. /cancel to abort.")
        return
    if x in THEMES:
        async with session_scope() as s:
            g = await s.get(Giveaway, gw.id)
            g.poster_theme, g.poster_file_id = x, None
            await audit.record(s, "poster_changed", cb.from_user.id, gw.id, theme=x)
            gw = g
        await ack(cb, "Generating…")
        await posts.send_post(bot, cb.from_user.id, gw)
        await cb.message.answer("✅ Poster updated (used for new publishes).", reply_markup=back_kb(gw.id))
        return
    rows = themes_kb(f"gw:poster:{gw.id}:")  # theme buttons are mapped to GwCb below
    rows = [[Btn(text=b.text, callback_data=GwCb(a="poster", id=gw.id, x=b.callback_data.rsplit(":", 1)[1]).pack())
             for b in row] for row in rows]
    rows.append([Btn(text="🖼 Upload my own", callback_data=GwCb(a="poster", id=gw.id, x="upload").pack())])
    rows.append([Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gw.id).pack())])
    await safe_edit(cb, "🎨 <b>Edit poster</b> — pick a theme or upload an image:", Kb(inline_keyboard=rows))


@router.message(EditSt.value)
async def on_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    gid, field = data["gid"], data["field"]
    async with session_scope() as s:
        g = await s.get(Giveaway, gid)
        if g is None or message.from_user.id not in (g.host_id, get_settings().bot_owner_id):
            await state.clear()
            return
        if field == "poster":
            if not message.photo:
                await message.answer("🖼 Please send a photo.")
                return
            g.poster_file_id = message.photo[-1].file_id
        else:
            try:
                g_value = VALIDATORS[field](message.text or "")
            except ValueError as exc:
                await message.answer(f"⚠️ {exc}")
                return
            setattr(g, field, g_value)
        await audit.record(s, "giveaway_edited", message.from_user.id, gid, field=field)
    await state.clear()
    await message.answer("✅ Updated.", reply_markup=back_kb(gid))


# ── publish ──
async def _act_publish(cb, gw, x, bot, redis, state):
    await ack(cb)
    await state.set_state(PubSt.target)
    await state.update_data(gid=gw.id)
    await cb.message.answer("📣 Send the channel/group to post in (<code>@name</code> or <code>-100…</code>) — the bot "
                            "must be an admin there with permission to post. Send <code>here</code> to post in this chat. "
                            "/cancel to abort.")


@router.message(PubSt.target)
async def on_publish_target(message: Message, state: FSMContext, bot: Bot) -> None:
    gid = (await state.get_data())["gid"]
    text = (message.text or "").strip()
    async with session_scope() as s:
        gw = await s.get(Giveaway, gid)
    if gw is None or message.from_user.id not in (gw.host_id, get_settings().bot_owner_id):
        await state.clear()
        return
    if gw.status != "active":
        await state.clear()
        await message.answer("⚠️ Only active giveaways can be published.")
        return
    try:
        if text.lower() == "here":
            chat_id = message.chat.id
        else:
            raw = text.split("/")[-1] if "t.me/" in text else text
            target = int(raw) if raw.lstrip("-").isdigit() else "@" + raw.lstrip("@")
            chat_id = (await bot.get_chat(target)).id
        await posts.send_post(bot, chat_id, gw, record=True)
    except Exception as exc:  # noqa: BLE001 - any Telegram failure is reported to the host
        await message.answer(f"⚠️ Couldn't publish there: {esc(str(exc))[:200]}")
        return
    async with session_scope() as s:
        await audit.record(s, "giveaway_published", message.from_user.id, gid, chat=chat_id)
    await state.clear()
    await message.answer("✅ Published!", reply_markup=back_kb(gid))
    await audit.flush(bot)


# ── export / reroll ──
async def _act_csv(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        rows = (await s.execute(select(Participant, User).join(User, User.id == Participant.user_id, isouter=True)
                                .where(Participant.giveaway_id == gw.id).order_by(Participant.id))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "username", "full_name", "joined_at_utc", "referrer_id"])
    for p, u in rows:
        w.writerow([p.user_id, u.username if u else "", u.full_name if u else "", fmt_dt(p.joined_at), p.referrer_id or ""])
    await ack(cb)
    await cb.message.answer_document(BufferedInputFile(buf.getvalue().encode(), filename=f"{gw.code}_participants.csv"),
                                     caption=f"📄 {len(rows)} participants")


async def _act_reroll(cb, gw, x, bot, redis, state):
    async with session_scope() as s:
        ws = await winners.active_winners(s, gw.id)
        users = {u.id: u for u in (await s.execute(select(User).where(User.id.in_([w.user_id for w in ws])))).scalars()}
    rows = [[Btn(text=f"🔄 #{w.position} {users[w.user_id].full_name[:24] if w.user_id in users else w.user_id}",
                 callback_data=GwCb(a="rr", id=gw.id, x=str(w.id)).pack())] for w in ws]
    rows.append([Btn(text="⬅️ Back", callback_data=GwCb(a="menu", id=gw.id).pack())])
    await safe_edit(cb, "🔄 <b>Reroll</b> — pick the winner to replace:", Kb(inline_keyboard=rows))


async def _act_rr(cb, gw, x, bot, redis, state):
    await ack(cb, "🎲 Rerolling…")
    new = await winners.reroll(bot, gw.id, int(x), cb.from_user.id)
    await cb.message.answer("✅ Winner replaced." if new else "⚠️ No eligible replacement found.",
                            reply_markup=back_kb(gw.id))


ACTIONS = {
    "menu": _act_menu, "parts": _act_parts, "stats": _act_stats, "pause": _act_pause, "resume": _act_resume,
    "extend": _act_extend, "end": _act_end, "cancel": _act_cancel, "channels": _act_channels,
    "chtoggle": _act_chtoggle, "chdel": _act_chdel, "chadd": _act_chadd, "edit": _act_edit, "editf": _act_editf,
    "poster": _act_poster, "publish": _act_publish, "csv": _act_csv, "reroll": _act_reroll, "rr": _act_rr,
}


@router.callback_query(GwCb.filter(F.a == "view"))
async def view_public(cb: CallbackQuery, callback_data: GwCb) -> None:
    async with session_scope() as s:
        gw = await s.get(Giveaway, callback_data.id)
        n = await count_participants(s, callback_data.id) if gw else 0
    await ack(cb)
    if gw is None:
        await safe_edit(cb, "❌ Giveaway not found.", home_kb())
        return
    text = (f"🎁 <b>{esc(gw.title)}</b> · {gw.code}\n\n🏆 Prize: {esc(gw.prize)}\n👥 Winners: {gw.winners_count}\n"
            f"🎟 Entries: {n}\n⏱ Ends in {fmt_remaining(gw.ends_at)}\n\n{esc(gw.description[:700])}")
    kb = Kb(inline_keyboard=[[Btn(text="🎁 Participate", callback_data=JoinCb(g=gw.id).pack())],
                             [Btn(text="⬅️ Back", callback_data=PageCb(kind="exp", p=0).pack())]])
    await safe_edit(cb, text, kb)


@router.callback_query(GwCb.filter())
async def gw_router(cb: CallbackQuery, callback_data: GwCb, bot: Bot, redis: Redis, state: FSMContext) -> None:
    async with session_scope() as s:
        gw = await s.get(Giveaway, callback_data.id)
    if gw is None:
        await ack(cb, "Giveaway not found", show_alert=True)
        return
    if cb.from_user.id not in (gw.host_id, get_settings().bot_owner_id):
        await ack(cb, "⛔ Not your giveaway", show_alert=True)
        return
    action = ACTIONS.get(callback_data.a)
    if action is None:
        await ack(cb)
        return
    if callback_data.a not in ("menu", "parts", "stats", "csv", "reroll", "rr") and gw.status not in ("active", "paused"):
        await ack(cb, "This giveaway is closed.", show_alert=True)
        return
    if callback_data.a not in {"pause", "resume", "chtoggle", "chdel", "rr"}:
        await ack(cb)
    await action(cb, gw, callback_data.x, bot, redis, state)
