# -*- coding: utf-8 -*-
"""
games.py — бозиҳои хурд барои вақти интизории мизоҷ.

Вақте мизоҷ чекро мефиристад, то тасдиқ шудани пардохт баъзан 10-15
дақиқа мегузарад. Дар ин муддат ӯ танҳо ба экран нигоҳ мекунад ва
асабӣ мешавад — ҳамин ҷо ҳашт бозии хурд пешниҳод мешавад:

  2048 · 4 дар қатор · Ҷанги баҳрӣ · Калимаро ёб ·
  Мина 5×5 · Пазли 15 · Квизи Free Fire · Тик-так-то

Ҳамаи онҳо бозиҳои ШИНОХТАанд. Пештар ин ҷо «Чароғҳо» (Lights Out) ва
«Рамзкушоӣ» (Mastermind) буданд — бозиҳои хубе, вале мизоҷони мо онҳоро
ҳаргиз надида буданд: мекушоянд, намефаҳманд ва мебанданд. Бозие, ки
қоидаашро фаҳмондан лозим аст, дар вақти интизорӣ кор намекунад.

Бозиҳое, ки бо БОТ бозӣ мешаванд (тик-так-то, 4 дар қатор), қасдан
беайб НЕСТАНД. Дар санҷиш боти беайби «4 дар қатор» 30 аз 30 бозиро
бурд — чунин бозӣ мизоҷро танҳо асабӣ мекунад. Ҳозир бот баъзан роҳи
мизоҷро намебандад, вале ғалабаи худашро ҳамеша мегирад. Дар натиҷа
мизоҷи фикркунанда тақрибан 68% мебарад.

Пазли 15 аз ҳолати ҲАЛШУДА бо ҳаракатҳои тасодуфӣ омехта мешавад — бо
ин он ҲАТМАН ҳалшаванда мемонад. Омехтаи тасодуфии оддӣ метавонад
ҳолати ҳалнашаванда диҳад ва мизоҷ беҳуда вақт сарф мекунад.

ҚОИДАИ АСОСӢ: тамоми бозӣ дар ЯК паём мегузарад ва ҳар ҳаракат ҳамон
паёмро НАВ мекунад, на паёми нав месозад. Вагарна паёми «✅ Тасдиқ шуд»
дар байни даҳҳо паёми бозӣ гум мешавад ва бозӣ ба ҷои кӯмак зарар
мерасонад. Дар сарлавҳаи ҳар паём ҳолати фармоиш ҳам навишта мешавад.

Ҳељ мукофот дода намешавад — бозӣ фақат барои хушҳолӣ аст. Ин қасдан
аст: ҳар мукофот роҳи сӯиистифода мекушояд ва адолати тӯҳфаи чархро,
ки дар канал «ҳама баробар» гуфта шудааст, вайрон мекунад.
"""
import logging
import random
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
router = Router()

# Ҳолати бозӣ дар хотира — на дар база. Бозӣ чизи муваққатист: агар
# сервер рестарт шавад, як бозии нотамом гум шавад, ҳељ гап нест.
_state: dict = {}
_STATE_TTL = 3 * 3600
_STATE_MAX = 500


def _clean_state():
    if len(_state) <= _STATE_MAX:
        return
    now = time.time()
    for k in [k for k, v in _state.items() if now - v.get("at", 0) > _STATE_TTL]:
        _state.pop(k, None)
    # Агар ҳанӯз зиёд бошад — кӯҳнатаринҳоро мепартоем
    if len(_state) > _STATE_MAX:
        for k, _v in sorted(_state.items(), key=lambda x: x[1].get("at", 0))[:100]:
            _state.pop(k, None)


def _st(uid: int) -> dict:
    s = _state.get(uid)
    if s is None:
        _clean_state()
        s = _state[uid] = {"at": time.time(), "note": ""}
    s["at"] = time.time()
    return s


def _head(uid: int, title: str) -> str:
    note = _st(uid).get("note") or ""
    line = f"\n{note}\n" if note else "\n"
    return f"{title}{line}"


# ==================== МЕНЮ ====================
def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 2048", callback_data="g:2s"),
         InlineKeyboardButton(text="🔴 4 дар қатор", callback_data="g:4s")],
        [InlineKeyboardButton(text="🚢 Ҷанги баҳрӣ", callback_data="g:bs"),
         InlineKeyboardButton(text="🔤 Калимаро ёб", callback_data="g:ws")],
        [InlineKeyboardButton(text="💣 Мина 5×5", callback_data="g:ms"),
         InlineKeyboardButton(text="🧩 Пазли 15", callback_data="g:ps")],
        [InlineKeyboardButton(text="🧠 Квизи Free Fire", callback_data="g:qs"),
         InlineKeyboardButton(text="⭕️ Тик-так-то", callback_data="g:ts")],
    ])


def menu_text(note: str = "") -> str:
    return (
        "🎮 <b>То тайёр шудан — бозӣ кунед!</b>\n"
        + (f"\n{note}\n" if note else "\n")
        + "\nВақт тезтар мегузарад 😊 Бозиро интихоб кунед:"
    )


