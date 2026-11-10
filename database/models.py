
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


BigPK = Integer
TS = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    # Internal database ID — not the Telegram ID
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    tg_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )
    username: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
    )
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
    host_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    prize: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
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
    rules: Mapped[str | None] = mapped_column(Text)
    max_participants: Mapped[int | None] = mapped_column(Integer)
    confirm_message: Mapped[str | None] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    # active | paused | ending | completed | cancelled
    status: Mapped[str] = mapped_column(
        String(16),
        default="active",
        server_default=text("'active'"),
        index=True,
        nullable=False,
    )
    ends_at: Mapped[datetime] = mapped_column(TS, index=True, nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(
        TS,
        default=utcnow,
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(TS)

    @property
    def code(self) -> str:
        return f"GW-{10000 + self.id}"

    @property
    def payload(self) -> str:
        return f"GW_{10000 + self.id}"


class GiveawayChannel(Base):
    __tablename__ = "giveaway_channels"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[str | None] = mapped_column(String(64))
    invite_link: Mapped[str | None] = mapped_column(String(256))
    join_request: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "user_id", name="uq_participant"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
    )
    # Telegram user IDs must be BIGINT
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referrer_id: Mapped[int | None] = mapped_column(BigInteger)
    joined_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Winner(Base):
    __tablename__ = "winners"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
    )
    # Telegram user IDs must be BIGINT
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    # random | reroll | owner_override
    selection_type: Mapped[str] = mapped_column(
        String(16),
        default="random",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    replaced_at: Mapped[datetime | None] = mapped_column(TS)


class GlobalChannel(Base):
    __tablename__ = "global_force_join_channels"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[str | None] = mapped_column(String(64))
    invite_link: Mapped[str | None] = mapped_column(String(256))
    join_request: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "referred_id", name="uq_referral"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
    )
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referred_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PublishedMessage(Base):
    __tablename__ = "published_messages"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"),
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class BannedUser(Base):
    __tablename__ = "banned_users"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    reason: Mapped[str | None] = mapped_column(Text)
    banned_by: Mapped[int] = mapped_column(BigInteger)
    banned_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LogEvent(Base):
    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    giveaway_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
