from __future__ import annotations

from utils.text import parse_duration


def _text(lo: int, hi: int, label: str):
    def check(raw: str) -> str:
        v = raw.strip()
        if not lo <= len(v) <= hi:
            raise ValueError(f"{label} must be {lo}–{hi} characters.")
        return v
    return check


def _int(lo: int, hi: int, label: str):
    def check(raw: str) -> int:
        v = raw.strip()
        if not v.isdigit() or not lo <= int(v) <= hi:
            raise ValueError(f"{label} must be a whole number between {lo} and {hi}.")
        return int(v)
    return check


def _duration(raw: str) -> int:
    td = parse_duration(raw)
    if td is None:
        raise ValueError("Use formats like 15m, 2h, 3d (between 1m and 90d).")
    return int(td.total_seconds())


VALIDATORS = {
    "title": _text(3, 80, "Title"),
    "prize": _text(2, 120, "Prize"),
    "description": _text(5, 1500, "Description"),
    "winners": _int(1, 100, "Winners"),
    "duration": _duration,
    "rules": _text(1, 1000, "Rules"),
    "max": _int(1, 1_000_000, "Participant limit"),
    "confirm": _text(1, 300, "Confirmation message"),
}
