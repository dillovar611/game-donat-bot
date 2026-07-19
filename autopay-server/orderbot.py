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
import re
import time

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

SHOP_BOT_USERNAME = "@DILOVARFFBOT"

# ==================== МАТНҲО ====================
GREETING = (
    "😊👋 САЛОМ АЛЕЙКУМ! Ман ёрдамчии автоматии Диловар ҳастам 🤖💎\n"
    "Барои санҷидани фармоиш рақамашро нависед (мисол: #6506) 🔍✍️ ман фавран мегӯям! ⚡\n\n"
    f"Барои харид ё саволи дигар: {SHOP_BOT_USERNAME} 💎"
)
NOT_FOUND = "🤔❌ Чунин рақами фармоиш дар ҳисоби шумо ёфт нашуд... Лутфан рақамро дуруст санҷед (мисол: #6506) 🔍"
GOT_PHOTO_NO_NUMBER = "📸✅ Расмро гирифтам, раҳмат! Лутфан рақами фармоишро ҳам ҳамчун матн нависед (мисол: #6506), то фавран санҷам 🔍"

PROMPT_THROTTLE_SEC = 150  # ~2.5 дақ — то дар ҷавоби "ало","ало","ало" такрор нашавад
_last_prompt_at: dict[int, float] = {}

_ORDER_RE = re.compile(r"#?(\d{2,7})\b")

bot = Bot(token=TOKEN)
dp = Dispatcher()
pool: aiomysql.Pool | None = None


async def create_pool():
    global pool
    pool = await aiomysql.create_pool(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        db=DB_NAME, autocommit=True, minsize=1, maxsize=3,
    )


async def get_order_for_user(order_id: int, user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, status, label, price, reject_reason FROM orders WHERE id=%s AND user_id=%s",
                (order_id, user_id),
            )
            return await cur.fetchone()


def _status_text(order: dict) -> str:
    order_id = order["id"]
    status = order.get("status")

    if status in ("pending", "awaiting_autopay"):
        return (
            f"⏳😅 Фармоиши #{order_id} ҳанӯз пардохт нашудагӣ менамояд. "
            f"Агар пардохт кардаед — ташвиш накашед, system баъзан каме дер мекунад 🙏💳"
        )
    if status in ("autopay_search", "paid"):
        return (
            f"🔍✅ Фармоиши #{order_id} — пардохти шумо гирифта шуд, ҳозир санҷида истодаем! "
            f"Каме сабр — зуд тайёр мешавад ⚡💎"
        )
    if status == "donating":
        return f"🚀🔥 Фармоиши #{order_id} ҳозир иҷро шуда истодааст! Як-ду дақиқа сабр — алмазҳо роҳанд 💎✨"
    if status == "confirmed":
        return f"🎉🎊 Фармоиши #{order_id} — тасдиқ шуд, алмазҳо фиристода шуданд! ✅💎 Раҳмат барои харид 🙏❤️"
    if status == "rejected":
        reason = (order.get("reject_reason") or "").strip()
        reason_line = f"\n📝 Сабаб: {reason}" if reason else ""
        return f"😔⚠️ Мутаассифона фармоиши #{order_id} рад шудааст.{reason_line}\nСавол дошта бошед — ҳамин ҷо бинависед 💬👇"
    if status == "failed":
        return (
            f"😅🔧 Фармоиши #{order_id} каме мушкили техникӣ дучор шуд, вале ХАВОТИР НАШАВЕД — "
            f"мо аллакай хабардорем ва зуд ҳал мекунем! ⚡🙏"
        )
    if status == "expired":
        return f"⌛ Фармоиши #{order_id} мӯҳлаташ гузаштааст."
    return f"ℹ️ Фармоиши #{order_id} — ҳолат: {status}"


@dp.business_message()
async def handle_business_message(message: Message):
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        text = message.text or message.caption or ""

        m = _ORDER_RE.search(text)
        if m:
            order_id = int(m.group(1))
            order = await get_order_for_user(order_id, user_id)
            if order:
                await message.answer(_status_text(order))
            else:
                await message.answer(NOT_FOUND)
            return

        if message.photo:
            await message.answer(GOT_PHOTO_NO_NUMBER)
            return

        now = time.time()
        last = _last_prompt_at.get(chat_id, 0)
        if now - last < PROMPT_THROTTLE_SEC:
            return  # хомӯш — ба ин чат наздик буд, ки хоҳиш кардем
        _last_prompt_at[chat_id] = now
        await message.answer(GREETING)
    except Exception as e:
        logger.error(f"handle_business_message хато: {e}")


@dp.business_connection()
async def handle_business_connection(event):
    logger.info(f"Business connection: id={event.id} user={event.user.id} is_enabled={event.is_enabled}")


async def main():
    await create_pool()
    me = await bot.get_me()
    logger.info(f"✅ orderbot омода аст! @{me.username}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        allowed_updates=["business_connection", "business_message", "edited_business_message"],
    )


if __name__ == "__main__":
    asyncio.run(main())
