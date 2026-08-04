# -*- coding: utf-8 -*-
"""
supportbot.py — боти алоқа бо мизоҷон.

Мизоҷ ба бот менависад → паёмаш ба соҳиб мерасад.
Соҳиб ба ҳамон паём REPLY мекунад → ҷавоб ба мизоҷ мерасад.

Мизоҷон ҳамдигарро НАМЕБИНАНД ва ба ҳам навишта НАМЕТАВОНАНД —
ҳар кас танҳо бо соҳиб гап мезанад.

МУҲИМ (амният): ин бот ба база ва ба калидҳои FazerCards ҲЕҶ дастрасӣ
НАДОРАД. Ҳатто агар касе онро вайрон кунад, ба пули воқеӣ роҳ намеёбад.

Аз боти кӯҳна се фарқи муҳим дорад:
  1. Ҷадвали «кадом паём аз кадом мизоҷ» дар ФАЙЛ нигоҳ дошта мешавад —
     баъди рестарти сервер ҳам ҷавоб додан ба паёмҳои кӯҳна кор мекунад.
  2. Ҳар сӯҳбат ба ҳамон бойгонии `chats/` навишта мешавад, ки orderbot
     истифода мебарад — яъне /find ва /backup инҳоро ҳам мебинанд.
  3. Ҳимоя аз спам: агар касе дар як дақиқа паёми зиёд фиристад, бот
     хомӯш мемонад ва соҳибро озор намедиҳад.

Иҷро (протсеси АЛОҲИДА): python3 supportbot.py
"""
import asyncio
import json
import logging
import os
import time
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import chatlog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Калидҳо дар supportbot_config.py (дар СЕРВЕР, берун аз git)
import supportbot_config as cfg

TOKEN = cfg.TOKEN
OWNER_ID = cfg.OWNER_ID
SHOP_BOT = getattr(cfg, "SHOP_BOT_USERNAME", "@DILOVARFFBOT")

_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_PATH = os.path.join(_DIR, "support_map.json")
BLOCK_PATH = os.path.join(_DIR, "support_blocked.json")

# Ҳимоя аз спам
RATE_WINDOW = 60          # дар чанд сония
RATE_LIMIT = 8            # чанд паём иҷозат аст
MAP_MAX = 5000            # чанд паёми охиринро дар ҷадвал нигоҳ дорем

WELCOME = (
    "👋 <b>Салом! Ин чати алоқа бо мағоза аст.</b>\n\n"
    "Саволатонро ҳамин ҷо нависед — ман онро мебинам ва ҷавоб медиҳам.\n"
    "Расм, овоз ё видео ҳам фиристода метавонед.\n\n"
    f"🛒 Барои ХАРИД бошад, ба боти мағоза равед: {SHOP_BOT}"
)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# {message_id дар чати соҳиб: user_id-и мизоҷ}
_map: dict = {}
_blocked: set = set()
_rate: dict = defaultdict(list)


# ==================== ЗАХИРА ====================
def _load():
    global _map, _blocked
    try:
        if os.path.isfile(MAP_PATH):
            with open(MAP_PATH, encoding="utf-8") as f:
                _map = {int(k): int(v) for k, v in json.load(f).items()}
    except Exception as e:
        logger.error(f"support_map хонда нашуд: {e}")
        _map = {}
    try:
        if os.path.isfile(BLOCK_PATH):
            with open(BLOCK_PATH, encoding="utf-8") as f:
                _blocked = {int(x) for x in json.load(f)}
    except Exception as e:
        logger.error(f"support_blocked хонда нашуд: {e}")
        _blocked = set()
    logger.info(f"Ҷадвал: {len(_map)} паём, басташуда: {len(_blocked)}")


def _save_map():
    """Ҷадвалро нигоҳ медорад — то баъди рестарт ҷавоб додан кор кунад."""
    try:
        if len(_map) > MAP_MAX:
            for k in sorted(_map)[:len(_map) - MAP_MAX]:
                _map.pop(k, None)
        with open(MAP_PATH, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in _map.items()}, f)
    except Exception as e:
        logger.error(f"support_map навишта нашуд: {e}")


def _save_blocked():
    try:
        with open(BLOCK_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(_blocked), f)
    except Exception as e:
        logger.error(f"support_blocked навишта нашуд: {e}")


def _spamming(uid: int) -> bool:
    now = time.time()
    arr = _rate[uid]
    arr[:] = [t for t in arr if now - t < RATE_WINDOW]
    arr.append(now)
    return len(arr) > RATE_LIMIT


# ==================== ФАРМОНҲОИ СОҲИБ ====================
@dp.message(Command("start"), F.from_user.id != OWNER_ID)
async def start_client(message: Message):
    await message.answer(WELCOME, parse_mode="HTML")


