from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

from database.models import utcnow
from database.session import session_scope
from keyboards.callbacks import GwCb
from keyboards.menus import themes_kb
from posters.generator import THEMES
from services import audit, posts
from services.channels import ChannelError, resolve
from services.giveaways import create_giveaway
from utils.validators import VALIDATORS

router = Router(name="create")

STEPS = ["title", "prize", "description", "winners", "poster", "duration",
         "rules", "channels", "max", "confirm", "visibility"]
SKIPPABLE = {"poster", "rules", "channels", "max", "confirm", "visibility"}
PROMPTS = {
    "title": "📌 <b>Giveaway Title</b>\nSend a short, catchy title (3–80 chars).",
    "prize": "🎁 <b>Prize Name</b>\nThe exact item/reward (2–120 chars).",
    "description": "📝 <b>Description</b>\nRules, conditions and prize delivery info (5–1500 chars).",
    "winners": "🏆 <b>Number of Winners</b>\nSend a whole number (1–100).",
    "poster": "🎨 <b>Poster</b>\nSend an image to use as your poster, or pick a theme and I'll generate one:",
    "duration": "⏱ <b>Duration</b>\nExamples: <code>15m</code>, <code>2h</code>, <code>3d</code>, <code>10d</code> (1m – 90d).",
    "rules": "📜 <b>Custom Rules</b> (optional)\nSend extra rules, or skip.",
    "channels": ("📢 <b>Required Channels</b> (optional)\nSend one per line:\n"
                 "• <code>@channel</code> or <code>-100123…</code> (public / by id)\n"
                 "• <code>-100123… https://t.me/+invite</code> (private)\n"
                 "• add <code>request</code> for join-request channels\n"
                 "The bot must be an admin there. Press <b>Done / Skip</b> when finished."),
    "max": "👥 <b>Max participants</b> (optional)\nSend a number, or skip for unlimited.",
    "confirm": "💬 <b>Confirmation message</b> (optional)\nShown to users after they join.",
    "visibility": "🔍 <b>Visibility</b>\nShow this giveaway in the public Explore menu?",
}


class Create(StatesGroup):
    active = State()


def nav_kb(idx: int) -> Kb:
    key = STEPS[idx]
    rows: list[list[Btn]] = []
    if key == "poster":
        rows += themes_kb("cr:theme:")
    if key == "visibility":
        rows.append([Btn(text="🌍 Public", callback_data="cr:vis:1"), Btn(text="🔒 Private", callback_data="cr:vis:0")])
    nav = []
    if idx > 0:
        nav.append(Btn(text="⬅️ Back", callback_data="cr:back"))
    if key in SKIPPABLE:
        nav.append(Btn(text="✅ Done / ⏭ Skip" if key == "channels" else "⏭ Skip", callback_data="cr:skip"))
    nav.append(Btn(text="❌ Cancel", callback_data="cr:cancel"))
    rows.append(nav)
    return Kb(inline_keyboard=rows)


async def show_step(target: Message, state: FSMContext) -> None:
    idx = (await state.get_data())["step"]
    await target.answer(f"<b>Step {idx + 1}/{len(STEPS)}</b>\n\n{PROMPTS[STEPS[idx]]}", reply_markup=nav_kb(idx))


async def advance(target: Message, state: FSMContext, bot: Bot, user_id: int, key: str, value=None) -> None:
    data = await state.get_data()
    form = data.get("form", {})
    if value is not None:
        form[key] = value
    idx = data["step"] + 1
    await state.update_data(form=form, step=idx)
    if idx >= len(STEPS):
        await finish(target, state, bot, user_id)
    else:
        await show_step(target, state)


