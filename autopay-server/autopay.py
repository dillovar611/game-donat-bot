"""
autopay.py — Автотасдиқи пардохти "Душанбе Сити" тавассути notification-ҳои
DC Next, ки барномаи Android ба гурӯҳи махсуси Telegram мефиристад.

Тартиб:
  1. buy.py ҳангоми интихоби "Душанбе Сити" фармоиши 'awaiting_autopay'
     месозад бо нархи каме фарқкунанда (масалан 46.03 ба ҷои 46.00).
  2. Барномаи Android notification-и "Зачисление"-и DC Next-ро ба гурӯҳи
     махсус (тавассути боти дигар — notifier) мефиристад.
  3. Ин ҳандлер матнро мехонад, Summa ва Kod-ро мебарорад, бо фармоиши
     'awaiting_autopay'-и мувофиқ муқоиса мекунад — агар ёфт ва Kod
     пештар истифода нашуда бошад → автотасдиқ + автодонат.

МУҲИМ (танзими Telegram, пеш аз истифода):
  - Бояд КАНАЛИ ХУСУСӢ бошад (на гурӯҳ!) ва ҲАРДУ бот админи он:
    боти notifier бо ҳуқуқи "Post messages", боти асосӣ — админи оддӣ.
    Сабаб: дар ГУРӮҲҳо Telegram ба ботҳо паёмҳои ботҳои дигарро
    намедиҳад (маҳдудияти расмӣ), аммо дар КАНАЛҳо ботҳои админ ҳамаи
    паёмҳоро (channel_post) мегиранд — ҳатто аз ботҳои дигар.
"""
import asyncio
import logging
import re

from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database as db
import ff_api

logger = logging.getLogger(__name__)
router = Router()

_SUMMA_RE = re.compile(r"Summa\s+([\d.,]+)\s*TJS", re.IGNORECASE)
_KOD_RE = re.compile(r"Kod\s+(\d+)", re.IGNORECASE)

MAX_AGE_MINUTES = 15  # мӯҳлати интизории пардохт (бояд бо buy.py мувофиқ бошад)


def _parse_notification(text: str):
    """
    Summa ва Kod-ро аз матни хоми notification мебарорад.
    Агар матн "Снятие" (баровардани пул, на воридот) бошад, ё Summa/Kod
    ёфт нашавад — None бармегардонад.
    """
    if not text:
        return None
    if "снятие" in text.lower():
        return None
    m_summa = _SUMMA_RE.search(text)
    m_kod = _KOD_RE.search(text)
    if not m_summa or not m_kod:
        return None
    try:
        summa = round(float(m_summa.group(1).replace(",", ".")), 2)
    except ValueError:
        return None
    return summa, m_kod.group(1)


@router.channel_post(F.chat.id == config.NOTIFIER_CHAT_ID)
@router.message(F.chat.id == config.NOTIFIER_CHAT_ID)
async def handle_dc_notification(message: Message):
    text = message.text or message.caption or ""
    parsed = _parse_notification(text)
    if not parsed:
        return  # на "Зачисление", ё формат нашинос — нодида мегирем
    summa, kod = parsed

    if await db.is_kod_seen(kod):
        logger.info(f"Autopay: Kod {kod} такрорист — нодида гирифта шуд")
        return
    await db.record_kod(kod, summa)

    order = await db.find_awaiting_order_by_price(summa, "dushanbe_city", MAX_AGE_MINUTES)
    if not order:
        await _notify_admins_unmatched(message.bot, summa, kod)
        return

    await _confirm_and_donate(message.bot, order, kod)


async def _notify_admins_unmatched(bot: Bot, summa: float, kod: str):
    text = (
        f"⚠️ <b>Пардохти ношинос</b>\n\n"
        f"💵 Маблағ: {summa:.2f} TJS\n"
        f"🔑 Kod: <code>{kod}</code>\n\n"
        f"Ба ягон фармоиши дар интизории автопардохт мувофиқат накард "
        f"(шояд мизоҷ маблағи галат фиристода бошад, ё вақташ гузашта бошад)."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии пардохти ношинос ба {admin_id} нарасид: {e}")


async def _confirm_and_donate(bot: Bot, order: dict, kod: str):
    order_id = order["id"]
    await db.mark_kod_matched(kod, order_id)
    await db.update_order_status(order_id, "paid")

    success, api_order_id = await ff_api.auto_donate(
        order["game_id"], order["offer_id"], order.get("api_order_id") or ""
    )
    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)

        try:
            reward, referrer_id = await db.credit_referral_for_order(order_id)
            if reward and referrer_id:
                await bot.send_message(
                    referrer_id,
                    f"🤝 <b>Мукофоти реферралӣ!</b>\n\n"
                    f"💰 Дусти шумо фармоиш дод ва шумо <b>{reward:.2f} сом</b> "
                    f"ба балансаи реферралии худ гирифтед!",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"credit_referral хато барои #{order_id}: {e}")

        try:
            await bot.send_message(
                order["user_id"],
                f"✅ <b>Пардохт худкор тасдиқ шуд! Алмазҳо фиристода шуданд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")],
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нарасид (#{order_id}): {e}")

        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚡ <b>Автотасдиқ шуд!</b>\n\n"
                    f"🆔 Фармоиш: #{order_id}\n"
                    f"👤 <code>{order['user_id']}</code>\n"
                    f"🎁 {order['label']} → <code>{order['game_id']}</code>\n"
                    f"💵 {float(order['price']):.2f} сом\n"
                    f"🔑 Kod: <code>{kod}</code>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Хабари автотасдиқ ба админ {admin_id} нарасид: {e}")
    else:
        await db.update_order_status(order_id, "failed")
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ <b>Пардохт омад, аммо донат худкор нашуд!</b>\n\n"
                    f"🆔 Фармоиш: #{order_id}\n"
                    f"👤 <code>{order['user_id']}</code>\n"
                    f"🎁 {order['label']} → <code>{order['game_id']}</code>\n"
                    f"🔑 Kod: <code>{kod}</code>\n\n"
                    f"Лутфан аз Панели Админ дастӣ тафтиш/тасдиқ кунед.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Огоҳии хатои донат ба админ {admin_id} нарасид: {e}")


async def expiry_loop(bot: Bot, interval_seconds: int = 120, max_age_minutes: int = MAX_AGE_MINUTES):
    """Ҳар 2 дақиқа фармоишҳои 'awaiting_autopay'-и мӯҳлаташ гузаштаро мебандад."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            stale = await db.expire_stale_awaiting_orders(max_age_minutes)
            for order in stale:
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"⏳ <b>Вақти пардохт барои фармоиши #{order['id']} гузашт.</b>\n\n"
                        f"Агар пардохт карда бошед, бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}\n"
                        f"Вагарна метавонед фармоиши навро аз нав созед.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Хабари мӯҳлатгузашта ба {order['user_id']} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар autopay.expiry_loop: {e}")
