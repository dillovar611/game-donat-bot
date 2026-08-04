"""
orderbot.py — Боти АЛОҲИДА барои "Автоматизация чатов"-и Telegram Business,
ки ба ҳисоби ШАХСИИ соҳиб пайваст мешавад.

МУҲИМ (амният): ин бот ҳељ калиди FazerCards/MooGold НАДОРАД ва фармоиш
НАМЕСОЗАД — танҳо ба таври ХОНДАНӢ (SELECT) ба ҷадвали orders дар ҳамон
база нигоҳ мекунад, бо ҳисоби МАҲДУДИ MySQL (танҳо SELECT). Агар ин бот
хатогӣ дошта бошад ё касе кӯшиши сӯиистифода кунад, ба боти асосӣ ва
пули воқеӣ ҳељ дастрасӣ надорад.

Иҷро (протсеси АЛОҲИДА, новобаста аз bot.py): python3 orderbot.py
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta

import aiomysql
from aiogram import Bot, Dispatcher, F
from aiogram.types import BusinessMessagesDeleted, FSInputFile, Message

import chatlog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== ТАНЗИМОТ ====================
# Калидҳо дар orderbot_config.py (дар СЕРВЕР, берун аз git — мисли config.py
# асосӣ) — то токен/пароли база ҳељ гоҳ ба GitHub нарасад
import orderbot_config as cfg

TOKEN = cfg.TOKEN
DB_HOST = cfg.DB_HOST
DB_PORT = cfg.DB_PORT
DB_USER = cfg.DB_USER          # ҳисоби МАҲДУД — танҳо SELECT ба orders
DB_PASSWORD = cfg.DB_PASSWORD
DB_NAME = cfg.DB_NAME
NOTIFY_CHAT_ID = cfg.NOTIFY_CHAT_ID   # ID-и шахсии соҳиб — паёми оддии бот (на business)

SHOP_BOT_USERNAME = "@DILOVARFFBOT"

# Калимаҳое, ки агар дар паёми мизоҷ пайдо шаванд, ба соҳиб фавран
# огоҳинома мефиристем (то ин ҳолатро худи бот "ҳал" накунад, балки
# шумо шахсан бинед). Рӯйхатро дар ҳар вақт васеъ карда метавонед.
SUSPICIOUS_WORDS = [
    "фиреб", "фирефт", "дузд", "кидал", "обман", "кинул", "развод",
    "шикоят", "жалоб", "полиц", "милиц", "прокурор", "суд ме",
    "чарг", "chargeback", "верни", "баргардон пул", "деньги назад",
    "мошенник", "scam", "фрод", "fraud", "блокир", "бан кардед",
    "адвокат", "юрист", "иск",
]

# ==================== МАТНҲО ====================
NOT_FOUND = "🤔❌ Чунин рақами фармоиш дар ҳисоби шумо ёфт нашуд... Лутфан рақамро дуруст санҷед (мисол: #17600) 🔍"
GOT_PHOTO_NO_NUMBER = "📸✅ Расмро гирифтам, раҳмат! Лутфан рақами фармоишро ҳам ҳамчун матн нависед (мисол: #17600), то фавран санҷам 🔍"

_ORDER_RE = re.compile(r"#(\d{5,7})\b")  # # ҲАТМӢ, ҳадди ақал 5 рақам (аз #10000 боло) — то рақами телефон/дигар рақами тасодуфӣ хато нагирад

# Агар мизоҷ рақами фармоиш нанависад, вале хоҳиши донистани "фармоиши
# охирин"-и худро нишон диҳад (масалан "фармоишам чи шуд", "фармоиш кай
# меояд"), охирин фармоиши ҳамон корбарро аз база меёбем ва ҷавоб медиҳем
def _is_last_order_query(text: str) -> bool:
    lower = text.lower()
    if "фармоиш" not in lower:
        return False
    triggers = ("охирин", "чи шуд", "чӣ шуд", "кай", "куҷо", "куҷост", "ҳолат", "холат", "хабар")
    return any(t in lower for t in triggers)


# Агар мизоҷ танҳо ташаккур/офарин гӯяд, бот бо як ҷумлаи кӯтоҳ ҷавоб
# гардонад, на ин ки хомӯш монад ё дубора GREETING-и пурраро такрор кунад
ACK_WORDS = (
    "раҳмат", "рахмат", "рахмататон", "раҳмататон", "ташаккур", "ташакур",
    "спасибо", "мерси", "мамнун", "миннатдор",
    "ok", "окей", "оке", "окай",
    "хуб шуд", "хубай", "хубя", "хуб-хуб",
    "зур", "олиҷаноб", "офарин", "класс", "супер", "afarin", "rahmat",
    "tashakur", "thanks", "thank you",
)
ACK_REPLIES = (
    "🙏😊 Хуш омадед!",
    "❤️🙏 Ҳамеша дар хизмататон!",
    "😊✅ Хурсандем, ки кӯмак карда тавонистем!",
)


def _is_ack_message(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped or len(stripped) > 40:
        return False
    return any(w in stripped for w in ACK_WORDS)


# Агар мизоҷ танҳо салом гӯяд (бе рақами фармоиш ё саволи дигар), як
# ҷумлаи КӮТОҲ мефиристем — то бидонад чӣ гуна рақами фармоишро санҷад.
# (На GREETING-и пурраи кӯҳна, ки барои ҲАР паёми номаълум такрор мешуд.)
GREETING_WORDS = (
    "салом", "ассалом", "ассалому", "саломалейкум", "салам",
    "здравств", "привет", "прив",
    "hi", "hello", "hey",
)
SHORT_GREETING_REPLY = (
    "👋 Салом! Барои санҷидани фармоиш рақамашро нависед (мисол: #17600) 🔍"
)


def _is_greeting_message(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped or len(stripped) > 40:
        return False
    return any(w in stripped for w in GREETING_WORDS)


# Агар мизоҷ бе рақами фармоиш чанд паёми паси ҳам нависад (на салом, на
# ташаккур, на саволи каталог), бот ҳар дафъа ХОМӮШ мемонад — вале ҳар
# 3-юмин паёми чунин, як ёдоварии КӮТОҲ мефиристад, то мизоҷ фаромӯш
# накунад, ки рақами фармоиш лозим аст (бе он ки ҳар паёмро халал расонад)
_unmatched_msg_count: dict[int, int] = {}
ORDER_NUMBER_NUDGE = (
    "🔍 Агар дар бораи фармоиш пурсида истода бошед, лутфан рақамашро "
    "нависед (мисол: #17600), то фавран санҷам."
)


# Агар мизоҷ бидуни рақами фармоиш дар бораи нарх/маҳсулот пурсад, ба ҷои
# GREETING-и умумӣ мустақим ба боти дӯкон равона мекунем
CATALOG_WORDS = (
    "нарх", "прайс", "нархнома", "чанд сом", "чанд пул",
    "маҳсулот", "махсулот", "чи доред", "чӣ доред", "мол доред",
    "алмос доред", "чи хел харид", "чӣ хел харид",
)


def _is_catalog_query(text: str) -> bool:
    lower = text.lower()
    return any(w in lower for w in CATALOG_WORDS)

# Ҳимоя аз "тахминзанӣ" — агар як корбар дар муддати кӯтоҳ бисёр рақами
# ГУНОГУНИ ношиносро санҷад (эҳтимоли кӯшиши ёфтани фармоиши каси дигар),
# ба соҳиб огоҳинома иловагӣ мефиристем
RATE_WINDOW_SEC = 5 * 60
RATE_THRESHOLD = 4
_recent_failed_queries: dict[int, list] = {}

# Барои ҳисоботи шабона ба соҳиб — ҳар шаб соати 23:00 бо занг мешавад
_stats = {"messages": 0, "orders_checked": 0, "alerts": 0}


def _flag_failed_query(user_id: int) -> bool:
    now = time.time()
    arr = _recent_failed_queries.setdefault(user_id, [])
    arr.append(now)
    arr[:] = [t for t in arr if now - t < RATE_WINDOW_SEC]
    return len(arr) == RATE_THRESHOLD  # маҳз як бор дар лаҳзаи расидан ба остона

bot = Bot(token=TOKEN)
dp = Dispatcher()
pool: aiomysql.Pool | None = None


async def create_pool():
    global pool
    pool = await aiomysql.create_pool(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        db=DB_NAME, autocommit=True, minsize=1, maxsize=3,
    )


async def get_order_by_id(order_id: int):
    """Фармоишро БЕ филтри соҳиб мехонад — то дар handle_business_message
    фаҳмем фармоиш умуман ВУҶУД ДОРАД, вале ба КОРБАРИ ДИГАР тааллуқ дорад
    (ин ҳолати шубҳанокро аз "рақами тамоман нодуруст" фарқ мекунад)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, user_id, status, label, price, reject_reason FROM orders WHERE id=%s",
                (order_id,),
            )
            return await cur.fetchone()


