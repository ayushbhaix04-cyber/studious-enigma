from __future__ import annotations

from typing import Any


class Ctx:
    bot: Any = None
    redis: Any = None
    scheduler: Any = None


ctx = Ctx()
