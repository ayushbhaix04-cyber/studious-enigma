from __future__ import annotations

import glob
import io
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1350
RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    emoji: str
    top: RGB
    bottom: RGB
    accent: RGB
    text: RGB
    muted: RGB


THEMES: dict[str, Theme] = {t.key: t for t in [
    Theme("neon", "Neon Glow", "🌟", (10, 5, 30), (40, 0, 80), (0, 255, 200), (255, 255, 255), (170, 170, 220)),
    Theme("gold", "Black & Gold", "👑", (8, 8, 8), (44, 34, 10), (212, 175, 55), (255, 248, 225), (180, 160, 100)),
    Theme("purple", "Purple Gradient", "🔮", (76, 29, 149), (168, 85, 247), (250, 204, 21), (255, 255, 255), (230, 215, 255)),
    Theme("cyber", "Blue Cyber", "⚡", (2, 12, 40), (0, 70, 140), (0, 229, 255), (235, 250, 255), (140, 190, 230)),
    Theme("gaming", "Red Gaming", "🎮", (30, 0, 0), (140, 10, 20), (255, 70, 70), (255, 255, 255), (255, 190, 190)),
    Theme("luxury", "Green Luxury", "💎", (2, 30, 20), (10, 90, 60), (120, 255, 190), (240, 255, 248), (160, 215, 190)),
    Theme("minimal", "Minimal White", "🤍", (250, 250, 250), (226, 229, 235), (30, 30, 30), (20, 20, 20), (110, 110, 120)),
    Theme("dark", "Dark Luxury", "🖤", (5, 5, 8), (28, 28, 38), (200, 200, 215), (245, 245, 250), (140, 140, 155)),
    Theme("rainbow", "Rainbow", "🌈", (255, 94, 98), (72, 85, 255), (255, 235, 59), (255, 255, 255), (255, 240, 240)),
    Theme("glass", "Futuristic Glass", "🧪", (20, 30, 60), (90, 120, 180), (180, 240, 255), (255, 255, 255), (210, 225, 245)),
    Theme("christmas", "Christmas", "🎄", (120, 10, 20), (12, 80, 40), (255, 215, 0), (255, 255, 255), (255, 225, 225)),
    Theme("festival", "Festival", "🎆", (25, 5, 60), (230, 80, 20), (255, 220, 80), (255, 255, 255), (255, 230, 200)),
]}
DEFAULT_THEME = "neon"

_FONT_PATTERNS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/nix/store/*dejavu*/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/nix/store/*/share/fonts/truetype/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
]


@lru_cache
def _font_path() -> str | None:
    for pattern in _FONT_PATTERNS:
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[0]
    return None


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        while draw.textlength(word, font=font) > max_w and len(word) > 1:  # hard-break huge words
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_w:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text: str, max_w: int, max_lines: int, sizes: tuple[int, ...]):
    for size in sizes:
        font = _font(size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _font(sizes[-1])
    return font, _wrap(draw, text, font, max_w)[:max_lines], sizes[-1]


def generate_poster(title: str, prize: str, winners: int, ends_at: str, host: str,
                    code: str, theme_key: str = DEFAULT_THEME) -> bytes:
    th = THEMES.get(theme_key, THEMES[DEFAULT_THEME])
    img = ImageOps.colorize(Image.linear_gradient("L").resize((W, H)), th.top, th.bottom).convert("RGBA")

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-250, -250, 600, 600), fill=th.accent + (70,))
    g.ellipse((600, 850, 1400, 1650), fill=th.accent + (60,))
    g.ellipse((700, 150, 1000, 450), fill=th.accent + (35,))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(70)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=48, outline=th.accent, width=6)
    d.rounded_rectangle((62, 62, W - 62, H - 62), radius=36, outline=th.muted, width=2)

    cx = W // 2
    d.text((cx, 130), "GIVEAWAY", font=_font(120), fill=th.accent, anchor="mt")
    d.line((200, 290, W - 200, 290), fill=th.accent, width=4)
    d.text((cx, 315), code, font=_font(38), fill=th.muted, anchor="mt")

    font, lines, size = _fit(d, title, W - 220, 3, (88, 76, 64, 54, 44))
    y = 430
    for line in lines:
        d.text((cx, y), line, font=font, fill=th.text, anchor="mt")
        y += int(size * 1.25)

    y = max(y + 50, 760)
    d.rounded_rectangle((110, y, W - 110, y + 250), radius=32, outline=th.accent, width=4)
    d.text((cx, y + 28), "PRIZE", font=_font(36), fill=th.muted, anchor="mt")
    pfont, plines, psize = _fit(d, prize, W - 300, 2, (66, 56, 48, 40))
    py = y + 90
    for line in plines:
        d.text((cx, py), line, font=pfont, fill=th.accent, anchor="mt")
        py += int(psize * 1.25)

    d.text((cx, 1090), f"{winners} WINNER{'S' if winners != 1 else ''}", font=_font(58), fill=th.text, anchor="mt")
    d.text((cx, 1170), f"ENDS  {ends_at}", font=_font(36), fill=th.muted, anchor="mt")
    d.text((cx, 1240), f"Hosted by {host[:30]}", font=_font(34), fill=th.muted, anchor="mt")

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
