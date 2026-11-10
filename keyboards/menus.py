from __future__ import annotations

from typing import Any, Iterable

from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from keyboards.callbacks import GwCb, OwnCb, PageCb
from posters.generator import THEMES
from services.channels import link_for


def main_menu_kb() -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="🎁 Create Giveaway", callback_data="menu:create"), Btn(text="🔍 Explore Giveaways", callback_data="menu:explore")],
        [Btn(text="📊 My Giveaways", callback_data="menu:mine"), Btn(text="🏆 My Wins", callback_data="menu:wins")],
        [Btn(text="👤 My Profile", callback_data="menu:profile"), Btn(text="ℹ️ Help & Support", callback_data="menu:help")],
    ])


def home_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🏠 Main Menu", callback_data="menu:home")]])


def force_join_kb(channels: Iterable[Any]) -> Kb:
    rows = [[Btn(text=f"📢 Join {c.title or 'Channel'}"[:60], url=link_for(c))] for c in channels]
    rows.append([Btn(text="✅ VERIFY MEMBERSHIP", callback_data="verify:global")])
    return Kb(inline_keyboard=rows)


def themes_kb(prefix: str) -> list[list[Btn]]:
    items = list(THEMES.values())
    return [[Btn(text=f"{t.emoji} {t.label}", callback_data=f"{prefix}{t.key}") for t in items[i:i + 2]]
            for i in range(0, len(items), 2)]


def manage_kb(gw: Any) -> Kb:
    g = gw.id
    rows = [[Btn(text="👥 Participants", callback_data=GwCb(a="parts", id=g).pack()),
             Btn(text="📊 Statistics", callback_data=GwCb(a="stats", id=g).pack())]]
    if gw.status == "active":
        rows.append([Btn(text="⏸ Pause", callback_data=GwCb(a="pause", id=g).pack()),
                     Btn(text="⏰ Extend", callback_data=GwCb(a="extend", id=g).pack())])
    elif gw.status == "paused":
        rows.append([Btn(text="▶️ Resume", callback_data=GwCb(a="resume", id=g).pack()),
                     Btn(text="⏰ Extend", callback_data=GwCb(a="extend", id=g).pack())])
    if gw.status in ("active", "paused"):
        rows += [
            [Btn(text="✏️ Edit Giveaway", callback_data=GwCb(a="edit", id=g).pack()),
             Btn(text="📢 Manage Channels", callback_data=GwCb(a="channels", id=g).pack())],
            [Btn(text="🎨 Edit Poster", callback_data=GwCb(a="poster", id=g).pack()),
             Btn(text="📣 Publish Again", callback_data=GwCb(a="publish", id=g).pack())],
            [Btn(text="🏁 End Giveaway", callback_data=GwCb(a="end", id=g).pack()),
             Btn(text="🚨 Cancel Giveaway", callback_data=GwCb(a="cancel", id=g).pack())],
        ]
    if gw.status == "completed":
        rows.append([Btn(text="🔄 Reroll Winner", callback_data=GwCb(a="reroll", id=g).pack())])
    rows.append([Btn(text="📄 Export Participants (CSV)", callback_data=GwCb(a="csv", id=g).pack())])
    rows.append([Btn(text="⬅️ Back", callback_data=PageCb(kind="mine", p=0).pack())])
    return Kb(inline_keyboard=rows)


def owner_panel_kb() -> Kb:
    def b(t: str, a: str) -> Btn:
        return Btn(text=t, callback_data=OwnCb(a=a).pack())
    return Kb(inline_keyboard=[
        [b("📊 Bot Statistics", "stats"), b("👥 Manage Users", "users")],
        [b("🎁 Manage Giveaways", "gws"), b("🔒 Force Join Channels", "fj")],
        [b("📋 Log Channel Setup", "log"), b("📣 Broadcast", "bc")],
        [b("🚫 Ban / Unban Users", "ban"), b("⚙️ Bot Settings", "settings")],
        [b("📈 System Analytics", "ana"), b("🧾 Audit Logs", "audit")],
        [b("🩺 System Health", "health")],
    ])