async def finish(target: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    form = (await state.get_data())["form"]
    ends_at = utcnow() + timedelta(seconds=form["duration"])
    async with session_scope() as s:
        gw = await create_giveaway(s, user_id, form, ends_at)
        await audit.record(s, "giveaway_created", user_id, gw.id, title=gw.title, winners=gw.winners_count,
                           channels=len(form.get("channels", [])))
    await state.clear()
    await target.answer(f"✅ <b>Giveaway created!</b> 🆔 <b>{gw.code}</b>\nHere's your post preview:")
    await posts.send_post(bot, user_id, gw)
    await target.answer("What next?", reply_markup=Kb(inline_keyboard=[
        [Btn(text="📣 Publish to a channel", callback_data=GwCb(a="publish", id=gw.id).pack())],
        [Btn(text="📊 Manage", callback_data=GwCb(a="menu", id=gw.id).pack())]]))
    await audit.flush(bot)


@router.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext) -> None:
    await state.set_state(Create.active)
    await state.update_data(step=0, form={})
    await show_step(message, state)


@router.callback_query(F.data == "menu:create")
async def menu_create(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Create.active)
    await state.update_data(step=0, form={})
    await show_step(cb.message, state)


@router.callback_query(Create.active, F.data == "cr:cancel")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("Cancelled")
    await cb.message.answer("❌ Giveaway creation cancelled. Use /start for the menu.")


@router.callback_query(Create.active, F.data == "cr:back")
async def on_back(cb: CallbackQuery, state: FSMContext) -> None:
    idx = max(0, (await state.get_data())["step"] - 1)
    await state.update_data(step=idx)
    await cb.answer()
    await show_step(cb.message, state)


@router.callback_query(Create.active, F.data == "cr:skip")
async def on_skip(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    key = STEPS[(await state.get_data())["step"]]
    await advance(cb.message, state, bot, cb.from_user.id, key)


@router.callback_query(Create.active, F.data.startswith("cr:theme:"))
async def on_theme(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    theme = cb.data.split(":")[2]
    if theme not in THEMES or STEPS[(await state.get_data())["step"]] != "poster":
        await cb.answer()
        return
    await cb.answer(f"{THEMES[theme].label} selected")
    form = (await state.get_data())["form"]
    form["theme"] = theme
    await state.update_data(form=form)
    await advance(cb.message, state, bot, cb.from_user.id, "theme", theme)


@router.callback_query(Create.active, F.data.startswith("cr:vis:"))
async def on_visibility(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    if STEPS[(await state.get_data())["step"]] != "visibility":
        return
    await advance(cb.message, state, bot, cb.from_user.id, "public", cb.data.endswith(":1"))


@router.message(Create.active)
async def on_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    key = STEPS[data["step"]]
    uid = message.from_user.id
    if message.text and message.text.startswith("/"):
        await message.answer("Finish the steps, or use /cancel to abort.")
        return
    if key == "poster":
        if message.photo:
            await advance(message, state, bot, uid, "poster_file_id", message.photo[-1].file_id)
        else:
            await message.answer("🖼 Send a photo, or pick a theme below.")
        return
    if key == "visibility":
        await message.answer("Please use the buttons above.")
        return
    if not message.text:
        await message.answer("⚠️ Please send text.")
        return
    if key == "channels":
        form = data["form"]
        found = form.setdefault("channels", [])
        report = []
        for line in [ln for ln in message.text.splitlines() if ln.strip()]:
            try:
                ref = await resolve(bot, line)
            except ChannelError as exc:
                report.append(f"⚠️ <code>{line[:40]}</code>: {exc}")
                continue
            if any(c["chat_id"] == ref.chat_id for c in found):
                report.append(f"ℹ️ {ref.title} already added")
                continue
            found.append({"chat_id": ref.chat_id, "title": ref.title, "username": ref.username,
                          "invite_link": ref.invite_link, "join_request": ref.join_request})
            note = "" if ref.bot_is_admin else " (⚠️ bot is not admin — membership checks may fail)"
            report.append(f"✅ {ref.title}{note}")
        await state.update_data(form=form)
        await message.answer("\n".join(report) + f"\n\nTotal: {len(found)}. Send more or press Done.",
                             reply_markup=nav_kb(data["step"]))
        return
    try:
        value = VALIDATORS[key](message.text)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await advance(message, state, bot, uid, key, value)
