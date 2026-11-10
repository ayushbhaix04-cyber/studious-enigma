from __future__ import annotations

import secrets
from typing import Literal

from redis.asyncio import Redis

_rng = secrets.SystemRandom()
MAX_FAILS = 5
LOCK_SECONDS = 600


def make_challenge() -> tuple[int, int, list[int]]:
    """Return (a, b, shuffled answer options) for an 'a + b = ?' puzzle."""
    a, b = _rng.randint(1, 9), _rng.randint(1, 9)
    answer = a + b
    options = {answer}
    while len(options) < 4:
        options.add(_rng.randint(2, 18))
    opts = list(options)
    _rng.shuffle(opts)
    return a, b, opts


def _key(user_id: int, giveaway_id: int) -> str:
    return f"captcha:{user_id}:{giveaway_id}"


async def issue(redis: Redis, user_id: int, giveaway_id: int, ttl: int = 300) -> tuple[str, list[int]]:
    a, b, opts = make_challenge()
    await redis.set(_key(user_id, giveaway_id), str(a + b), ex=ttl)
    return f"{a} + {b} = ?", opts


async def is_locked(redis: Redis, user_id: int) -> bool:
    return bool(await redis.exists(f"captcha_lock:{user_id}"))


Result = Literal["ok", "wrong", "expired", "locked"]


async def verify(redis: Redis, user_id: int, giveaway_id: int, value: int) -> Result:
    """Single-use: the stored answer is consumed on every attempt, so a wrong answer needs a new sum."""
    if await is_locked(redis, user_id):
        return "locked"
    stored = await redis.getdel(_key(user_id, giveaway_id))
    if stored is None:
        return "expired"
    if str(stored) == str(value):
        await redis.delete(f"captcha_fails:{user_id}")
        return "ok"
    fails = await redis.incr(f"captcha_fails:{user_id}")
    await redis.expire(f"captcha_fails:{user_id}", LOCK_SECONDS)
    if fails >= MAX_FAILS:
        await redis.set(f"captcha_lock:{user_id}", "1", ex=LOCK_SECONDS)
        await redis.delete(f"captcha_fails:{user_id}")
    return "wrong"
