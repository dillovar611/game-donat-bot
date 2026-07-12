"""
banner.py — Банери хушомадгӯии бот (барои /start).

Ҳамон услуби receipt.py (неон-кабуд, шрифти DejaVu, supersampling)-ро идома
медиҳад, то тамоми "визуал"-и бот ба ҳам мувофиқ бошад.

МУҲИМ: ҳеч эмоҷӣ (emoji) дар расм истифода намешавад — шрифти DejaVu
эмоҷиро дастгирӣ намекунад ва ба ҷои он чоркунҷаи холӣ (tofu) мебарояд.
Ба ҷои он ҳама иконаҳо (алмос, нишонгирӣ, ситора, галка, барқ) бо
вектор (полигон/хат) дастӣ кашида мешаванд — дар ҳар сервере кор мекунад.
"""
import os
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

BG_TOP = (7, 11, 20)
BG_BOTTOM = (12, 20, 34)
ACCENT = (56, 189, 248)
ACCENT_LIGHT = (150, 224, 253)
ACCENT_DARK = (18, 92, 130)
ACCENT_SOFT = (9, 38, 58)
FIRE = (255, 122, 26)
WHITE = (241, 245, 249)
GRAY = (139, 150, 168)
GOLD = (250, 204, 84)

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
    glow = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((r, r, r * 3, r * 3), fill=(*color, opacity))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.5))
    img.alpha_composite(glow, (int(cx - r * 2), int(cy - r * 2)))


# ==================== ИКОНАҲОИ ВЕКТОРӢ (бе эмоҷӣ) ====================
def _flat_diamond(draw, cx, cy, size, fill, outline=None, width=0):
    """Алмоси одии сода — барои иконаҳои хурди ороишӣ."""
    pts = [
        (cx, cy - size),
        (cx + size * 0.72, cy - size * 0.15),
        (cx, cy + size),
        (cx - size * 0.72, cy - size * 0.15),
    ]
    draw.polygon(pts, fill=fill, outline=outline, width=width)


