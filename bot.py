from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis

from config import get_settings
from database import session as dbs
from handlers import create, dashboard, owner, participate, secret, start
from middlewares.access import AccessMiddleware, ForceJoinMiddleware, ThrottleMiddleware
from scheduler.context import ctx

log = logging.getLogger("giveaway-bot")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


async def set_commands(bot: Bot, owner_id: int) -> None:
    public = [BotCommand(command=c, description=d) for c, d in start.PUBLIC_COMMANDS]
    await bot.set_my_commands(public, scope=BotCommandScopeDefault())
    # NOTE: /winner is intentionally absent from every list.
    owner_cmds = public + [BotCommand(command=c, description=d) for c, d in [
        ("panel", "Owner control panel"), ("addchannel", "Add global channel"), ("delchannel", "Remove global channel"),
        ("listchannels", "List global channels"), ("channelsettings", "Force join settings"),
        ("testchannels", "Channel health check"), ("ban", "Ban a user"), ("unban", "Unban a user"),
        ("setlog", "Set log channel"), ("broadcast", "Broadcast a message")]]
    try:
        await bot.set_my_commands(owner_cmds, scope=BotCommandScopeChat(chat_id=owner_id))
    except Exception:  # noqa: BLE001 - owner may not have opened the bot yet
        log.warning("could not set owner command scope yet")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if settings.run_migrations_on_start:
        await asyncio.to_thread(run_migrations)  # alembic's async env needs its own event loop
    dbs.init_engine(settings.database_url)

    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    await redis.ping()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(storage=RedisStorage.from_url(settings.redis_dsn), redis=redis)

    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(AccessMiddleware())
        observer.outer_middleware(ThrottleMiddleware())
        observer.outer_middleware(ForceJoinMiddleware())
    # Order matters: secret & owner first, then commands, then FSM-heavy flows.
    dp.include_routers(secret.router, start.router, owner.router, dashboard.router, participate.router, create.router)

    ctx.bot, ctx.redis = bot, redis
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.sync_database_url, tablename="apscheduler_jobs")},
        timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    scheduler.add_job("scheduler.jobs:sweep_giveaways", "interval", seconds=15, id="sweep", replace_existing=True)
    scheduler.add_job("scheduler.jobs:flush_log_queue", "interval", seconds=60, id="log_flush", replace_existing=True)
    scheduler.start()
    ctx.scheduler = scheduler

    await set_commands(bot, settings.bot_owner_id)
    me = await bot.get_me()
    log.info("started as @%s", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await redis.aclose()
        if dbs.engine is not None:
            await dbs.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
