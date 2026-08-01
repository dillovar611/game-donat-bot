# -*- coding: utf-8 -*-
"""
games.py — бозиҳои хурд барои вақти интизории мизоҷ.

Вақте мизоҷ чекро мефиристад, то тасдиқ шудани пардохт баъзан 10-15
дақиқа мегузарад. Дар ин муддат ӯ танҳо ба экран нигоҳ мекунад ва
асабӣ мешавад — ҳамин ҷо се бозии хурд пешниҳод мешавад.

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
        [InlineKeyboardButton(text="🧠 Квизи Free Fire", callback_data="g:qs")],
        [InlineKeyboardButton(text="⭕️ Тик-так-то бо бот", callback_data="g:ts")],
        [InlineKeyboardButton(text="💣 Мина (5×5)", callback_data="g:ms")],
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
