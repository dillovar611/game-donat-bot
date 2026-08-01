# -*- coding: utf-8 -*-
"""
chatlog.py — бойгонии сӯҳбатҳои боти business бо мизоҷон.

Ҳар мизоҷ дар сервер папкаи ХУДРО дорад:

    chats/<user_id>/
        info.json          — ном, username, санаи аввал/охир, шумораи паём
        messages.jsonl     — сабти ҲАМАИ ҳодисаҳо (паём, ислоҳ, несткунӣ)
        photos/            — расмҳои воқеии фиристодаи мизоҷ (чекҳо)
        sohbat.txt         — ҳамон сӯҳбат ҳамчун матни оддии хондашаванда
        screenshot_*.png   — «скриншот»-и сӯҳбат

`messages.jsonl` ТАНҲО ИЛОВА мешавад (append-only): ислоҳ ва несткунӣ
сатри кӯҳнаро иваз намекунанд, балки ҳамчун ҳодисаи нав навишта мешаванд.
Барои ҳамин матни АВВАЛА ҳатто баъди несткунии мизоҷ боқӣ мемонад.

Ин модул ба база ДАСТРАСӢ НАДОРАД ва ҳељ калид намехонад — танҳо бо
файлҳои папкаи худаш кор мекунад.
"""
import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(_DIR, "chats")

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception as e:                                    # pragma: no cover
    logger.warning(f"chatlog: Pillow нест — скриншот кор намекунад: {e}")
    _PIL_OK = False

