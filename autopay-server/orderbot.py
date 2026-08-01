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
import logging
import random
import re
import time
from datetime import datetime, timedelta

import aiomysql
from aiogram import Bot, Dispatcher
from aiogram.types import Message

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


@dp.business_message()
async def handle_business_message(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    sender = message.from_user.full_name or str(user_id)
    text = message.text or message.caption or ""
    logger.info(f"[IN] chat={chat_id} user={user_id} bcid={message.business_connection_id!r} text={text!r}")

    await _log_to_channel(message, user_id, sender, text)

    if user_id == NOTIFY_CHAT_ID:
        # Ин паёми ХУДИ соҳиб аст (шумо аз app-и худ ба мизоҷ навиштед).
        # Telegram Business API ин паёмҳоро ҳам ҳамчун business_message
        # мефиристад (барои синхронизатсия) — бот НАБОЯД ба паёми худи
        # соҳиб ҷавоб гардонад, вагарна ду "овоз" дар як чат пайдо мешавад.
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


@dp.business_connection()
async def handle_business_connection(event):
    logger.info(f"Business connection: id={event.id} user={event.user.id} is_enabled={event.is_enabled}")


async def main():
    await create_pool()
    me = await bot.get_me()
    logger.info(f"✅ orderbot омода аст! @{me.username}")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(nightly_report_loop())
    await dp.start_polling(
        bot,
        allowed_updates=["business_connection", "business_message", "edited_business_message"],
    )


if __name__ == "__main__":
    asyncio.run(main())
