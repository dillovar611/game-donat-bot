# -*- coding: utf-8 -*-
"""
spinwheel.py — GIF-и «чархи тӯҳфа» барои эълони баранда дар канал.

Чарх бо номи ВОҚЕИИ харидорон кашида мешавад ва маҳз дар сектори
баранда меистад. Баранда ПЕШТАР дар код интихоб шудааст (giveaway_loop)
— чарх танҳо ҳамон натиҷаро нишон медиҳад.

Паснамо (assets/spin_bg.png) як расми тайёр аст; ҳама чизи дигар бо
Pillow кашида мешавад, то номҳо ҳамеша дуруст бошанд.

Агар ягон чиз намерасад (паснамо, шрифт) — None бармегардад ва
даъваткунанда бояд ба паёми оддии матнӣ баргардад.
"""
import io
import logging
import math
import os
import re

logger = logging.getLogger(__name__)

try:
    from PIL import (Image, ImageDraw, ImageFont, ImageFilter, ImageChops,
                     ImageEnhance)
    _PIL_OK = True
except Exception as e:                                    # pragma: no cover
    logger.warning(f"spinwheel: Pillow нест — чарх кор намекунад: {e}")
    _PIL_OK = False

_DIR = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(_DIR, "assets", "spin_bg.png")

_EMOJI_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/NotoColorEmoji.ttf",
]

MAG = (255, 45, 235)
CYA = (0, 235, 255)
PUR = (150, 80, 255)
GOLD = (255, 205, 70)
WHITE = (255, 255, 255)
GREEN = (80, 255, 175)

# Рангҳои секторҳо — мисли conic-gradient
TONES = [(34, 56, 168), (74, 0, 148), (0, 104, 168), (150, 0, 76), (36, 72, 176),
         (108, 0, 172), (0, 106, 138), (76, 36, 150), (44, 64, 160), (96, 0, 128)]

CX, CY = 448, 516      # маркази чарх дар паснамо
R = 250                # радиуси чарх
SS = 3                 # supersampling ҳангоми сохтани қисмҳо
PT, PB = 812, 978      # панели натиҷа
BT, BB = 1006, 1092    # тугмаи поёнӣ
SPINS = 4              # чанд гардиши пурра

# Бари ниҳоии видео. Паснамо 896px аст — ҳама чиз ба ин миқёс калон
# карда мешавад, то матн ва чарх тезтар бароянд ва Telegram ҳангоми
# фишурдан камтар вайрон кунад.
TARGET_W = 1080
MP4_FPS = 25


# ==================== ШРИФТҲО ====================
_font_cache = {}


_FS = 1.0          # зарбкунандаи андозаи шрифт (аз миқёси расм)


def _font(size: int):
    size = max(6, int(round(size * _FS)))
    if size in _font_cache:
        return _font_cache[size]
    try:
        import receipt
        src = receipt._get_font_bytes("DejaVuSans-Bold.ttf")
        fnt = ImageFont.truetype(io.BytesIO(src) if isinstance(src, bytes) else src, size)
    except Exception:
        fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    _font_cache[size] = fnt
    return fnt


_emoji_font = None
_emoji_checked = False


def _emoji_base():
    """Шрифти эмоҷӣ — агар набошад, эмоҷиҳо танҳо партофта мешаванд."""
    global _emoji_font, _emoji_checked
    if _emoji_checked:
        return _emoji_font
    _emoji_checked = True
    for p in _EMOJI_CANDIDATES:
        if os.path.isfile(p):
            try:
                _emoji_font = ImageFont.truetype(p, 109)
                break
            except Exception as e:
                logger.warning(f"spinwheel: шрифти эмоҷӣ бор нашуд ({p}): {e}")
    if _emoji_font is None:
        logger.info("spinwheel: шрифти эмоҷӣ нест — эмоҷиҳо кашида намешаванд")
    return _emoji_font


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]+")
_emoji_cache = {}


def _split(t: str):
    out, last = [], 0
    for m in _EMOJI_RE.finditer(t):
        if m.start() > last:
            out.append((t[last:m.start()], False))
        out.append((m.group(), True))
        last = m.end()
    if last < len(t):
        out.append((t[last:], False))
    return out