# ==================== ШРИФТ ====================
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/DejaVuSans.ttf",
]
_FONT_PATHS_BOLD = [p.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf") for p in _FONT_PATHS]
_font_cache: dict = {}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    fnt = None
    for p in (_FONT_PATHS_BOLD if bold else _FONT_PATHS):
        if os.path.isfile(p):
            try:
                fnt = ImageFont.truetype(p, size)
                break
            except Exception:
                pass
    if fnt is None:
        # Шрифти системавӣ нест — шрифти base64-и боти асосӣ (агар бошад)
        try:
            import io
            import receipt
            src = receipt._get_font_bytes(
                "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
            fnt = ImageFont.truetype(
                io.BytesIO(src) if isinstance(src, bytes) else src, size)
        except Exception:
            fnt = ImageFont.load_default()
    _font_cache[key] = fnt
    return fnt


# Ҳарфҳое, ки DejaVu кашида метавонад. Эмоҷӣ дар он НЕСТ — агар онҳоро
# нагузарем, ба ҷои ҳар эмоҷӣ чоркунҷаи холӣ (▯) мебарояд ва скриншот
# нохоно мешавад. Дар `sohbat.txt` бошад матни ПУРРА бо эмоҷӣ мемонад.
_OK_RANGES = (
    (0x20, 0x7E),      # ASCII
    (0xA0, 0x24F),     # Latin-1 + Latin Extended
    (0x400, 0x52F),    # Кириллица (бо ҳарфҳои тоҷикӣ: ӯ ҳ ҷ қ ғ ӣ)
    (0x2010, 0x2027),  # тире, нохунак, се нуқта
    (0x20AC, 0x20AC),  # €
)


def _drawable(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\n\t" or any(a <= o <= b for a, b in _OK_RANGES):
            out.append(ch)
    # Ҷойҳои холии пайдарпай, ки аз партофтани эмоҷӣ мемонанд
    lines = [" ".join(l.split()) for l in "".join(out).split("\n")]
    return "\n".join(lines).strip()


# ==================== ПАПКА ВА САБТ ====================
def _udir(user_id: int) -> str:
    d = os.path.join(BASE, str(user_id))
    os.makedirs(os.path.join(d, "photos"), exist_ok=True)
    return d


def _append(user_id: int, entry: dict):
    d = _udir(user_id)
    with open(os.path.join(d, "messages.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_events(user_id: int) -> list:
    p = os.path.join(BASE, str(user_id), "messages.jsonl")
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _save_info(user_id: int, **kw):
    d = _udir(user_id)
    p = os.path.join(d, "info.json")
    info = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            info = {}
    info.update({k: v for k, v in kw.items() if v is not None})
    info.setdefault("first_seen", time.time())
    info["last_seen"] = time.time()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.error(f"chatlog: info.json навишта нашуд ({user_id}): {e}")
    return info


def get_info(user_id: int) -> dict:
    p = os.path.join(BASE, str(user_id), "info.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


async def record(bot, message, chat_id: int, who: str, name: str, username: str = ""):
    """
    Паёмро сабт мекунад. `who`: "client" ё "owner".
    Расми фиристодашуда ба папкаи ҳамон мизоҷ бор карда мешавад.
    Хатогӣ ҳељ гоҳ ба боти асосӣ намебарояд — сабт набояд ҷавобдиҳиро вайрон кунад.
    """
    try:
        text = message.text or message.caption or ""
        entry = {
            "t": "msg",
            "mid": message.message_id,
            "ts": time.time(),
            "who": who,
            "name": name,
            "text": text,
        }
        if message.photo:
            entry["photo"] = await _save_media(
                bot, message.photo[-1].file_id, chat_id,
                "photos", f"{message.message_id}_{int(time.time())}.jpg")
            if not entry["photo"]:
                # Худи расм наомад — вале паём набояд ХОЛӢ намояд,
                # вагарна дар бойгонӣ гӯё чизе нафиристода бошад
                entry["file"] = "📸 расм"
        elif message.sticker:
            entry["text"] = text or f"[стикер {message.sticker.emoji or ''}]".strip()
        else:
            # Овоз, видео, файл — ҳамааш ҲАМЧУН ФАЙЛ захира мешавад, на
            # танҳо навишта. Агар мизоҷ дар паёми овозӣ чизе гӯяд ва
            # баъд онро нест кунад, худи овоз дар сервер боқӣ мемонад.
            got = _media_of(message)
            if got:
                kind, file_id, ext, label = got
                entry["file"] = label
                entry["fpath"] = await _save_media(
                    bot, file_id, chat_id, "media",
                    f"{message.message_id}_{int(time.time())}{ext}")
        _append(chat_id, entry)
        if who == "client":
            _save_info(chat_id, name=name, username=username)
        else:
            _save_info(chat_id)
    except Exception as e:
        logger.error(f"chatlog.record хато ({chat_id}): {e}")


def _media_of(message):
    """
    Кадом навъи файл дар паём аст: (навъ, file_id, пасванд, навишта).
    Агар паём танҳо матн бошад — None.
    """
    if message.voice:
        return "voice", message.voice.file_id, ".ogg", "🎤 паёми овозӣ"
    if message.video_note:
        return "video_note", message.video_note.file_id, ".mp4", "⭕️ видео-доира"
    if message.video:
        return "video", message.video.file_id, ".mp4", "🎬 видео"
    if message.audio:
        return "audio", message.audio.file_id, ".mp3", "🎵 аудио"
    if message.animation:
        return "animation", message.animation.file_id, ".mp4", "🎞 GIF"
    if message.document:
        d = message.document
        ext = os.path.splitext(d.file_name or "")[1] or ".bin"
        return "document", d.file_id, ext, f"📎 {d.file_name or 'файл'}"
    return None


async def _save_media(bot, file_id: str, chat_id: int, sub: str, fname: str) -> str:
    """
    Файлро ба папкаи мизоҷ бор мекунад ва номашро бармегардонад.
    Агар нашавад — сатри холӣ, вале дар лог сабаби АНИҚ навишта мешавад
    (вагарна маълум намешавад, ки чаро расм дар бойгонӣ нест).
    """
    try:
        d = os.path.join(_udir(chat_id), sub)
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, fname)
        await bot.download(file_id, destination=dest)
        size = os.path.getsize(dest)
        if size <= 0:
            logger.error(f"chatlog: файли холӣ бор шуд ({chat_id}/{sub}/{fname})")
            return ""
        logger.info(f"chatlog: захира шуд {chat_id}/{sub}/{fname} ({size // 1024} КБ)")
        return fname
    except Exception as e:
        logger.error(f"chatlog: файл бор НАШУД ({chat_id}/{sub}/{fname}): "
                     f"{type(e).__name__}: {e}")
        return ""


def record_edit(chat_id: int, message_id: int, new_text: str):
    """
    Ислоҳи паёмро сабт мекунад ва матни КӮҲНАро бармегардонад
    (ё None, агар паёми аслӣ дар сабт набошад).
    """
    try:
        old = None
        for ev in _read_events(chat_id):
            if ev.get("mid") == message_id and ev.get("t") in ("msg", "edit"):
                old = ev.get("text", "")
        _append(chat_id, {"t": "edit", "mid": message_id, "ts": time.time(),
                          "text": new_text, "old": old})
        return old
    except Exception as e:
        logger.error(f"chatlog.record_edit хато ({chat_id}): {e}")
        return None


def record_delete(chat_id: int, message_ids: list):
    """
    Несткунии паёмҳоро сабт мекунад ва рӯйхати матнҳои несткардашударо
    бармегардонад: [(message_id, matn, who), ...]
    """
    out = []
    try:
        state = {}
        for ev in _read_events(chat_id):
            mid = ev.get("mid")
            if ev.get("t") == "msg":
                state[mid] = {"text": ev.get("text", ""), "who": ev.get("who", "?"),
                              "photo": ev.get("photo", "")}
            elif ev.get("t") == "edit" and mid in state:
                state[mid]["text"] = ev.get("text", "")
        for mid in message_ids:
            st = state.get(mid, {})
            txt = st.get("text", "")
            if not txt and st.get("photo"):
                txt = "📸 (расм)"
            out.append((mid, txt, st.get("who", "?")))
            _append(chat_id, {"t": "del", "mid": mid, "ts": time.time(),
                              "text": txt, "who": st.get("who", "?")})
    except Exception as e:
        logger.error(f"chatlog.record_delete хато ({chat_id}): {e}")
    return out


# ==================== ҶАМЪБАСТИ СӮҲБАТ ====================
def build_thread(user_id: int) -> list:
    """
    Ҳодисаҳоро ба рӯйхати паёмҳои омодаи нишондиҳӣ табдил медиҳад.
    Ҳар паём: {mid, ts, who, name, text, photo, edited(list), deleted(bool)}
    """
    msgs, order = {}, []
    for ev in _read_events(user_id):
        mid, t = ev.get("mid"), ev.get("t")
        if t == "msg":
            if mid not in msgs:
                order.append(mid)
                msgs[mid] = {"mid": mid, "ts": ev.get("ts", 0),
                             "who": ev.get("who", "?"), "name": ev.get("name", ""),
                             "text": ev.get("text", ""), "photo": ev.get("photo", ""),
                             "file": ev.get("file", ""), "fpath": ev.get("fpath", ""),
                             "edited": [], "deleted": False}
        elif t == "edit" and mid in msgs:
            m = msgs[mid]
            m["edited"].append({"old": m["text"], "new": ev.get("text", ""),
                                "ts": ev.get("ts", 0)})
            m["text"] = ev.get("text", "")
        elif t == "del" and mid in msgs:
            msgs[mid]["deleted"] = True
            msgs[mid]["deleted_ts"] = ev.get("ts", 0)
    return [msgs[m] for m in order]


def list_chats() -> list:
    """Рӯйхати ҳамаи мизоҷон: [{user_id, name, username, count, last_seen}, ...]"""
    out = []
    if not os.path.isdir(BASE):
        return out
    for d in os.listdir(BASE):
        p = os.path.join(BASE, d)
        if not os.path.isdir(p) or not d.lstrip("-").isdigit():
            continue
        info = get_info(int(d))
        thread = build_thread(int(d))
        out.append({
            "user_id": int(d),
            "name": info.get("name") or str(d),
            "username": info.get("username") or "",
            "count": len(thread),
            "deleted": sum(1 for m in thread if m["deleted"]),
            "edited": sum(1 for m in thread if m["edited"]),
            "last_seen": info.get("last_seen", 0),
        })
    out.sort(key=lambda x: x["last_seen"], reverse=True)
    return out


def make_zip(dest_dir: str = None) -> str:
    """
    Ҳамаи бойгониро ба як файли ZIP мебандад ва роҳашро бармегардонад
    (ё сатри холӣ, агар ҳанӯз ягон сӯҳбат набошад).

    Матн ва сабтҳо ҲАМЕША дохил мешаванд. Расмҳо танҳо он вақт, ки ҳаҷми
    умумӣ аз ҳудуди Telegram (50 МБ) нагузарад — вагарна файл умуман
    фиристода намешавад ва нусхаи эҳтиётӣ маъно надорад.
    """
    import zipfile
    if not os.path.isdir(BASE):
        return ""
    dest_dir = dest_dir or BASE
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(dest_dir, f"sohbatho_{stamp}.zip")

    files, photos = [], []
    for root, _dirs, names in os.walk(BASE):
        for n in names:
            if n.endswith(".zip"):
                continue
            full = os.path.join(root, n)
            rel = os.path.relpath(full, BASE)
            (photos if f"{os.sep}photos{os.sep}" in full else files).append((full, rel))
    if not files and not photos:
        return ""

    LIMIT = 45 * 1024 * 1024
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for full, rel in files:
                z.write(full, rel)
            # Расмҳо аз навтарин ба кӯҳна, то ҷои холӣ бас кунад
            photos.sort(key=lambda p: os.path.getmtime(p[0]), reverse=True)
            skipped = 0
            for full, rel in photos:
                if os.path.getsize(path) > LIMIT:
                    skipped += 1
                    continue
                z.write(full, rel)
            if skipped:
                z.writestr("ЭЗОҲ.txt",
                           f"{skipped} расми кӯҳна ба ин архив дохил нашуд — "
                           f"ҳаҷм аз ҳудуди Telegram мегузашт.\n"
                           f"Онҳо дар сервер боқӣ мондаанд.\n")
    except Exception as e:
        logger.error(f"chatlog: ZIP сохта нашуд: {e}")
        return ""
    return path


def search(needle: str, limit: int = 40) -> list:
    """
    Дар ҳамаи сӯҳбатҳо калима ё рақами фармоишро меёбад.

    Матни НЕСТКАРДАШУДА ва матни то ислоҳ ҳам ҷустуҷӯ мешавад — маҳз
    онҳо аз ҳама муҳиманд, чунки дар худи Telegram дигар вуҷуд надоранд.

    Бармегардонад: [{user_id, name, ts, who, text, deleted, edited}, ...]
    навтарин аввал.
    """
    q = (needle or "").strip().lower()
    if not q:
        return []
    hits = []
    for c in list_chats():
        uid = c["user_id"]
        for m in build_thread(uid):
            found, mark = None, ""
            if q in (m["text"] or "").lower():
                found = m["text"]
            else:
                for e in m["edited"]:
                    if q in (e["old"] or "").lower():
                        found, mark = e["old"], "edited"
                        break
            if found is None:
                continue
            hits.append({
                "user_id": uid, "name": c["name"], "ts": m["ts"],
                "who": m["who"], "text": found,
                "deleted": m["deleted"],
                "edited": bool(mark) or bool(m["edited"]),
                "only_in_archive": m["deleted"] or bool(mark),
            })
    hits.sort(key=lambda h: h["ts"], reverse=True)
    return hits[:limit]


def dump_text(user_id: int) -> str:
    """Сӯҳбатро ҳамчун файли матнии оддӣ менависад ва роҳашро бармегардонад."""
    thread = build_thread(user_id)
    info = get_info(user_id)
    lines = [
        f"СӮҲБАТ БО МИЗОҶ: {info.get('name') or user_id}",
        f"ID: {user_id}" + (f"   Username: @{info['username']}" if info.get("username") else ""),
        f"Шумораи паёмҳо: {len(thread)}",
        f"Сабт то: {datetime.now():%Y-%m-%d %H:%M}",
        "=" * 60, "",
    ]
    for m in thread:
        ts = datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d %H:%M")
        who = "МИЗОҶ" if m["who"] == "client" else "МО"
        lines.append(f"[{ts}] {who}:")
        if m["photo"]:
            lines.append(f"    📸 расм: photos/{m['photo']}")
        if m["file"]:
            where = f": media/{m['fpath']}" if m.get("fpath") else " (файл захира нашуд)"
            lines.append(f"    {m['file']}{where}")
        if m["text"]:
            for l in m["text"].split("\n"):
                lines.append(f"    {l}")
        for e in m["edited"]:
            ets = datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
            lines.append(f"    ✏️ ИСЛОҲ ШУД ({ets}). Матни аввала буд:")
            for l in (e["old"] or "").split("\n"):
                lines.append(f"        {l}")
        if m["deleted"]:
            dts = datetime.fromtimestamp(m.get("deleted_ts", 0)).strftime("%Y-%m-%d %H:%M")
            lines.append(f"    ❌ ИН ПАЁМ НЕСТ КАРДА ШУД ({dts}) — матнаш дар боло боқӣ монд")
        lines.append("")
    path = os.path.join(_udir(user_id), "sohbat.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ==================== СКРИНШОТ ====================
W = 900                     # бари расм
PAD = 18
BUBBLE_MAX = 620
MAX_H = 3800                # аз ин баландтар шавад — ба саҳифаи нав мегузарад

BG = (17, 20, 26)
BUB_CLIENT = (38, 42, 51)
BUB_OWNER = (24, 66, 104)
BUB_DEL = (74, 26, 32)
TXT = (236, 238, 242)
DIM = (150, 158, 170)
RED = (255, 120, 120)
YEL = (255, 205, 90)
ACC = (90, 190, 255)


def _wrap(d, text, fnt, max_w):
    lines = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        cur = ""
        for w in raw.split(" "):
            test = (cur + " " + w).strip()
            if d.textlength(test, font=fnt) <= max_w:
                cur = test
                continue
            if cur:
                lines.append(cur)
            # Худи калима аз бар дарозтар — маҷбуран мешиканем
            while d.textlength(w, font=fnt) > max_w and len(w) > 1:
                k = 1
                while k < len(w) and d.textlength(w[:k + 1], font=fnt) <= max_w:
                    k += 1
                lines.append(w[:k])
                w = w[k:]
            cur = w
        lines.append(cur)
    return lines


def _blocks(thread, probe):
    """Ҳар паёмро ба «блок»-и тайёр бо баландии ҳисобшуда табдил медиҳад."""
    f_txt, f_sm, f_nm = _font(20), _font(15), _font(16, True)
    out = []
    last_day = None
    for m in thread:
        day = datetime.fromtimestamp(m["ts"]).strftime("%d.%m.%Y")
        if day != last_day:
            out.append({"kind": "day", "text": day, "h": 46})
            last_day = day
        body = _drawable(m["text"])
        lines = _wrap(probe, body, f_txt, BUBBLE_MAX - 2 * PAD) if body else []
        th = 0
        img_h = 0
        if m["photo"]:
            p = m.get("_photo_path")
            if p and os.path.isfile(p):
                img_h = 230
            else:
                lines = ["[расм]"] + lines
        if m["file"]:
            lines = [_drawable(m["file"])] + lines
        th += len(lines) * 27
        # Дар қайдҳо эмоҷӣ намегузорем — шрифт онро надорад ва ба ҷои он
        # чоркунҷа мебарояд. Ранг худаш фарқро нишон медиҳад.
        notes = []
        for e in m["edited"]:
            old = _drawable(e["old"] or "")
            notes.append((("ИСЛОҲ ШУД. Матни аввала буд: " + old)[:260]
                          if old else "ИСЛОҲ ШУД", YEL))
        if m["deleted"]:
            notes.append(("МИЗОҶ ИН ПАЁМРО НЕСТ КАРД", RED))
        nh = 0
        note_lines = []
        for nt, col in notes:
            nl = _wrap(probe, nt, f_sm, BUBBLE_MAX - 2 * PAD)
            note_lines.append((nl, col))
            nh += len(nl) * 21 + 6
        h = PAD + 22 + img_h + th + nh + PAD + 12
        out.append({"kind": "msg", "m": m, "lines": lines, "note_lines": note_lines,
                    "img_h": img_h, "h": h})
    return out


def _draw_page(blocks, header, page_no, pages, out_path):
    f_txt, f_sm, f_nm = _font(20), _font(15), _font(16, True)
    f_h1, f_h2 = _font(26, True), _font(16)
    head_h = 96
    total = head_h + sum(b["h"] for b in blocks) + 40
    im = Image.new("RGB", (W, total), BG)
    d = ImageDraw.Draw(im)

    d.rectangle((0, 0, W, head_h - 8), fill=(11, 14, 19))
    d.text((PAD, 16), _drawable(header["title"]), font=f_h1, fill=TXT)
    d.text((PAD, 52), _drawable(header["sub"]), font=f_h2, fill=DIM)
    if pages > 1:
        pg = f"саҳифаи {page_no} аз {pages}"
        d.text((W - PAD - d.textlength(pg, font=f_h2), 52), pg, font=f_h2, fill=DIM)
    d.line((0, head_h - 8, W, head_h - 8), fill=(46, 52, 62), width=2)

    y = head_h
    for b in blocks:
        if b["kind"] == "day":
            tw = d.textlength(b["text"], font=f_sm)
            d.rounded_rectangle((W // 2 - tw // 2 - 14, y + 8, W // 2 + tw // 2 + 14, y + 34),
                                radius=13, fill=(30, 34, 42))
            d.text((W // 2 - tw // 2, y + 12), b["text"], font=f_sm, fill=DIM)
            y += b["h"]
            continue

        m = b["m"]
        client = m["who"] == "client"
        widths = [d.textlength(l, font=f_txt) for l in b["lines"]] or [0]
        for nl, _c in b["note_lines"]:
            widths += [d.textlength(l, font=f_sm) for l in nl]
        bw = int(max(widths + ([300] if b["img_h"] else [0])) + 2 * PAD)
        bw = max(150, min(BUBBLE_MAX, bw))
        x0 = PAD if client else W - PAD - bw
        col = BUB_DEL if m["deleted"] else (BUB_CLIENT if client else BUB_OWNER)
        d.rounded_rectangle((x0, y, x0 + bw, y + b["h"] - 12), radius=16, fill=col)
        if m["deleted"]:
            d.rounded_rectangle((x0, y, x0 + bw, y + b["h"] - 12), radius=16,
                                outline=RED, width=2)

        who = _drawable(m["name"]) if client else "Мо"
        tstr = datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
        d.text((x0 + PAD, y + 8), who or "Мизоҷ", font=f_nm,
               fill=ACC if client else (150, 210, 255))
        d.text((x0 + bw - PAD - d.textlength(tstr, font=f_sm), y + 10),
               tstr, font=f_sm, fill=DIM)
        ty = y + 34

        if b["img_h"]:
            p = m.get("_photo_path")
            try:
                th_im = Image.open(p).convert("RGB")
                sc = min((bw - 2 * PAD) / th_im.width, 210 / th_im.height)
                th_im = th_im.resize((max(1, int(th_im.width * sc)),
                                      max(1, int(th_im.height * sc))), Image.LANCZOS)
                im.paste(th_im, (x0 + PAD, ty))
            except Exception:
                d.text((x0 + PAD, ty), "[расм]", font=f_txt, fill=DIM)
            ty += b["img_h"]

        for l in b["lines"]:
            d.text((x0 + PAD, ty), l, font=f_txt, fill=TXT)
            ty += 27
        for nl, ncol in b["note_lines"]:
            for l in nl:
                d.text((x0 + PAD, ty), l, font=f_sm, fill=ncol)
                ty += 21
            ty += 6
        y += b["h"]

    im.save(out_path, "PNG", optimize=True)
    return out_path


def render(user_id: int, tag: str = "") -> list:
    """
    «Скриншот»-и сӯҳбатро месозад. Агар сӯҳбат дароз бошад, ба чанд
    саҳифа тақсим мешавад. Рӯйхати роҳи файлҳоро бармегардонад.
    """
    if not _PIL_OK:
        return []
    thread = build_thread(user_id)
    if not thread:
        return []
    d = _udir(user_id)
    for m in thread:
        if m["photo"]:
            m["_photo_path"] = os.path.join(d, "photos", m["photo"])

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    blocks = _blocks(thread, probe)

    pages, cur, h = [], [], 0
    for b in blocks:
        if cur and h + b["h"] > MAX_H:
            pages.append(cur)
            cur, h = [], 0
        cur.append(b)
        h += b["h"]
    if cur:
        pages.append(cur)

    info = get_info(user_id)
    uname = f" · @{info['username']}" if info.get("username") else ""
    ndel = sum(1 for m in thread if m["deleted"])
    ned = sum(1 for m in thread if m["edited"])
    warn = ""
    if ndel or ned:
        warn = f" · ❌ {ndel} несткарда · ✏️ {ned} ислоҳшуда"
    header = {
        "title": (info.get("name") or str(user_id)),
        "sub": f"ID {user_id}{uname} · {len(thread)} паём{warn} · "
               f"{datetime.now():%d.%m.%Y %H:%M}",
    }
    stamp = tag or datetime.now().strftime("%Y-%m-%d")
    out = []
    for i, pg in enumerate(pages, 1):
        p = os.path.join(d, f"screenshot_{stamp}"
                            + (f"_{i}" if len(pages) > 1 else "") + ".png")
        try:
            _draw_page(pg, header, i, len(pages), p)
            out.append(p)
        except Exception as e:
            logger.error(f"chatlog: саҳифаи {i} кашида нашуд ({user_id}): {e}")
    return out