def start_state(uid: int, note: str = ""):
    """Даъваткунанда (buy.py) ҳолати фармоишро ин ҷо мегузорад."""
    _st(uid)["note"] = note


async def _show(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    """Ҳамон паёмро нав мекунад. Агар матн наваш якхела бошад, Telegram
    хато медиҳад — онро нодида мегирем."""
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.info(f"games: паём нав нашуд: {e}")
    await call.answer()


@router.callback_query(F.data == "g:menu")
async def g_menu(call: CallbackQuery):
    await _show(call, menu_text(_st(call.from_user.id).get("note", "")), menu_kb())


_BACK = InlineKeyboardButton(text="🔙 Бозиҳои дигар", callback_data="g:menu")


# ==================== 1) КВИЗИ FREE FIRE ====================
# (савол, [ҷавобҳо], рақами ҷавоби дуруст)
QUIZ = [
    ("Дар Free Fire як матч чанд бозикун дорад?", ["30", "50", "100", "150"], 1),
    ("Номи асосии харитаи аввалини Free Fire чист?", ["Purgatory", "Bermuda", "Kalahari", "Alpine"], 1),
    ("Ғалаба дар Free Fire чӣ ном дорад?", ["Booyah!", "Victory!", "Winner!", "Top 1!"], 0),
    ("Пули дохилии Free Fire чӣ ном дорад?", ["Кристалл", "Алмос", "Тилло", "Ҷавоҳир"], 1),
    ("Гандаи (pet) машҳури Free Fire кадом аст?", ["Rocky", "Ottero", "Falco", "Ҳамааш"], 3),
    ("Дар як даста (squad) чанд нафар мешавад?", ["2", "3", "4", "5"], 2),
    ("Кадоме аз инҳо силоҳи снайперӣ аст?", ["MP40", "AWM", "M1014", "UMP"], 1),
    ("Free Fire-ро кадом ширкат сохтааст?", ["Tencent", "Garena", "Krafton", "Riot"], 1),
    ("«Gloo Wall» барои чӣ аст?", ["Ҳамла", "Пинҳоншавӣ/девор", "Тезӣ", "Дармон"], 1),
    ("Кадом мошин дар Free Fire тезтарин аст?", ["Ҷип", "Мотосикл", "Трактор", "Қаиқ"], 1),
    ("«Character skill» чист?", ["Либос", "Қобилияти махсус", "Силоҳ", "Харита"], 1),
    ("Дар Free Fire чӣ хел зинда мондан осонтар аст?", ["Дар маркази харита", "Дар зона мондан", "Давидан", "Тирандозӣ"], 1),
    ("«Rank»-и баландтарини Free Fire кадом аст?", ["Diamond", "Heroic", "Grandmaster", "Master"], 2),
    ("Барои донат чӣ лозим аст?", ["Парол", "Player ID", "Рақами телефон", "Почта"], 1),
    ("«Airdrop» чист?", ["Ҳавопаймо", "Қуттии тӯҳфа аз осмон", "Мина", "Зона"], 1),
]


def _quiz_new(uid: int):
    s = _st(uid)
    order = list(range(len(QUIZ)))
    random.shuffle(order)
    s["q"] = {"order": order, "i": 0, "score": 0, "picked": None}


def _quiz_kb(uid: int) -> InlineKeyboardMarkup:
    s = _st(uid)["q"]
    qi = s["order"][s["i"]]
    _, answers, right = QUIZ[qi]
    rows = []
    if s["picked"] is None:
        for i, a in enumerate(answers):
            rows.append([InlineKeyboardButton(text=a, callback_data=f"g:qa:{i}")])
    else:
        for i, a in enumerate(answers):
            mark = "✅" if i == right else ("❌" if i == s["picked"] else "▫️")
            rows.append([InlineKeyboardButton(text=f"{mark} {a}", callback_data="g:noop")])
        last = s["i"] >= len(s["order"]) - 1
        rows.append([InlineKeyboardButton(
            text="🏁 Натиҷа" if last else "➡️ Саволи навбатӣ", callback_data="g:qn")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quiz_text(uid: int) -> str:
    s = _st(uid)["q"]
    qi = s["order"][s["i"]]
    q, answers, right = QUIZ[qi]
    t = _head(uid, "🧠 <b>Квизи Free Fire</b>")
    t += f"\nСавол {s['i'] + 1} аз {len(s['order'])} · Дуруст: <b>{s['score']}</b>\n\n"
    t += f"<b>{q}</b>"
    if s["picked"] is not None:
        t += ("\n\n✅ Дуруст! Офарин!" if s["picked"] == right
              else f"\n\n❌ Не. Ҷавоби дуруст: <b>{answers[right]}</b>")
    return t


@router.callback_query(F.data == "g:qs")
async def g_quiz_start(call: CallbackQuery):
    _quiz_new(call.from_user.id)
    await _show(call, _quiz_text(call.from_user.id), _quiz_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:qa:"))
async def g_quiz_answer(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("q")
    if not s:
        return await g_quiz_start(call)
    if s["picked"] is None:
        s["picked"] = int(call.data.split(":")[2])
        if s["picked"] == QUIZ[s["order"][s["i"]]][2]:
            s["score"] += 1
    await _show(call, _quiz_text(uid), _quiz_kb(uid))


@router.callback_query(F.data == "g:qn")
async def g_quiz_next(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("q")
    if not s:
        return await g_quiz_start(call)
    if s["i"] >= len(s["order"]) - 1:
        n, total = s["score"], len(s["order"])
        if n == total:
            verdict = "🏆 Ҳамаашро донистед! Шумо устоди воқеӣ!"
        elif n >= total * 0.7:
            verdict = "🔥 Хеле хуб! Шумо Free Fire-ро нағз медонед."
        elif n >= total * 0.4:
            verdict = "😊 Бад не! Боз кӯшиш кунед."
        else:
            verdict = "😅 Ин дафъа нашуд — боз бозӣ кунед!"
        t = _head(uid, "🏁 <b>Квиз тамом шуд</b>")
        t += f"\nНатиҷаи шумо: <b>{n} аз {total}</b>\n\n{verdict}"
        await _show(call, t, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:qs")], [_BACK]]))
        return
    s["i"] += 1
    s["picked"] = None
    await _show(call, _quiz_text(uid), _quiz_kb(uid))


# ==================== 2) ТИК-ТАК-ТО ====================
_WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
         (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def _winner(b):
    for a, c, d in _WINS:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a], (a, c, d)
    return (None, None)


def _bot_move(b):
    """
    Аввал ғалаба, баъд бастани роҳи мизоҷ, баъд марказ/кунҷ.

    Боти ИДЕАЛӢ дар тик-так-то ҳаргиз намебозад — яъне мизоҷ ҳељ гоҳ
    бурда наметавонад ва бозӣ ба ҷои хушҳолӣ асабоният меорад. Барои
    ҳамин бот баъзан қасдан роҳи мизоҷро НАМЕБАНДАД. Ғалабаи худашро
    бошад ҳамеша мегирад — вагарна беақл ба назар мерасад.
    """
    free = [i for i in range(9) if not b[i]]
    if not free:
        return None
    for who, careful in (("O", 1.0), ("X", 0.65)):   # O = бурдан, X = бастан
        if random.random() > careful:
            continue
        for a, c, d in _WINS:
            line = [b[a], b[c], b[d]]
            if line.count(who) == 2 and line.count("") == 1:
                return (a, c, d)[line.index("")]
    if random.random() < 0.2:
        return random.choice(free)
    for i in [4, 0, 2, 6, 8, 1, 3, 5, 7]:
        if not b[i]:
            return i
    return None


def _ttt_kb(uid: int) -> InlineKeyboardMarkup:
    s = _st(uid)["t"]
    b, hl = s["b"], s.get("hl") or ()
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            ch = b[i] or "·"
            if i in hl:
                ch = f"[{ch}]"
            row.append(InlineKeyboardButton(
                text=ch, callback_data=(f"g:tm:{i}" if not b[i] and not s["over"] else "g:noop")))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:ts")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ttt_text(uid: int) -> str:
    s = _st(uid)["t"]
    t = _head(uid, "⭕️ <b>Тик-так-то бо бот</b>")
    t += f"\nШумо: <b>X</b> · Бот: <b>O</b>\n"
    t += f"🏆 Ҳисоб — шумо {s['win']} : {s['lose']} бот · дуранг {s['draw']}\n\n"
    t += s["msg"]
    return t


@router.callback_query(F.data == "g:ts")
async def g_ttt_start(call: CallbackQuery):
    uid = call.from_user.id
    old = _st(uid).get("t") or {}
    _st(uid)["t"] = {"b": [""] * 9, "over": False, "hl": (), "msg": "Навбати шумост 👇",
                     "win": old.get("win", 0), "lose": old.get("lose", 0),
                     "draw": old.get("draw", 0)}
    await _show(call, _ttt_text(uid), _ttt_kb(uid))


@router.callback_query(F.data.startswith("g:tm:"))
async def g_ttt_move(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("t")
    if not s:
        return await g_ttt_start(call)
    i = int(call.data.split(":")[2])
    if s["over"] or s["b"][i]:
        return await call.answer()
    s["b"][i] = "X"
    w, line = _winner(s["b"])
    if not w and "" in s["b"]:
        j = _bot_move(s["b"])
        if j is not None:
            s["b"][j] = "O"
        w, line = _winner(s["b"])
    if w:
        s["over"], s["hl"] = True, line
        if w == "X":
            s["win"] += 1
            s["msg"] = "🎉 Шумо бурдед! Офарин!"
        else:
            s["lose"] += 1
            s["msg"] = "😅 Бот бурд. Боз кӯшиш кунед!"
    elif "" not in s["b"]:
        s["over"] = True
        s["draw"] += 1
        s["msg"] = "🤝 Дуранг! Ҳељ кас набурд."
    else:
        s["msg"] = "Навбати шумост 👇"
    await _show(call, _ttt_text(uid), _ttt_kb(uid))


# ==================== 3) МИНА (5×5) ====================
_MN, _MINES = 5, 4


def _mines_new(uid: int):
    old = _st(uid).get("m") or {}
    cells = _MN * _MN
    mines = set(random.sample(range(cells), _MINES))
    around = []
    for i in range(cells):
        r, c = divmod(i, _MN)
        n = sum(1 for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                if (dr or dc) and 0 <= r + dr < _MN and 0 <= c + dc < _MN
                and (r + dr) * _MN + (c + dc) in mines)
        around.append(n)
    _st(uid)["m"] = {"mines": mines, "around": around, "open": set(),
                     "over": False, "won": False, "msg": "Катакеро кушоед 👇",
                     "win": old.get("win", 0), "lose": old.get("lose", 0)}


_DIG = ["▫️", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


def _mines_open(s, i):
    """Катакро мекушояд; агар дар атрофаш мина набошад, ҳамсоягонро ҳам."""
    stack, seen = [i], set()
    while stack:
        k = stack.pop()
        if k in seen or k in s["open"]:
            continue
        seen.add(k)
        s["open"].add(k)
        if s["around"][k] == 0:
            r, c = divmod(k, _MN)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if (dr or dc) and 0 <= rr < _MN and 0 <= cc < _MN:
                        j = rr * _MN + cc
                        if j not in s["mines"]:
                            stack.append(j)


def _mines_kb(uid: int) -> InlineKeyboardMarkup:
    s = _st(uid)["m"]
    rows = []
    for r in range(_MN):
        row = []
        for c in range(_MN):
            i = r * _MN + c
            if s["over"] and i in s["mines"]:
                ch, cb = "💥", "g:noop"
            elif i in s["open"]:
                ch, cb = _DIG[s["around"][i]], "g:noop"
            else:
                ch, cb = "⬛️", ("g:noop" if s["over"] else f"g:mo:{i}")
            row.append(InlineKeyboardButton(text=ch, callback_data=cb))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:ms")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mines_text(uid: int) -> str:
    s = _st(uid)["m"]
    left = _MN * _MN - _MINES - len(s["open"])
    t = _head(uid, "💣 <b>Мина 5×5</b>")
    t += f"\n{_MINES} мина пинҳон аст · монд: <b>{max(0, left)}</b> катак\n"
    t += f"🏆 Бурд {s['win']} · бохт {s['lose']}\n\n{s['msg']}"
    return t


@router.callback_query(F.data == "g:ms")
async def g_mines_start(call: CallbackQuery):
    _mines_new(call.from_user.id)
    await _show(call, _mines_text(call.from_user.id), _mines_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:mo:"))
async def g_mines_open(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("m")
    if not s:
        return await g_mines_start(call)
    i = int(call.data.split(":")[2])
    if s["over"] or i in s["open"]:
        return await call.answer()
    if i in s["mines"]:
        s["over"] = True
        s["lose"] += 1
        s["msg"] = "💥 Мина! Ин дафъа нашуд — боз кӯшиш кунед."
    else:
        _mines_open(s, i)
        if len(s["open"]) >= _MN * _MN - _MINES:
            s["over"] = s["won"] = True
            s["win"] += 1
            s["msg"] = "🎉 Ҳамаро кушодед! Офарин!"
        else:
            s["msg"] = "Давом диҳед 👇"
    await _show(call, _mines_text(uid), _mines_kb(uid))


@router.callback_query(F.data == "g:noop")
async def g_noop(call: CallbackQuery):
    await call.answer()


# ==================== 4) 2048 ====================
# Аз ҳама бозии «часпанда»: одам мехоҳад рақами калонтар гирад ва
# сониҳо тез мегузаранд. Мантиқи пурра: ҳаракат, якҷояшавӣ, рақами нав.
def _2048_spawn(b):
    free = [i for i, v in enumerate(b) if not v]
    if free:
        b[random.choice(free)] = 2 if random.random() < 0.9 else 4


def _2048_line(row):
    """Як қаторро ба ЧАП мефишорад ва холҳои ҷамъшударо бармегардонад."""
    vals = [v for v in row if v]
    out, gained, i = [], 0, 0
    while i < len(vals):
        if i + 1 < len(vals) and vals[i] == vals[i + 1]:
            out.append(vals[i] * 2)
            gained += vals[i] * 2
            i += 2
        else:
            out.append(vals[i])
            i += 1
    return out + [0] * (4 - len(out)), gained


def _2048_move(b, d):
    """d: l/r/u/d. Бармегардонад (тахтаи нав, хол, оё чизе ҳаракат кард)."""
    nb, gained = [0] * 16, 0
    for k in range(4):
        if d in "lr":
            row = [b[k * 4 + c] for c in range(4)]
        else:
            row = [b[r * 4 + k] for r in range(4)]
        if d in "rd":
            row = row[::-1]
        row, g = _2048_line(row)
        gained += g
        if d in "rd":
            row = row[::-1]
        for j in range(4):
            if d in "lr":
                nb[k * 4 + j] = row[j]
            else:
                nb[j * 4 + k] = row[j]
    return nb, gained, nb != b


def _2048_new(uid):
    old = _st(uid).get("g2") or {}
    b = [0] * 16
    _2048_spawn(b)
    _2048_spawn(b)
    _st(uid)["g2"] = {"b": b, "score": 0, "best": old.get("best", 0),
                      "over": False, "msg": "Ба кадом тараф фишорем? 👇"}


_TILE = {0: "·", 2: "2", 4: "4", 8: "8", 16: "16", 32: "32", 64: "64",
         128: "128", 256: "256", 512: "512", 1024: "1K", 2048: "2K",
         4096: "4K", 8192: "8K"}


def _2048_kb(uid):
    s = _st(uid)["g2"]
    rows = [[InlineKeyboardButton(text=_TILE.get(s["b"][r * 4 + c], str(s["b"][r * 4 + c])),
                                  callback_data="g:noop") for c in range(4)]
            for r in range(4)]
    if s["over"]:
        rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:2s")])
    else:
        rows.append([InlineKeyboardButton(text="⬅️", callback_data="g:2m:l"),
                     InlineKeyboardButton(text="⬆️", callback_data="g:2m:u"),
                     InlineKeyboardButton(text="⬇️", callback_data="g:2m:d"),
                     InlineKeyboardButton(text="➡️", callback_data="g:2m:r")])
        rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:2s")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _2048_text(uid):
    s = _st(uid)["g2"]
    t = _head(uid, "🔢 <b>2048</b>")
    t += (f"\nХол: <b>{s['score']}</b> · беҳтарин: <b>{s['best']}</b>\n"
          f"Рақами калонтарин: <b>{max(s['b'])}</b>\n\n{s['msg']}")
    return t


@router.callback_query(F.data == "g:2s")
async def g_2048_start(call: CallbackQuery):
    _2048_new(call.from_user.id)
    await _show(call, _2048_text(call.from_user.id), _2048_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:2m:"))
async def g_2048_move(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("g2")
    if not s:
        return await g_2048_start(call)
    if s["over"]:
        return await call.answer()
    nb, gained, moved = _2048_move(s["b"], call.data.split(":")[2])
    if not moved:
        return await call.answer("Ба ин тараф ҷой нест 🙂")
    s["b"], s["score"] = nb, s["score"] + gained
    s["best"] = max(s["best"], s["score"])
    _2048_spawn(s["b"])
    if 2048 in s["b"] and not s.get("won2048"):
        s["won2048"] = True
        s["msg"] = "🏆 2048 сохтед! Офарин! Давом дода метавонед."
    elif 0 not in s["b"] and not any(_2048_move(s["b"], d)[2] for d in "lrud"):
        s["over"] = True
        s["msg"] = f"🏁 Ҷой намонд! Холи ниҳоӣ: {s['score']}"
    else:
        s["msg"] = f"+{gained} хол" if gained else "Давом диҳед 👇"
    await _show(call, _2048_text(uid), _2048_kb(uid))


# ==================== 5) 4 ДАР ЯК ҚАТОР ====================
_C4W, _C4H = 7, 6


def _c4_lines():
    out = []
    for r in range(_C4H):
        for c in range(_C4W):
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = [(r + dr * k, c + dc * k) for k in range(4)]
                if all(0 <= rr < _C4H and 0 <= cc < _C4W for rr, cc in cells):
                    out.append([rr * _C4W + cc for rr, cc in cells])
    return out


_C4LINES = _c4_lines()


def _c4_win(b):
    for ln in _C4LINES:
        v = b[ln[0]]
        if v and all(b[i] == v for i in ln):
            return v, ln
    return None, None


def _c4_drop(b, col):
    for r in range(_C4H - 1, -1, -1):
        if not b[r * _C4W + col]:
            return r * _C4W + col
    return None


def _c4_bot(b):
    """
    Мебарад агар тавонад, роҳи мизоҷро мебандад, вагарна ба марказ.

    Мисли тик-так-то, бот қасдан беайб НЕСТ: баъзан роҳи мизоҷро
    намебандад ва баъзан ҳаракати тасодуфӣ мекунад. Дар санҷиш боти
    беайб 30 аз 30 бозиро бурд — чунин бозӣ мизоҷро танҳо асабӣ мекунад.
    Ғалабаи худашро бошад ҳамеша мегирад.
    """
    free = [c for c in range(_C4W) if _c4_drop(b, c) is not None]
    if not free:
        return None
    for who, careful in (("O", 1.0), ("X", 0.62)):
        if random.random() > careful:
            continue
        for c in free:
            i = _c4_drop(b, c)
            b[i] = who
            w, _ = _c4_win(b)
            b[i] = ""
            if w:
                return c
    if random.random() < 0.25:
        return random.choice(free)
    # Ҷойҳое, ки мизоҷро дар ҳаракати оянда мебаранд, канор мегузорем
    safe = []
    for c in free:
        i = _c4_drop(b, c)
        b[i] = "O"
        j = _c4_drop(b, c)
        risky = False
        if j is not None:
            b[j] = "X"
            risky = _c4_win(b)[0] is not None
            b[j] = ""
        b[i] = ""
        if not risky:
            safe.append(c)
    pool = safe or free
    return min(pool, key=lambda c: abs(c - 3) + random.random())


def _c4_new(uid):
    old = _st(uid).get("c4") or {}
    _st(uid)["c4"] = {"b": [""] * (_C4W * _C4H), "over": False, "hl": (),
                      "msg": "Сутунро интихоб кунед 👇",
                      "win": old.get("win", 0), "lose": old.get("lose", 0),
                      "draw": old.get("draw", 0)}


def _c4_kb(uid):
    s = _st(uid)["c4"]
    b, hl = s["b"], s.get("hl") or ()
    rows = []
    if not s["over"]:
        rows.append([InlineKeyboardButton(
            text="⬇️" if _c4_drop(b, c) is not None else "✖️",
            callback_data=(f"g:4m:{c}" if _c4_drop(b, c) is not None else "g:noop"))
            for c in range(_C4W)])
    for r in range(_C4H):
        row = []
        for c in range(_C4W):
            i = r * _C4W + c
            ch = {"X": "🔴", "O": "🟡", "": "⚫️"}[b[i]]
            if i in hl:
                ch = "✨"
            row.append(InlineKeyboardButton(text=ch, callback_data="g:noop"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:4s")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _c4_text(uid):
    s = _st(uid)["c4"]
    t = _head(uid, "🔴 <b>4 дар як қатор</b>")
    t += (f"\nШумо: 🔴 · Бот: 🟡 — 4-тоашро дар як қатор ҷамъ кунед!\n"
          f"🏆 Шумо {s['win']} : {s['lose']} бот · дуранг {s['draw']}\n\n{s['msg']}")
    return t


@router.callback_query(F.data == "g:4s")
async def g_c4_start(call: CallbackQuery):
    _c4_new(call.from_user.id)
    await _show(call, _c4_text(call.from_user.id), _c4_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:4m:"))
async def g_c4_move(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("c4")
    if not s:
        return await g_c4_start(call)
    if s["over"]:
        return await call.answer()
    col = int(call.data.split(":")[2])
    i = _c4_drop(s["b"], col)
    if i is None:
        return await call.answer("Ин сутун пур аст 🙂")
    s["b"][i] = "X"
    w, line = _c4_win(s["b"])
    if not w and "" in s["b"]:
        c = _c4_bot(s["b"])
        if c is not None:
            s["b"][_c4_drop(s["b"], c)] = "O"
        w, line = _c4_win(s["b"])
    if w:
        s["over"], s["hl"] = True, line
        if w == "X":
            s["win"] += 1
            s["msg"] = "🎉 Шумо бурдед! Офарин!"
        else:
            s["lose"] += 1
            s["msg"] = "😅 Бот бурд. Боз кӯшиш кунед!"
    elif "" not in s["b"]:
        s["over"] = True
        s["draw"] += 1
        s["msg"] = "🤝 Тахта пур шуд — дуранг!"
    else:
        s["msg"] = "Навбати шумост 👇"
    await _show(call, _c4_text(uid), _c4_kb(uid))


# ==================== 6) ПАЗЛИ 15 ====================
def _p15_moves(blank):
    r, c = divmod(blank, 4)
    out = []
    if r > 0:
        out.append(blank - 4)
    if r < 3:
        out.append(blank + 4)
    if c > 0:
        out.append(blank - 1)
    if c < 3:
        out.append(blank + 1)
    return out


def _p15_new(uid):
    old = _st(uid).get("p15") or {}
    b = list(range(1, 16)) + [0]
    blank = 15
    # Аз ҳолати ДУРУСТ ҳаракатҳои тасодуфӣ мекунем — бо ин пазл ҳатман
    # ҳалшаванда мемонад (омехтаи тасодуфии оддӣ метавонад ҳалнашаванда шавад)
    for _ in range(200):
        j = random.choice(_p15_moves(blank))
        b[blank], b[j] = b[j], b[blank]
        blank = j
    _st(uid)["p15"] = {"b": b, "blank": blank, "moves": 0, "over": False,
                       "msg": "Катакеро, ки паҳлӯи ҷои холист, пахш кунед 👇",
                       "best": old.get("best", 0)}


_P15 = ["  ", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣",
        "🔟", "1️⃣1️⃣", "1️⃣2️⃣", "1️⃣3️⃣", "1️⃣4️⃣", "1️⃣5️⃣"]


def _p15_kb(uid):
    s = _st(uid)["p15"]
    ok = set(_p15_moves(s["blank"]))
    rows = []
    for r in range(4):
        row = []
        for c in range(4):
            i = r * 4 + c
            v = s["b"][i]
            row.append(InlineKeyboardButton(
                text=("▫️" if v == 0 else str(v)),
                callback_data=(f"g:pm:{i}" if i in ok and not s["over"] else "g:noop")))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:ps")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _p15_text(uid):
    s = _st(uid)["p15"]
    t = _head(uid, "🧩 <b>Пазли 15</b>")
    best = f" · рекорд: <b>{s['best']}</b>" if s["best"] else ""
    t += (f"\nРақамҳоро аз 1 то 15 ба тартиб гузоред.\n"
          f"Ҳаракат: <b>{s['moves']}</b>{best}\n\n{s['msg']}")
    return t


@router.callback_query(F.data == "g:ps")
async def g_p15_start(call: CallbackQuery):
    _p15_new(call.from_user.id)
    await _show(call, _p15_text(call.from_user.id), _p15_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:pm:"))
async def g_p15_move(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("p15")
    if not s:
        return await g_p15_start(call)
    i = int(call.data.split(":")[2])
    if s["over"] or i not in _p15_moves(s["blank"]):
        return await call.answer()
    s["b"][s["blank"]], s["b"][i] = s["b"][i], s["b"][s["blank"]]
    s["blank"] = i
    s["moves"] += 1
    if s["b"] == list(range(1, 16)) + [0]:
        s["over"] = True
        s["msg"] = f"🎉 Ҳал шуд — {s['moves']} ҳаракат! Офарин!"
        if not s["best"] or s["moves"] < s["best"]:
            s["best"] = s["moves"]
    else:
        s["msg"] = "Давом диҳед 👇"
    await _show(call, _p15_text(uid), _p15_kb(uid))


# ==================== 7) ҶАНГИ БАҲРӢ ====================
# Бозии ҳамафаҳм: ҳама онро дар дафтар бозӣ кардаанд. Флоти душман
# пинҳон аст, шумо тир мезанед — 🔥 расид, 🌊 об.
_BW = 6
_SHIPS = (3, 2, 2, 1, 1)          # дарозии киштиҳо, ҳамагӣ 9 катак


def _bs_place():
    """Киштиҳоро тасодуфӣ мегузорад, ки ба ҳам нарасанд."""
    cells = set()
    for size in _SHIPS:
        for _ in range(200):
            horiz = random.random() < 0.5
            r = random.randrange(_BW if horiz else _BW - size + 1)
            c = random.randrange(_BW - size + 1 if horiz else _BW)
            spot = {(r, c + k) if horiz else (r + k, c) for k in range(size)}
            near = {(rr + dr, cc + dc) for rr, cc in spot
                    for dr in (-1, 0, 1) for dc in (-1, 0, 1)}
            if not (near & cells):
                cells |= spot
                break
    return {r * _BW + c for r, c in cells}


def _bs_new(uid):
    old = _st(uid).get("bs") or {}
    _st(uid)["bs"] = {"ships": _bs_place(), "shots": set(), "over": False,
                      "msg": "Оташ кушоед 👇",
                      "best": old.get("best", 0), "wins": old.get("wins", 0)}


def _bs_kb(uid):
    s = _st(uid)["bs"]
    rows = []
    for r in range(_BW):
        row = []
        for c in range(_BW):
            i = r * _BW + c
            if i in s["shots"]:
                ch, cb = ("🔥" if i in s["ships"] else "🌊"), "g:noop"
            elif s["over"] and i in s["ships"]:
                ch, cb = "🚢", "g:noop"
            else:
                ch, cb = "🟦", ("g:noop" if s["over"] else f"g:bf:{i}")
            row.append(InlineKeyboardButton(text=ch, callback_data=cb))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔄 Аз нав", callback_data="g:bs")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bs_text(uid):
    s = _st(uid)["bs"]
    hit = len(s["shots"] & s["ships"])
    t = _head(uid, "🚢 <b>Ҷанги баҳрӣ</b>")
    best = f" · рекорд: <b>{s['best']} тир</b>" if s["best"] else ""
    t += (f"\nДар баҳр <b>5 киштӣ</b> пинҳон аст (9 катак). Онҳоро ғарқ кунед!\n"
          f"🔥 расид · 🌊 об\n\n"
          f"Ғарқшуда: <b>{hit}/9</b> · тир: <b>{len(s['shots'])}</b>{best}\n"
          f"🏆 Бурд: {s['wins']}\n\n{s['msg']}")
    return t


@router.callback_query(F.data == "g:bs")
async def g_bs_start(call: CallbackQuery):
    _bs_new(call.from_user.id)
    await _show(call, _bs_text(call.from_user.id), _bs_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:bf:"))
async def g_bs_fire(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("bs")
    if not s:
        return await g_bs_start(call)
    i = int(call.data.split(":")[2])
    if s["over"] or i in s["shots"]:
        return await call.answer()
    s["shots"].add(i)
    if i in s["ships"]:
        if len(s["shots"] & s["ships"]) >= len(s["ships"]):
            s["over"] = True
            s["wins"] += 1
            n = len(s["shots"])
            if not s["best"] or n < s["best"]:
                s["best"] = n
            s["msg"] = f"🎉 Ҳамаи киштиҳо ғарқ шуданд — бо {n} тир! Офарин!"
        else:
            s["msg"] = "🔥 Расид! Давом диҳед 👇"
    else:
        s["msg"] = "🌊 Об... боз кӯшиш кунед 👇"
    await _show(call, _bs_text(uid), _bs_kb(uid))


# ==================== 8) КАЛИМАРО ЁБ ====================
# «Виселица» — ҳама медонад: ҳарф интихоб мекунед, агар дар калима
# бошад кушода мешавад, вагарна як ҷон кам мешавад.
WORDS = [
    ("АЛМОС", "пули дохилии бозӣ"), ("ДОНАТ", "пур кардани бозӣ"),
    ("ЧЕК", "расми пардохт"), ("БАЛАНС", "пули шумо дар бот"),
    ("ТУҲФА", "чизи ройгон"), ("ФАРМОИШ", "чизе, ки шумо мехаред"),
    ("ГАРЕНА", "ширкати Free Fire"), ("СНАЙПЕР", "силоҳи дурзан"),
    ("ХАРИТА", "ҷои бозӣ"), ("ПАРАШУТ", "бо он мефуроед"),
    ("МАШИНА", "бо он мегардед"), ("ТИРАНДОЗ", "касе, ки тир мезанад"),
    ("ДӮСТ", "ҳамроҳи шумо дар бозӣ"), ("ҒАЛАБА", "Booyah!"),
    ("ЗИРЕҲ", "шуморо аз тир нигоҳ медорад"), ("ТЕЛЕФОН", "бо он бозӣ мекунед"),
    ("МАҒОЗА", "ҷои харид"), ("СОМОНӢ", "пули Тоҷикистон"),
]
_ALPHA = list("АБВГҒДЕЁЖЗИӢЙКҚЛМНОПРСТУӮФХҲЧҶШЪЭЮЯ")
_LIVES = 6


def _wd_new(uid):
    old = _st(uid).get("wd") or {}
    word, hint = random.choice(WORDS)
    _st(uid)["wd"] = {"word": word, "hint": hint, "used": set(), "bad": 0,
                      "over": False, "msg": "Ҳарфро интихоб кунед 👇",
                      "win": old.get("win", 0), "lose": old.get("lose", 0)}


def _wd_shown(s):
    return " ".join(ch if ch in s["used"] or ch == " " else "_" for ch in s["word"])


def _wd_kb(uid):
    s = _st(uid)["wd"]
    rows = []
    if not s["over"]:
        for r in range(0, len(_ALPHA), 7):
            rows.append([InlineKeyboardButton(
                text=("·" if ch in s["used"] else ch),
                callback_data=("g:noop" if ch in s["used"] else f"g:wl:{ch}"))
                for ch in _ALPHA[r:r + 7]])
    rows.append([InlineKeyboardButton(text="🔄 Калимаи нав", callback_data="g:ws")])
    rows.append([_BACK])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _wd_text(uid):
    s = _st(uid)["wd"]
    t = _head(uid, "🔤 <b>Калимаро ёб</b>")
    t += (f"\nМаслиҳат: <i>{s['hint']}</i>\n\n"
          f"<code>{_wd_shown(s)}</code>\n\n"
          f"{'❤️' * (_LIVES - s['bad'])}{'🖤' * s['bad']}\n"
          f"🏆 Бурд {s['win']} · бохт {s['lose']}\n\n{s['msg']}")
    return t


@router.callback_query(F.data == "g:ws")
async def g_wd_start(call: CallbackQuery):
    _wd_new(call.from_user.id)
    await _show(call, _wd_text(call.from_user.id), _wd_kb(call.from_user.id))


@router.callback_query(F.data.startswith("g:wl:"))
async def g_wd_letter(call: CallbackQuery):
    uid = call.from_user.id
    s = _st(uid).get("wd")
    if not s:
        return await g_wd_start(call)
    ch = call.data.split(":")[2]
    if s["over"] or ch in s["used"]:
        return await call.answer()
    s["used"].add(ch)
    if ch in s["word"]:
        if all(c in s["used"] for c in s["word"] if c != " "):
            s["over"] = True
            s["win"] += 1
            s["msg"] = f"🎉 Ёфтед — «{s['word']}»! Офарин!"
        else:
            s["msg"] = "✅ Ҳаст! Давом диҳед 👇"
    else:
        s["bad"] += 1
        if s["bad"] >= _LIVES:
            s["over"] = True
            s["lose"] += 1
            s["used"] |= set(s["word"])
            s["msg"] = f"😅 Ҷонҳо тамом шуд. Калима ин буд: «{s['word']}»"
        else:
            s["msg"] = f"❌ Ин ҳарф нест. Ҷон монд: {_LIVES - s['bad']}"
    await _show(call, _wd_text(uid), _wd_kb(uid))
