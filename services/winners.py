from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    BannedUser, Giveaway, Participant, PublishedMessage, User, Winner, utcnow,
)
from database.session import session_scope
from services import audit
from services.channels import missing
from services.giveaways import channels_of, count_participants, lock_giveaway
from utils.text import esc, mention

log = logging.getLogger(__name__)
_rng = secrets.SystemRandom()


def pick(pool: list[int], k: int) -> list[int]:
    """Cryptographically secure, duplicate-free selection."""
    return _rng.sample(pool, min(k, len(pool))) if pool and k > 0 else []


async def active_winners(s: AsyncSession, gid: int) -> list[Winner]:
    return list((await s.execute(select(Winner).where(Winner.giveaway_id == gid, Winner.is_active.is_(True))
                                 .order_by(Winner.position, Winner.id))).scalars().all())


async def all_winner_ids(s: AsyncSession, gid: int) -> set[int]:
    return set((await s.execute(select(Winner.user_id).where(Winner.giveaway_id == gid))).scalars().all())


async def pool_ids(s: AsyncSession, gw: Giveaway, exclude: set[int]) -> list[int]:
    q = select(Participant.user_id).where(
        Participant.giveaway_id == gw.id,
        Participant.user_id != gw.host_id,
        Participant.user_id.not_in(select(BannedUser.user_id)),
    )
    return [u for u in (await s.execute(q)).scalars().all() if u not in exclude]


async def reverify(bot: Bot, uids: list[int], channels: list, concurrency: int = 10) -> list[int]:
    """Drop participants who left a mandatory channel before the draw."""
    if not channels:
        return list(uids)
    sem = asyncio.Semaphore(concurrency)

    async def check(uid: int) -> int | None:
        async with sem:
            return uid if not await missing(bot, uid, channels) else None

    return [u for u in await asyncio.gather(*(check(u) for u in uids)) if u is not None]


async def finalize(bot: Bot, redis: Redis, gid: int) -> list[int] | None:
    """Lock -> halt joins -> re-verify -> draw -> complete -> announce. Safe to call concurrently."""
    if not await redis.set(f"lock:finalize:{gid}", "1", nx=True, ex=600):
        return None
    try:
        async with session_scope() as s:
            gw = await lock_giveaway(s, gid)
            if gw is None or gw.status not in ("active", "paused", "ending"):
                return None
            gw.status = "ending"  # participation halted
            channels = await channels_of(s, gid, mandatory_only=True)
            uids = await pool_ids(s, gw, exclude=await all_winner_ids(s, gid))
        eligible = await reverify(bot, uids, channels)
        async with session_scope() as s:
            gw = await lock_giveaway(s, gid)
            if gw is None or gw.status != "ending":
                return None
            existing = await active_winners(s, gid)
            need = max(0, gw.winners_count - len(existing))
            chosen = pick(eligible, need)
            for i, uid in enumerate(chosen):
                s.add(Winner(giveaway_id=gid, user_id=uid, position=len(existing) + i + 1, selection_type="random"))
            everyone = [w.user_id for w in existing] + chosen
            if chosen:
    await s.execute(
        update(User)
        .where(User.tg_id.in_(chosen))
        .values(wins_count=User.wins_count + 1)
            )
            gw.status, gw.ended_at = "completed", utcnow()
            await audit.record(s, "giveaway_completed", giveaway_id=gid, winners=len(everyone),
                               participants=await count_participants(s, gid), eligible=len(eligible))
        await announce(bot, gid, notify=everyone)
        await audit.flush(bot)
        return everyone
    finally:
        await redis.delete(f"lock:finalize:{gid}")


async def winners_text(s: AsyncSession, gw: Giveaway, ws: list[Winner]) -> str:
    if not ws:
        return (f"🏁 <b>{esc(gw.title)}</b> ({gw.code}) has ended.\n\n😕 No eligible participants this time.")
        users = {
    u.tg_id: u
    for u in (
        await s.execute(
            select(User).where(
                User.tg_id.in_([w.user_id for w in ws])
            )
        )
    ).scalars().all()
        }
    lines = [f"🏁 <b>{esc(gw.title)}</b> ({gw.code}) has ended!", "", "🏆 <b>Winners</b>"]
    for w in ws:
        u = users.get(w.user_id)
        lines.append(f"{w.position}. {mention(w.user_id, (u.full_name if u else None) or (u.username if u else None))}")
    lines += ["", f"🎁 Prize: {esc(gw.prize)}", "🎉 Congratulations! The host will contact you."]
    return "\n".join(lines)


