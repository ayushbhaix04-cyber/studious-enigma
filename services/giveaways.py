
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Giveaway,
    GiveawayChannel,
    Participant,
    Referral,
    User,
    utcnow,
)
from utils.text import as_utc

logger = logging.getLogger(__name__)

JoinResult = Literal["ok", "full", "closed", "duplicate", "own"]


def generate_giveaway_code() -> str:
    return f"GW-{secrets.token_hex(4).upper()}"


async def create_giveaway(
    s: AsyncSession,
    host_id: int,
    form: dict[str, Any],
    ends_at: datetime,
) -> Giveaway:
    """Create a giveaway and its mandatory channel requirements."""

    now = utcnow()

    gw = Giveaway(
        code=generate_giveaway_code(),
        host_id=host_id,
        title=form["title"],
        prize=form["prize"],
        description=form.get("description") or "",
        rules=form.get("rules") or "",
        winners_count=int(form["winners"]),
        poster_file_id=form.get("poster_file_id"),
        poster_theme=form.get("theme") or "neon",
        max_participants=form.get("max"),
        confirm_message=form.get("confirm"),
        start_at=now,
        ends_at=ends_at,
        is_public=form.get("public", True),
        referral_enabled=False,
        bonus_per_referral=0,
        status="active",
        created_at=now,
    )

    s.add(gw)
    await s.flush()

    for channel in form.get("channels") or []:
        s.add(
            GiveawayChannel(
                giveaway_id=gw.id,
                chat_id=channel["chat_id"],
                title=channel.get("title") or "",
                username=channel.get("username"),
                invite_link=channel.get("invite_link"),
                join_request=channel.get("join_request", False),
                is_mandatory=True,
            )
        )

    await s.flush()
    return gw


async def lock_giveaway(
    s: AsyncSession,
    gid: int,
) -> Giveaway | None:
    result = await s.execute(
        select(Giveaway)
        .where(Giveaway.id == gid)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def count_participants(
    s: AsyncSession,
    gid: int,
) -> int:
    result = await s.execute(
        select(func.count())
        .select_from(Participant)
        .where(Participant.giveaway_id == gid)
    )
    return result.scalar_one()


async def channels_of(
    s: AsyncSession,
    gid: int,
    mandatory_only: bool = False,
) -> list[GiveawayChannel]:
    query = (
        select(GiveawayChannel)
        .where(GiveawayChannel.giveaway_id == gid)
        .order_by(GiveawayChannel.id)
    )

    if mandatory_only:
        query = query.where(
            GiveawayChannel.is_mandatory.is_(True)
        )

    result = await s.execute(query)
    return list(result.scalars().all())


async def register_participant(
    s: AsyncSession,
    gid: int,
    user_id: int,
    referrer_id: int | None = None,
    tg_user: Any | None = None,
) -> JoinResult:
    """Register a participant and optionally save their Telegram profile."""

    gw = await lock_giveaway(s, gid)

    if (
        gw is None
        or gw.status != "active"
        or as_utc(gw.ends_at) <= utcnow()
    ):
        return "closed"

    if gw.host_id == user_id:
        return "own"

    if (
        gw.max_participants is not None
        and await count_participants(s, gid) >= gw.max_participants
    ):
        return "full"

    safe_referrer_id = (
        referrer_id
        if referrer_id is not None and referrer_id != user_id
        else None
    )

    username_snapshot = (
        getattr(tg_user, "username", None)
        if tg_user is not None
        else None
    )

    display_name_snapshot = ""

    if tg_user is not None:
        display_name_snapshot = " ".join(
            part
            for part in (
                getattr(tg_user, "first_name", None),
                getattr(tg_user, "last_name", None),
            )
            if part
        )

    try:
        async with s.begin_nested():
            s.add(
                Participant(
                    giveaway_id=gid,
                    user_id=user_id,
                    username_snapshot=username_snapshot,
                    display_name_snapshot=display_name_snapshot,
                    referrer_id=safe_referrer_id,
                )
            )
            await s.flush()

    except IntegrityError:
        return "duplicate"

    if safe_referrer_id is not None:
        referrer_is_participant = (
            await s.execute(
                select(Participant.id).where(
                    Participant.giveaway_id == gid,
                    Participant.user_id == safe_referrer_id,
                )
            )
        ).first()

        if referrer_is_participant:
            try:
                async with s.begin_nested():
                    s.add(
                        Referral(
                            giveaway_id=gid,
                            referrer_id=safe_referrer_id,
                            referred_id=user_id,
                        )
                    )
                    await s.flush()

            except IntegrityError:
                logger.info(
                    "Duplicate referral ignored: giveaway=%s referred=%s",
                    gid,
                    user_id,
                )

    return "ok"


async def referral_count(
    s: AsyncSession,
    gid: int,
    user_id: int | None = None,
) -> int:
    query = (
        select(func.count())
        .select_from(Referral)
        .where(Referral.giveaway_id == gid)
    )

    if user_id is not None:
        query = query.where(
            Referral.referrer_id == user_id
        )

    result = await s.execute(query)
    return result.scalar_one()


async def upsert_user(
    s: AsyncSession,
    tg_user: Any,
) -> User:
    """Insert or update a user using their Telegram ID."""

    name = " ".join(
        part
        for part in (
            getattr(tg_user, "first_name", None),
            getattr(tg_user, "last_name", None),
        )
        if part
    )

    user = (
        await s.execute(
            select(User).where(
                User.tg_id == tg_user.id
            )
        )
    ).scalar_one_or_none()

    if user is None:
        user = User(
            tg_id=tg_user.id,
            username=tg_user.username,
            full_name=name,
            last_seen_at=utcnow(),
        )
        s.add(user)

    else:
        user.username = tg_user.username
        user.full_name = name
        user.last_seen_at = utcnow()

    return user
