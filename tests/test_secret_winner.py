from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from bot import set_commands
from database import session as dbs
from database.models import Winner
from handlers import secret, start
from tests.conftest import OWNER, make_giveaway


def msg(user_id):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id), answer=AsyncMock())


def cmd(args):
    return SimpleNamespace(args=args)


async def test_non_owner_is_silently_ignored(db, redis, bot):
    gid = await make_giveaway(n_users=3)
    for who in (2000, 5001, 424242):
        m = msg(who)
        await secret.winner_cmd(m, cmd(f"GW-{10000 + gid} 5001"), bot, redis)
        await secret.winner_cmd(m, cmd(None), bot, redis)
        m.answer.assert_not_called()
    assert bot.sent == []
    async with dbs.session_scope() as s:
        assert (await s.execute(select(Winner))).scalars().all() == []


async def test_missing_from_user_is_ignored(db, redis, bot):
    m = SimpleNamespace(from_user=None, answer=AsyncMock())
    await secret.winner_cmd(m, cmd("GW-10001 1"), bot, redis)
    m.answer.assert_not_called()


async def test_owner_sets_winner_by_id_and_username(db, redis, bot):
    gid = await make_giveaway(n_users=5, winners=2)
    m = msg(OWNER)
    await secret.winner_cmd(m, cmd(f"GW-{10000 + gid} 5001"), bot, redis)
    m.answer.assert_called_with(secret.MESSAGES["ok"])
    m2 = msg(OWNER)
    await secret.winner_cmd(m2, cmd(f"GW-{10000 + gid} @user2"), bot, redis)
    m2.answer.assert_called_with(secret.MESSAGES["ok"])
    async with dbs.session_scope() as s:
        ws = (await s.execute(select(Winner))).scalars().all()
    assert {w.user_id for w in ws} == {5001, 5002}
    assert all(w.selection_type == "owner_override" for w in ws)


async def test_owner_usage_and_errors(db, redis, bot):
    gid = await make_giveaway(n_users=2)
    m = msg(OWNER)
    await secret.winner_cmd(m, cmd("onlyone"), bot, redis)
    assert "Usage" in m.answer.call_args.args[0]
    m = msg(OWNER)
    await secret.winner_cmd(m, cmd(f"GW-{10000 + gid} 999999"), bot, redis)
    assert "never started" in m.answer.call_args.args[0]
    m = msg(OWNER)
    await secret.winner_cmd(m, cmd("GW-19999 5001"), bot, redis)
    assert m.answer.call_args.args[0] == secret.MESSAGES["no_giveaway"]


def test_winner_never_exposed_in_help_or_commands():
    assert "winner" not in start.HELP.lower()
    assert "winner" not in start.WELCOME.lower()
    assert all(c != "winner" for c, _ in start.PUBLIC_COMMANDS)


async def test_winner_not_registered_with_botfather():
    registered = []

    class B:
        async def set_my_commands(self, commands, scope=None):
            registered.extend(c.command for c in commands)

    await set_commands(B(), OWNER)
    assert registered and "winner" not in registered