async def announce(bot: Bot, gid: int, notify: list[int] | None = None) -> None:
    async with session_scope() as s:
        gw = await s.get(Giveaway, gid)
        ws = await active_winners(s, gid)
        text = await winners_text(s, gw, ws)
        pubs = list((await s.execute(select(PublishedMessage).where(PublishedMessage.giveaway_id == gid))).scalars())
        host_id, title, prize = gw.host_id, gw.title, gw.prize
    for chat in {p.chat_id for p in pubs} | {host_id}:
        try:
            await bot.send_message(chat, text)
        except TelegramAPIError as exc:
            log.warning("announce to %s failed: %s", chat, exc)
    for p in pubs:
        try:
            await bot.edit_message_reply_markup(chat_id=p.chat_id, message_id=p.message_id, reply_markup=None)
        except TelegramAPIError:
            pass
    for uid in notify if notify is not None else [w.user_id for w in ws]:
        try:
            await bot.send_message(uid, f"🏆 <b>You won!</b>\n\n🎉 Congratulations — you won <b>{esc(prize)}</b> "
                                        f"in <b>{esc(title)}</b>. The host will contact you shortly.")
        except TelegramAPIError:
            pass


async def reroll(bot: Bot, gid: int, winner_row_id: int, actor_id: int) -> int | None:
    """Replace one winner, preserving the rest. Returns the new winner's user id, or None."""
    async with session_scope() as s:
        gw = await lock_giveaway(s, gid)
        w = await s.get(Winner, winner_row_id)
        if gw is None or w is None or w.giveaway_id != gid or not w.is_active or gw.status != "completed":
            return None
        channels = await channels_of(s, gid, mandatory_only=True)
        uids = await pool_ids(s, gw, exclude=await all_winner_ids(s, gid))
    eligible = await reverify(bot, uids, channels)
    async with session_scope() as s:
        gw = await lock_giveaway(s, gid)
        w = await s.get(Winner, winner_row_id)
        if gw is None or w is None or not w.is_active:
            return None
        taken = await all_winner_ids(s, gid)
        chosen = pick([u for u in eligible if u not in taken], 1)
        if not chosen:
            return None
        w.is_active, w.replaced_at = False, utcnow()
        s.add(Winner(giveaway_id=gid, user_id=chosen[0], position=w.position, selection_type="reroll"))
        await s.execute(
    update(User)
    .where(User.tg_id == chosen[0])
    .values(wins_count=User.wins_count + 1)
                  )
        await audit.record(s, "winner_rerolled", actor_id, gid, replaced=w.user_id, new=chosen[0])
    await announce(bot, gid, notify=[chosen[0]])
    await audit.flush(bot)
    return chosen[0]


OverrideResult = Literal["ok", "no_giveaway", "no_user", "already", "bad_status", "full"]


async def owner_override(bot: Bot, gid: int, target_id: int, actor_id: int) -> OverrideResult:
    """Owner-designated winner (selection_type=owner_override). Audited; never shown in any UI."""
    async with session_scope() as s:
        gw = await lock_giveaway(s, gid)
        if gw is None:
            return "no_giveaway"
        if (
    await s.execute(
        select(User).where(User.tg_id == target_id)
    )
).scalar_one_or_none() is None:
    return "no_user"
        if gw.status == "cancelled":
            return "bad_status"
        existing = await active_winners(s, gid)
        if any(w.user_id == target_id for w in existing):
            return "already"
        position = len(existing) + 1
        if len(existing) >= gw.winners_count:
            if gw.status != "completed":
                return "full"
            victim = next((w for w in reversed(existing) if w.selection_type != "owner_override"), existing[-1])
            victim.is_active, victim.replaced_at = False, utcnow()
            position = victim.position
        s.add(Winner(giveaway_id=gid, user_id=target_id, position=position, selection_type="owner_override"))
        await s.execute(update(User).where(User.id == target_id).values(wins_count=User.wins_count + 1))
        completed = gw.status == "completed"
        await audit.record(s, "owner_override_winner", actor_id, gid, target=target_id, position=position)
    if completed:
        await announce(bot, gid, notify=[target_id])
    await audit.flush(bot)
    return "ok"