@dp.message(Command("help", "start"), F.from_user.id == OWNER_ID)
async def owner_help(message: Message):
    await message.answer(
        "💬 <b>Боти алоқа</b>\n\n"
        "Паёми ҳар мизоҷ ба ин ҷо меояд. Барои ҷавоб додан — ба ҳамон "
        "паём <b>REPLY</b> кунед, ҷавобатон ба ӯ мерасад.\n\n"
        "<b>Фармонҳо:</b>\n"
        "/block — ҳангоми REPLY: ин мизоҷро мебандад\n"
        "/unblock 123456 — мекушояд\n"
        "/blocked — рӯйхати басташудагон\n\n"
        "ℹ️ Ҳар сӯҳбат дар бойгонии сервер сабт мешавад.",
        parse_mode="HTML")


@dp.message(Command("blocked"), F.from_user.id == OWNER_ID)
async def owner_blocked(message: Message):
    if not _blocked:
        await message.answer("✅ Ҳељ кас баста нашудааст.")
        return
    await message.answer("🚫 Басташудагон:\n" +
                         "\n".join(f"• <code>{u}</code>  /unblock {u}"
                                   for u in sorted(_blocked)),
                         parse_mode="HTML")


@dp.message(Command("unblock"), F.from_user.id == OWNER_ID)
async def owner_unblock(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Нависед: /unblock 123456789")
        return
    _blocked.discard(int(parts[1]))
    _save_blocked()
    await message.answer(f"✅ <code>{parts[1]}</code> кушода шуд.", parse_mode="HTML")


@dp.message(Command("block"), F.from_user.id == OWNER_ID)
async def owner_block(message: Message):
    uid = _map.get(message.reply_to_message.message_id) if message.reply_to_message else None
    if not uid:
        await message.answer("Ба паёми ҳамон мизоҷ REPLY карда, /block нависед.")
        return
    _blocked.add(uid)
    _save_blocked()
    await message.answer(f"🚫 <code>{uid}</code> баста шуд — паёмҳояш дигар намеоянд.",
                         parse_mode="HTML")


# ==================== ҶАВОБИ СОҲИБ ====================
@dp.message(F.from_user.id == OWNER_ID, F.reply_to_message)
async def owner_reply(message: Message):
    uid = _map.get(message.reply_to_message.message_id)
    if not uid:
        await message.reply(
            "🤔 Ин паём дар ҷадвал нест — намедонам ба КӢ фиристам.\n"
            "Ба худи паёми мизоҷ REPLY кунед.")
        return
    try:
        # copy_to ҳар навъро мефиристад: матн, расм, овоз, видео, файл
        await message.copy_to(uid)
    except Exception as e:
        logger.error(f"Ҷавоб ба {uid} нарасид: {e}")
        await message.reply(
            f"❌ Нарасид: {e}\n\n"
            f"Эҳтимол мизоҷ ботро баста ё нест кардааст.")
        return
    await message.reply("✅ Ҷавоб фиристода шуд.")
    await chatlog.record(bot, message, uid, "owner",
                         message.from_user.full_name or "Мо", src="sup")


# ==================== ПАЁМИ МИЗОҶ ====================
@dp.message(F.from_user.id != OWNER_ID)
async def from_client(message: Message):
    uid = message.from_user.id
    if uid in _blocked:
        return
    if _spamming(uid):
        logger.info(f"[SPAM] {uid} — паёмҳои зиёд, четак карда шуд")
        return

    name = message.from_user.full_name or str(uid)
    uname = f"@{message.from_user.username}" if message.from_user.username else "—"

    ids = []
    try:
        fwd = await message.forward(OWNER_ID)
        ids.append(fwd.message_id)
    except Exception:
        # Мизоҷ дар танзимот форвардро баста — нусхаашро мефиристем
        try:
            cp = await message.copy_to(OWNER_ID)
            ids.append(cp.message_id)
        except Exception as e:
            logger.error(f"Паёми {uid} ба соҳиб нарасид: {e}")
            return
    try:
        info = await bot.send_message(
            OWNER_ID,
            f"👆 Паём аз: <b>{name}</b> ({uname})\n"
            f"ID: <code>{uid}</code>\n\n"
            f"Барои ҷавоб додан, ба паёми боло <b>REPLY</b> кунед.",
            parse_mode="HTML")
        ids.append(info.message_id)
    except Exception as e:
        logger.error(f"Маълумоти мизоҷ нарасид: {e}")

    for mid in ids:
        _map[mid] = uid
    _save_map()

    await chatlog.record(bot, message, uid, "client", name,
                         message.from_user.username or "", src="sup")


async def main():
    _load()
    me = await bot.get_me()
    logger.info(f"✅ supportbot омода аст! @{me.username}")
    logger.info(f"📁 Бойгонӣ: {chatlog.BASE}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
