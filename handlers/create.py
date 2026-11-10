
from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Kb,
    Message,
)

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
logger = logging.getLogger(__name__)


STEPS = [
    "title",
    "prize",
    "description",
    "winners",
    "poster",
    "duration",
    "rules",
    "channels",
    "max",
    "confirm",
    "visibility",
]

SKIPPABLE = {
    "poster",
    "rules",
    "channels",
    "max",
    "confirm",
    "visibility",
}

PROMPTS = {
    "title": "📌 <b>Giveaway Title</b>\nSend a short, catchy title (3–80 chars).",
    "prize": "🎁 <b>Prize Name</b>\nThe exact item/reward (2–120 chars).",
    "description": "📝 <b>Description</b>\nRules, conditions and prize delivery info (5–1500 chars).",
    "winners": "🏆 <b>Number of Winners</b>\nSend a whole number (1–100).",
    "poster": "🎨 <b>Poster</b>\nSend an image to use as your poster, or pick a theme and I'll generate one:",
    "duration": "⏱ <b>Duration</b>\nExamples: <code>15m</code>, <code>2h</code>, <code>3d</code>, <code>10d</code> (1m – 90d).",
    "rules": "📜 <b>Custom Rules</b> (optional)\nSend extra rules, or skip.",
    "channels": (
        "📢 <b>Required Channels</b> (optional)\n"
        "Send one per line:\n"
        "• <code>@channel</code> or <code>-100123…</code> (public / by id)\n"
        "• <code>-100123… https://t.me/+invite</code> (private)\n"
        "• add <code>request</code> for join-request channels\n"
        "The bot must be an admin there. Press <b>Done / Skip</b> when finished."
    ),
    "max": "👥 <b>Max participants</b> (optional)\nSend a number, or skip for unlimited.",
    "confirm": "💬 <b>Confirmation message</b> (optional)\nShown to users after they join.",
    "visibility": "🔍 <b>Visibility</b>\nShow this giveaway in the public Explore menu?",
}


class Create(StatesGroup):
    active = State()


def valid_step(data: dict) -> bool:
    step = data.get("step")
    return type(step) is int and 0 <= step < len(STEPS)


def nav_kb(idx: int) -> Kb:
    key = STEPS[idx]
    rows: list[list[Btn]] = []

    if key == "poster":
        rows.extend(themes_kb("cr:theme:"))

    if key == "visibility":
        rows.append([
            Btn(text="🌍 Public", callback_data="cr:vis:1"),
            Btn(text="🔒 Private", callback_data="cr:vis:0"),
        ])

    nav = []

    if idx > 0:
        nav.append(Btn(text="⬅️ Back", callback_data="cr:back"))

    if key in SKIPPABLE:
        nav.append(
            Btn(
                text="✅ Done / ⏭ Skip" if key == "channels" else "⏭ Skip",
                callback_data="cr:skip",
            )
        )

    nav.append(Btn(text="❌ Cancel", callback_data="cr:cancel"))
    rows.append(nav)

    return Kb(inline_keyboard=rows)


async def show_step(target: Message, state: FSMContext) -> None:
    data = await state.get_data()

    if not valid_step(data):
        await state.clear()
        await target.answer(
            "⚠️ Your giveaway setup session expired or became invalid. "
            "Please start again using /create."
        )
        return

    idx = data["step"]

    await target.answer(
        f"<b>Step {idx + 1}/{len(STEPS)}</b>\n\n"
        f"{PROMPTS[STEPS[idx]]}",
        reply_markup=nav_kb(idx),
    )


async def advance(
    target: Message,
    state: FSMContext,
    bot: Bot,
    user_id: int,
    key: str,
    value=None,
) -> None:
    data = await state.get_data()

    if not valid_step(data):
        return

    form = data.get("form", {})

    if not isinstance(form, dict):
        await state.clear()
        await target.answer(
            "⚠️ Your setup data is invalid. Please start again with /create."
        )
        return

    if value is not None:
        form[key] = value

    next_idx = data["step"] + 1

    await state.update_data(form=form, step=next_idx)

    if next_idx >= len(STEPS):
        await finish(target, state, bot, user_id)
    else:
        await show_step(target, state)


async def finish(
    target: Message,
    state: FSMContext,
    bot: Bot,
    user_id: int,
) -> None:
    data = await state.get_data()
    form = data.get("form", {})

    if not isinstance(form, dict):
        await state.clear()
        await target.answer("⚠️ Invalid setup data. Please start again with /create.")
        return

    if not form.get("duration"):
        await state.clear()
        await target.answer(
            "⚠️ Giveaway duration is missing. Please start again with /create."
        )
        return

    # Defaults for optional fields.
    form.setdefault("rules", "")
    form.setdefault("channels", [])
    form.setdefault("poster_file_id", None)
    form.setdefault("theme", "neon")
    form.setdefault("max", None)
    form.setdefault("confirm", None)
    form.setdefault("public", True)

    start_at = utcnow()
    ends_at = start_at + timedelta(seconds=form["duration"])

    try:
        async with session_scope() as s:
            gw = await create_giveaway(
                s,
                user_id,
                form,
                ends_at,
            )

            await audit.record(
                s,
                "giveaway_created",
                user_id,
                gw.id,
                title=gw.title,
                winners=gw.winners_count,
                channels=len(form.get("channels", [])),
            )

        # Clear the FSM only after the database transaction succeeds.
        await state.clear()

        await target.answer(
            f"✅ <b>Giveaway created!</b> 🆔 <b>{gw.code}</b>\n"
            "Here's your post preview:"
        )

        await posts.send_post(bot, user_id, gw)

        await target.answer(
            "What next?",
            reply_markup=Kb(
                inline_keyboard=[
                    [
                        Btn(
                            text="📣 Publish to a channel",
                            callback_data=GwCb(
                                a="publish",
                                id=gw.id,
                            ).pack(),
                        )
                    ],
                    [
                        Btn(
                            text="📊 Manage",
                            callback_data=GwCb(
                                a="menu",
                                id=gw.id,
                            ).pack(),
                        )
                    ],
                ]
            ),
        )

        await audit.flush(bot)

    except Exception:
        logger.exception(
            "Giveaway creation failed for Telegram user %s",
            user_id,
        )

        await target.answer(
            "❌ Giveaway creation failed. Your setup data has been kept. "
            "Please check the bot logs or contact the owner."
        )


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
    await cb.message.answer(
        "❌ Giveaway creation cancelled. Use /start for the menu."
    )


