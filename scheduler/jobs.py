from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from database.models import Giveaway, utcnow
from database.session import session_scope
from scheduler.context import ctx
from services import audit, winners

log = logging.getLogger(__name__)


async def sweep_giveaways() -> None:
    """Persistent-by-design: due giveaways are discovered from PostgreSQL, so restarts lose nothing."""
    async with session_scope() as s:
        ids = list((await s.execute(select(Giveaway.id).where(
            Giveaway.status.in_(("active", "ending")), Giveaway.ends_at <= utcnow()))).scalars())
    if not ids:
        return
    results = await asyncio.gather(*(winners.finalize(ctx.bot, ctx.redis, gid) for gid in ids), return_exceptions=True)
    for gid, res in zip(ids, results):
        if isinstance(res, Exception):
            log.exception("finalize failed for %s", gid, exc_info=res)


async def flush_log_queue() -> None:
    await audit.flush(ctx.bot)
