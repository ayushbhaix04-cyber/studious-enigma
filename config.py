
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

    bot_token: str
    bot_owner_id: int
    database_url: str

    redis_url: str | None = None
    redis_public_url: str | None = None

    log_channel_id: int | None = None
    bot_username: str | None = None

    captcha_ttl: int = 300
    run_migrations_on_start: bool = True

    @field_validator(
        "log_channel_id",
        "redis_url",
        "redis_public_url",
        "bot_username",
        mode="before"
    )
    @classmethod
    def _empty_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("database_url")
    @classmethod
    def _normalize_db(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]

        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://"):]

        return v

    @property
    def sync_database_url(self) -> str:
        url = self.database_url.replace(
            "+asyncpg",
            "+psycopg2"
        )

        url = url.replace(
            "ssl=require",
            "sslmode=require"
        )

        return url

    @property
    def redis_dsn(self) -> str:
        return (
            self.redis_url
            or self.redis_public_url
            or "redis://localhost:6379/0"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
