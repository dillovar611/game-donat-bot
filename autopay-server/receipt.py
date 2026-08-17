"""
receipt.py — Тасвири чек (расм)-и худкор барои фармоишҳои МУВАФФАҚ.

Ин ФАҚАТ бо Pillow кор мекунад (API-и FazerCards ҳеҷ расме намедиҳад — расм
пурра дар ин ҷо, дар худи бот, сохта мешавад).

Шрифт: аз папкаи fonts/ (ҳамроҳи ин бот омада), то новобаста аз он ки дар
сервер шрифти кириллӣ насб аст ё не, кор кунад.
"""
import os
import logging
from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Роҳҳои эҳтимолии шрифт дар сервер (агар fonts_data.py набошад)
_FALLBACK_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/liberation",
]

# Кэши байтҳои шрифт (base64 → bytes), то ҳар бор аз нав декод нашавад
_FONT_BYTES_CACHE = {}


def _get_font_bytes(filename: str):
    """
    Байтҳои шрифтро бармегардонад. Аввал аз fonts_data.py (base64-и
    дарунсохт) — ягон файл ё папкаи fonts/ лозим нест. Агар fonts_data.py
    набошад, ба папкаи fonts/ ё шрифти системавӣ бармегардад.
    Натиҷа: bytes (агар аз base64) ё str-и роҳи файл (агар аз диск).
    """
    if filename in _FONT_BYTES_CACHE:
        return _FONT_BYTES_CACHE[filename]

    # 1) Кӯшиши аввал — base64-и дарунсохт (fonts_data.py)
    try:
        import base64
        import fonts_data
        b64 = None
        if "Bold" in filename:
            b64 = getattr(fonts_data, "DEJAVU_SANS_BOLD_B64", "") or None
        else:
            b64 = getattr(fonts_data, "DEJAVU_SANS_REGULAR_B64", "") or None
        if b64:
            data = base64.b64decode(b64)
            if data and len(data) > 1000:  # шрифти воқеӣ, на холӣ
                _FONT_BYTES_CACHE[filename] = data
                return data
    except Exception as e:
        logger.debug(f"fonts_data.py истифода нашуд: {e}")

    # 2) Ба папкаи fonts/ ё шрифти системавӣ бармегардем
    candidates = [os.path.join(_FONT_DIR, filename)]
    for d in _FALLBACK_DIRS:
        candidates.append(os.path.join(d, filename))
    for path in candidates:
        if os.path.isfile(path):
            _FONT_BYTES_CACHE[filename] = path  # роҳи файлро кэш мекунем
            return path

    raise FileNotFoundError(
        f"Шрифти '{filename}' ёфт нашуд. Файли fonts_data.py-ро дар паҳлӯи "
        f"receipt.py гузоред, ё папкаи 'fonts/' бо файлҳои .ttf, ё дар сервер: "
        f"apt install fonts-dejavu-core"
    )

W = 900
# ==================== ПАЛИТРАИ ПЕШФАРЗ (КАБУДИ НЕОН) ====================
BG = (7, 11, 20)
CARD = (15, 23, 38)
CARD_BORDER = (32, 60, 88)
WHITE = (241, 245, 249)
GRAY = (139, 150, 168)
LINE = (32, 60, 88)

# Ранги асосӣ — кабуди неон
GREEN = (56, 189, 248)        # (номаш GREEN монд, вале қимат кабуд аст — то дигар ҷойҳо вайрон нашаванд)
GREEN_SOFT = (9, 38, 58)

ROW_HEIGHT = 54
MARGIN = 46


_FONT_CACHE = {}


