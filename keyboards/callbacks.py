from aiogram.filters.callback_data import CallbackData


class GwCb(CallbackData, prefix="gw"):
    a: str
    id: int = 0
    x: str = ""


class JoinCb(CallbackData, prefix="gv"):
    g: int
    r: int = 0


class CapCb(CallbackData, prefix="cap"):
    g: int
    v: int
    r: int = 0


class PageCb(CallbackData, prefix="pg"):
    kind: str
    p: int = 0


class OwnCb(CallbackData, prefix="own"):
    a: str
    id: int = 0