def _gem_realistic(img, cx, cy, s):
    """
    Алмоси "буришхӯрда" (faceted gem) — фан аз якчанд факет (мисли
    алмоси воқеӣ аз боло дида шуда), ҳар факет аз рӯи самти нурафканӣ
    (top-left) ранги худро мегирад (аз равшан то торик) — намуди
    3D-и воқеитар аз 2 факети сода.
    """
    import math
    draw = ImageDraw.Draw(img)

    # Соя дар зери гем (умқ медиҳад)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse((cx - 0.75 * s, cy + s - 0.15 * s, cx + 0.75 * s, cy + s + 0.4 * s),
               fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(s * 0.14))
    img.alpha_composite(shadow)
    draw = ImageDraw.Draw(img)

    outline = [
        (cx - 0.30 * s, cy - s),        # table чап
        (cx + 0.30 * s, cy - s),        # table рост
        (cx + 0.68 * s, cy - 0.58 * s), # китфи рост
        (cx + 0.32 * s, cy + 0.04 * s), # камари рост
        (cx, cy + s),                   # нӯги поён
        (cx - 0.32 * s, cy + 0.04 * s), # камари чап
        (cx - 0.68 * s, cy - 0.58 * s), # китфи чап
    ]
    center = (cx, cy - 0.60 * s)
    light_angle = math.radians(-130)  # сарчашмаи нур — болоичап
    n = len(outline)

    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        mx, my = (a[0] + b[0]) / 2 - cx, (a[1] + b[1]) / 2 - cy
        ang = math.atan2(my, mx)
        t = (math.cos(ang - light_angle) + 1) / 2  # 0 (соя) .. 1 (рӯшноӣ)
        col = tuple(int(ACCENT_DARK[k] + (ACCENT_LIGHT[k] - ACCENT_DARK[k]) * t) for k in range(3))
        draw.polygon([center, a, b], fill=col)

    # Хатҳои факет (буришҳои борик)
    edge_w = max(1, int(s * 0.028))
    for i in range(n):
        draw.line([outline[i], outline[(i + 1) % n]], fill=(255, 255, 255, 110), width=edge_w)
        draw.line([center, outline[i]], fill=(255, 255, 255, 70), width=max(1, edge_w // 2))
    draw.polygon(outline, outline=WHITE, width=edge_w)

    # Милтии рӯшноӣ (sparkle)
    _sparkle(img, cx - 0.42 * s, cy - 0.68 * s, s * 0.18, WHITE)
    _sparkle(img, cx + 0.50 * s, cy - 0.08 * s, s * 0.10, WHITE)


def _sparkle(img, cx, cy, r, color):
    """Милтии 4-шоха (barqi нур)."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    pts = [
        (cx, cy - r), (cx + r * 0.22, cy - r * 0.22),
        (cx + r, cy), (cx + r * 0.22, cy + r * 0.22),
        (cx, cy + r), (cx - r * 0.22, cy + r * 0.22),
        (cx - r, cy), (cx - r * 0.22, cy - r * 0.22),
    ]
    ld.polygon(pts, fill=(*color, 230))
    layer = layer.filter(ImageFilter.GaussianBlur(r * 0.08))
    img.alpha_composite(layer)


def _icon_diamond(draw, cx, cy, s, color):
    _flat_diamond(draw, cx, cy, s, fill=color)


def _icon_crosshair(draw, cx, cy, s, color, width):
    draw.ellipse((cx - s, cy - s, cx + s, cy + s), outline=color, width=width)
    draw.line((cx - s * 1.35, cy, cx - s * 0.45, cy), fill=color, width=width)
    draw.line((cx + s * 0.45, cy, cx + s * 1.35, cy), fill=color, width=width)
    draw.line((cx, cy - s * 1.35, cx, cy - s * 0.45), fill=color, width=width)
    draw.line((cx, cy + s * 0.45, cx, cy + s * 1.35), fill=color, width=width)
    draw.ellipse((cx - s * 0.18, cy - s * 0.18, cx + s * 0.18, cy + s * 0.18), fill=color)


def _icon_star(draw, cx, cy, s, color):
    import math
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = s if i % 2 == 0 else s * 0.42
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=color)


def _icon_check(draw, cx, cy, s, color, width):
    draw.line([(cx - s, cy + s * 0.1), (cx - s * 0.25, cy + s * 0.85), (cx + s, cy - s * 0.7)],
               fill=color, width=width, joint="curve")


def _icon_bolt(draw, cx, cy, s, color):
    pts = [
        (cx + s * 0.15, cy - s), (cx - s * 0.55, cy + s * 0.15), (cx - s * 0.05, cy + s * 0.15),
        (cx - s * 0.15, cy + s), (cx + s * 0.55, cy - s * 0.15), (cx + s * 0.05, cy - s * 0.15),
    ]
    draw.polygon(pts, fill=color)


def generate_welcome_banner(
    title: str = "DILOVAR FF BOT",
    subtitle: str = "Донати худкор дар якчанд дақиқа",
    trust_line: str = "Зиёда аз 10,000 муштарӣ",
    badge_text: str = "24/7 АВТОМАТӢ",
) -> BytesIO:
    S = 2
    Ws, Hs = W * S, H * S

    img = _vertical_gradient(Ws, Hs, BG_TOP, BG_BOTTOM).convert("RGBA")

    _glow_dot(img, Ws * 0.14, Hs * 0.20, int(180 * S), ACCENT, opacity=90)
    _glow_dot(img, Ws * 0.90, Hs * 0.78, int(220 * S), FIRE, opacity=70)
    _glow_dot(img, Ws * 0.80, Hs * 0.12, int(140 * S), ACCENT, opacity=60)

    draw = ImageDraw.Draw(img)

    # Алмосҳои ороишӣ дар паснамо (хира)
    deco = [
        (Ws * 0.08, Hs * 0.80, 22 * S, 45),
        (Ws * 0.90, Hs * 0.28, 18 * S, 40),
        (Ws * 0.05, Hs * 0.42, 12 * S, 35),
        (Ws * 0.96, Hs * 0.58, 14 * S, 35),
    ]
    for dx, dy, dsize, op in deco:
        layer = Image.new("RGBA", (int(dsize * 4), int(dsize * 4)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        _flat_diamond(ld, dsize * 2, dsize * 2, dsize, fill=(*ACCENT, op))
        img.alpha_composite(layer, (int(dx - dsize * 2), int(dy - dsize * 2)))

    # ---- Гем-логои марказӣ (реалистӣ) ----
    cx, cy = Ws * 0.5, Hs * 0.255
    big = int(64 * S)
    _glow_dot(img, cx, cy, int(big * 1.7), ACCENT, opacity=120)
    _gem_realistic(img, cx, cy, big)
    draw = ImageDraw.Draw(img)

    # Унвон
    f_title = _font("DejaVuSans-Bold.ttf", 66 * S)
    tw = draw.textlength(title, font=f_title)
    ty = cy + big + 40 * S
    draw.text((cx - tw / 2, ty), title, font=f_title, fill=WHITE)

    # Зерунвон
    f_sub = _font("DejaVuSans.ttf", 30 * S)
    sy = ty + 84 * S
    sw = draw.textlength(subtitle, font=f_sub)
    draw.text((cx - sw / 2, sy), subtitle, font=f_sub, fill=ACCENT)

    # Хати боварӣ (галка + омори воқеӣ)
    f_trust = _font("DejaVuSans-Bold.ttf", 24 * S)
    trust_w = draw.textlength(trust_line, font=f_trust)
    check_r = 13 * S
    total_w = trust_w + check_r * 2 + 14 * S
    trust_x0 = cx - total_w / 2
    trust_y = sy + 56 * S
    _icon_check(draw, trust_x0 + check_r, trust_y + f_trust.size * 0.55, check_r,
                (110, 231, 160), max(2, int(4 * S)))
    draw.text((trust_x0 + check_r * 2 + 14 * S, trust_y), trust_line, font=f_trust, fill=(110, 231, 160))

    # Хати ҷудокунанда
    line_y = trust_y + 62 * S
    draw.line((Ws * 0.30, line_y, Ws * 0.70, line_y), fill=(*ACCENT, 90), width=2 * S)

    # ---- Сатри хизматҳо (иконаи вектор + матн, БЕ эмоҷӣ) ----
    f_srv = _font("DejaVuSans-Bold.ttf", 26 * S)
    services = ["Free Fire", "PUBG Mobile", "Telegram"]
    icon_r = 17 * S
    gap_icon_text = 12 * S
    gap_between = 60 * S

    widths = [draw.textlength(t, font=f_srv) for t in services]
    seg_widths = [icon_r * 2 + gap_icon_text + w for w in widths]
    total = sum(seg_widths) + gap_between * (len(services) - 1)
    x = cx - total / 2
    srv_y = line_y + 38 * S
    icon_cy = srv_y + f_srv.size * 0.42

    # Free Fire — иконаи алмос
    _icon_diamond(draw, x + icon_r, icon_cy, icon_r * 0.95, ACCENT)
    draw.text((x + icon_r * 2 + gap_icon_text, srv_y), services[0], font=f_srv, fill=WHITE)
    x += seg_widths[0] + gap_between

    # PUBG Mobile — иконаи нишонгирӣ
    _icon_crosshair(draw, x + icon_r, icon_cy, icon_r * 0.85, FIRE, max(2, int(3.2 * S)))
    draw.text((x + icon_r * 2 + gap_icon_text, srv_y), services[1], font=f_srv, fill=WHITE)
    x += seg_widths[1] + gap_between

    # Telegram — иконаи ситора
    _icon_star(draw, x + icon_r, icon_cy, icon_r, GOLD)
    draw.text((x + icon_r * 2 + gap_icon_text, srv_y), services[2], font=f_srv, fill=WHITE)

    # ---- Бейҷи "24/7" дар кунҷи болои рост (иконаи барқ, бе эмоҷӣ) ----
    f_badge = _font("DejaVuSans-Bold.ttf", 21 * S)
    bw = draw.textlength(badge_text, font=f_badge)
    bolt_r = 11 * S
    bpad = 16 * S
    bh = 46 * S
    seg = bolt_r * 2 + 10 * S + bw
    bx0 = Ws - seg - bpad * 2 - 34 * S
    by0 = 32 * S
    draw.rounded_rectangle(
        (bx0, by0, bx0 + seg + bpad * 2, by0 + bh),
        radius=bh / 2, fill=ACCENT_SOFT, outline=ACCENT, width=2 * S
    )
    _icon_bolt(draw, bx0 + bpad + bolt_r, by0 + bh / 2, bolt_r, GOLD)
    draw.text((bx0 + bpad + bolt_r * 2 + 10 * S, by0 + (bh - f_badge.size) / 2 - 2 * S),
               badge_text, font=f_badge, fill=ACCENT)

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
