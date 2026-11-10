from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import escape as esc  # noqa: F401  (re-exported)

_DUR = re.compile(r"^\s*(\d{1,4})\s*([mhd])\s*$", re.I)
MIN_DURATION = timedelta(minutes=1)
MAX_DURATION = timedelta(days=90)


def parse_duration(text: str) -> timedelta | None:
    m = _DUR.match(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    td = timedelta(minutes=n) if unit == "m" else timedelta(hours=n) if unit == "h" else timedelta(days=n)
    return td if MIN_DURATION <= td <= MAX_DURATION else None


def as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_dt(dt: datetime) -> str:
    return as_utc(dt).strftime("%Y-%m-%d %H:%M UTC")


def fmt_remaining(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    secs = int((as_utc(dt) - now).total_seconds())
    if secs <= 0:
        return "ended"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m or not (d or h) else ""]
    return " ".join(p for p in parts if p)


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def mention(user_id: int, name: str | None) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or str(user_id))}</a>'


def parse_giveaway_ref(text: str) -> int | None:
    """'GW-10001' / 'GW_10001' / '10001' -> internal id (1)."""
    t = re.sub(r"^gw[-_]?", "", text.strip(), flags=re.I)
    if not t.isdigit():
        return None
    n = int(t)
    return n - 10000 if n > 10000 else None