@router.callback_query(Create.active, F.data == "cr:back")
async def on_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()

    if not valid_step(data):
        await cb.answer(
            "This setup session is invalid. Start again with /create.",
            show_alert=True,
        )
        await state.clear()
        return

    idx = max(0, data["step"] - 1)

    await state.update_data(step=idx)
    await cb.answer()
    await show_step(cb.message, state)


@router.callback_query(Create.active, F.data == "cr:skip")
async def on_skip(
    cb: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()

    if not valid_step(data):
        await cb.answer(
            "This step is already completed or invalid.",
            show_alert=True,
        )
        return

    key = STEPS[data["step"]]

    if key not in SKIPPABLE:
        await cb.answer("This step cannot be skipped.", show_alert=True)
        return

    await cb.answer()
    await advance(
        cb.message,
        state,
        bot,
        cb.from_user.id,
        key,
    )


@router.callback_query(Create.active, F.data.startswith("cr:theme:"))
async def on_theme(
    cb: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()

    if not valid_step(data) or STEPS[data["step"]] != "poster":
        await cb.answer("This poster step is no longer active.")
        return

    theme = cb.data.split(":")[2]

    if theme not in THEMES:
        await cb.answer("Invalid theme.", show_alert=True)
        return

    await cb.answer(f"{THEMES[theme].label} selected")

    await advance(
        cb.message,
        state,
        bot,
        cb.from_user.id,
        "theme",
        theme,
    )


@router.callback_query(Create.active, F.data.startswith("cr:vis:"))
async def on_visibility(
    cb: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()

    if not valid_step(data):
        await cb.answer(
            "This giveaway setup has already finished.",
            show_alert=True,
        )
        return

    if STEPS[data["step"]] != "visibility":
        await cb.answer(
            "Please complete the current step first.",
            show_alert=True,
        )
        return

    is_public = cb.data.endswith(":1")

    await cb.answer()

    await advance(
        cb.message,
        state,
        bot,
        cb.from_user.id,
        "public",
        is_public,
    )


@router.message(Create.active)
async def on_input(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()

    if not valid_step(data):
        await state.clear()
        await message.answer(
            "⚠️ Your setup session is invalid. Please start again using /create."
        )
        return

    key = STEPS[data["step"]]
    uid = message.from_user.id

    if message.text and message.text.startswith("/"):
        await message.answer(
            "Finish the steps, or use /cancel to abort."
        )
        return

    if key == "poster":
        if message.photo:
            await advance(
                message,
                state,
                bot,
                uid,
                "poster_file_id",
                message.photo[-1].file_id,
            )
        else:
            await message.answer(
                "🖼 Send a photo, or pick a theme below."
            )
        return

    if key == "visibility":
        await message.answer("Please use the buttons above.")
        return

    if not message.text:
        await message.answer("⚠️ Please send text.")
        return

    if key == "channels":
        form = data.get("form", {})

        if not isinstance(form, dict):
            await state.clear()
            await message.answer(
                "⚠️ Your setup data is invalid. Start again with /create."
            )
            return

        found = form.setdefault("channels", [])
        report = []

        for line in message.text.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                ref = await resolve(bot, line)
            except ChannelError as exc:
                report.append(
                    f"⚠️ <code>{line[:40]}</code>: {exc}"
                )
                continue

            if any(
                channel["chat_id"] == ref.chat_id
                for channel in found
            ):
                report.append(f"ℹ️ {ref.title} already added")
                continue

            found.append({
                "chat_id": ref.chat_id,
                "title": ref.title,
                "username": ref.username,
                "invite_link": ref.invite_link,
                "join_request": ref.join_request,
            })

            note = (
                ""
                if ref.bot_is_admin
                else " (⚠️ bot is not admin — membership checks may fail)"
            )

            report.append(f"✅ {ref.title}{note}")

        await state.update_data(form=form)

        if not report:
            report.append("No channels were added.")

        await message.answer(
            "\n".join(report)
            + f"\n\nTotal: {len(found)}. Send more or press Done.",
            reply_markup=nav_kb(data["step"]),
        )
        return

    try:
        value = VALIDATORS[key](message.text)
    except (ValueError, KeyError) as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await advance(
        message,
        state,
        bot,
        uid,
        key,
        value,
    )
