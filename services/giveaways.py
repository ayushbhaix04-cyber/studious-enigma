from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Giveaway, GiveawayChannel, Participant, Referral, User, utcnow
from utils.text import as_utc

JoinResult = Literal["ok", "full", "closed", "duplicate", "own"]


async def create_giveaway(s: AsyncSession, host_id: int, form: dict[str, Any], ends_at: datetime) -> Giveaway:
    gw = Giveaway(
        host_id=host_id, title=form["title"], prize=form["prize"], description=form["description"],
        winners_count=form["winners"], poster_file_id=form.get("poster_file_id"),
        poster_theme=form.get("theme") or "neon", rules=form.get("rules"),
        max_participants=form.get("max"), confirm_message=form.get("confirm"),
        is_public=form.get("public", True), ends_at=ends_at, status="active",
    )
    s.add(gw)
    await s.flush()
    for ch in form.get("channels", []):
        s.add(GiveawayChannel(
            giveaway_id=gw.id, chat_id=ch["chat_id"], title=ch.get("title", ""), username=ch.get("username"),
            invite_link=ch.get("invite_link"), join_request=ch.get("join_request", False), is_mandatory=True,
        ))
    return gw


async def lock_giveaway(s: AsyncSession, gid: int) -> Giveaway | None:
    """Row-level lock (SELECT ... FOR UPDATE) serialising every state change of a giveaway."""
    return (await s.execute(select(Giveaway).where(Giveaway.id == gid).with_for_update())).scalar_one_or_none()


async def count_participants(s: AsyncSession, gid: int) -> int:
    return (await s.execute(select(func.count()).select_from(Participant).where(Participant.giveaway_id == gid))).scalar_one()


async def channels_of(s: AsyncSession, gid: int, mandatory_only: bool = False) -> list[GiveawayChannel]:
    q = select(GiveawayChannel).where(GiveawayChannel.giveaway_id == gid).order_by(GiveawayChannel.id)
    if mandatory_only:
        q = q.where(GiveawayChannel.is_mandatory.is_(True))
    return list((await s.execute(q)).scalars().all())


async def register_participant(s: AsyncSession, gid: int, user_id: int, referrer_id: int | None = None) -> JoinResult:
    """Atomic join: locks the giveaway, enforces the cap and relies on the unique constraint for duplicates."""
    gw = await lock_giveaway(s, gid)
    if gw is None or gw.status != "active" or as_utc(gw.ends_at) <= utcnow():
        return "closed"
    if gw.host_id == user_id:
        return "own"
    if gw.max_participants and await count_participants(s, gid) >= gw.max_participants:
        return "full"
    try:
        async with s.begin_nested():
            s.add(Participant(giveaway_id=gid, user_id=user_id,
                              referrer_id=referrer_id if referrer_id != user_id else None))
            await s.flush()
    except IntegrityError:
        return "duplicate"
    if referrer_id and referrer_id != user_id:
        ref_is_participant = (await s.execute(select(Participant.id).where(
            Participant.giveaway_id == gid, Participant.user_id == referrer_id))).first()
        if ref_is_participant:
            try:
                async with s.begin_nested():
                    s.add(Referral(giveaway_id=gid, referrer_id=referrer_id, referred_id=user_id))
                    await s.flush()
            except IntegrityError:
                pass
    return "ok"


async def referral_count(s: AsyncSession, gid: int, user_id: int | None = None) -> int:
    q = select(func.count()).select_from(Referral).where(Referral.giveaway_id == gid)
    if user_id is not None:
        q = q.where(Referral.referrer_id == user_id)
    return (await s.execute(q)).scalar_one()




async def upsert_user(s: AsyncSession, tg_user: Any) -> User:
    name = " ".join(
        p for p in (
            tg_user.first_name,
            getattr(tg_user, "last_name", None)
        ) if p
    )

    u = (
        await s.execute(
            select(User).where(User.tg_id == tg_user.id)
        )
    ).scalar_one_or_none()

    if u is None:
        u = User(
            tg_id=tg_user.id,
            username=tg_user.username,
            full_name=name
        )
        s.add(u)
    else:
        u.username = tg_user.username
        u.full_name = name
        u.last_seen_at = utcnow()

    return u

