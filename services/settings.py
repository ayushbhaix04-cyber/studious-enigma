from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import BotSetting

DEFAULTS = {
    "force_join_enabled": "1",
    "force_join_text": ("🔒 <b>ACCESS REQUIRED</b>\n\nTo use this bot and participate in giveaways, "
                        "please join all required channels below: 📢"),
    "log_channel_id": "",
}


async def get_setting(s: AsyncSession, key: str) -> str:
    row = await s.get(BotSetting, key)
    return row.value if row else DEFAULTS.get(key, "")


async def set_setting(s: AsyncSession, key: str, value: str) -> None:
    row = await s.get(BotSetting, key)
    if row:
        row.value = value
    else:
        s.add(BotSetting(key=key, value=value))


async def log_channel(s: AsyncSession) -> int | None:
    raw = (await get_setting(s, "log_channel_id")).strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return get_settings().log_channel_id
