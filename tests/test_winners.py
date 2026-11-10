from sqlalchemy import select

from database import session as dbs
from database.models import BannedUser, Giveaway, GiveawayChannel, LogEvent, Winner
from services import winners
from tests.conftest import HOST, OWNER, make_giveaway


async def _winners(gid, active=True):
    async with dbs.session_scope() as s:
        q = select(Winner).where(Winner.giveaway_id == gid)
        if active:
            q = q.where(Winner.is_active.is_(True))
        return list((await s.execute(q)).scalars())


def test_pick_is_unique_and_bounded():
    pool = list(range(50))
    got = winners.pick(pool, 10)
    assert len(got) == len(set(got)) == 10
    assert winners.pick([1, 2], 5) in ([1, 2], [2, 1])
    assert winners.pick([], 3) == []


async def test_finalize_draws_unique_winners_and_completes(db, redis, bot):
    gid = await make_giveaway(n_users=10, winners=3)
    res = await winners.finalize(bot, redis, gid)
    ws = await _winners(gid)
    assert len(res) == 3 and len({w.user_id for w in ws}) == 3
    assert all(w.selection_type == "random" and w.user_id != HOST for w in ws)
    async with dbs.session_scope() as s:
        assert (await s.get(Giveaway, gid)).status == "completed"
        events = (await s.execute(select(LogEvent.event))).scalars().all()
    assert "giveaway_completed" in events
    assert any(chat in {w.user_id for w in ws} for chat, _ in bot.sent)  # winners notified
    assert await winners.finalize(bot, redis, gid) is None  # idempotent


async def test_banned_users_are_never_drawn(db, redis, bot):
    gid = await make_giveaway(n_users=4, winners=4)
    async with dbs.session_scope() as s:
        s.add(BannedUser(user_id=5001, banned_by=OWNER))
    await winners.finalize(bot, redis, gid)
    assert 5001 not in {w.user_id for w in await _winners(gid)}
    assert len(await _winners(gid)) == 3


async def test_users_who_left_mandatory_channel_are_excluded(db, redis, bot):
    gid = await make_giveaway(n_users=5, winners=5)
    async with dbs.session_scope() as s:
        s.add(GiveawayChannel(giveaway_id=gid, chat_id=-100500, title="C", is_mandatory=True))
    bot.members[-100500] = {5001, 5002}  # only two are still subscribed
    await winners.finalize(bot, redis, gid)
    assert {w.user_id for w in await _winners(gid)} == {5001, 5002}


async def test_optional_channels_are_not_enforced(db, redis, bot):
    gid = await make_giveaway(n_users=3, winners=3)
    async with dbs.session_scope() as s:
        s.add(GiveawayChannel(giveaway_id=gid, chat_id=-100500, title="C", is_mandatory=False))
    await winners.finalize(bot, redis, gid)
    assert len(await _winners(gid)) == 3


async def test_reroll_replaces_one_and_preserves_others(db, redis, bot):
    gid = await make_giveaway(n_users=10, winners=3)
    await winners.finalize(bot, redis, gid)
    before = await _winners(gid)
    victim = before[0]
    new = await winners.reroll(bot, gid, victim.id, OWNER)
    after = await _winners(gid)
    assert new is not None and len(after) == 3
    assert victim.user_id not in {w.user_id for w in after}
    assert {w.user_id for w in before[1:]} <= {w.user_id for w in after}
    assert new not in {w.user_id for w in before}  # never re-picks a previous winner
    assert next(w for w in after if w.user_id == new).selection_type == "reroll"


async def test_owner_override_before_draw_is_kept(db, redis, bot):
    gid = await make_giveaway(n_users=10, winners=3)
    assert await winners.owner_override(bot, gid, 5007, OWNER) == "ok"
    assert await winners.owner_override(bot, gid, 5007, OWNER) == "already"
    await winners.finalize(bot, redis, gid)
    ws = await _winners(gid)
    assert len(ws) == 3 and any(w.user_id == 5007 and w.selection_type == "owner_override" for w in ws)
    async with dbs.session_scope() as s:
        ev = (await s.execute(select(LogEvent).where(LogEvent.event == "owner_override_winner"))).scalars().all()
    assert len(ev) == 1 and ev[0].actor_id == OWNER


async def test_owner_override_after_completion_replaces_random_winner(db, redis, bot):
    gid = await make_giveaway(n_users=10, winners=2)
    await winners.finalize(bot, redis, gid)
    target = next(u for u in range(5001, 5011) if u not in {w.user_id for w in await _winners(gid)})
    assert await winners.owner_override(bot, gid, target, OWNER) == "ok"
    ws = await _winners(gid)
    assert len(ws) == 2 and target in {w.user_id for w in ws}