def _emoji_img(ch: str, size: int):
    key = (ch, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    base = _emoji_base()
    if base is None:
        return None
    try:
        tmp = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((4, 4), ch, font=base, embedded_color=True)
        bb = tmp.getbbox()
        if not bb:
            return None
        tmp = tmp.crop(bb)
        h = max(1, int(size * 1.05))
        tmp = tmp.resize((max(1, int(tmp.width * h / tmp.height)), h), Image.LANCZOS)
    except Exception:
        return None
    _emoji_cache[key] = tmp
    return tmp


def _width(d, t, fnt):
    w = 0
    for s, is_e in _split(t):
        if is_e:
            for c in s:
                i = _emoji_img(c, fnt.size)
                if i:
                    w += i.width + int(fnt.size * .2)
        else:
            w += d.textbbox((0, 0), s, font=fnt)[2]
    return int(w)


def _put(im, d, x, y, t, fnt, col):
    for s, is_e in _split(t):
        if is_e:
            for c in s:
                i = _emoji_img(c, fnt.size)
                if i:
                    im.paste(i, (int(x), int(y + fnt.size * .1)), i)
                    x += i.width + int(fnt.size * .2)
        else:
            d.text((x, y), s, font=fnt, fill=col)
            x += d.textbbox((0, 0), s, font=fnt)[2]


def _cput(im, d, y, t, fnt, col, w):
    _put(im, d, (w - _width(d, t, fnt)) // 2, y, t, fnt, col)


# ==================== ТОЗА КАРДАНИ НОМ ====================
# Ҳарфҳое, ки шрифти DejaVu кашида метавонад
_OK_RANGES = (
    (0x20, 0x7E),      # ASCII
    (0xA0, 0x24F),     # Latin-1 + Latin Extended A/B
    (0x400, 0x52F),    # Кириллица (бо ҳарфҳои тоҷикӣ: ӯ ҳ ҷ қ ғ ӣ)
    (0x2010, 0x2027),  # тире, нохунакҳо
)


def _renderable(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in _OK_RANGES)


def clean_name(name: str, fallback: str = "Мизоҷ") -> str:
    """
    Номро барои кашидан тайёр мекунад.

    Бисёр корбарони Telegram дар ном ҳарфҳои «зебои» Unicode
    (𝗌𝗎𝖽𝖺𝗒𝗌, 𝓐𝓵𝓲) ё эмоҷӣ мегузоранд. Шрифти DejaVu онҳоро надорад ва
    ба ҷои ҳарф чоркунҷаи холӣ (▯▯▯) мекашид. NFKC онҳоро ба ҳарфҳои
    оддӣ табдил медиҳад (𝗌𝗎𝖽𝖺𝗒𝗌 → sudays), бақияаш партофта мешавад.
    """
    import unicodedata
    s = unicodedata.normalize("NFKC", (name or "").strip())
    s = "".join(c for c in s if _renderable(c))
    s = " ".join(s.split())
    if len(s) > 18:
        s = s[:17].rstrip() + "…"
    return s or fallback


def _draw_gift(size: int):
    """Қуттии тӯҳфа — ивазкунандаи 🎁 барои серверҳое, ки шрифти
    эмоҷии рангаро надоранд."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    box_top = int(S * 0.34)
    body = (int(S * .09), box_top, int(S * .91), int(S * .95))
    lid = (int(S * .03), int(S * .20), int(S * .97), int(S * .40))
    gold_d = (196, 138, 20)
    d.rounded_rectangle(body, radius=int(S * .05), fill=gold_d)
    d.rounded_rectangle(lid, radius=int(S * .05), fill=GOLD)
    # лентаи амудӣ
    d.rectangle((int(S * .42), box_top, int(S * .58), int(S * .95)), fill=MAG)
    d.rectangle((int(S * .42), int(S * .20), int(S * .58), int(S * .40)), fill=MAG)
    # камон
    d.ellipse((int(S * .16), int(S * .02), int(S * .50), int(S * .26)),
              outline=MAG, width=int(S * .07))
    d.ellipse((int(S * .50), int(S * .02), int(S * .84), int(S * .26)),
              outline=MAG, width=int(S * .07))
    return im.resize((size, size), Image.LANCZOS)


# ==================== НЕОН ====================
def _add(base, layer_rgb, k=1.0):
    if k != 1.0:
        layer_rgb = layer_rgb.point(lambda v: min(255, int(v * k)))
    return ImageChops.add(base, layer_rgb)


def _flatten(layer_rgba, size, k=1.0):
    g = Image.new("RGB", size, (0, 0, 0))
    g.paste(layer_rgba.convert("RGB"), (0, 0), layer_rgba)
    if k != 1.0:
        g = g.point(lambda v: min(255, int(v * k)))
    return g


def _glow_rgb(layer_rgba, size, passes):
    """Дурахши бисёрқабатаро ЯК БОР ҳисоб мекунад (кадрҳо онро такрор
    намекунанд — вагарна ҳар кадр чанд Gaussian blur мешуд ва рендер
    даҳҳо сония тӯл мекашид)."""
    out = Image.new("RGB", size, (0, 0, 0))
    for r, k in passes:
        out = ImageChops.add(out, _flatten(layer_rgba.filter(ImageFilter.GaussianBlur(r)), size, k))
    return out


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _chamfer(d, box, cut, **kw):
    x0, y0, x1, y1 = box
    d.polygon([(x0 + cut, y0), (x1 - cut, y0), (x1, y0 + cut), (x1, y1 - cut),
               (x1 - cut, y1), (x0 + cut, y1), (x0, y1 - cut), (x0, y0 + cut)], **kw)


# ==================== ҚИСМҲОИ ЧАРХ ====================
def _wheel_parts(names, winner_idx, win, view):
    n = len(names)
    seg = 360.0 / n
    S = (R * 2 + 20) * SS
    c = S // 2
    r = R * SS
    box = (c - r, c - r, c + r, c + r)

    fill = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    for i in range(n):
        col = (214, 158, 24) if (win and i == winner_idx) else TONES[i % len(TONES)]
        fd.pieslice(box, i * seg, (i + 1) * seg, fill=col + (255,))

    ln = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(ln)
    inner = int(r * .27)
    lw = max(int(1.6 * SS), int(3.4 * SS * 10 / max(10, n)))
    for i in range(n):
        a = math.radians(i * seg)
        ld.line([(c + math.cos(a) * inner, c + math.sin(a) * inner),
                 (c + math.cos(a) * r, c + math.sin(a) * r)],
                fill=(190, 140, 255, 255), width=lw)
    if win:
        for a in (math.radians(winner_idx * seg), math.radians(winner_idx * seg + seg)):
            ld.line([(c + math.cos(a) * inner, c + math.sin(a) * inner),
                     (c + math.cos(a) * r, c + math.sin(a) * r)],
                    fill=GOLD + (255,), width=int(3.4 * SS))
        ld.arc(box, winner_idx * seg, winner_idx * seg + seg, fill=GOLD + (255,), width=int(4.5 * SS))

    # Ҳамаи номҳо аз ЯК хатти доиравии назди ҳалқа сар шуда, ба тарафи
    # марказ мераванд. Бо ин чарх пур ба назар мерасад (дар канор ҷои
    # холӣ намемонад), вале ҳеҷ ном аз ҳалқа намебарояд — дарозиаш ҳар
    # қадар бошад, танҳо ба дарун дарозтар мешавад.
    base_fs = int(min(34, max(16, 380 / n)) * SS)
    r_out = r * .88          # нӯги берунии ҳар ном
    r_in = r * .30           # аз мағзи чарх наздиктар нашавад
    max_w = r_out - r_in
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for i, nm in enumerate(names):
        mid = i * seg + seg / 2
        fs = base_fs
        while fs > int(11 * SS) and probe.textbbox((0, 0), nm, font=_font(fs))[2] > max_w:
            fs -= SS
        fnt = _font(fs)
        # Ҳарфи хурдтарин ҳам нарасид — номро мебурем. Бе ин, номи дароз
        # аз чарх мебарояд ва ба ҳалқа ва ҳамсояаш медарояд.
        if probe.textbbox((0, 0), nm, font=fnt)[2] > max_w:
            while len(nm) > 3 and probe.textbbox((0, 0), nm + "…", font=fnt)[2] > max_w:
                nm = nm[:-1]
            nm += "…"
        col = (255, 255, 255) if (win and i == winner_idx) else WHITE
        tl = Image.new("RGBA", (int(300 * SS), int(64 * SS)), (0, 0, 0, 0))
        td = ImageDraw.Draw(tl)
        tw = td.textbbox((0, 0), nm, font=fnt)[2]
        # сояи тира — ном дар заминаи равшан низ хоно бошад
        td.text(((tl.width - tw) // 2, int(10 * SS)), nm, font=fnt,
                fill=col + (255,), stroke_width=max(2, int(SS * 1.6)),
                stroke_fill=(3, 0, 10, 255))
        # Матн вақте хоно аст, ки кунҷи дидашавандааш байни -90 ва +90 бошад.
        # Дар 270° (маҳз боло — ҷои баранда) чаппа мекунем, то мисли
        # чархи воқеӣ аз поён ба боло хонда шавад.
        flip = 90 <= ((mid - view) % 360) <= 270
        rot = tl.rotate(-(mid + 180) if flip else -mid, expand=True, resample=Image.BICUBIC)
        # Нӯги берунӣ дар r_out мемонад, пас маркази матн ба дарозии
        # ҳамон ном вобаста аст — номи дароз ба дарун дарозтар меравад
        rad = r_out - tw / 2.0
        ln.alpha_composite(rot, (int(c + math.cos(math.radians(mid)) * rad - rot.width / 2),
                                 int(c + math.sin(math.radians(mid)) * rad - rot.height / 2)))
    sz = R * 2 + 20
    return fill.resize((sz, sz), Image.LANCZOS), ln.resize((sz, sz), Image.LANCZOS)


def _ring(n, flash, w):
    seg = 360.0 / n
    S = w * SS
    c = S // 2
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for ro, wd, al in ((int(R * 1.05 * SS), int(12 * SS), 255),
                       (int(R * 1.15 * SS), int(4 * SS), 215),
                       (int(R * 1.21 * SS), int(2 * SS), 150)):
        for a in range(0, 360, 3):
            t = abs(((a + 90) % 360) - 180) / 180.0
            d.arc((c - ro, c - ro, c + ro, c + ro), a, a + 4,
                  fill=_lerp(CYA, MAG, t) + (al,), width=wd)
    if n <= 16:
        for i in range(n):
            rr = int(R * 1.05 * SS)
            ar = math.radians(i * seg - 90)
            x = c + math.cos(ar) * rr
            y = c + math.sin(ar) * rr
            s = int(6 * SS)
            d.ellipse((x - s, y - s, x + s, y + s), fill=WHITE + (255,))
    ir = int(R * .27 * SS)
    d.ellipse((c - ir, c - ir, c + ir, c + ir), fill=(8, 0, 22, 255),
              outline=MAG + (255,), width=int(6 * SS))
    d.ellipse((c - int(ir * 1.15), c - int(ir * 1.15), c + int(ir * 1.15), c + int(ir * 1.15)),
              outline=CYA + (205,), width=int(2.6 * SS))
    ring_img = lay.resize((w, w), Image.LANCZOS)

    # Нишондиҳанда ҶУДО кашида мешавад: агар вай дар ҳамон қабати ҳалқа
    # бошад, дурахши васеаш ба сектори баранда мерезад ва номи барандаро
    # хонданашаванда мекунад. Ин ҷо дурахшаш хурд ва нӯгаш ба чарх
    # ҲАМ намедарояд.
    pl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pl)
    ty = c - int(R * 1.05 * SS)
    col = WHITE if flash else GOLD
    pw = int(max(10, min(34, 340 / n)) * SS)
    pd.polygon([(c - pw, ty - int(74 * SS)), (c + pw, ty - int(74 * SS)),
                (c, ty - int(4 * SS))], fill=col + (255,))
    pd.ellipse((c - int(pw * .58), ty - int(96 * SS), c + int(pw * .58), ty - int(60 * SS)),
               fill=col + (255,))
    return ring_img, pl.resize((w, w), Image.LANCZOS)


def _glow_text(base, t, fnt, y, color, w, h, stroke=4):
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tw = d.textbbox((0, 0), t, font=fnt, stroke_width=stroke)[2]
    d.text(((w - tw) // 2, y), t, font=fnt, fill=color + (255,),
           stroke_width=stroke, stroke_fill=color + (255,))
    base = _add(base, _glow_rgb(lay, (w, h), ((24, .38), (10, .58), (4, .8))))
    lay2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(lay2)
    d2.text(((w - tw) // 2, y), t, font=fnt, fill=(255, 255, 255, 255),
            stroke_width=max(1, stroke // 2), stroke_fill=color + (255,))
    return _add(base, _flatten(lay2, (w, h)))


def _save_mp4(frames, durs, path):
    """
    Кадрҳоро ҳамчун MP4 (H.264) сабт мекунад. Telegram онро ҳамчун
    аниматсия нишон медиҳад, вале сифаташ аз GIF хеле баландтар аст.

    MP4 фақат fps-и СОБИТ дорад, пас ҳар кадр ба қадри давомнокияш
    такрор мешавад. Кадрҳо якто-якто навишта мешаванд — вагарна ҳамаи
    онҳо дар хотира ҷамъ шуда, сервери хурдро аз кор мемонанд.
    """
    try:
        import imageio.v2 as imageio
        import numpy as np
    except Exception as e:
        logger.info(f"spinwheel: MP4 нашуд ({e}) — GIF истифода мешавад")
        return None
    try:
        w, h = frames[0].size
        # H.264 бари ҷуфт талаб мекунад
        w2, h2 = w - (w % 2), h - (h % 2)
        wr = imageio.get_writer(path, fps=MP4_FPS, codec="libx264",
                                quality=9, macro_block_size=1,
                                ffmpeg_params=["-pix_fmt", "yuv420p"])
        step = 1000.0 / MP4_FPS
        for img, ms in zip(frames, durs):
            if (w2, h2) != (w, h):
                img = img.crop((0, 0, w2, h2))
            arr = np.asarray(img)
            for _ in range(max(1, int(round(ms / step)))):
                wr.append_data(arr)
        wr.close()
        return path
    except Exception as e:
        logger.warning(f"spinwheel: MP4 сабт нашуд ({e}) — GIF истифода мешавад")
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
        return None


def render_spin_gif(names, winner_idx, gift_label, total_wins,
                    bot_username="", out_path=None, subtitle="ТӮҲФАИ МИННАТДОРИИ МАҒОЗА"):
    """
    GIF-и чархро месозад ва роҳи файлро бармегардонад (ё None).

    names       — рӯйхати номҳои харидорон (баранда ҳам дар он)
    winner_idx  — индекси баранда дар names
    """
    if not _PIL_OK:
        return None
    if not os.path.isfile(BG_PATH):
        logger.warning(f"spinwheel: паснамо ёфт нашуд: {BG_PATH}")
        return None
    if not names or not (0 <= winner_idx < len(names)):
        logger.warning("spinwheel: рӯйхати номҳо ё индекси баранда нодуруст")
        return None
    # Ҳарфҳои «зебо»-и Unicode ва эмоҷӣ ба ҳарфи оддӣ табдил меёбанд —
    # вагарна ба ҷои ном чоркунҷаи холӣ кашида мешавад
    names = [clean_name(x, f"Мизоҷ {i + 1}") if x else f"Мизоҷ {i + 1}"
             for i, x in enumerate(names)]

    try:
        base_img = Image.open(BG_PATH).convert("RGB")
    except Exception as e:
        logger.error(f"spinwheel: паснамо кушода нашуд: {e}")
        return None

    # Ҳама чиз ба TARGET_W миқёс мешавад: матн ва чарх дар андозаи калон
    # кашида мешаванд, пас тезтар мебароянд ва Telegram ҳангоми фишурдан
    # камтар вайрон мекунад.
    global CX, CY, R, PT, PB, BT, BB, _FS
    _CX0, _CY0, _R0, _PT0, _PB0, _BT0, _BB0 = CX, CY, R, PT, PB, BT, BB
    sc = TARGET_W / float(base_img.width)
    if abs(sc - 1.0) > 0.01:
        base_img = base_img.resize(
            (TARGET_W, int(round(base_img.height * sc))), Image.LANCZOS)
        CX, CY, R = int(CX * sc), int(CY * sc), int(R * sc)
        PT, PB = int(PT * sc), int(PB * sc)
        BT, BB = int(BT * sc), int(BB * sc)
        _FS = sc
        _font_cache.clear()
        _emoji_cache.clear()

    W, H = base_img.size
    n = len(names)
    seg = 360.0 / n
    angle = ((winner_idx * seg + seg / 2) - 270.0) % 360.0 + 360.0 * SPINS

    # ---- Паснамои статикӣ: сарлавҳа, панел, тугма ----
    im = base_img
    im = _glow_text(im, "ТӮҲФАИ РОЙГОН", _font(58), 36, MAG, W, H, 4)
    # Хатакаш ба ДАРОЗИИ матн сохта мешавад; агар матн аз расм васеътар
    # бошад, ҳарф хурд мешавад — вагарна матн аз хатакаш мебарояд
    sub_fs = 26
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    while sub_fs > 15 and probe.textbbox((0, 0), subtitle,
                                         font=_font(sub_fs))[2] > W - 150:
        sub_fs -= 1
    sub_w = probe.textbbox((0, 0), subtitle, font=_font(sub_fs))[2]
    half = min(W // 2 - 30, sub_w // 2 + 34)
    l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(l)
    d.rounded_rectangle((W // 2 - half, 124, W // 2 + half, 124 + sub_fs + 26),
                        radius=13, outline=CYA + (255,), width=4)
    im = _add(im, _glow_rgb(l, (W, H), ((15, .38), (6, .62))))
    im = _add(im, _flatten(l, (W, H)))
    im = _glow_text(im, subtitle, _font(sub_fs), 136, CYA, W, H, 2)

    fl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fl)
    _chamfer(fd, (52, PT, W - 52, PB), 28, fill=(8, 1, 24, 244))
    _chamfer(fd, (W // 2 - 190, BT, W // 2 + 190, BB), 18, fill=(30, 0, 48, 250))
    im.paste(fl.convert("RGB"), (0, 0), fl)

    ol = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ol)
    _chamfer(od, (52, PT, W - 52, PB), 28, outline=MAG + (255,), width=4)
    od.line([(W // 2, PT), (W - 80, PT)], fill=CYA + (255,), width=4)
    od.line([(W - 52, PT + 28), (W - 52, PB - 28)], fill=CYA + (255,), width=4)
    od.line([(W // 2, PB), (W - 80, PB)], fill=CYA + (255,), width=4)
    _chamfer(od, (W // 2 - 190, BT, W // 2 + 190, BB), 18, outline=MAG + (255,), width=4)
    im = _add(im, _glow_rgb(ol, (W, H), ((18, .32), (7, .52), (2, .82))))
    im = _add(im, _flatten(ol, (W, H)))
    static = im

    # ---- Қисмҳо ЯК БОР сохта мешаванд, баъд танҳо гардонида ----
    fill_spin, lines_spin = _wheel_parts(names, winner_idx, False, 0.0)
    fill_win, lines_win = _wheel_parts(names, winner_idx, True, angle)
    ws = fill_spin.size[0]
    lg_spin = _glow_rgb(lines_spin, (ws, ws), ((7, .22), (3, .45)))
    lg_win = _glow_rgb(lines_win, (ws, ws), ((7, .22), (3, .45)))

    rings = {}
    pos = (CX - W // 2, CY - W // 2)
    for flash in (False, True):
        rg, pt = _ring(n, flash, W)
        sharp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sharp.alpha_composite(rg, pos)
        psharp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        psharp.alpha_composite(pt, pos)
        glow = _glow_rgb(sharp, (W, H), ((16, .26), (6, .48), (2, .85)))
        # дурахши нишондиҳанда — танҳо хурд, то ба чарх нарезад
        glow = ImageChops.add(glow, _glow_rgb(psharp, (W, H), ((9, .5), (3, .7))))
        merged = sharp.copy()
        merged.alpha_composite(psharp)
        rings[flash] = (glow, merged)

    gift = _emoji_img("🎁", 66)
    if gift is None:
        # Дар сервер шрифти эмоҷӣ нест — қуттии тӯҳфаро худамон мекашем,
        # вагарна маркази чарх холӣ ва нотамом менамояд
        gift = _draw_gift(74)

    def compose(a, win, flash):
        img = static.copy()
        fl_, ln_, lg_ = (fill_win, lines_win, lg_win) if win else (fill_spin, lines_spin, lg_spin)
        rf = fl_.rotate(a, resample=Image.BICUBIC)
        rl = ln_.rotate(a, resample=Image.BICUBIC)
        rg = lg_.rotate(a, resample=Image.BICUBIC)
        off = (CX - rf.width // 2, CY - rf.height // 2)
        tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tmp.alpha_composite(rf, off)
        img.paste(tmp.convert("RGB"), (0, 0), tmp)
        gl = Image.new("RGB", (W, H), (0, 0, 0))
        gl.paste(rg, off)
        img = ImageChops.add(img, gl)
        tmp2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tmp2.alpha_composite(rl, off)
        img.paste(tmp2.convert("RGB"), (0, 0), tmp2)
        gimg, gsharp = rings[flash]
        img = ImageChops.add(img, gimg)
        img.paste(gsharp.convert("RGB"), (0, 0), gsharp)
        if gift:
            img.paste(gift, (CX - gift.width // 2, CY - gift.height // 2), gift)
        # Каме контраст ва серобӣ — вагарна расм хира менамояд
        img = ImageEnhance.Color(img).enhance(1.30)
        img = ImageEnhance.Contrast(img).enhance(1.24)
        return img

    winner = names[winner_idx]
    tail = f"{total_wins}-умин барандаи мо · аз {n} харидор"
    if bot_username:
        tail += f" · @{bot_username}"

    def panel(img, win):
        """Панели поёнӣ дар ҲАМА кадрҳо пур мешавад — вагарна ҳангоми
        гардиш (ва GIF беохир такрор мешавад) як қуттии холӣ менамояд."""
        d2 = ImageDraw.Draw(img)
        if win:
            _cput(img, d2, PT + 26, "🏅 " + winner, _font(44), WHITE, W)
            _cput(img, d2, PT + 96, "🎁 " + gift_label + " — БЕПУЛ!", _font(28), GREEN, W)
            _cput(img, d2, PT + 140, "🏆 " + tail, _font(16), (195, 175, 250), W)
        else:
            _cput(img, d2, PT + 34, "ИНТИХОБИ ТАСОДУФӢ...", _font(34), CYA, W)
            _cput(img, d2, PT + 96, gift_label + " — БЕПУЛ!", _font(26), GREEN, W)
            _cput(img, d2, PT + 140, "Ҳар харидор дар рӯйхат аст", _font(16),
                  (195, 175, 250), W)
        _cput(img, d2, BT + 24, "ФАРМОИШ ДИҲЕД!", _font(30), WHITE, W)
        return img

    frames, durs = [], []
    steps = 26
    for i in range(steps):
        t = i / (steps - 1)
        e = 1 - (1 - t) ** 3.2
        frames.append(panel(compose(angle * e, False, False), False))
        durs.append(int(50 + 170 * t ** 2.5))

    for j in range(4):
        frames.append(panel(compose(angle, True, j % 2 == 0), True))
        durs.append(2800 if j == 3 else 250)

    # ---- Қиматҳои глобалиро барқарор мекунем ----
    CX, CY, R, PT, PB, BT, BB = _CX0, _CY0, _R0, _PT0, _PB0, _BT0, _BB0
    _FS = 1.0
    _font_cache.clear()
    _emoji_cache.clear()

    base_out = out_path or os.path.join(_DIR, "spin_last")
    base_out = os.path.splitext(base_out)[0]

    # ---- Кӯшиши 1: MP4 (H.264). GIF танҳо 256 ранг дорад — маҳз аз ҳамин
    # градиентҳо доғдор ва хира мебароянд. MP4 ин маҳдудиятро надорад. ----
    mp4 = _save_mp4(frames, durs, base_out + ".mp4")
    if mp4:
        return mp4

    # ---- Кӯшиши 2: GIF (агар ffmpeg дар сервер набошад) ----
    gif_path = base_out + ".gif"
    try:
        small = [x if x.width <= 900 else
                 x.resize((900, int(x.height * 900 / x.width)), Image.LANCZOS)
                 for x in frames]
        pal = small[-1].convert("P", palette=Image.ADAPTIVE, colors=170)
        q = [x.quantize(palette=pal, dither=Image.FLOYDSTEINBERG) for x in small]
        q[0].save(gif_path, save_all=True, append_images=q[1:],
                  duration=durs, loop=0, optimize=True)
    except Exception as e:
        logger.error(f"spinwheel: GIF сабт нашуд: {e}")
        return None
    return gif_path