async def get_last_order_by_user(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, user_id, status, label, price, reject_reason FROM orders "
                "WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            return await cur.fetchone()


# ==================== ПАЙГИРИИ ФАРМОИШ ====================
# Вақте мизоҷ дар бораи фармоише мепурсад, мо ҳолати ҳозираашро дар ёд
# мегирем. Агар он ҳолат БАЪДТАР иваз шавад, худамон ба ӯ хабар медиҳем
# — то ӯ маҷбур нашавад ҳар чанд дақиқа боз пурсад.
#
# Дар ФАЙЛ нигоҳ дошта мешавад, на дар хотира: ин бот ба база НАВИСТА
# НАМЕТАВОНАД (ҳисоби SELECT-ӣ), ва баъди рестарти сервер пайгирӣ набояд
# гум шавад.
WATCH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "watch_orders.json")
WATCH_MAX_HOURS = 24        # аз ин дертар пайгириро бас мекунем
WATCH_INTERVAL = 60         # ҳар чанд сония ҳолатҳоро месанҷем
WATCH_START_DELAY = 20      # то пурра сар шудани бот сабр мекунем

# Ҳолатҳое, ки дигар иваз намешаванд — баъди онҳо пайгирӣ маъно надорад
_FINAL = {"confirmed", "rejected", "expired", "archived"}