def _font(name, size):
    """
    Шрифтро бор мекунад ва дар кэш нигоҳ медорад (танҳо як бор). Шрифт
    метавонад аз хотира (base64-и дарунсохт) ё аз диск (папкаи fonts/ ё
    системавӣ) бор шавад. Агар "Bold" ёфт нашавад, ба Regular бармегардад.
    """
    key = (name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        src = _get_font_bytes(name)
    except FileNotFoundError:
        if "Bold" in name:
            logger.warning(
                f"Шрифти '{name}' ёфт нашуд — ба ҷои он 'DejaVuSans.ttf' "
                f"(Regular) истифода мешавад."
            )
            src = _get_font_bytes("DejaVuSans.ttf")
        else:
            raise
    # src метавонад bytes (аз base64) ё str-и роҳи файл бошад
    if isinstance(src, (bytes, bytearray)):
        font = ImageFont.truetype(BytesIO(src), size)
    else:
        font = ImageFont.truetype(src, size)
    _FONT_CACHE[key] = font
    return font


# ЭЗОҲ: шрифтҳо ДИГАР дар вақти import бор карда намешаванд (ин пештар
# метавонист тамоми ботро ҳангоми старт crash кунад, агар шрифт дар
# сервер набошад). Ба ҷои он, ҳар маротиба ки чек сохта мешавад,
# _font() даъват мешавад — натиҷа кэш мешавад, пас танҳо бори аввал аз
# диск хонда мешавад.


import re
import unicodedata

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\uFE0F"
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def clean_name(text, fallback="Бозингар") -> str:
    """
    Номи бозингарро барои расми чек тоза мекунад:
      1) ҳарфҳои услубии Unicode (small-caps, bold, script)-ро ба оддӣ табдил
      2) NFKC — аломатҳои декоративӣ/васеъро ба оддӣ (𝓒𝓸𝓸𝓵 → Cool, ＰＲＯ → PRO)
      3) эмоҷиро хориҷ мекунад
      4) танҳо лотинӣ, кириллӣ, рақам, фосила ва пунктуатсияи оддӣ нигоҳ медорад
      5) фосилаҳои изофиро тоза мекунад
    Агар пас аз тозакунӣ чизе намонад, fallback бармегардонад.
    """
    if not text:
        return fallback
    s = str(text)

    # 1) Ҳарфҳои услубии Unicode-ро ба лотинии оддӣ табдил медиҳем.
    #    (NFKC инҳоро табдил намедиҳад, барои ҳамин дастӣ мекунем)
    styled = {
        # small caps (ᴀ-ᴢ)
        "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f",
        "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l",
        "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r",
        "ѕ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "x": "x",
        "ʏ": "y", "ᴢ": "z",
    }
    s = "".join(styled.get(ch, ch) for ch in s)

    # 2) NFKC — bold/italic/fullwidth/script-ро ба оддӣ мекунад
    s = unicodedata.normalize("NFKC", s)
    s = _strip_emoji(s)

    def is_safe(ch):
        if ch == " ":
            return True
        code = ord(ch)
        if 0x0020 <= code <= 0x007E:      # лотинии асосӣ
            return True
        if 0x00A0 <= code <= 0x024F:      # латинии васеъшуда (é, ñ ...)
            return True
        if 0x0400 <= code <= 0x04FF:      # кириллӣ
            return True
        return False

    out = "".join(ch for ch in s if is_safe(ch))
    cleaned = " ".join(out.split()).strip()
    return cleaned or fallback


def _rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _apply_watermark(img, card_box, radius, text, font, opacity=16, angle=-28):
    """
    Матни such 'text'-ро такроран (тахтии) дар тамоми масоҳати корт мекашад,
    бо шаффофияти хеле паст (фони обшуста), фақат дар дохили гӯшаҳои
    мудаввари корт (берун аз он чизе нест).
    """
    w, h = img.size
    x0, y0, x1, y1 = card_box

    # Як "тахта"-и матни чаппашуда месозем
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = tmp_draw.textlength(text, font=font)
    th = font.size
    tile = Image.new("RGBA", (int(tw) + 24, int(th) + 24), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((12, 12), text, font=font, fill=(255, 255, 255, opacity))
    tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
    tw2, th2 = tile.size
    step_x, step_y = tw2 + 46, th2 + 46

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    row = 0
    for yy in range(-th2, h + th2, step_y):
        shift = (step_x // 2) if row % 2 else 0
        for xx in range(-tw2 + shift, w + tw2, step_x):
            layer.alpha_composite(tile, (xx, yy))
        row += 1

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=255)
    clipped = Image.composite(layer, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask)
    img.alpha_composite(clipped)


def _draw_check_icon(img, cx, cy, r, color):
    """Иконаи ✓ бо тобиши мулоим дар атроф."""
    glow = Image.new("RGBA", (r * 6, r * 6), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((r, r, r * 5, r * 5), fill=(*color, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.35))
    img.alpha_composite(glow, (int(cx - r * 3), int(cy - r * 3)))
    draw = ImageDraw.Draw(img)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    draw.line(
        [(cx - r * 0.45, cy + r * 0.05), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.35)],
        fill=WHITE, width=int(r * 0.16), joint="curve"
    )
    return draw


def generate_success_receipt(order_id, product_label, game_id, payment_label,
                              nickname=None, price=None, dt=None,
                              bot_username="@Dilovarffbot",
                              bg=BG, card_bg=CARD, card_border=CARD_BORDER,
                              accent=GREEN, accent_soft=GREEN_SOFT) -> BytesIO:
    """
    Расми чеки муваффақ месозад (бо 3x supersampling барои сифати баланд —
    расм 3 карат калонтар сохта мешавад, баъд бо LANCZOS ба андозаи ниҳоӣ
    кӯтоҳ карда мешавад, то канораҳо ва матн ҳамвор бошанд, на "зина-зина").

    accent / accent_soft — ранги асосӣ (барои варианти дигари ранг —
    масалан тиллоӣ ё кабуд — ин ду параметрро иваз кунед).
    """
    dt = dt or datetime.now()
    S = 3  # сколаи supersampling (3x сохта, баъд кӯтоҳ карда мешавад)

    rows = [
        ("Фармоиш №", f"#{order_id}"),
        ("ID аккаунт", game_id),
    ]
    if nickname:
        rows.append(("Ном", clean_name(nickname)))
    rows.append(("Усули пардохт", payment_label))
    rows.append(("Сана", dt.strftime("%d.%m.%Y  •  %H:%M")))
    if price is not None:
        rows.append(("Маблағ", f"{price:.2f} сомонӣ"))

    # Баландии расмро (мантиқӣ, бе supersampling) вобаста ба шумораи
    # сатрҳо ҳисоб мекунем, то на кӯтоҳ монад ва на фазои холии зиёдатӣ.
    H = 752 + ROW_HEIGHT * len(rows)
    Ws, Hs = W * S, H * S
    MARGINs = MARGIN * S
    ROW_HEIGHTs = ROW_HEIGHT * S

    img = Image.new("RGBA", (Ws, Hs), (*bg, 255))
    draw = ImageDraw.Draw(img)

    card_box = (MARGINs, MARGINs, Ws - MARGINs, Hs - MARGINs)
    _rounded_rect(draw, card_box, 32 * S, fill=card_bg, outline=card_border, width=2 * S)

    f_wm = _font("DejaVuSans-Bold.ttf", 40 * S)
    _apply_watermark(img, card_box, 32 * S, bot_username, f_wm, opacity=38)
    draw = ImageDraw.Draw(img)  # аз нав, зеро watermark болои img кашида шуд

    cx = Ws // 2
    r = 68 * S
    icon_cy = MARGINs + 58 * S + r
    draw = _draw_check_icon(img, cx, icon_cy, r, accent)

    f_title = _font("DejaVuSans-Bold.ttf", 34 * S)
    f_label = _font("DejaVuSans.ttf", 25 * S)
    f_value = _font("DejaVuSans-Bold.ttf", 25 * S)
    f_value_small = _font("DejaVuSans-Bold.ttf", 20 * S)
    f_small = _font("DejaVuSans.ttf", 21 * S)
    f_amount = _font("DejaVuSans-Bold.ttf", 56 * S)
    f_badge = _font("DejaVuSans-Bold.ttf", 22 * S)

    y = icon_cy + r + 35 * S
    title = "Донат бо муваффақият анҷом ёфт!"
    tw = draw.textlength(title, font=f_title)
    draw.text((cx - tw / 2, y), title, font=f_title, fill=WHITE)

    y += 62 * S
    product_text = _strip_emoji(str(product_label))
    pf = f_amount
    pw = draw.textlength(product_text, font=pf)
    max_product_w = Ws - 2 * MARGINs - 40 * S
    if pw > max_product_w:
        pf = f_title
        pw = draw.textlength(product_text, font=pf)
    draw.text((cx - pw / 2, y), product_text, font=pf, fill=accent)

    y += 96 * S
    draw.line((MARGINs + 44 * S, y, Ws - MARGINs - 44 * S, y), fill=LINE, width=2 * S)
    y += 36 * S

    for label, value in rows:
        draw.text((MARGINs + 44 * S, y), label, font=f_label, fill=GRAY)
        # Эмоҷиро аз қимат дур мекунем (масалан 🏙 дар "🏙 Душанбе Сити"),
        # то ба ҷои он чоркунҷаи холӣ (tofu) нашавад. Аломати • (bullet) дар
        # сана дур намешавад, зеро он эмоҷӣ нест.
        value = _strip_emoji(str(value))
        vf = f_value
        vw = draw.textlength(value, font=vf)
        max_w = Ws - 2 * MARGINs - 220 * S
        if vw > max_w:
            vf = f_value_small
            vw = draw.textlength(value, font=vf)
        draw.text((Ws - MARGINs - 44 * S - vw, y), value, font=vf, fill=WHITE)
        y += ROW_HEIGHTs

    y += 8 * S
    badge_text = "МУВАФФАҚ"
    bw = draw.textlength(badge_text, font=f_badge)
    badge_pad_x = 22 * S
    badge_h = 44 * S
    badge_box = (cx - bw / 2 - badge_pad_x, y, cx + bw / 2 + badge_pad_x, y + badge_h)
    _rounded_rect(draw, badge_box, badge_h / 2, fill=accent_soft, outline=accent, width=2 * S)
    draw.text((cx - bw / 2, y + (badge_h - 26 * S) / 2), badge_text, font=f_badge, fill=accent)
    y += badge_h + 32 * S

    draw.line((MARGINs + 44 * S, y, Ws - MARGINs - 44 * S, y), fill=LINE, width=2 * S)
    y += 32 * S

    f_brand_bottom = _font("DejaVuSans-Bold.ttf", 24 * S)
    bw2 = draw.textlength(bot_username, font=f_brand_bottom)
    draw.text((cx - bw2 / 2, y), bot_username, font=f_brand_bottom, fill=accent)
    y += 34 * S

    footer = "Ташаккур барои интихоби шумо!"
    fw2 = draw.textlength(footer, font=f_small)
    draw.text((cx - fw2 / 2, y), footer, font=f_small, fill=GRAY)

    # Аз андозаи 3x-supersampled ба андозаи ниҳоӣ бо LANCZOS кӯтоҳ мекунем
    # — ин канораҳои мудаввар, иконаи ✓ ва матнро хеле ҳамвортар мекунад.
    final_img = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = BytesIO()
    buf.name = "receipt.png"
    final_img.save(buf, format="PNG")
    buf.seek(0)
    return buf
