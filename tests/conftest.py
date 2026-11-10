import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("BOT_OWNER_ID", "1000")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from database import session as dbs  # noqa: E402
from database.models import Base, Giveaway, Participant, User, utcnow  # noqa: E402

OWNER = 1000
HOST = 2000


class FakeBot:
    """Minimal async stand-in for aiogram.Bot; `members` maps chat_id -> set of member user ids."""

    id = 99
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.members: dict[int, set[int]] = {}

    async def me(self):
        return SimpleNamespace(username="testbot")

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=len(self.sent), photo=None)

    async def edit_message_reply_markup(self, **kw):
        return True

    async def get_chat_member(self, chat_id, user_id):
        status = "member" if user_id in self.members.get(chat_id, set()) else "left"
        return SimpleNamespace(status=status, is_member=status == "member")


@pytest_asyncio.fixture
async def db():
    dbs.init_engine("sqlite+aiosqlite://")
    async with dbs.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dbs.engine.dispose()


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def bot():
    return FakeBot()


async def make_giveaway(n_users: int = 10, winners: int = 3, **over) -> int:
    """Create a host, n users, a giveaway and enter every user. Returns the giveaway id."""
    async with dbs.session_scope() as s:
        s.add(User(id=HOST, username="host", full_name="Host"))
        for i in range(1, n_users + 1):
            s.add(User(id=5000 + i, username=f"user{i}", full_name=f"User {i}"))
        gw = Giveaway(host_id=HOST, title="T", prize="P", description="D", winners_count=winners,
                      ends_at=utcnow() + timedelta(hours=1), **over)
        s.add(gw)
        await s.flush()
        for i in range(1, n_users + 1):
            s.add(Participant(giveaway_id=gw.id, user_id=5000 + i))
        return gw.id