_watch: dict = {}           # "chat:order" -> {chat, order, status, bcid, ts}


def _watch_load():
    global _watch
    try:
        if os.path.isfile(WATCH_PATH):
            with open(WATCH_PATH, encoding="utf-8") as f:
                _watch = json.load(f)
    except Exception as e:
        logger.error(f"watch_orders хонда нашуд: {e}")
        _watch = {}
    logger.info(f"Пайгирии фармоиш: {len(_watch)} дона")


def _watch_save():
    try:
        with open(WATCH_PATH, "w", encoding="utf-8") as f:
            json.dump(_watch, f)
    except Exception as e:
        logger.error(f"watch_orders навишта нашуд: {e}")


def _watch_add(chat_id: int, order: dict, bcid: str):
    """Фармоишро ба пайгирӣ мегузорад (агар ҳолаташ ҳанӯз иваз шуданӣ бошад)."""
    if order.get("status") in _FINAL:
        return
    _watch[f"{chat_id}:{order['id']}"] = {
        "chat": chat_id, "order": order["id"],
        "status": order.get("status"), "bcid": bcid, "ts": time.time(),
    }
    _watch_save()


async def watch_loop():
    """Ҳар дақиқа фармоишҳои пайгиришавандаро месанҷад."""
    await asyncio.sleep(WATCH_START_DELAY)
    while True:
        await asyncio.sleep(WATCH_INTERVAL)
        if not _watch:
            continue
        changed = False
        for key, w in list(_watch.items()):
            try:
                if time.time() - w.get("ts", 0) > WATCH_MAX_HOURS * 3600:
                    _watch.pop(key, None)
                    changed = True
                    continue
                order = await get_order_by_id(w["order"])
                if not order or order["status"] == w["status"]:
                    continue
                # Ҳолат ИВАЗ шуд — ба мизоҷ хабар медиҳем
                try:
                    await bot.send_message(
                        w["chat"],
                        "🔔 Хабари фармоиши шумо:\n\n" + _status_text(order),
                        business_connection_id=w.get("bcid") or None,
                    )
                    _stats["messages"] += 1
                except Exception as e:
                    logger.error(f"Хабари ҳолат ба {w['chat']} нарасид: {e}")
                    _watch.pop(key, None)
                    changed = True
                    continue
                if order["status"] in _FINAL:
                    _watch.pop(key, None)
                else:
                    w["status"] = order["status"]
                changed = True
            except Exception as e:
                logger.error(f"watch_loop хато ({key}): {e}")
        if changed:
            _watch_save()


async def notify_owner(text: str):
    _stats["alerts"] += 1
    try:
        await bot.send_message(NOTIFY_CHAT_ID, text)
    except Exception as e:
        logger.error(f"notify_owner хато: {e}")


async def nightly_report_loop():
    """Ҳар шаб соати 23:00 ба соҳиб омори кӯтоҳи имрӯзаро мефиристад."""
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await nightly_snapshot()
        except Exception as e:
            logger.error(f"nightly_snapshot хато: {e}")
        stats = dict(_stats)
        _stats["messages"] = 0
        _stats["orders_checked"] = 0
        _stats["alerts"] = 0
        try:
            await bot.send_message(
                NOTIFY_CHAT_ID,
                f"📊 Ҳисоботи имрӯзаи orderbot:\n"
                f"💬 Паёмҳои ҷавобдодашуда: {stats['messages']}\n"
                f"🔍 Фармоишҳои санҷидашуда: {stats['orders_checked']}\n"
                f"⚠️ Огоҳиномаҳо: {stats['alerts']}",
            )
        except Exception as e:
            logger.error(f"nightly_report хато: {e}")


def _find_suspicious_word(text: str) -> str | None:
    lower = text.lower()
    for w in SUSPICIOUS_WORDS:
        if w in lower:
            return w
    return None


def _order_header(order: dict) -> str:
    label = (order.get("label") or "").strip()
    price = order.get("price")
    parts = [f"🆔 #{order['id']}"]
    if label:
        parts.append(f"— {label}")
    if price is not None:
        try:
            parts.append(f"({float(price):.2f} сом)")
        except (TypeError, ValueError):
            pass
    return " ".join(parts)


