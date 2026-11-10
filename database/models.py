
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Neon uses INTEGER for internal primary keys.
BigPK = Integer
TS = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # Telegram user ID, not the internal database ID.
    tg_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )

    username: Mapped[str | None] = mapped_column(String(64), index=True)

    full_name: Mapped[str] = mapped_column(
        String(256),
        default="",
        nullable=False,
    )

    wins_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    is_banned: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class Giveaway(Base):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    host_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    prize: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    winners_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    poster_file_id: Mapped[str | None] = mapped_column(String(256))

    poster_theme: Mapped[str] = mapped_column(
        String(32),
        default="neon",
        server_default=text("'neon'"),
        nullable=False,
    )

    rules: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )

    max_participants: Mapped[int | None] = mapped_column(Integer)

    confirm_message: Mapped[str | None] = mapped_column(Text)

    start_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )

    is_public: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    referral_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    bonus_per_referral: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        default="active",
        server_default=text("'active'"),
        index=True,
        nullable=False,
    )

    ends_at: Mapped[datetime] = mapped_column(
        TS,
        index=True,
        nullable=False,
    )

    paused_at: Mapped[datetime | None] = mapped_column(TS)

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )

    ended_at: Mapped[datetime | None] = mapped_column(TS)

    @property
    def payload(self) -> str:
        return f"GW_{10000 + self.id}"


class GiveawayChannel(Base):
    __tablename__ = "giveaway_channels"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(256),
        default="",
        nullable=False,
    )

    username: Mapped[str | None] = mapped_column(String(64))

    invite_link: Mapped[str | None] = mapped_column(String(256))

    join_request: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


class Participant(Base):
    __tablename__ = "participants"

    __table_args__ = (
        UniqueConstraint(
            "giveaway_id",
            "user_id",
            name="uq_participant",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Telegram user ID.
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    username_snapshot: Mapped[str | None] = mapped_column(String(64))

    display_name_snapshot: Mapped[str] = mapped_column(
        String(256),
        default="",
        server_default=text("''"),
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )

    eligible: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    extra_entries: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    referrer_id: Mapped[int | None] = mapped_column(BigInteger)


class Winner(Base):
    __tablename__ = "winners"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Telegram user ID.
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    selection_type: Mapped[str] = mapped_column(
        String(16),
        default="random",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )

    replaced_at: Mapped[datetime | None] = mapped_column(TS)


class GlobalChannel(Base):
    __tablename__ = "global_force_join_channels"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(256),
        default="",
        nullable=False,
    )

    username: Mapped[str | None] = mapped_column(String(64))

    invite_link: Mapped[str | None] = mapped_column(String(256))

    join_request: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class Referral(Base):
    __tablename__ = "referrals"

    __table_args__ = (
        UniqueConstraint(
            "giveaway_id",
            "referred_id",
            name="uq_referral",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    referrer_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    referred_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class PublishedMessage(Base):
    __tablename__ = "published_messages"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    message_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class BannedUser(Base):
    __tablename__ = "banned_users"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )

    reason: Mapped[str | None] = mapped_column(Text)

    banned_by: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    banned_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class LogEvent(Base):
    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(
        BigPK,
        primary_key=True,
        autoincrement=True,
    )

    event: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )

    actor_id: Mapped[int | None] = mapped_column(BigInteger)

    giveaway_id: Mapped[int | None] = mapped_column(BigInteger)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    delivered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        index=True,
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    value: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )
