"""
banner.py — Банери хушомадгӯии бот (барои /start).

Ҳамон услуби receipt.py (неон-кабуд, шрифти DejaVu, 3x supersampling)-ро
идома медиҳад, то тамоми "визуал"-и бот ба ҳам мувофиқ бошад.
"""
import os
import math
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FALLBACK_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/liberation",
]

W, H = 1200, 630

# Ҳамон палитра ки дар receipt.py
BG_TOP = (7, 11, 20)
BG_BOTTOM = (12, 20, 34)
ACCENT = (56, 189, 248)      # кабуди неон
ACCENT_SOFT = (9, 38, 58)
FIRE = (255, 122, 26)        # норинҷӣ — барои лаҳни "Free Fire"
WHITE = (241, 245, 249)
GRAY = (139, 150, 168)

_FONT_CACHE = {}


def _font(name, size):
    key = (name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [os.path.join(_FONT_DIR, name)]
    for d in _FALLBACK_DIRS:
        candidates.append(os.path.join(d, name))
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if not path:
        raise FileNotFoundError(f"Шрифти '{name}' ёфт нашуд")
    font = ImageFont.truetype(path, size)
    _FONT_CACHE[key] = font
    return font


def _vertical_gradient(w, h, top, bottom):
    base = Image.new("RGB", (1, h), 0)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        base.putpixel((0, y), (r, g, b))
    return base.resize((w, h))


def _glow_dot(img, cx, cy, r, color, opacity=140):
    """Доғи рӯшноии мулоим (барои фазо/чуқурӣ)."""
    glow = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((r, r, r * 3, r * 3), fill=(*color, opacity))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.5))
    img.alpha_composite(glow, (int(cx - r * 2), int(cy - r * 2)))


def _diamond(draw, cx, cy, size, fill, outline=None, width=0):
    """Шакли алмоз (ромб)-и оддӣ."""
    pts = [
        (cx, cy - size),
        (cx + size * 0.72, cy - size * 0.15),
        (cx, cy + size),
        (cx - size * 0.72, cy - size * 0.15),
    ]
    draw.polygon(pts, fill=fill, outline=outline, width=width)
    # хатти дарунӣ (барои ҳиссиёти "буриш"-и алмос)
    draw.line([(cx, cy - size), (cx, cy + size)], fill=outline or fill, width=max(1, width))
    draw.line([(cx - size * 0.72, cy - size * 0.15), (cx + size * 0.72, cy - size * 0.15)],
               fill=outline or fill, width=max(1, width))


def generate_welcome_banner(
    title: str = "DILOVAR FF",
    subtitle: str = "Донати худкор дар якчанд дақиқа ⚡",
    services: str = "💎 Free Fire   •   🔫 PUBG Mobile   •   ⭐ Telegram",
) -> BytesIO:
    S = 2
    Ws, Hs = W * S, H * S

    img = _vertical_gradient(Ws, Hs, BG_TOP, BG_BOTTOM).convert("RGBA")

    # Атмосфераи рӯшноӣ (глоу)-и мулоим дар гӯшаҳо
    _glow_dot(img, Ws * 0.14, Hs * 0.20, int(180 * S), ACCENT, opacity=90)
    _glow_dot(img, Ws * 0.90, Hs * 0.75, int(220 * S), FIRE, opacity=70)
    _glow_dot(img, Ws * 0.80, Hs * 0.15, int(140 * S), ACCENT, opacity=60)

    draw = ImageDraw.Draw(img)

    # Алмосҳои ороишӣ (заминаи парокандаи хира)
    deco = [
        (Ws * 0.10, Hs * 0.78, 26 * S, 40),
        (Ws * 0.88, Hs * 0.30, 20 * S, 35),
        (Ws * 0.06, Hs * 0.45, 14 * S, 30),
        (Ws * 0.95, Hs * 0.60, 16 * S, 30),
        (Ws * 0.20, Hs * 0.90, 18 * S, 25),
    ]
    for dx, dy, dsize, op in deco:
        layer = Image.new("RGBA", (int(dsize * 4), int(dsize * 4)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        _diamond(ld, dsize * 2, dsize * 2, dsize, fill=(*ACCENT, op))
        img.alpha_composite(layer, (int(dx - dsize * 2), int(dy - dsize * 2)))

    # Алмоси марказии калон бо глоу (лого-шакл)
    cx, cy = Ws * 0.5, Hs * 0.30
    big = int(70 * S)
    _glow_dot(img, cx, cy, int(big * 1.6), ACCENT, opacity=130)
    draw = ImageDraw.Draw(img)
    _diamond(draw, cx, cy, big, fill=ACCENT, outline=WHITE, width=3 * S)

    # Унвон
    f_title = _font("DejaVuSans-Bold.ttf", 78 * S)
    tw = draw.textlength(title, font=f_title)
    ty = cy + big + 36 * S
    draw.text((cx - tw / 2, ty), title, font=f_title, fill=WHITE)

    # Зерунвон
    f_sub = _font("DejaVuSans.ttf", 34 * S)
    sy = ty + 96 * S
    sw = draw.textlength(subtitle, font=f_sub)
    draw.text((cx - sw / 2, sy), subtitle, font=f_sub, fill=ACCENT)

    # Хати ҷудокунанда
    line_y = sy + 62 * S
    draw.line((Ws * 0.28, line_y, Ws * 0.72, line_y), fill=(*ACCENT, 90), width=2 * S)

    # Рӯйхати хизматҳо
    f_srv = _font("DejaVuSans.ttf", 28 * S)
    srv_y = line_y + 30 * S
    srv_w = draw.textlength(services, font=f_srv)
    draw.text((cx - srv_w / 2, srv_y), services, font=f_srv, fill=GRAY)

    # Бейҷи "24/7" дар кунҷ
    badge_text = "⚡ 24/7 АВТОМАТӢ"
    f_badge = _font("DejaVuSans-Bold.ttf", 22 * S)
    bw = draw.textlength(badge_text, font=f_badge)
    bpad = 18 * S
    bh = 44 * S
    bx0, by0 = Ws - bw - bpad * 2 - 30 * S, 30 * S
    draw.rounded_rectangle(
        (bx0, by0, bx0 + bw + bpad * 2, by0 + bh),
        radius=bh / 2, fill=ACCENT_SOFT, outline=ACCENT, width=2 * S
    )
    draw.text((bx0 + bpad, by0 + (bh - 24 * S) / 2), badge_text, font=f_badge, fill=ACCENT)

    final_img = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = BytesIO()
    buf.name = "welcome_banner.png"
    final_img.save(buf, format="PNG")
    buf.seek(0)
    return buf


if __name__ == "__main__":
    buf = generate_welcome_banner()
    with open("/tmp/welcome_banner_preview.png", "wb") as f:
        f.write(buf.read())
    print("Сохта шуд: /tmp/welcome_banner_preview.png")