def _status_text(order: dict) -> str:
    order_id = order["id"]
    status = order.get("status")
    header = _order_header(order)

    if status in ("pending", "awaiting_autopay"):
        body = (
            f"⏳😅 Ҳанӯз пардохт нашудагӣ менамояд. "
            f"Агар пардохт кардаед — ташвиш накашед, system баъзан каме дер мекунад 🙏💳"
        )
    elif status in ("autopay_search", "paid"):
        body = (
            f"🔍✅ Пардохти шумо гирифта шуд, ҳозир санҷида истодаем! "
            f"Каме сабр — зуд тайёр мешавад ⚡💎"
        )
    elif status == "donating":
        body = "🚀🔥 Ҳозир иҷро шуда истодааст! Як-ду дақиқа сабр — алмазҳо роҳанд 💎✨"
    elif status == "confirmed":
        body = "🎉🎊 Тасдиқ шуд, алмазҳо фиристода шуданд! ✅💎 Раҳмат барои харид 🙏❤️"
    elif status == "rejected":
        reason = (order.get("reject_reason") or "").strip()
        reason_line = f"\n📝 Сабаб: {reason}" if reason else ""
        body = f"😔⚠️ Мутаассифона рад шудааст.{reason_line}\nСавол дошта бошед — ҳамин ҷо бинависед 💬👇"
    elif status == "failed":
        body = (
            f"😅🔧 Каме мушкили техникӣ дучор шуд, вале ХАВОТИР НАШАВЕД — "
            f"мо аллакай хабардорем ва зуд ҳал мекунем! ⚡🙏"
        )
    elif status == "expired":
        body = "⌛ Мӯҳлаташ гузаштааст."
    elif status == "archived":
        # Фармоиши кӯҳна, ки дар бот пӯшида нашуда буд ва ба архив гузашт.
        # Ба мизоҷ "archived" гуфтан маъное надорад — оддӣ мефаҳмонем.
        body = (
            "📁 Ин фармоиши кӯҳна аллакай баста шудааст.\n"
            "Агар масъалае ҳал нашуда бошад — ҳамин ҷо нависед, "
            "мо месанҷем 🙏💬"
        )
    else:
        body = f"ℹ️ Ҳолат: {status}"

    return f"{header}\n{body}"


