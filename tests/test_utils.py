from datetime import timedelta

from posters.generator import THEMES, generate_poster
from services.channels import parse_channel_input
from utils.text import parse_duration, parse_giveaway_ref
from utils.validators import VALIDATORS


def test_duration_parsing():
    assert parse_duration("1m") == timedelta(minutes=1)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("10d") == timedelta(days=10)
    for bad in ("0m", "91d", "abc", "5", "", "-3h", "1w"):
        assert parse_duration(bad) is None


def test_giveaway_ref():
    assert parse_giveaway_ref("GW-10001") == 1
    assert parse_giveaway_ref("gw_10042") == 42
    assert parse_giveaway_ref("10001") == 1
    assert parse_giveaway_ref("5") is None
    assert parse_giveaway_ref("hello") is None


def test_channel_parsing():
    assert parse_channel_input("@mychannel")["username"] == "mychannel"
    assert parse_channel_input("https://t.me/mychannel")["username"] == "mychannel"
    p = parse_channel_input("-1001234567890 https://t.me/+AbCdEf request")
    assert p["chat_id"] == -1001234567890 and p["join_request"] and p["invite_link"].endswith("+AbCdEf")
    assert parse_channel_input("https://t.me/+AbCdEf") is None  # invite alone can't be resolved
    assert parse_channel_input("hello world") is None


def test_validators():
    assert VALIDATORS["winners"]("5") == 5
    for bad in ("0", "abc", "101"):
        try:
            VALIDATORS["winners"](bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_all_twelve_posters_render():
    assert len(THEMES) == 12
    for key in THEMES:
        png = generate_poster("A Very Long Giveaway Title That Wraps Across Lines", "Nitro x3", 3,
                              "2026-12-01 10:00 UTC", "Host", "GW-10001", key)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