async def _log_to_channel(message: Message, user_id: int, sender: str, text: str):
    """
    Нусхаи ҲАР паёми чати business (аз ду тараф — мизоҷ ва соҳиб) ба
    каналчаи хусусии сабт мефиристад — то агар мизоҷ баъдтар паёмашро
    аз Telegram нест кунад ҳам, нусхаи он дар канал боқӣ монад. Ин бот
    ба база НАВИСТА НАМЕТАВОНАД (ҳисоби МАҲДУДИ SELECT-ӣ), бинобар ин
    сабт танҳо тавассути Telegram-и худ (канал) сурат мегирад.
    """
    log_channel_id = getattr(cfg, "LOG_CHANNEL_ID", None)
    if not log_channel_id:
        return
    who = "🧑‍💼 Шумо" if user_id == NOTIFY_CHAT_ID else f"👤 {sender}"
    header = f"{who} (ID: <code>{user_id}</code>)"
    try:
        if message.photo:
            await bot.send_photo(
                log_channel_id, message.photo[-1].file_id,
                caption=f"{header}\n{text}" if text else header,
                parse_mode="HTML"
            )
        elif text:
            await bot.send_message(log_channel_id, f"{header}:\n{text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"[LOG-CHANNEL] нашуд: {e}")


async def _owner_order_lookup(message: Message, chat_id: int, text: str):
    """
    Соҳиб дар чат бо мизоҷ рақами фармоиш навишт — ҳолаташро ҳамин ҷо
    нишон медиҳем, то ба боти асосӣ рафтан лозим нашавад.

    Агар фармоиш ба ҲАМИН мизоҷ тааллуқ дошта бошад, ҷавоб дар чат
    менависем — мизоҷ ҳам мебинад ва ин фоиданок аст.

    Агар ба каси ДИГАР тааллуқ дошта бошад, дар чат ЧИЗЕ намекушоем
    (вагарна маълумоти мизоҷи дигар ба ин мизоҷ ошкор мешавад) —
    ҷавобро ба чати шахсии соҳиб мефиристем.
    """
    ids = {int(m.group(1)) for m in _ORDER_RE.finditer(text or "")}
    if not ids or len(ids) > 3:
        return
    for order_id in sorted(ids):
        try:
            order = await get_order_by_id(order_id)
        except Exception as e:
            logger.error(f"[OWNER-LOOKUP] #{order_id}: {e}")
            continue
        if not order:
            await notify_owner(f"🔍 Фармоиши #{order_id} дар база нест.")
            continue
        if order["user_id"] == chat_id:
            await message.answer(_status_text(order))
            _watch_add(chat_id, order, message.business_connection_id)
        else:
            await notify_owner(
                f"🔍 Фармоиши #{order_id}\n\n{_status_text(order)}\n\n"
                f"⚠️ Ин фармоиш ба мизоҷи ДИГАР (ID: {order['user_id']}) "
                f"тааллуқ дорад — барои ҳамин дар чат нанавиштам."
            )


@dp.business_message()
async def handle_business_message(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    sender = message.from_user.full_name or str(user_id)
    text = message.text or message.caption or ""
    logger.info(f"[IN] chat={chat_id} user={user_id} bcid={message.business_connection_id!r} text={text!r}")

    await _log_to_channel(message, user_id, sender, text)
    # Бойгонии сервер: ХОМӮШОНА сабт мешавад — ҳељ огоҳӣ ба соҳиб намеравад.
    # Соҳиб танҳо ҳангоми несткунӣ/ислоҳи паём хабар мегирад (поёнтар).
    await chatlog.record(
        bot, message, chat_id,
        "owner" if user_id == NOTIFY_CHAT_ID else "client",
        sender, message.from_user.username or "",
    )

    if user_id == NOTIFY_CHAT_ID:
        # Ин паёми ХУДИ соҳиб аст (шумо аз app-и худ ба мизоҷ навиштед).
        # Telegram Business API ин паёмҳоро ҳам ҳамчун business_message
        # мефиристад (барои синхронизатсия) — бот НАБОЯД ба паёми худи
        # соҳиб ҷавоб гардонад, вагарна ду "овоз" дар як чат пайдо мешавад.
        # ИСТИСНО: агар СОҲИБ худаш рақами фармоиш нависад, бот ҳолаташро
        # ҲАМИН ҶО нишон медиҳад — то соҳиб маҷбур нашавад ба боти асосӣ
        # равад. Ин ягона ҳолатест, ки бот ба паёми соҳиб ҷавоб медиҳад.
        await _owner_order_lookup(message, chat_id, text)
        logger.info(f"[SKIP-OWN] chat={chat_id} — паёми худи соҳиб, четак карда шуд")
        return

    if message.forward_origin is not None:
        # Паёми ФОРВАРДШУДА (масалан реклама/чат дигар) — дар дохилаш
        # метавонанд рақамҳои тасодуфӣ бошанд (вақт, шумора ва ғ.), ки
        # ҳамчун рақами фармоиш хато шинохта мешаванд. Ин гуна паёмро
        # тамоман нодида мегирем, то бот спам-ҷавоб нафиристад.
        logger.info(f"[SKIP-FORWARD] chat={chat_id} — паёми форвардшуда, четак карда шуд")
        return

    _stats["messages"] += 1

    try:
        # Калимаҳои шубҳанок — новобаста аз он ки рақами фармоиш ҳаст ё не,
        # ба соҳиб огоҳинома мефиристем (бо матни пурраи паём)
        sw = _find_suspicious_word(text)
        if sw:
            await notify_owner(
                f"⚠️ Калимаи шубҳанок дар чати шахсӣ!\n\n"
                f"👤 {sender} (ID: {user_id})\n"
                f"🔑 Калима: «{sw}»\n"
                f"💬 Матн: {text[:500]}"
            )

        order_ids = []
        seen_ids = set()
        for mm in _ORDER_RE.finditer(text):
            oid = int(mm.group(1))
            if oid not in seen_ids:
                seen_ids.add(oid)
                order_ids.append(oid)
        logger.info(f"[MATCH] text={text!r} -> {order_ids}")

        if len(order_ids) > 3:
            # Эҳтимоли зиёд, ки ин матн умуман рақами фармоиш нест (масалан
            # матни дигар бо бисёр рақами тасодуфӣ) — барои пешгирии спам-и
            # чандин "ёфт нашуд" паём, тамоман нодида мегирем
            logger.info(f"[SKIP-TOO-MANY] chat={chat_id} count={len(order_ids)}")
            return

        if order_ids:
            replies = []
            flagged = False
            for order_id in order_ids:
                _stats["orders_checked"] += 1
                try:
                    order = await get_order_by_id(order_id)
                except Exception as e:
                    logger.error(f"[DB-ERROR] order_id={order_id}: {e}")
                    replies.append(f"😅 #{order_id} — мушкили хурди техникӣ, баъдтар кӯшиш кунед 🙏")
                    continue
                logger.info(f"[ORDER] id={order_id} found={order is not None}")
                if order and order["user_id"] == user_id:
                    replies.append(_status_text(order))
                    _watch_add(chat_id, order, message.business_connection_id)
                elif order:
                    # Фармоиш ҳаст, вале ба ИН корбар тааллуқ надорад — мизоҷ
                    # ҳамон "ёфт нашуд"-ро мебинад (то маълумоти каси дигар
                    # ошкор нашавад), вале соҳиб огоҳ мешавад
                    replies.append(NOT_FOUND)
                    await notify_owner(
                        f"⚠️ Касе фармоиши #{order_id}-ро санҷид, ки ба ӯ тааллуқ НАДОРАД!\n\n"
                        f"👤 Пурсанда: {sender} (ID: {user_id})\n"
                        f"🆔 Ин фармоиш воқеан ба корбари дигар (ID: {order['user_id']}) тааллуқ дорад."
                    )
                    if _flag_failed_query(user_id):
                        flagged = True
                else:
                    replies.append(NOT_FOUND)
                    if _flag_failed_query(user_id):
                        flagged = True

            if flagged:
                await notify_owner(
                    f"⚠️ Корбар {sender} (ID: {user_id}) дар 5 дақиқаи охир {RATE_THRESHOLD}+ "
                    f"рақами ГУНОГУНИ ношиносро санҷид — эҳтимоли кӯшиши тахминзанӣ!"
                )

            await message.answer("\n\n".join(replies))
            return

        if _is_last_order_query(text):
            _stats["orders_checked"] += 1
            try:
                order = await get_last_order_by_user(user_id)
            except Exception as e:
                logger.error(f"[DB-ERROR] last-order user={user_id}: {e}")
                await message.answer("😅 Мушкили хурди техникӣ, баъдтар кӯшиш кунед 🙏")
                return
            if order:
                await message.answer(_status_text(order))
                _watch_add(chat_id, order, message.business_connection_id)
            else:
                await message.answer(
                    "🤔 Ягон фармоиши қаблии шумо ёфт нашуд. "
                    "Лутфан рақами фармоишро нависед (мисол: #17600) 🔍"
                )
            return

        if _is_ack_message(text):
            await message.answer(random.choice(ACK_REPLIES))
            return

        if _is_catalog_query(text):
            await message.answer(
                f"💎🛍 Барои нарх ва маҳсулот, лутфан ба {SHOP_BOT_USERNAME} равед — "
                f"ҳамаи маълумот дар он ҷост, фавран мебинед! 😊"
            )
            return

        if _is_greeting_message(text):
            await message.answer(SHORT_GREETING_REPLY)
            return

        if message.photo:
            await message.answer(GOT_PHOTO_NO_NUMBER)
            return

        # Дигар паёмҳо (сӯҳбати оддии мизоҷ бо соҳиб) — бот одатан хомӯш
        # мемонад, то соҳиб худаш ҷавоб диҳад; фақат ҳар 3-юмин чунин паём
        # ёдоварии кӯтоҳи рақами фармоишро мефиристад
        _unmatched_msg_count[chat_id] = _unmatched_msg_count.get(chat_id, 0) + 1
        if _unmatched_msg_count[chat_id] % 3 == 0:
            await message.answer(ORDER_NUMBER_NUDGE)
    except Exception as e:
        logger.error(f"[FATAL] handle_business_message хато: {e}", exc_info=True)


def _short(t: str, n: int = 400) -> str:
    t = (t or "").strip()
    return (t[:n] + "…") if len(t) > n else (t or "—")


@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    """
    Мизоҷ паёмашро ИСЛОҲ кард. Матни аввала дар бойгонӣ мемонад ва ба
    соҳиб ҳам матни кӯҳна, ҳам матни нав фиристода мешавад.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    new_text = message.text or message.caption or ""
    old_text = chatlog.record_edit(chat_id, message.message_id, new_text)
    if user_id == NOTIFY_CHAT_ID:
        return                     # ислоҳи ХУДИ соҳиб — огоҳӣ лозим нест
    sender = message.from_user.full_name or str(user_id)
    await notify_owner(
        f"✏️ Мизоҷ паёмашро ИСЛОҲ кард!\n\n"
        f"👤 {sender} (ID: {user_id})\n\n"
        f"Пештар навишта буд:\n«{_short(old_text)}»\n\n"
        f"Ҳоло шуд:\n«{_short(new_text)}»\n\n"
        f"📁 Матни аввала дар бойгонӣ боқӣ монд."
    )


@dp.deleted_business_messages()
async def handle_deleted_business_messages(event: BusinessMessagesDeleted):
    """
    Мизоҷ паём(ҳо)-ро НЕСТ кард. Telegram танҳо рақами паёмро медиҳад,
    матнашро не — вале мо онро аз бойгонии худамон бармегардонем.
    """
    chat_id = event.chat.id
    rows = chatlog.record_delete(chat_id, list(event.message_ids or []))
    rows = [r for r in rows if r["who"] != "owner"]   # несткунии худи соҳиб не
    if not rows:
        return
    name = (event.chat.full_name or event.chat.first_name
            or event.chat.username or str(chat_id))
    parts = []
    for r in rows:
        if not r["known"]:
            parts.append("• (ин паём пеш аз оғози бойгонӣ фиристода шуда буд — "
                         "матнаш дар даст нест)")
        elif r["text"] and r["media"]:
            parts.append(f"• {r['media']} + «{_short(r['text'], 300)}»")
        elif r["text"]:
            parts.append(f"• «{_short(r['text'], 300)}»")
        elif r["media"]:
            parts.append(f"• {r['media']} (бе матн) — худи файл дар бойгонӣ ҳаст")
        else:
            parts.append("• (паёми бе матн)")
    known = sum(1 for r in rows if r["known"])
    tail = (f"\n\n📁 Дар бойгонии сервер боқӣ монд — /chat {chat_id}"
            if known else "")
    await notify_owner(
        f"❌ Мизоҷ {len(rows)} паёмашро НЕСТ кард!\n\n"
        f"👤 {name} (ID: {chat_id})\n\n"
        f"Матни несткардашуда:\n" + "\n".join(parts) + tail
    )


# ==================== ФАРМОНҲОИ СОҲИБ (чати шахсӣ бо ҳамин бот) ====================
HELP = (
    "📁 <b>Бойгонии сӯҳбатҳо</b>\n\n"
    "Ҳар сӯҳбат бо мизоҷ дар сервер захира мешавад: матн, расмҳо, "
    "инчунин паёмҳои несткарда ва ислоҳшуда.\n\n"
    "<b>Фармонҳо:</b>\n"
    "/chats — рӯйхати ҳамаи мизоҷон\n"
    "/chat 5961814932 — скриншот ва файли сӯҳбати ҳамон мизоҷ\n"
    "/find дузд — ҷустуҷӯи калима дар ҲАМАИ сӯҳбатҳо (ҳатто дар "
    "паёмҳои несткарда)\n"
    "/backup — нусхаи ҳамаи бойгонӣ ҳамчун як файл\n\n"
    "ℹ️ Ҳангоми несткунӣ ё ислоҳи паём аз ҷониби мизоҷ, ман фавран "
    "худам ба шумо хабар медиҳам."
)


@dp.message(F.text.startswith("/chats"))
async def cmd_chats(message: Message):
    if message.from_user.id != NOTIFY_CHAT_ID:
        return
    rows = chatlog.list_chats()
    if not rows:
        await message.answer("📭 Ҳанӯз ягон сӯҳбат сабт нашудааст.")
        return
    lines = [f"📁 <b>Сӯҳбатҳои сабтшуда: {len(rows)}</b>\n"]
    for r in rows[:40]:
        uname = f" @{r['username']}" if r["username"] else ""
        marks = ""
        if r["deleted"]:
            marks += f" ❌{r['deleted']}"
        if r["edited"]:
            marks += f" ✏️{r['edited']}"
        when = datetime.fromtimestamp(r["last_seen"]).strftime("%d.%m %H:%M") if r["last_seen"] else "—"
        lines.append(f"👤 {r['name']}{uname}\n   💬 {r['count']} паём{marks} · {when}\n"
                     f"   /chat {r['user_id']}")
    if len(rows) > 40:
        lines.append(f"\n… ва боз {len(rows) - 40} мизоҷи дигар")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(F.text.startswith("/chat"))
async def cmd_chat(message: Message):
    if message.from_user.id != NOTIFY_CHAT_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Нависед: /chat 5961814932\n(рӯйхат: /chats)")
        return
    uid = int(parts[1])
    thread = chatlog.build_thread(uid)
    if not thread:
        await message.answer("📭 Барои ин мизоҷ ягон паём сабт нашудааст.")
        return
    await message.answer(f"⏳ Скриншоти {len(thread)} паём тайёр шуда истодааст...")
    try:
        shots = await asyncio.to_thread(chatlog.render, uid)
        txt = await asyncio.to_thread(chatlog.dump_text, uid)
    except Exception as e:
        logger.error(f"cmd_chat хато ({uid}): {e}", exc_info=True)
        await message.answer(f"❌ Нашуд: {e}")
        return
    for p in shots:
        try:
            await message.answer_photo(FSInputFile(p))
        except Exception as e:
            logger.error(f"скриншот нарафт ({p}): {e}")
    try:
        await message.answer_document(
            FSInputFile(txt), caption="📄 Ҳамон сӯҳбат ҳамчун матн (бо эмоҷӣ)")
    except Exception as e:
        logger.error(f"файли матн нарафт: {e}")

    # Овоз/видео/файлҳо — дар скриншот дида намешаванд, пас алоҳида
    # мефиристем. Вагарна паёми овозии мизоҷ танҳо дар сервер мемонад.
    media = [m for m in thread if m.get("fpath")]
    if media:
        d = os.path.join(chatlog.BASE, str(uid), "media")
        await message.answer(f"🎤 Боз {len(media)} файл (овоз/видео) дар ин сӯҳбат:")
        for m in media[-10:]:
            p = os.path.join(d, m["fpath"])
            if not os.path.isfile(p):
                continue
            when = datetime.fromtimestamp(m["ts"]).strftime("%d.%m %H:%M")
            who = "мизоҷ" if m["who"] == "client" else "мо"
            cap = f"{m['file']} · {who} · {when}"
            if m["deleted"]:
                cap += "\n❌ ИН ПАЁМ НЕСТ КАРДА ШУД"
            try:
                await message.answer_document(FSInputFile(p), caption=cap)
            except Exception as e:
                logger.error(f"файли {p} нарафт: {e}")


@dp.message(F.text.startswith("/find"))
async def cmd_find(message: Message):
    if message.from_user.id != NOTIFY_CHAT_ID:
        return
    q = (message.text or "")[len("/find"):].strip()
    if len(q) < 2:
        await message.answer("Нависед: /find дузд\nё: /find 29738")
        return
    hits = await asyncio.to_thread(chatlog.search, q)
    if not hits:
        await message.answer(f"🔍 «{q}» дар ягон сӯҳбат ёфт нашуд.")
        return
    lines = [f"🔍 <b>«{q}» — {len(hits)} ҷой ёфт шуд</b>\n"]
    for h in hits[:25]:
        when = datetime.fromtimestamp(h["ts"]).strftime("%d.%m.%Y %H:%M")
        who = h["name"] if h["who"] == "client" else "Мо"
        flag = ""
        if h["deleted"]:
            flag = " ❌ НЕСТ КАРДА ШУД"
        elif h["edited"]:
            flag = " ✏️ ислоҳ шуд"
        body = h["text"].replace("\n", " ")
        lines.append(f"👤 <b>{who}</b> · {when}{flag}\n"
                     f"   «{_short(body, 200)}»\n"
                     f"   /chat {h['user_id']}")
    hidden = sum(1 for h in hits if h["only_in_archive"])
    if hidden:
        lines.append(f"\n❗️ Аз инҳо <b>{hidden}</b>-тоаш дар худи Telegram "
                     f"дигар НЕСТ — танҳо дар бойгонии мо боқӣ мондааст.")
    if len(hits) > 25:
        lines.append(f"\n… ва боз {len(hits) - 25} ҷои дигар")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(F.text.startswith("/backup"))
async def cmd_backup(message: Message):
    if message.from_user.id != NOTIFY_CHAT_ID:
        return
    await message.answer("⏳ Архиви бойгонӣ тайёр шуда истодааст...")
    await send_backup(manual=True)


# ДИҚҚАТ: ин handler ҲАМА чизро мегирад, пас бояд ОХИРИН бошад —
# вагарна фармонҳои поёнтар сабтшуда ҳаргиз кор намекунанд.
@dp.message()
async def cmd_other(message: Message):
    if message.from_user.id != NOTIFY_CHAT_ID:
        # Ба каси бегона ҷавоб намедиҳем, вале ба лог менависем — вагарна
        # ҳангоми санҷиш маълум намешавад, ки чаро бот хомӯш монд
        logger.info(f"[NOT-OWNER] паём аз {message.from_user.id} "
                    f"({message.from_user.full_name}) — NOTIFY_CHAT_ID={NOTIFY_CHAT_ID}")
        return
    await message.answer(HELP, parse_mode="HTML")


async def send_backup(manual: bool = False):
    """Ҳамаи бойгониро ҳамчун ZIP ба соҳиб мефиристад."""
    try:
        path = await asyncio.to_thread(chatlog.make_zip)
    except Exception as e:
        logger.error(f"send_backup: ZIP нашуд: {e}")
        path = ""
    if not path:
        if manual:
            await bot.send_message(NOTIFY_CHAT_ID, "📭 Ҳанӯз ягон сӯҳбат сабт нашудааст.")
        return
    mb = os.path.getsize(path) / (1024 * 1024)
    n = len(chatlog.list_chats())
    try:
        await bot.send_document(
            NOTIFY_CHAT_ID, FSInputFile(path),
            caption=(f"📦 <b>Нусхаи бойгонии сӯҳбатҳо</b>\n\n"
                     f"👥 {n} мизоҷ · 💾 {mb:.1f} МБ\n"
                     f"🗓 {datetime.now():%d.%m.%Y}\n\n"
                     f"Ин файлро нигоҳ доред — агар сервер аз кор монад, "
                     f"ҳамаи сӯҳбатҳо дар ҳамин ҷо боқӣ мемонанд."),
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"send_backup: файл нарафт: {e}")
        if manual:
            await bot.send_message(NOTIFY_CHAT_ID, f"❌ Архив фиристода нашуд: {e}")
    finally:
        try:
            os.remove(path)      # дар сервер ҷой нагирад — нусхааш дар Telegram аст
        except Exception:
            pass


async def weekly_backup_loop():
    """Ҳар якшанбе соати 23:30 нусхаи бойгониро ба соҳиб мефиристад."""
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=30, second=0, microsecond=0)
        # 6 = якшанбе
        days = (6 - now.weekday()) % 7
        target += timedelta(days=days)
        if target <= now:
            target += timedelta(days=7)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await send_backup()
        except Exception as e:
            logger.error(f"weekly_backup хато: {e}")


async def nightly_snapshot():
    """
    Ҳар шаб скриншоти ҳамаи сӯҳбатҳоро месозад ва дар папкаи ҳар мизоҷ
    захира мекунад. ХОМӮШОНА — ҳељ чиз ба соҳиб фиристода намешавад
    (вагарна ҳар шаб даҳҳо расм меомад). Ҳар вақт хоҳед: /chat <id>.
    """
    stamp = datetime.now().strftime("%Y-%m-%d")
    made = 0
    for r in chatlog.list_chats():
        try:
            if await asyncio.to_thread(chatlog.render, r["user_id"], stamp):
                await asyncio.to_thread(chatlog.dump_text, r["user_id"])
                made += 1
        except Exception as e:
            logger.error(f"nightly_snapshot ({r['user_id']}): {e}")
    logger.info(f"nightly_snapshot: {made} сӯҳбат захира шуд")


@dp.business_connection()
async def handle_business_connection(event):
    logger.info(f"Business connection: id={event.id} user={event.user.id} is_enabled={event.is_enabled}")


async def main():
    await create_pool()
    me = await bot.get_me()
    logger.info(f"✅ orderbot омода аст! @{me.username}")
    await bot.delete_webhook(drop_pending_updates=True)
    _watch_load()
    asyncio.create_task(nightly_report_loop())
    asyncio.create_task(weekly_backup_loop())
    asyncio.create_task(watch_loop())
    logger.info(f"📁 Бойгонии сӯҳбатҳо: {chatlog.BASE}")
    await dp.start_polling(
        bot,
        allowed_updates=["business_connection", "business_message",
                         "edited_business_message", "deleted_business_messages",
                         "message"],
    )


if __name__ == "__main__":
    asyncio.run(main())
