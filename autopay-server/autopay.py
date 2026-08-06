"""
autopay.py — Автотасдиқи пардохти "Душанбе Сити" тавассути notification-ҳои
DC Next, ки барномаи Android ба канали махсуси Telegram мефиристад.

Раванд:
  1. Мизоҷ "Душанбе Сити"-ро интихоб мекунад → фармоиш бо нархи нодир
     (масалан 9.03 ба ҷои 9.00) ва статуси 'awaiting_autopay' сохта мешавад.
  2. Мизоҷ пулро мефиристад ва РАСМИ ЧЕКРО ба бот мефиристад (ҳатмӣ —
     зидди сӯиистифода) → статус 'autopay_search' мешавад.
  3. Барномаи телефон notification-и DC Next-ро ба канал мефиристад.
  4. Ин модул Summa+Kod-ро мехонад, фармоиши мувофиқро меёбад ва ба
     НАВБАТИ автодонат мегузорад — мизоҷ дар ҳар марҳила хабар мегирад.
  5. Агар то 10 дақиқа пардохт ёфта нашавад — чек ба админ барои
     тафтиши дастӣ фиристода мешавад (бо тугмаҳои Тасдиқ/Рад).

МУҲИМ (танзими Telegram):
  - Бояд КАНАЛИ ХУСУСӢ бошад (на гурӯҳ!) ва ҲАРДУ бот админи он.
    Сабаб: дар гурӯҳҳо Telegram ба ботҳо паёмҳои ботҳои дигарро намедиҳад.
"""
import asyncio
import html
import json
import os
import random
import time
import unicodedata
import logging
from datetime import datetime
import re

from aiogram import Router, F, Bot
from aiogram.types import (Message, InlineKeyboardMarkup, InlineKeyboardButton,
                           FSInputFile)

import config
import database as db
import ff_api

logger = logging.getLogger(__name__)
router = Router()

_SUMMA_RE = re.compile(r"Summa\s+([\d.,]+)\s*TJS", re.IGNORECASE)
_KOD_RE = re.compile(r"Kod\s+(\d+)", re.IGNORECASE)
# Коменти пардохт: мо ба линк c=card_8848 мегузорем, DC онро ҳамчун
# "card§8848" (ё card_8848) дар notification нишон медиҳад
_CARD_RE = re.compile(r"card\D{0,3}(\d{1,10})", re.IGNORECASE)

MAX_AGE_MINUTES = 20      # мӯҳлати умумии фармоиши автопардохт
SEARCH_TIMEOUT_MIN = 10   # чек омад, вале пардохт то ин дақиқа ёфт нашуд → ба админ
EXPIRY_WARN_BEFORE_MIN = 3  # чанд дақиқа пеш аз итмоми мӯҳлат огоҳ кунем
FEED_QUIET_MIN = 30       # чанд дақиқа бе ягон пардохт — аломати мушкил
FEED_MIN_WAITING = 2      # ва ҳадди ақал чанд мизоҷ бояд интизор бошад
NUDGE_AFTER_HOURS = 3     # баъди чанд соат ба фармоиши нотамом ёдоварӣ кунем
NUDGE_UNTIL_HOURS = 24    # аз ин кӯҳнатар бошад, дигар ёдоварӣ намекунем

# Навбати автодонат — то 4 донат ҳамзамон иҷро мешаванд (пеш танҳо 1,
# ки дар соати пик боиси интизории беҳуда мешуд; бехатарии зидди
# дукаратшавӣ аз claim_order_for_donate/claim_paid_order_for_autodonate
# (атомикӣ дар база) меояд, на аз ин семафор — пас мувозисозӣ бехатар аст)
_DONATE_CONCURRENCY = 4
_donate_semaphore = asyncio.Semaphore(_DONATE_CONCURRENCY)
_queue_count = 0  # чанд фармоиш ҳоло дар навбат/кор аст

# Монеаи иловагӣ (дар хотираи барнома, на база) — зидди он ки run_donate
# ду бор ҳамзамон барои ҲАМОН фармоиш сар шавад (пеш аз он ки дархости
# claim_order_for_donate ба база расад). Хеле тезтар аз DB-claim, пас
# race-ро дар ҳамон лаҳза мебандад.
_in_flight_orders: set[int] = set()

# Огоҳии худкори "эҳтимол проблемаи шабака" — агар якчанд фармоиш паси
# ҳам бо сабаби таймаути шабака (uncertain=True) ноком шаванд, ба админ
# ЯК огоҳии алоҳида фиристода мешавад (на ҳар фармоиш алоҳида), то админ
# фавран фаҳмад мушкил дар шабака аст, на дар як фармоиши мушаххас.
_NETWORK_ALERT_THRESHOLD = 3      # чанд ноком дар равзан барои огоҳӣ
_NETWORK_ALERT_WINDOW_MIN = 10    # равзани вақт (дақиқа)
_NETWORK_ALERT_COOLDOWN_MIN = 30  # то огоҳии навбатӣ (зидди спам)
_recent_uncertain_failures: list = []  # рӯйхати вақти ҳар ноками "номуайян"
_last_network_alert_at = None


def esc(text) -> str:
    if text is None:
        return ""
    s = "".join(
        ch for ch in str(text)
        if unicodedata.category(ch) not in ("Cf", "Cc", "Co", "Cs", "Cn")
    )
    return html.escape(s, quote=False)


def _progress_bar(pct: int, length: int = 10) -> str:
    """Прогресс-бар мисли ███████░░░ 72%"""
    pct = max(0, min(100, pct))
    filled = round(length * pct / 100)
    return f"{'█' * filled}{'░' * (length - filled)} {pct}%"


async def _run_with_live_progress_text(msg: Message, header: str, coro):
    """
    Мисли _run_with_live_progress-и admin.py, вале барои паёми ОДДИИ
    матнӣ (мизоҷ) — ҳар сония caption/матнро бо progress-bar навсозӣ
    мекунад, то 95%, то натиҷаи воқеӣ ояд.
    """
    stop_event = asyncio.Event()

    async def _updater():
        start = asyncio.get_event_loop().time()
        est_total = 25.0
        last_pct = -1
        while not stop_event.is_set():
            elapsed = asyncio.get_event_loop().time() - start
            pct = min(95, int(elapsed / est_total * 100))
            if pct != last_pct:
                try:
                    await msg.edit_text(f"{header}\n\n{_progress_bar(pct)}", parse_mode="HTML")
                except Exception:
                    pass
                last_pct = pct
            if pct >= 95:
                # Ба 95% расид — то натиҷаи воқеӣ дигар навсозӣ лозим нест,
                # навсозии бефоида (ҳар сония)-ро қатъ мекунем
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    updater_task = asyncio.create_task(_updater())
    try:
        return await coro
    finally:
        stop_event.set()
        await updater_task


def _parse_notification(text: str):
    """Summa, Kod ва рақами фармоиш (аз комент)-ро аз матни хом мебарорад.
    None = ин пардохти воридотӣ нест."""
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
    m_card = _CARD_RE.search(text)
    order_ref = int(m_card.group(1)) if m_card else None
    return summa, m_kod.group(1), order_ref


@router.channel_post(F.chat.id == config.NOTIFIER_CHAT_ID)
@router.message(F.chat.id == config.NOTIFIER_CHAT_ID)
async def handle_dc_notification(message: Message):
    text = message.text or message.caption or ""
    # DCSCAN — паёми санҷиши даврии барномаи телефон. Дар як Router aiogram
    # ҳамин handler-и аввал ҳамаи паёмҳои ин каналро мегирад, пас
    # handle_dc_scan_message ҳаргиз худаш иҷро намешуд — пардохтҳое, ки
    # notification-и оддӣ гум кард, барқарор намешуданд. Ин ҷо равона мекунем.
    if text.startswith("DCSCAN"):
        return await handle_dc_scan_message(message)
    parsed = _parse_notification(text)
    if not parsed:
        return
    summa, kod, order_ref = parsed

    # Атомикӣ: record_kod худаш такрорро мебандад (INSERT IGNORE + rowcount).
    # Ду қадами is_kod_seen→record_kod равзанаи такрор дошт.
    if not await db.record_kod(kod, summa):
        logger.info(f"Autopay: Kod {kod} такрорист — нодида гирифта шуд")
        return

    # ==== Роҳи асосӣ: РАҚАМИ ФАРМОИШ аз коменти пардохт (card_8848) ====
    if order_ref:
        order = await db.get_order(order_ref)
        if order and order.get("payment_method") in ("dushanbe_city", "alif"):
            if order.get("status") in ("autopay_search", "awaiting_autopay", "expired"):
                # Маблағро месанҷем — бояд бо нархи фармоиш баробар бошад
                if abs(float(order["price"]) - summa) > 0.011:
                    await _notify_admins_wrong_amount(message.bot, order, summa, kod)
                    return
                # Kod-ро ба ин фармоиш мебандем (резерв, зидди такрор) — ҳатто
                # агар фармоиш "мӯҳлаташ гузашта" бошад (мизоҷ бо силкаи
                # кӯҳна баъд аз якчанд рӯз пул фиристода бошад), то вақте
                # чекашро фиристад, buy.py ҳамин Kod-ро ёфта тавонад
                was_expired = order["status"] == "expired"
                await db.mark_kod_matched(kod, order_ref)
                if was_expired:
                    # Пардохти дерина барои фармоиши аллакай "мӯҳлаташ
                    # гузашта" — мизоҷро огоҳ мекунем, то чекро фиристад
                    await db.mark_order_late_recovered(order_ref)
                    try:
                        await message.bot.send_message(
                            order["user_id"],
                            f"✅ <b>Мо пардохти шуморо ёфтем!</b>\n\n"
                            f"🆔 Фармоиш: #{order_ref}\n\n"
                            f"Лутфан расми чекро ба ин чат фиристед, то донат "
                            f"худкор иҷро шавад. 🙏",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Огоҳии пардохти дерина ба {order['user_id']} нарасид: {e}")
                if order["status"] == "autopay_search":
                    # Чек аллакай омадааст → фавран донат
                    asyncio.create_task(run_donate(message.bot, order, kod))
                else:
                    # Чек ҳанӯз наомадааст → интизор; вақте чек ояд,
                    # buy.py ҳамин Kod-и резервшударо меёбад
                    logger.info(f"Autopay: пардохти #{order_ref} омад (статус: {order['status']}), чек интизор")
                return
            if order.get("status") == "paid":
                # Фармоиш аллакай ба админ фиристода шуда буд (мӯҳлати
                # ҷустуҷӯи худкор гузашта), вале ҳоло notification омад —
                # то бе сабаб дар навбати админ намонад, худкор анҷом медиҳем
                if abs(float(order["price"]) - summa) > 0.011:
                    await _notify_admins_wrong_amount(message.bot, order, summa, kod)
                    return
                asyncio.create_task(run_donate_for_escalated(message.bot, order, kod))
                return
        # order_ref ҳаст, вале фармоиши мувофиқ нест — поён fallback

    # ==== Роҳи эҳтиётӣ: муқоисаи МАБЛАҒ (агар комент наомада бошад) ====
    order = await db.find_awaiting_order_by_price(summa, "dushanbe_city", MAX_AGE_MINUTES)
    if order:
        asyncio.create_task(run_donate(message.bot, order, kod))
        return

    if await db.has_awaiting_order_by_price(summa, "dushanbe_city", MAX_AGE_MINUTES):
        logger.info(f"Autopay: пардохти {summa} омад, чек ҳанӯз нест — интизор")
        return

    # ==== Ҳеҷ фармоиши мувофиқ нест — огоҳӣ ба админ ====
    await _notify_admins_unmatched(message.bot, summa, kod)


_SCAN_ENTRY_RE = re.compile(
    r"(✅|❓|⚠️)[^\n]*\n(?:Фармоиши #(\d+)\n)?Маблағ:\s*([\d.]+)\s*TJS\nВақт:\s*(\d{2}:\d{2}:\d{2})"
)


@router.channel_post(F.chat.id == config.NOTIFIER_CHAT_ID)
@router.message(F.chat.id == config.NOTIFIER_CHAT_ID)
async def handle_dc_scan_message(message: Message):
    """
    Паёмҳои DCSCAN (аз санҷиши даврии барномаи телефон, на аз notification-и
    оддӣ)-ро мехонад — барои пардохтҳое, ки notification гум карда буд
    (масалан вақте телефон/интернет муддате қатъ буд), вале санҷиши даврӣ
    онҳоро дертар дар экрани DC пайдо кард.
    """
    text = message.text or message.caption or ""
    if not text.startswith("DCSCAN"):
        return

    for m in _SCAN_ENTRY_RE.finditer(text):
        emoji, order_ref, amount_s, time_s = m.groups()
        try:
            summa = round(float(amount_s), 2)
        except ValueError:
            continue

        synth_kod = f"DCSCAN{order_ref or '0'}-{time_s.replace(':', '')}-{int(summa * 100)}"
        if not await db.record_kod(synth_kod, summa):
            continue

        if emoji == "✅" and order_ref:
            order = await db.get_order(int(order_ref))
            if not order or order.get("payment_method") not in ("dushanbe_city", "alif"):
                continue
            status = order.get("status")
            if status not in ("autopay_search", "awaiting_autopay", "paid", "expired"):
                continue
            if abs(float(order["price"]) - summa) > 0.011:
                await _notify_admins_wrong_amount(message.bot, order, summa, synth_kod)
                continue
            if status == "paid":
                # Фармоиш аллакай ба админ фиристода шуда буд (мӯҳлати
                # ҷустуҷӯи худкор гузашта), вале санҷиши даврӣ пардохтро
                # ёфт — то бе сабаб дар навбати админ намонад, худкор
                # анҷом медиҳем
                asyncio.create_task(run_donate_for_escalated(message.bot, order, synth_kod))
                continue
            was_expired = status == "expired"
            await db.mark_kod_matched(synth_kod, int(order_ref))
            if was_expired:
                await db.mark_order_late_recovered(int(order_ref))
                try:
                    await message.bot.send_message(
                        order["user_id"],
                        f"✅ <b>Мо пардохти шуморо ёфтем!</b>\n\n"
                        f"🆔 Фармоиш: #{order_ref}\n\n"
                        f"Лутфан расми чекро ба ин чат фиристед, то донат "
                        f"худкор иҷро шавад. 🙏",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Огоҳии пардохти дерина (DCSCAN) ба {order['user_id']} нарасид: {e}")
            if status == "autopay_search":
                # Чек аллакай омадааст → фавран донат
                asyncio.create_task(run_donate(message.bot, order, synth_kod))
            else:
                # Чек ҳанӯз наомадааст → интизор (мисли DCNOTIF-и оддӣ)
                logger.info(f"Autopay(DCSCAN): пардохти #{order_ref} ёфт шуд, чек интизор")

        elif emoji == "❓":
            order = await db.find_awaiting_order_by_price(summa, "alif", MAX_AGE_MINUTES)
            if order:
                asyncio.create_task(run_donate(message.bot, order, synth_kod))


def _confirm_cb(order: dict) -> str:
    """Callback-и тугмаи «Тасдиқ — донат кун» аз рӯи хизмати фармоиш.
    Ҳар хизмат ҳандлери худро дорад (API-и ҷудогона) — агар ҳамеша `ok_`
    фиристем, фармоиши FFID/FFBR/PUBG/Stars/Premium ба API-и FF СНГ мерафт."""
    gid = order.get("game_id") or ""
    for prefix, cb in (("FFID:", "okffid"), ("FFBR:", "okffbr"),
                       ("ML:", "okml"), ("PUBG:", "okpubg"),
                       ("STARS:", "okstars"), ("PREMIUM:", "okpremium")):
        if gid.startswith(prefix):
            return f"{cb}_{order['id']}"
    return f"ok_{order['id']}"


async def _notify_admins_wrong_amount(bot: Bot, order: dict, summa: float, kod: str):
    """Фармоиш ёфт шуд, вале маблағ мувофиқ нест — донати худкор НАМЕШАВАД."""
    text = (
        f"⚠️ <b>Маблағи пардохт МУВОФИҚ НЕСТ!</b>\n\n"
        f"🆔 Фармоиш: #{order['id']}\n"
        f"👤 Корбар: <code>{order['user_id']}</code>\n"
        f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
        f"💵 Бояд мебуд: <b>{float(order['price']):.2f} сомонӣ</b>\n"
        f"💵 Воқеан омад: <b>{summa:.2f} сомонӣ</b>\n\n"
        f"Донати худкор НАШУД — дастӣ ҳал кунед (мизоҷ кам/зиёд фиристод)."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии маблағи нодуруст ба {admin_id} нарасид: {e}")


async def _notify_admins_unmatched(bot: Bot, summa: float, kod: str):
    text = (
        f"⚠️ <b>Пардохти ношинос ба корти DC!</b>\n\n"
        f"💵 Маблағ: <b>{summa:.2f} TJS</b>\n"
        f"🔑 Kod: <code>{kod}</code>\n\n"
        f"Ин пардохт ба ЯГОН фармоиши интизорӣ мувофиқат накард.\n"
        f"Эҳтимол: мизоҷ маблағи ҒАЛАТ фиристод, дер фиристод (мӯҳлат гузашт), "
        f"ё ин пардохти шахсист. Дастӣ тафтиш кунед."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии пардохти ношинос ба {admin_id} нарасид: {e}")


_PM_LABELS_SHORT = {
    "dushanbe_city": "🏙 Душанбе Сити",
    "alif": "💳 Алиф",
    "referral_balance": "💰 Аз баланс",
}


async def _admin_report_success(bot: Bot, order: dict, kod: str, api_order_id: str):
    """Ҳисоботи муфассал ба админ баъд аз донати муваффақ."""
    user = await db.get_user(order["user_id"])
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    created_at = order.get("created_at")
    time_str = created_at.strftime("%H:%M") if created_at else "—"
    api_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""

    payment_method = order.get("payment_method")
    payment_line = f"💳 Тариқи пардохт: {_PM_LABELS_SHORT.get(payment_method, payment_method or '—')}\n"
    balance_line = ""
    if payment_method == "referral_balance":
        current_balance = await db.get_referral_balance(order["user_id"])
        old_balance = current_balance + float(order["price"])
        balance_line = (
            f"👛 Баланси корбар буд: {old_balance:.2f} сомонӣ\n"
            f"💰 Баланси ҳозираи мизоҷ: {current_balance:.2f} сом\n"
        )

    text = (
        f"⚡ <b>АВТОТАСДИҚ — Донат муваффақ шуд!</b>\n\n"
        f"👤 Харидор: {esc(full_name)}\n"
        f"📱 Username: {esc(username)}\n"
        f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
        f"💵 Нархи маҳсулот: {float(order['price']):.2f} сомонӣ\n"
        f"{payment_line}"
        f"{balance_line}"
        f"🕒 Вақти харид: {time_str}\n\n"
        f"🆔 Фармоиш: #{order['id']}\n"
        f"{api_line}"
        f"🎁 {order['label']} → <code>{order['game_id']}</code>"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ҳисоботи автотасдиқ ба админ {admin_id} нарасид: {e}")


async def _check_network_health_alert(bot: Bot):
    """
    Агар дар равзани охирин якчанд ноками "номуайян" (таймаути шабака ба
    FazerCards) ҷамъ шуда бошанд, ба админ ЯК огоҳии умумӣ мефиристад —
    то ки худи мушкили шабакаро фавран фаҳмад, на аз рӯи фармоишҳои
    алоҳида тахмин занад.
    """
    global _last_network_alert_at
    now = time.monotonic()
    cutoff = now - _NETWORK_ALERT_WINDOW_MIN * 60
    _recent_uncertain_failures[:] = [t for t in _recent_uncertain_failures if t >= cutoff]

    if len(_recent_uncertain_failures) < _NETWORK_ALERT_THRESHOLD:
        return
    if _last_network_alert_at is not None and now - _last_network_alert_at < _NETWORK_ALERT_COOLDOWN_MIN * 60:
        return

    _last_network_alert_at = now
    count = len(_recent_uncertain_failures)
    text = (
        f"🚨 <b>Эҳтимол проблемаи шабака бо FazerCards!</b>\n\n"
        f"Дар {_NETWORK_ALERT_WINDOW_MIN} дақиқаи охир <b>{count} фармоиш</b> бо сабаби "
        f"таймаути шабака (на радди воқеӣ) ноком шуданд.\n\n"
        f"Ин эҳтимолан алоқаи байни сервери шумо ва FazerCards аст, на "
        f"мушкили худи фармоишҳо. Агар идома дошта бошад, шабака/провайдерро санҷед."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии саломатии шабака ба админ {admin_id} нарасид: {e}")


async def _handle_donate_failure(bot: Bot, order: dict, kod: str,
                                 api_order_id: str, uncertain: bool = False):
    """
    Донат дар вақти муқаррарӣ тамом нашуд.

    ФАЛСАФА: админро БЕҲУДА безобита накунем.
      • Агар дар FazerCards/MooGold ID-и ВОҚЕӢ бошад — фармоиш ба
        тафтишгари худкор (recheck_loop) супорида мешавад ва админ ҲЕҶ
        паём намегирад. Тафтишгар ҳар 3 дақиқа мепурсад ва аксаран худаш
        ҳал мекунад. Танҳо агар ~30 дақиқа гузарад ё FazerCards ВОҚЕАН
        "рад кард" гӯяд, ЯК паём ба админ меравад.
      • Агар ID нест (фармоиш умуман ба FazerCards нарасид) — чизе барои
        санҷидан нест, пас фавран ба админ хабар меравад.
    """
    order_id = order["id"]
    user_id = order["user_id"]

    await db.update_order_status(order_id, "failed")
    # Ин кӯшиши НАВ буд — ҳисоби тафтишгар ва аломати "ба админ хабар
    # дода шуд" аз сифр сар мешаванд. Вагарна баъд аз «Дубора донат»-и
    # дастӣ, агар боз ноком шавад, админ ҳеҷ хабар намегирифт.
    _recheck_settled.discard(order_id)
    try:
        await db.reset_recheck_state(order_id)
    except Exception as e:
        logger.error(f"reset_recheck_state #{order_id} нашуд: {e}")
    if uncertain:
        try:
            await db.flag_order_uncertain(order_id)
        except Exception as e:
            logger.error(f"flag_order_uncertain #{order_id} нашуд: {e}")

    try:
        if api_order_id:
            customer_text = (
                f"⏳ <b>Пардохти шумо қабул шуд — донат каме дертар мерасад.</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"Фармоиши шумо ба система фиристода шудааст ва бот ҳар "
                f"чанд дақиқа ҳолати онро месанҷад. Ҳамин ки тайёр шавад, "
                f"ба шумо хабар медиҳем. Пулатон бехатар аст. 🙏"
            )
        else:
            customer_text = (
                f"⚠️ <b>Пардохти шумо қабул шуд, вале донат каме ба таъхир афтод.</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"Хавотир нашавед — админ огоҳ карда шуд ва ба зудӣ "
                f"дастӣ ҳал мекунад. Пулатон бехатар аст. 🙏"
            )
        await bot.send_message(user_id, customer_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Паёми таъхир ба {user_id} нарасид: {e}")

    if api_order_id:
        # Ба тафтишгари худкор месупорем — админ ҳоло безобита намешавад
        logger.info(
            f"#{order_id}: донат дар 10 дақиқа тамом нашуд, вале ID-и "
            f"воқеӣ ({api_order_id}) ҳаст — ба тафтишгари худкор супорида "
            f"шуд, админ ҳоло хабар намегирад"
        )
        return

    # ID нест — чизе барои санҷидан нест, админ бояд дастӣ ҳал кунад
    await _report_failure_to_admin(bot, order, kod, api_order_id, uncertain)


async def _admin_report_failure(bot: Bot, order: dict, kod: str, api_order_id: str, uncertain: bool = False):
    """Пардохт омад, вале донат нашуд — админ бо тугмаҳо огоҳ мешавад."""
    if uncertain:
        _recent_uncertain_failures.append(time.monotonic())
        await _check_network_health_alert(bot)
    user = await db.get_user(order["user_id"])
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    api_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""
    # Тугмаи "Дубора донат" бояд ба ҳандлери ДУРУСТИ хидмат равад (на ҳамеша
    # ba FF СНГ) — вагарна харидҳои FFID/PUBG/Stars/Premium-и аз баланс ба
    # API-и нодуруст мераванд ва боз ноком мешаванд
    retry_cb = _confirm_cb(order)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=retry_cb)],
        [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order['id']}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order['id']}")],
    ])
    if uncertain and api_order_id:
        # Тафтишгари худкор ~30 дақиқа кӯшиш кард ва натавонист
        head = "⏰ <b>Ин фармоиш дер монд — кӯмаки шумо лозим аст</b>"
        warning_line = (
            f"\n🤖 Тафтишгари худкор ҳудуди <b>30 дақиқа</b> ҳар 3 дақиқа "
            f"FazerCards-ро пурсид, вале ҷавоби аниқ нагирифт "
            f"(на «иҷро шуд», на «рад шуд»).\n\n"
            f"📌 <b>Чӣ кор кунед:</b> дар кабинети FazerCards ID-и болоро "
            f"кушоед ва бо чашм бинед:\n"
            f"• Агар <b>completed</b> бошад → «✅ Дастӣ тасдиқ кардам»\n"
            f"• Агар <b>failed/cancelled</b> бошад → «🔄 Дубора донат»\n\n"
            f"ℹ️ Тафтишгар кори худро БАС НАКАРДААСТ — агар FazerCards "
            f"баъдтар ҷавоб диҳад, бот худаш ҳал мекунад ва ба шумо хабар "
            f"медиҳад. Агар шитоб надоред, боз каме сабр кардан мумкин.\n"
        )
    elif uncertain:
        head = "⚠️ <b>ПАРДОХТ ОМАД, вале донати худкор НАШУД!</b>"
        warning_line = (
            f"\n⚠️ Фармоиш умуман ба FazerCards нарасид (ID нест) — "
            f"пас дучандон донат шудан хатар надорад.\n"
            f"«🔄 Дубора донат»-ро бехатар пахш кардан мумкин аст.\n"
        )
    else:
        head = "⚠️ <b>ПАРДОХТ ОМАД, вале донати худкор НАШУД!</b>"
        warning_line = (
            f"\n❌ FazerCards ин фармоишро <b>ВОҚЕАН рад кард</b> — "
            f"алмос нарафтааст.\n"
            f"✅ «🔄 Дубора донат» дар ин ҳолат комилан бехатар аст "
            f"(дучандон харҷ намешавад).\n"
        )
    text = (
        f"{head}\n\n"
        f"👤 Харидор: {esc(full_name)} ({esc(username)})\n"
        f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
        f"💵 Маблағ: {float(order['price']):.2f} сомонӣ\n\n"
        f"🆔 Фармоиш: #{order['id']}\n"
        f"{api_line}"
        f"🎁 {order['label']} → <code>{order['game_id']}</code>\n"
        f"{warning_line}\n"
        f"Пули мизоҷ ҚАБУЛ шудааст — ҳатман ҳал кунед!"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии хатои донат ба админ {admin_id} нарасид: {e}")


async def run_donate(bot: Bot, order: dict, kod: str):
    """
    Пардохт ёфт шуд → навбати автодонат бо паёмҳои марҳилавӣ ба мизоҷ.
    """
    global _queue_count
    order_id = order["id"]
    user_id = order["user_id"]
    price = float(order["price"])

    # Монеаи фаврӣ (хотира) — агар ҳамин лаҳза дигар даъвате барои ин
    # фармоиш дар кор бошад, фавран баромадан (пеш аз расидан ба база)
    if order_id in _in_flight_orders:
        logger.info(f"Autopay: фармоиши #{order_id} аллакай дар хотира дар кор аст — такрор нашуд")
        return
    _in_flight_orders.add(order_id)
    try:
        # Ҳимояи атомикӣ аз ду бор донат шудан (race): танҳо ЯК даъват
        # метавонад фармоишро аз autopay_search/awaiting ба 'paid' гузаронад
        if not await db.claim_order_for_donate(order_id):
            logger.info(f"Autopay: фармоиши #{order_id} аллакай дар кор аст — такрор нашуд")
            return
        if order.get("is_balance_topup"):
            # Пуркунии баланс — ДОНАТ НЕСТ, танҳо ба баланси мизоҷ илова мешавад
            await _credit_balance_topup(bot, order)
            return
        await run_donate_inner(bot, order, kod)
    finally:
        _in_flight_orders.discard(order_id)


async def run_donate_inner(bot: Bot, order: dict, kod: str):
    """Қисми асосии run_donate (баъд аз claim_order_for_donate)."""
    global _queue_count
    order_id = order["id"]
    user_id = order["user_id"]
    price = float(order["price"])

    await db.mark_kod_matched(kod, order_id)

    # ---- Марҳилаи 1: пардохт ёфта шуд ----
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Пардохти шумо ёфта шуд!</b>\n\n"
            f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
            f"🆔 Фармоиш: #{order_id}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Паёми 'ёфта шуд' ба {user_id} нарасид: {e}")

    # ---- Марҳилаи 2: ҳолати навбат ----
    ahead = _queue_count
    _queue_count += 1
    try:
        if ahead >= _DONATE_CONCURRENCY:
            queue_text = (
                f"⏳ <b>Автодонати шумо дар навбат аст.</b>\n\n"
                f"Пеш аз шумо: <b>{ahead} фармоиш</b>.\n"
                f"Баъд аз чанд дақиқа навбати шумо мерасад — сабр кунед, "
                f"ҳамааш худкор иҷро мешавад. 🤖"
            )
        else:
            queue_text = (
                f"🚀 <b>Навбат холӣ — автодонати шумо ҲОЗИР сар шуд!</b>\n\n"
                f"Одатан 1-3 дақиқа мегирад. Мунтазир бошед..."
            )
        await bot.send_message(user_id, queue_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Паёми навбат ба {user_id} нарасид: {e}")

    # ---- Марҳилаи 3: донат (паси ҳам, тавассути навбат) — бо progress bar ----
    try:
        async with _donate_semaphore:
            # Бехатарии иловагӣ: пеш аз фиристодан ба API, аз база маълумоти
            # ТОЗАРО мехонем (на он чи дар аввали функсия дошта будем) — то
            # агар ин фармоиш аллакай ба FazerCards/MooGold фиристода шуда
            # бошад (масалан бо даъвати параллели дигар), ID-и мавҷударо
            # истифода барем, на фармоиши комилан НАВ созем (зидди донати
            # дукарата — зарари молиявӣ).
            fresh_order = await db.get_order(order_id) or order
            # Банди АТОМИКӢ (на танҳо хондан) — то агар дар ҳамин лаҳза
            # админ низ "✅ Тасдиқ"-ро пахш карда бошад (order_confirm низ
            # ҳамин claim-ро мекунад), танҳо ЯКЕ аз ду тараф донатро сар
            # кунад (дигараш False мегирад ва бе амал бармегардад) — зидди
            # ду бор донат шудани як фармоиш
            if not await db.claim_paid_order_for_autodonate(order_id):
                logger.warning(
                    f"Autopay: фармоиши #{order_id} аллакай аз тарафи дигар "
                    f"(масалан админ) гирифта шудааст — Марҳилаи 3 гузаронида шуд"
                )
                return

            header = (
                "🚀 <b>Автодонати шумо оғоз шуд!</b>\n"
                "Одатан 1-3 дақиқа мегирад..."
            )
            progress_msg = None
            try:
                progress_msg = await bot.send_message(user_id, header, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Паёми оғози донат ба {user_id} нарасид: {e}")

            # Ҳар хизмат API-и худро дорад (FFID/FFBR/PUBG/Stars/Premium) —
            # ниг. _dispatch_donate_call. Пештар ин ҷо ҳамеша auto_donate-и
            # FF СНГ даъват мешуд, пас фармоиши хизмати дигар ба категорияи
            # нодуруст мерафт.
            donate_coro = _dispatch_donate_call(fresh_order)
            if progress_msg:
                success, api_order_id, uncertain, cost_usd = await _run_with_live_progress_text(progress_msg, header, donate_coro)
            else:
                success, api_order_id, uncertain, cost_usd = await donate_coro
            logger.info(f"[COST-DEBUG] run_donate_inner: order={order_id} success={success} cost_usd={cost_usd!r}")

            # Сабти ID ҳанӯз ДАР ДОХИЛИ қулф — то даъвати навбатӣ (агар
            # бошад) ҳатман ин ID-ро тоза бинад, на холӣ (равзанаи race)
            if api_order_id:
                await db.set_order_api_id(order_id, api_order_id)
    finally:
        _queue_count -= 1

    # ---- Марҳилаи 4: натиҷа ----
    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))

        # Мукофоти реферралӣ
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
                user_id,
                f"🎉 <b>Донат анҷом ёфт! Алмазҳо фиристода шуданд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
                f"🙏 Ташаккур барои харид!\n"
                f"🎁 Шумо ҳоло дар рӯйхати тӯҳфаи ройгон ҳастед — шояд навбати шумо расад! 🍀\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")],
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми анҷом ба {user_id} нарасид: {e}")

        await _admin_report_success(bot, order, kod, api_order_id)
    else:
        await _handle_donate_failure(bot, order, kod, api_order_id, uncertain)


async def run_donate_for_escalated(bot: Bot, order: dict, kod: str):
    """
    Фармоише, ки АЛЛАКАЙ ба админ фиристода шуда буд (масалан 10 дақиқаи
    ҷустуҷӯи худкор гузашт — статус 'paid'), вале баъдтар DCNOTIF/DCSCAN
    пардохти мувофиқро ёфт — то фармоиш беҳуда дар навбати админ намонад,
    худкор анҷом дода мешавад.

    Атомикӣ ба 'donating' банд карда мешавад (claim_paid_order_for_autodonate)
    — то агар дар ҳамин лаҳза админ низ дастӣ "✅ Тасдиқ" пахш кунад, ду бор
    донат нашавад (яке аз ду тараф claim-ро мебарад, дигараш бе амал мемонад).
    """
    order_id = order["id"]
    user_id = order["user_id"]

    if order_id in _in_flight_orders:
        logger.info(f"Autopay(эскалатсия): фармоиши #{order_id} аллакай дар хотира дар кор аст")
        return
    _in_flight_orders.add(order_id)
    try:
        if not await db.claim_paid_order_for_autodonate(order_id):
            logger.info(f"Autopay(эскалатсия): фармоиши #{order_id} аллакай гирифта шудааст (админ ё дигар роҳ) — такрор нашуд")
            return

        await db.mark_kod_matched(kod, order_id)

        if order.get("is_balance_topup"):
            # Пуркунии баланс — ДОНАТ НЕСТ, танҳо ба баланси мизоҷ илова мешавад
            await _credit_balance_topup(bot, order)
            return

        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Пардохти шумо ёфта шуд!</b>\n\n"
                f"💵 Маблағ: <b>{float(order['price']):.2f} сомонӣ</b>\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"🚀 Донат ҳозир иҷро мешавад...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми 'ёфта шуд' (эскалатсия) ба {user_id} нарасид: {e}")

        async with _donate_semaphore:
            # Санҷиши иловагӣ: агар байни claim ва расидан ба ин ҷо касе
            # (масалан «❌ Рад кардан»-и админ) фармоишро ба ҳолати ниҳоӣ
            # гузаронида бошад, донат намекунем
            fresh_order = await db.get_order(order_id) or order
            if fresh_order.get("status") in ("confirmed", "rejected"):
                logger.warning(
                    f"Autopay(эскалатсия): фармоиши #{order_id} аллакай "
                    f"{fresh_order.get('status')} — донат гузаронида шуд"
                )
                return
            # Ба API-и ДУРУСТИ хизмат (на ҳамеша FF СНГ) — ниг. _dispatch_donate_call
            success, api_order_id, uncertain, cost_usd = await _dispatch_donate_call(fresh_order)
            logger.info(f"[COST-DEBUG] run_donate_for_escalated: order={order_id} success={success} cost_usd={cost_usd!r}")
            if api_order_id:
                await db.set_order_api_id(order_id, api_order_id)

        if success:
            await db.update_order_status(order_id, "confirmed")
            await db.set_confirmed_at(order_id)
            if cost_usd:
                await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))

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
                    user_id,
                    f"🎉 <b>Донат анҷом ёфт! Алмазҳо фиристода шуданд!</b>\n\n"
                    f"🆔 Фармоиш: #{order_id}\n"
                    f"{order['label']} → <code>{order['game_id']}</code>\n\n"
                    f"🙏 Ташаккур барои харид!\n\n"
                    f"⭐ Лутфан отзив гузоред:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")],
                        [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                    ]),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Паёми анҷом (эскалатсия) ба {user_id} нарасид: {e}")

            await _admin_report_success(bot, order, kod, api_order_id)
        else:
            await _handle_donate_failure(bot, order, kod, api_order_id, uncertain)
    finally:
        _in_flight_orders.discard(order_id)


async def _dispatch_donate_call(order: dict):
    """
    Ба API-и ДУРУСТИ хизмат муроҷиат мекунад (аз рӯи префикси game_id) ва
    натиҷаро ба як шакл меорад: (success, api_order_id, uncertain, cost_usd).
    FFID/PUBG 2-tuple бармегардонанд — ин ҷо ба 4-tuple табдил мешаванд.

    Даъват дар try — то агар exception партояд, фармоиш дар 'donating' гир
    намонад; чунин ҳолат ҳамчун "номаълум" (uncertain) баҳо дода мешавад,
    яъне фармоиши НАВ сохта намешавад.
    """
    order_id = order["id"]
    game_id = order["game_id"] or ""
    try:
        if game_id.startswith("FFID:"):
            player_id = game_id.replace("FFID:", "")
            success, api_order_id = await ff_api.auto_donate_ffid(
                player_id, order["offer_id"], order.get("api_order_id") or "", order_id
            )
            return success, api_order_id, False, None
        if game_id.startswith("FFBR:"):
            player_id = game_id.replace("FFBR:", "")
            success, api_order_id = await ff_api.auto_donate_ffbr(
                player_id, order["offer_id"], order.get("api_order_id") or "", order_id
            )
            return success, api_order_id, False, None
        if game_id.startswith("ML:"):
            # "ML:<player_id>:<server_id>" — ML ДУ майдон дорад
            player_id, _, server_id = game_id[3:].partition(":")
            success, api_order_id = await ff_api.auto_donate_ml(
                player_id, server_id, order["offer_id"],
                order.get("api_order_id") or "", order_id
            )
            return success, api_order_id, False, None
        if game_id.startswith("PUBG:"):
            player_id = game_id.replace("PUBG:", "")
            success, api_order_id = await ff_api.auto_donate_pubg(
                player_id, order["offer_id"], order.get("api_order_id") or "", order_id
            )
            return success, api_order_id, False, None
        if game_id.startswith("STARS:"):
            tg_username = game_id.replace("STARS:", "")
            return await ff_api.buy_telegram_stars(
                tg_username, order["amount"], order_id, order.get("api_order_id") or ""
            )
        if game_id.startswith("PREMIUM:"):
            tg_username = game_id.replace("PREMIUM:", "")
            return await ff_api.buy_telegram_premium(
                tg_username, order["amount"], order_id, order.get("api_order_id") or ""
            )
        return await ff_api.auto_donate(
            game_id, order["offer_id"], order.get("api_order_id") or "", order_id
        )
    except Exception as e:
        logger.error(f"_dispatch_donate_call: ff_api хато барои #{order_id}: {e}")
        return False, "", True, None


async def run_donate_from_balance(bot: Bot, order: dict):
    """
    Фармоише, ки МИЗОҶ АЗ БАЛАНСИ ХУД пардохт кардааст (баланс аллакай
    атомикӣ кам шудааст дар buy.py) — донат ФАВРАН оғоз мешавад, бе
    интизории тасдиқи дастии админ (бо тугмаҳо). Комбоҳо ба ин ФУНКСИЯ
    ҳељ гоҳ намерасанд — онҳо дастӣ мемонанд (buy.py филтр мекунад).

    Бар хилофи run_donate/run_donate_for_escalated, ин ҷо claim-и атомикӣ
    лозим нест — фармоиши пардохтшуда аз баланс ҳељ рақиби дигар надорад
    (на DCSCAN, на DCNOTIF метавонанд ба ин фармоиш бархӯранд).
    """
    order_id = order["id"]
    user_id = order["user_id"]

    await db.update_order_status(order_id, "donating")

    async with _donate_semaphore:
        # Ҳар хизмат API-и худро дорад — ниг. _dispatch_donate_call.
        # Даъвати API дар try — то агар exception партояд, фармоиш дар 'donating'
        # гир намонад (баланс аллакай кам шудааст) — онро ҳамчун ноком коркард
        # мекунем ва админ огоҳ мешавад (мисли уncertain).
        success, api_order_id, uncertain, cost_usd = await _dispatch_donate_call(order)
        logger.info(f"[COST-DEBUG] run_donate_from_balance: order={order_id} success={success} cost_usd={cost_usd!r}")
        if api_order_id:
            await db.set_order_api_id(order_id, api_order_id)

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))

        try:
            reward, referrer_id = await db.credit_referral_for_order(order_id)
            if reward and referrer_id:
                await bot.send_message(
                    referrer_id,
                    f"🤝 <b>Мукофоти реферралӣ!</b>\n\n"
                    f"💰 Дусти шумо фармоиш дод ва шумо <b>{reward:.2f} сом</b> "
                    f"ба балансатон гирифтед!",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"credit_referral хато барои #{order_id}: {e}")

        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Донат анҷом ёфт! {esc(order['label'])} фиристода шуд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")],
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми анҷом (аз баланс) ба {user_id} нарасид: {e}")

        await _admin_report_success(bot, order, "", api_order_id)
    else:
        await _handle_donate_failure(bot, order, "", api_order_id, uncertain)


async def expiry_loop(bot: Bot, interval_seconds: int = 60):
    """
    Ҳар дақиқа:
      - фармоишҳои 'awaiting_autopay' (чек наомада) > 15 дақ → 'expired' + хабар
      - фармоишҳои 'autopay_search' (чек омада, пардохт ёфт нашуда) > 10 дақ →
        чек ба админ барои тафтиши ДАСТӢ (бо тугмаҳои Тасдиқ/Рад)
    """
    while True:
        await asyncio.sleep(interval_seconds)

        # ---- Фармоишҳое, ки дар 'donating' гир мондаанд (масалан сервер
        # маҳз дар вақти донат рестарт шуда буд) — ба 'paid' бармегардонем,
        # то боз кӯшиш карда шаванд ----
        try:
            # 25 дақиқа — на 3. Донати воқеӣ то 10 дақ (FazerCards) ё то
            # ~20 дақ (бо fallback-и MooGold) давом мекунад. Бо 3 дақиқа
            # фармоиши ЗИНДА ба 'paid' бармегашт ва админ метавонист онро
            # дубора тасдиқ карда, донати дуюм (харҷи дучанд) созад. 25 дақ
            # аз ҳадди аксари вақти донат зиёдтар аст — танҳо фармоише, ки
            # сервер ҳангоми рестарт нимкора монд, барқарор мешавад.
            await db.recover_stuck_donating_orders(25)
        except Exception as e:
            logger.error(f"Хатогӣ дар барқарорсозии 'donating': {e}")

        # ---- Огоҳии "шитоб кунед!" пеш аз ба итмом расидани мӯҳлат ----
        try:
            nearing = await db.get_orders_nearing_expiry(EXPIRY_WARN_BEFORE_MIN, MAX_AGE_MINUTES)
            for order in nearing:
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"⏰ <b>Шитоб кунед! Фармоиши #{order['id']}</b>\n\n"
                        f"Танҳо {EXPIRY_WARN_BEFORE_MIN} дақиқа то ба итмом расидани вақт монд. "
                        f"Агар пардохт карда бошед, расми чекро ҳозир фиристед — "
                        f"то донат худкор иҷро шавад. 🙏",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Огоҳии итмоми мӯҳлат ба {order['user_id']} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар огоҳии итмоми мӯҳлат: {e}")

        # ---- Фармоиши нотамом монда: ЯК ёдоварии нарм баъди чанд соат ----
        # Паёми «мӯҳлат гузашт» ҳамон лаҳза меравад, вақте мизоҷ шояд банд
        # бошад ва онро нахонад. Ин ёдоварии дуюм баъди 3 соат меояд —
        # танҳо як бор ва танҳо ба касе, ки баъд аз он ҳанӯз харид накард.
        try:
            for order in await db.get_abandoned_orders_for_nudge(
                    NUDGE_AFTER_HOURS, NUDGE_UNTIL_HOURS):
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"👋 <b>Фармоишатон нотамом монд</b>\n\n"
                        f"🎁 {esc(order['label'])} — {float(order['price']):.2f} сом\n\n"
                        f"Пардохт наомад, пас фармоиш пӯшида шуд. Ҳељ пуле кам "
                        f"нашуд — хавотир нашавед.\n\n"
                        f"Агар ҳанӯз хоҳед, харидро аз нав сар кардан мумкин "
                        f"аст — ду дақиқа вақт мегирад 👇",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛒 Харидро давом додан",
                                                  callback_data="back_main")],
                        ]),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.info(f"Ёдоварии фармоиши нотамом ба {order['user_id']} нарасид: {e}")
                # Новобаста аз он ки паём расид ё не, аломат мегузорем —
                # вагарна ба касе, ки ботро баста, ҳар дақиқа кӯшиш мешавад
                await db.mark_nudge_sent(order["user_id"])
        except Exception as e:
            logger.error(f"Хатогӣ дар ёдоварии фармоишҳои нотамом: {e}")

        # ---- Чек наомада, мӯҳлат гузашт ----
        try:
            stale = await db.expire_stale_awaiting_orders(MAX_AGE_MINUTES)
            for order in stale:
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"⏳ <b>Вақти пардохти фармоиши #{order['id']} гузашт.</b>\n\n"
                        f"💳 Агар аллакай пардохт карда бошед — расми чекро ҳозир ҳам "
                        f"фиристед, системаи мо худкор тафтиш мекунад ва донат мешавад.\n\n"
                        f"🛒 Агар пардохт накарда бошед — хавотир нашавед, ҳељ пуле кам "
                        f"нашуд. Барои харид тугмаи поёнро пахш кунед 👇",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛒 Харидро давом додан", callback_data="back_main")],
                            [InlineKeyboardButton(text="🆘 Дастгирӣ", url=config.SUPPORT_URL)],
                        ]),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Хабари мӯҳлат ба {order['user_id']} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар expiry (awaiting): {e}")

        # ---- Чек омада, вале пардохт ёфт нашуд → ба админ ----
        try:
            unfound = await db.get_stale_search_orders(SEARCH_TIMEOUT_MIN)
            for order in unfound:
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"🔍 <b>Пардохти шумо худкор ёфта нашуд.</b>\n\n"
                        f"🆔 Фармоиш: #{order['id']}\n\n"
                        f"Чеки шумо ба админ фиристода шуд — дастӣ тафтиш мекунад. "
                        f"Каме сабр кунед. 🙏",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

                user = await db.get_user(order["user_id"])
                username = f"@{user['username']}" if user and user.get("username") else "—"
                caption = (
                    f"🔍 <b>Автопардохт пардохтро НАЁФТ — дастӣ тафтиш кунед!</b>\n\n"
                    f"👤 Харидор: {esc(user.get('full_name') if user else '—')}\n"
                    f"📱 Username: {esc(username)}\n"
                    f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
                    f"💵 Маблағи интизорӣ: <b>{float(order['price']):.2f} сомонӣ</b>\n\n"
                    f"🆔 Фармоиш: #{order['id']}\n"
                    f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
                    f"Чекро тафтиш кунед: агар пул воқеан омада бошад — «Тасдиқ»."
                )
                # Тугмаи тасдиқ бояд ба ҳандлери ДУРУСТИ хизмат равад —
                # вагарна фармоиши FFID/FFBR/PUBG/Stars/Premium ба API-и
                # FF СНГ мерафт ва донат ноком мешуд
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Тасдиқ — донат кун",
                                          callback_data=_confirm_cb(order))],
                    [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order['id']}")],
                ])
                for admin_id in config.ADMIN_IDS:
                    try:
                        if order.get("check_file_id"):
                            await bot.send_photo(
                                admin_id, order["check_file_id"],
                                caption=caption, reply_markup=kb, parse_mode="HTML"
                            )
                        else:
                            await bot.send_message(
                                admin_id, caption, reply_markup=kb, parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.error(f"Чеки дастӣ ба админ {admin_id} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар expiry (search): {e}")


# Аз ин маблағ боло — пуркунии баланс "калон" ҳисоб мешавад ва огоҳии
# махсус ба админ меравад (аз танзими 'big_topup_alert' иваз кардан мумкин)
BIG_TOPUP_DEFAULT = 200.0

GIVEAWAY_DEFAULT_EVERY_N = 25
GIVEAWAY_NEAR_MISS_THRESHOLD = 3  # чанд фармоиш монда огоҳии "наздикӣ" фиристода шавад


async def _credit_balance_topup(bot: Bot, order: dict):
    """
    Пардохти пуркунии баланс ёфта шуд — БЕ донат, БЕ мукофоти реферралӣ
    (топуп худаш харид нест; мукофот вақти харид АЗ баланс дода мешавад).
    Танҳо фармоишро 'confirmed' карда, маблағро ба балансаи мизоҷ илова мекунад.
    """
    order_id = order["id"]
    user_id = order["user_id"]
    amount = float(order["price"])

    old_balance = await db.get_referral_balance(user_id)
    ok = await db.credit_balance_topup(order_id, user_id, amount)
    if not ok:
        logger.warning(f"Balance topup: фармоиши #{order_id} аллакай коркард шудааст — такрор нашуд")
        return

    # ---- ОГОҲИИ АДМИН ФАВРАН (пеш аз ҳама) ----
    # Ин бояд ҲАТМАН ба админ расад — то соҳиб бидонад КӢ чанд сум пур кард
    # ва чеки ДС-ро бинад. Ҳамаи ҳисобкуниҳо мудофиавӣ (try/except) карда
    # шуданд, то ягон хатои фаръӣ (масалан хондани ном аз база) ин огоҳиро
    # НАБАНДАД (пештар агар байни кредит ва огоҳӣ хатое мешуд, огоҳӣ гум мешуд).
    try:
        new_balance = await db.get_referral_balance(user_id)
    except Exception:
        new_balance = old_balance + amount
    try:
        user = await db.get_user(user_id)
    except Exception:
        user = None
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    # Пуркунии КАЛОН — огоҳии махсус (диққати соҳибро ҷалб мекунад)
    big_line = ""
    try:
        threshold = float(await db.get_setting("big_topup_alert") or BIG_TOPUP_DEFAULT)
    except Exception:
        threshold = BIG_TOPUP_DEFAULT
    if threshold > 0 and amount >= threshold:
        big_line = f"\n🔔 <b>ДИҚҚАТ: пуркунии КАЛОН ({amount:.2f} сом)!</b>\n"
    admin_text = (
        f"💰 <b>Баланси мизоҷ пур шуд</b>\n"
        f"{big_line}\n"
        f"👤 Харидор: {esc(full_name)} ({esc(username)})\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 {old_balance:.2f} сом буд → {new_balance:.2f} сом шуд (+{amount:.2f} сом)\n"
        f"🆔 Фармоиш: #{order_id}"
    )
    check_file_id = order.get("check_file_id")
    for admin_id in config.ADMIN_IDS:
        try:
            # Агар чек бошад — расми чекро МУСТАҚИМ мефиристем (соҳиб фавран
            # пардохти воқеии ДС-ро мебинад, на танҳо матн)
            if check_file_id:
                await bot.send_photo(admin_id, check_file_id, caption=admin_text, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии пуркунии баланс ба админ {admin_id} нарасид: {e}")

    # ---- Паём ба мизоҷ ----
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Баланси шумо пур шуд!</b>\n\n"
            f"💰 {old_balance:.2f} сом баланс дошт → баъди пуркунӣ "
            f"<b>{new_balance:.2f} сом</b> шуд (+{amount:.2f} сом)\n\n"
            f"Акнун метавонед аз баланс харид кунед.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Паёми пуркунии баланс ба {user_id} нарасид: {e}")

    # Агар ин пуркунӣ аз "норасогӣ"-и харид оғоз шуда буд — ҳамон харидро
    # ҲОЗИР худкор анҷом медиҳем (мизоҷ дигар ҳељ коре накунад)
    try:
        pending = await db.get_pending_purchase_for_topup(order_id)
        if pending:
            await db.mark_pending_purchase_fulfilled(pending["id"])
            await _complete_pending_purchase(bot, user_id, pending)
    except Exception as e:
        logger.error(f"Хатогӣ дар анҷоми хариди интизорӣ барои #{order_id}: {e}")


async def _complete_pending_purchase(bot: Bot, user_id: int, pending: dict):
    """
    Хариди интизориро (ки мизоҷ барояш баланс пур карда буд) худкор анҷом
    медиҳад: маблағро аз баланс кам мекунад, фармоиш месозад ва донат мекунад.
    Агар баланс кофӣ набошад (ҳолати нодир) — бесадо мегузарад, мизоҷ баланси
    пуршударо худаш истифода бурда метавонад.
    """
    price = float(pending["price"])
    ok = await db.deduct_referral_balance(user_id, price)
    if not ok:
        logger.warning(
            f"Хариди интизорӣ барои {user_id}: баланс кофӣ нест ({price} сом) — гузаронида шуд"
        )
        return
    # Агар байни кам шудани баланс ва сохтани фармоиш хатои база шавад —
    # пул ба баланс ХУДКОР БАРНАМЕГАРДАД (қоидаи соҳиб). Ба ҷои он админ
    # огоҳ мешавад, то ДАСТӢ ҳал кунад.
    try:
        order_id = await db.create_order(
            user_id=user_id,
            game_id=pending["game_id"],
            nickname=pending.get("nickname", "") or "",
            amount=pending.get("amount", 0) or 0,
            price=price,
            label=pending["label"],
            offer_id=pending.get("offer_id", "") or "",
            payment_method="referral_balance",
        )
        await db.mark_order_paid_with_balance(order_id)
        order = await db.get_order(order_id)
    except Exception as e:
        logger.error(f"Хариди интизорӣ: сохтани фармоиш нашуд ({user_id}, {price}): {e}")
        try:
            await bot.send_message(
                user_id,
                "⚠️ <b>Хатои система рӯй дод.</b>\n\n"
                f"Лутфан бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ <b>Хатои хариди интизорӣ (аз баланс) — ДАСТӢ ҳал кунед!</b>\n\n"
                    f"👤 ID: <code>{user_id}</code>\n"
                    f"💵 {price:.2f} сом аз баланс кам шуд, вале фармоиш сохта НАШУД.\n"
                    f"🎁 {esc(pending.get('label', '—'))}\n"
                    f"Хато: {esc(str(e))[:200]}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return
    try:
        await bot.send_message(
            user_id,
            f"🛍 <b>Хариди шумо худкор оғоз шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"🎁 {esc(pending['label'])}\n\n"
            f"🚀 Донат ҳозир иҷро мешавад...",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Паёми оғози хариди интизорӣ ба {user_id} нарасид: {e}")
    await run_donate_from_balance(bot, order)


async def spin_names_for(batch):
    """
    Номҳои тозашуда барои чарх. Ҳамин номҳо дар МАТНИ эълон ҳам истифода
    мешаванд — вагарна дар расм «Мизоҷ 7» ва дар матн «😐» мебуд.
    """
    try:
        import spinwheel
        names_map = await db.get_display_names(batch)
        # Агар мизоҷ на username дошта бошад, на номи хондашаванда —
        # 4 рақами охири ID-и ӯро мегузорем. Мизоҷ ID-и худро мешиносад,
        # «Мизоҷ 24» бошад ба ӯ ҳеҷ чиз намегӯяд.
        return [spinwheel.clean_name(names_map.get(int(u), ""),
                                     f"ID •{str(u)[-4:]}")
                for u in batch]
    except Exception as e:
        logger.error(f"Номҳои чарх тайёр нашуданд: {e}")
        return [f"ID •{str(u)[-4:]}" for u in (batch or [])]


async def _spin_gif_path(batch, winner_idx, label, total_wins, names=None):
    """
    GIF-и чархро месозад (дар риштаи алоҳида — то боти асосӣ кунд нашавад).
    Агар чизе нашавад — None, ва эълон бо матни оддӣ меравад.
    """
    try:
        import spinwheel
        if names is None:
            names = await spin_names_for(batch)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spin_last.gif")
        return await asyncio.to_thread(
            spinwheel.render_spin_gif, names, winner_idx, label,
            total_wins, config.BOT_USERNAME, out)
    except Exception as e:
        logger.error(f"Чархи тӯҳфа сохта нашуд: {e}")
        return None


async def _send_giveaway_gift(bot: Bot, winner_id: int, product_id: int,
                              batch: list = None, winner_idx: int = -1):
    """Ба барандаи тасодуфӣ маҳсулоти тӯҳфаро худкор донат мекунад ва огоҳ мекунад."""
    product = await db.get_product(product_id)
    if not product:
        logger.error(f"Giveaway: маҳсулоти тӯҳфа #{product_id} дигар вуҷуд надорад")
        return

    player = await db.get_last_player_id(winner_id, "")
    if not player or not player.get("player_id"):
        logger.warning(f"Giveaway: барандаи {winner_id} ID-и бозӣ надорад — тӯҳфа гузаронида шуд")
        return

    label = product.get("label") or f"💎 {product['amount']}"
    order_id = await db.create_order(
        user_id=winner_id,
        game_id=player["player_id"],
        nickname=player.get("nickname", ""),
        amount=product["amount"],
        price=0,
        label=f"🎁 Тӯҳфаи ройгон: {label}",
        offer_id=product.get("offer_id") or "",
        payment_method="giveaway",
    )
    success, api_order_id, uncertain, cost_usd = await ff_api.auto_donate(
        player["player_id"], product.get("offer_id") or "", "", order_id
    )
    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    winner_user = await db.get_user(winner_id)
    winner_name = esc(winner_user.get("full_name")) if winner_user and winner_user.get("full_name") else "—"
    winner_username = f"@{winner_user['username']}" if winner_user and winner_user.get("username") else "—"

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
        # Мизоҷ бояд бидонад ба КАДОМ аккаунт тӯҳфа рафт — вагарна
        # намефаҳмад куҷоро санҷад (баъзеҳо чанд ID доранд)
        _nick = (player.get("nickname") or "").strip()
        nick_line = f"👤 Ном: <b>{esc(_nick)}</b>\n" if _nick else ""
        try:
            await bot.send_message(
                winner_id,
                f"🎉🎁 <b>Муборак! Шумо тӯҳфаи ройгон гирифтед!</b>\n\n"
                f"Ҳамчун ташаккур барои харидатон, мағозаи мо тасодуфан шуморо "
                f"интихоб кард ва тӯҳфаро БЕПУЛ фиристод! 🎊\n\n"
                f"🎁 Тӯҳфа: <b>{label}</b>\n"
                f"🆔 Ба ин ID фиристода шуд: <code>{player['player_id']}</code>\n"
                f"{nick_line}"
                f"\n📲 Ҳоло аккаунти худро санҷед — тӯҳфа он ҷост.\n\n"
                f"🙏 Ташаккур, ки бо мо ҳастед!",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми тӯҳфа ба {winner_id} нарасид: {e}")

        total_wins = await db.count_giveaway_wins()

        # Эълони ҷамъиятӣ дар канали асосӣ — бо НОМ, вале БЕ юзернейм.
        # Ҳар бор шарҳи механизм илова мешавад — то мизоҷон "чӣ хел дигарон
        # ройгон гирифтанд?" напурсанд (тӯҳфаи возеҳ, на дархост/VIP)
        try:
            every_n = int(await db.get_setting("giveaway_every_n") or str(GIVEAWAY_DEFAULT_EVERY_N))
            # Номи чарх ва номи матн бояд ЯКХЕЛА бошанд
            spin_names = await spin_names_for(batch) if batch else None
            if spin_names and 0 <= winner_idx < len(spin_names):
                display_name = esc(spin_names[winner_idx])
            else:
                display_name = winner_name if winner_name != "—" else "Яке аз мизоҷони мо"
            caption = (
                f"🎉 <b>БАРАНДАИ НАВ!</b> 🎁\n\n"
                f"🏅 <b>{display_name}</b>\n"
                f"🎁 <b>{label}</b> — БЕПУЛ! 🍀\n\n"
                f"━━━━━━━━━━━━━━\n\n"
                f"🎲 <b>ИН ЧӢ АСТ?</b>\n\n"
                f"Ин <b>тӯҳфаи миннатдории мағозаи мо</b> ба мизоҷон. "
                f"Ҳар харидор ХУДКОР ба рӯйхат дохил мешавад.\n\n"
                f"<b>Чӣ хел кор мекунад:</b>\n"
                f"1️⃣ Шумо фармоиши <b>оддӣ</b> медиҳед\n"
                f"2️⃣ Мағоза {every_n} фармоишро ҷамъ мекунад\n"
                f"3️⃣ Бот аз ҳамон <b>{every_n} харидор ЯК нафарро "
                f"тасодуфан</b> интихоб мекунад\n"
                f"4️⃣ Ӯ тӯҳфаро <b>БЕПУЛ</b> мегирад\n\n"
                f"❗️ <b>Дархост кардан лозим НЕСТ</b>\n"
                f"❗️ <b>Пули иловагӣ додан лозим НЕСТ</b>\n"
                f"❗️ Ҳеҷ кас VIP нест — ҳама <b>баробар</b>\n\n"
                f"🏆 То ҳол <b>{total_wins} нафар</b> ройгон гирифтаанд!\n\n"
                f"💎 Фармоиш диҳед — шояд навбати шумо расад!\n\n"
                f"🤖 Боти мо: @{config.BOT_USERNAME}"
            )
            # Чархи гарданда — агар сохта шавад, эълон ҳамчун GIF меравад.
            # Матн ҳамчун caption мемонад: GIF дар Telegram беохир такрор
            # мешавад, пас маълумот бояд дар матн ҳам бошад.
            # Эълони баранда ба канали ОТЗИВ меравад, на ба канали асосӣ.
            # Агар канали отзив танзим нашуда бошад, ба канали асосӣ
            # бармегардем — вагарна эълон хомӯшона гум мешавад.
            chan = getattr(config, "REVIEW_CHANNEL_ID", "") or config.CHANNEL_ID

            gif = None
            if batch and winner_idx >= 0:
                gif = await _spin_gif_path(batch, winner_idx, label, total_wins,
                                           names=spin_names)
            if gif and os.path.isfile(gif):
                try:
                    # Ҳудуди caption дар Telegram 1024 аломат аст. Агар номи
                    # баранда хеле дароз бошад ва аз он гузарем, шарҳро
                    # ҳамчун паёми ҷудогона мефиристем — вагарна эълон
                    # тамоман нарафтан мегирад.
                    if len(caption) <= 1000:
                        await bot.send_animation(
                            chan, FSInputFile(gif),
                            caption=caption, parse_mode="HTML")
                    else:
                        head, _, rest = caption.partition("━━━━━━━━━━━━━━")
                        await bot.send_animation(
                            chan, FSInputFile(gif),
                            caption=head.strip(), parse_mode="HTML")
                        await bot.send_message(
                            chan, rest.strip(), parse_mode="HTML")
                except Exception as e:
                    logger.error(f"GIF-и чарх ба канал нарафт ({e}) — матн мефиристем")
                    await bot.send_message(chan, caption, parse_mode="HTML")
            else:
                await bot.send_message(chan, caption, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Эълони тӯҳфа ба канал нарасид: {e}")

        cost_line = ""
        if cost_usd:
            cost_line = f"💵 Арзиши тӯҳфа: {cost_usd * config.USD_TO_TJS_RATE:.2f} сом\n"

        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🎁 <b>Тӯҳфаи тасодуфӣ фиристода шуд!</b>\n\n"
                    f"👤 Баранда: {winner_name} ({winner_username})\n"
                    f"🆔 ID Telegram: <code>{winner_id}</code>\n"
                    f"🎁 {label}\n"
                    f"{cost_line}"
                    f"🆔 Фармоиш: #{order_id}\n"
                    f"🏆 Ин {total_wins}-умин тӯҳфаи додашуда аст",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Огоҳии тӯҳфа ба админ {admin_id} нарасид: {e}")
    else:
        await db.update_order_status(order_id, "failed")
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ <b>Тӯҳфаи тасодуфӣ НАШУД!</b>\n\n"
                    f"👤 Баранда: {winner_name} ({winner_username})\n"
                    f"🆔 ID Telegram: <code>{winner_id}</code>\n"
                    f"🎁 {label}\n"
                    f"🆔 Фармоиш: #{order_id}\n\n"
                    f"Лутфан дастӣ иҷро кунед.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Огоҳии хатои тӯҳфа ба админ {admin_id} нарасид: {e}")


# Фармоишҳое, ки FazerCards ВОҚЕАН "ноком" гуфтааст — дигар напурсем
_recheck_settled: set = set()

# Баъд аз чанд санҷиши бенатиҷа ба админ хабар диҳем.
# 10 санҷиш × 3 дақиқа ≈ 30 дақиқа
RECHECK_ALERT_AFTER_TRIES = 10

# Реҷаи хомӯшии шабона: аз 00:00 то 08:00 огоҳиҳои "кӯмак лозим" ҷамъ
# мешаванд ва субҳ дар ЯК паёми ҷамъбастӣ мераванд (то хоб халал нашавад).
QUIET_START_HOUR = 0
QUIET_END_HOUR = 8

# Лаҳзаи оғози бот. Фармоишҳое, ки ПЕШ аз ин сохта шудаанд, соҳиб
# аллакай ДАСТӢ ҳал кардааст — тафтишгар ба онҳо УМУМАН даст намерасонад
# (на такрор, на тасдиқ, на паём). Кор аз фармоишҳои НАВ сар мешавад.
_BOT_START_TS = datetime.now()


async def _quiet_hours_on() -> bool:
    """Реҷаи хомӯшии шабона фаъол аст ё не (админ хомӯш карда метавонад)."""
    try:
        return (await db.get_setting("quiet_hours") or "1") == "1"
    except Exception:
        return True


def _is_quiet_now() -> bool:
    return QUIET_START_HOUR <= datetime.now().hour < QUIET_END_HOUR


async def _should_defer_alert() -> bool:
    """Огоҳии «кӯмак лозим»-ро то субҳ таъхир кунем ё не.

    ХОМӮШ карда шуд (хости соҳиб): пеш аз 00:00 то 08:00 фармоишҳои
    нашуда таъхир мешуданд ва субҳ дар паёми ҷамъбастӣ БЕ расми чек
    меомаданд — соҳиб чеки фармоишҳои шабро ҳеҷ гоҳ намедид. Акнун
    ҳамеша фавран бо расми чек фиристода мешавад."""
    return False


async def _report_failure_to_admin(bot: Bot, order: dict, kod: str,
                                   api_order_id: str, uncertain: bool = False) -> bool:
    """
    Огоҳии «ин фармоиш кӯмаки шуморо мехоҳад»-ро мефиристад — ҳадди аксар
    ЯК бор барои ҳар фармоиш (claim_admin_alert).

    Дар реҷаи хомӯшии шабона (00:00–08:00) чизе фиристода намешавад ва
    аломат ҳам гузошта намешавад — субҳ quiet_digest_loop ин фармоишро
    дар ЯК паёми ҷамъбастӣ мефиристад.

    True = огоҳӣ ВОҚЕАН фиристода шуд.
    """
    if await _should_defer_alert():
        logger.info(
            f"#{order['id']}: реҷаи хомӯшии шабона — огоҳӣ то субҳ таъхир шуд"
        )
        return False
    if not await db.claim_admin_alert(order["id"]):
        return False
    await _admin_report_failure(bot, order, kod, api_order_id, uncertain)
    return True


async def _auto_retry_donate(bot: Bot, order: dict):
    """
    ЯК кӯшиши ХУДКОРИ такрорӣ барои фармоише, ки FazerCards онро ВОҚЕАН
    рад кардааст.

    Чаро ин бехатар аст: ҳолати ниҳоӣ МАЪЛУМ аст — донат НАШУДААСТ. Пас
    фармоиши нав дучандон харҷ карда наметавонад. (Агар ҳолат номаълум
    мебуд, ин ҷо ҳељ гоҳ намерасидем — ниг. recheck_loop.)

    Статус аллакай атомикӣ ба 'donating' гузаштааст
    (claim_failed_order_for_autoretry), пас рақиб нест.
    Такрори ХУДКОР танҳо ЯК бор мешавад (auto_retried=1).
    """
    order_id = order["id"]
    user_id = order["user_id"]
    try:
        async with _donate_semaphore:
            fresh = await db.get_order(order_id) or order
            success, api_order_id, uncertain, cost_usd = await _dispatch_donate_call(fresh)
            if api_order_id:
                await db.set_order_api_id(order_id, api_order_id)
    except Exception as e:
        logger.error(f"_auto_retry_donate #{order_id} хато: {e}")
        success, api_order_id, uncertain, cost_usd = False, "", True, None

    if success:
        await db.update_order_status(order_id, "confirmed")
        logger.info(f"_auto_retry_donate: #{order_id} такрори худкор МУВАФФАҚ шуд")
        await _finish_recovered_order(
            bot, order, api_order_id, cost_usd,
            admin_note=(
                "FazerCards кӯшиши аввалро рад карда буд, бот ХУДАШ як бор "
                "такрор кард ва ин дафъа муваффақ шуд. Ҳеҷ кори дастӣ "
                "лозим набуд."
            ),
            customer_text=(
                f"🎉 <b>Донати шумо анҷом ёфт!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
                f"Кӯшиши аввал нашуда буд, бот худаш такрор кард — ҳоло "
                f"ҳама чиз дуруст расид. Аккаунтатонро санҷед. 🙏"
            ),
        )
        return

    # Такрори худкор ҳам ноком шуд — акнун бо роҳи муқаррарӣ коркард
    # мешавад (auto_retried=1 мемонад, пас такрори дуюми худкор намешавад)
    logger.warning(f"_auto_retry_donate: #{order_id} такрори худкор ҳам ноком шуд")
    _recheck_settled.discard(order_id)
    await _handle_donate_failure(bot, order, "", api_order_id, uncertain)


async def _finish_recovered_order(bot: Bot, order: dict, api_order_id: str, cost_usd,
                                  admin_note: str = "", customer_text: str = ""):
    """Фармоише, ки тафтишгар/такрори худкор онро анҷомёфта кард — расман мебандад."""
    order_id = order["id"]
    user_id = order["user_id"]

    await db.set_confirmed_at(order_id)
    if cost_usd:
        try:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
        except Exception as e:
            logger.error(f"set_order_cost барои #{order_id} нашуд: {e}")

    # Мукофоти реферралӣ (агар ҳанӯз дода нашуда бошад — худаш атомикӣ аст)
    try:
        reward, referrer_id = await db.credit_referral_for_order(order_id)
        if reward and referrer_id:
            await bot.send_message(
                referrer_id,
                f"🤝 <b>Мукофоти реферралӣ!</b>\n\n"
                f"💰 Дусти шумо фармоиш дод ва шумо <b>{reward:.2f} сом</b> "
                f"ба балансаи худ гирифтед!",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"credit_referral (recheck) барои #{order_id} хато: {e}")

    if not customer_text:
        customer_text = (
            f"🎉 <b>Хушхабар — донати шумо анҷом ёфт!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
            f"Каме таъхир шуд, вале ҳама чиз дуруст расид. "
            f"Аккаунтатонро санҷед. 🙏"
        )
    try:
        await bot.send_message(
            user_id,
            customer_text + "\n\n⭐ Лутфан отзив гузоред:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")],
                [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
            ]),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Паёми барқароршуда ба {user_id} нарасид: {e}")

    if not admin_note:
        admin_note = (
            "Тафтишгари худкор санҷид: ин фармоиш дар асл ИҶРО ШУДААСТ "
            "(таймаути шабака гумроҳ карда буд). Фармоиш тасдиқ шуд ва "
            "ба мизоҷ хабар дода шуд."
        )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"✅ <b>Худкор ҲАЛ ШУД — кори дастӣ ЛОЗИМ НЕСТ!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"🆔 ID FazerCards: <code>{api_order_id}</code>\n"
                f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
                f"{admin_note}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Огоҳии барқарорсозӣ ба админ {admin_id} нарасид: {e}")


# Ҳар огоҳӣ ТАНҲО ЯК БОР меравад. Дар хотира нигоҳ доштан кофист:
# агар сервер рестарт шавад ва як огоҳӣ такрор шавад, зараре нест —
# вале сутуни нав дар база барои ҳар огоҳӣ сохтан лозим намеояд.
_alerted_loss: set = set()
_alerted_reject: set = set()
_alerted_reseller: set = set()
_last_feed_alert = 0.0


async def _alert(bot: Bot, text: str):
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳӣ ба админ {admin_id} нарасид: {e}")


AUDIT_EVERY = 3            # баъди ҳар чанд сабад санҷиш кунем


async def _audit_carts(bot: Bot):
    """
    Санҷиши ХУДКОРИ ҳисоби сабад — баъди ҳар 3 хариди сабадӣ.

    Маҳз ҳамон хатоҳоеро мегирад, ки дастӣ ёфта шуданд: сабад аз баланс
    маблағи пурра мегирифт, вале як фармоиш месохт; ва тахфиф ба ҷамъ
    татбиқ мешуд, вале нархи донаҳо бетағйир мемонд.

    Ҷои сабти «то куҷо санҷидем» дар settings аст — то баъди рестарти
    сервер санҷиш аз аввал сар нашавад ва як харид ду бор санҷида нашавад.
    """
    try:
        last_id = int(await db.get_setting("audit_last_order_id") or 0)
    except Exception:
        last_id = 0
    groups = await db.get_cart_groups_after(last_id)
    if len(groups) < AUDIT_EVERY:
        return                       # ҳанӯз 3 сабад ҷамъ нашуд

    lines, problems = [], 0
    for g in groups[:AUDIT_EVERY]:
        total = round(float(g["total"]), 2)
        bad = []
        if g["users"] != 1:
            bad.append(f"дар як гурӯҳ {g['users']} мизоҷи гуногун")
        if g["pms"] != 1:
            bad.append(f"дар як гурӯҳ {g['pms']} тариқи пардохт")
        if total <= 0:
            bad.append("ҷамъи нарх сифр ё манфӣ")
        paid_note = ""
        if g["pm"] == "referral_balance":
            tx = await db.find_purchase_tx(g["user_id"], g["created"])
            if not tx:
                bad.append("камкунии баланс ёфт нашуд")
            else:
                paid = round(abs(float(tx["amount"])), 2)
                paid_note = f" · аз баланс: {paid:.2f}"
                if abs(paid - total) >= 0.01:
                    bad.append(f"аз баланс {paid:.2f} кам шуд, вале "
                               f"фармоишҳо {total:.2f} — фарқи "
                               f"{abs(paid - total):.2f} сом")
        mark = "✅" if not bad else "❗️"
        lines.append(f"{mark} #{g['first_id']}–#{g['last_id']} · {g['n']} дона · "
                     f"{total:.2f} сом{paid_note}")
        for b in bad:
            problems += 1
            lines.append(f"     ⚠️ {b}")

    checked = groups[:AUDIT_EVERY]
    await db.set_setting("audit_last_order_id", str(max(g["last_id"] for g in checked)))

    head = ("🧮 <b>Санҷиши ҳисоби 3 сабади охирин</b>\n\n" if not problems
            else f"🚨 <b>САНҶИШ ХАТО ЁФТ ({problems})!</b>\n\n")
    tail = ("\n\nҲама ҳисобҳо дурустанд ✅" if not problems
            else "\n\n❗️ Инро дастӣ санҷед — эҳтимол мизоҷ камтар "
                 "гирифтааст ё зиёдтар пардохтааст.")
    await _alert(bot, head + "\n".join(lines) + tail)


async def _watch_losses(bot: Bot):
    """Фурӯш ба зарар — ҳамон рӯз, на дар ҳисоботи шаб."""
    for o in await db.get_loss_orders():
        if o["id"] in _alerted_loss:
            continue
        _alerted_loss.add(o["id"])
        price, cost = float(o["price"]), float(o["cost_tjs"])
        await _alert(bot, (
            f"📉 <b>Ба ЗАРАР фурӯхта шуд!</b>\n\n"
            f"🆔 Фармоиш: #{o['id']}\n"
            f"🎁 {esc(o['label'])}\n"
            f"💵 Фурӯхтем: <b>{price:.2f} сом</b>\n"
            f"🏷 Арзиши харид: <b>{cost:.2f} сом</b>\n"
            f"➖ Зарар: <b>{cost - price:.2f} сом</b>\n\n"
            f"Эҳтимол нархи провайдер боло рафтааст — нархи худро санҷед."
        ))


async def _watch_payment_feed(bot: Bot):
    """
    Пардохтҳо тамоман намеоянд, вале мизоҷон интизоранд — эҳтимол
    ҳамон ҳолати имрӯзаи «Душанбе Сити кор намекунад».
    """
    global _last_feed_alert
    if time.time() - _last_feed_alert < 3600:
        return
    h = await db.payment_feed_health(FEED_QUIET_MIN)
    last = h.get("last_min")
    if h["waiting"] < FEED_MIN_WAITING or last is None or last < FEED_QUIET_MIN:
        return
    _last_feed_alert = time.time()
    await _alert(bot, (
        f"🏦 <b>Пардохтҳо намеоянд!</b>\n\n"
        f"⏳ Охирин пардохт: <b>{last} дақиқа</b> пеш\n"
        f"👥 Интизори пардохт: <b>{h['waiting']} фармоиш</b>\n\n"
        f"Мизоҷон пардохт мекунанд, вале ба система чизе намерасад — "
        f"эҳтимол мушкили бонк аст.\n"
        f"Санҷед ва агар лозим бошад, ба мизоҷон эълон диҳед."
    ))


async def _watch_problem_customers(bot: Bot):
    """Мизоҷе, ки такроран фармоишаш рад мешавад — шояд мушкиле дорад."""
    for r in await db.get_repeat_rejected_users():
        key = (r["user_id"], r["last_id"])
        if key in _alerted_reject:
            continue
        _alerted_reject.add(key)
        u = await db.get_user(r["user_id"])
        name = esc(u.get("full_name")) if u and u.get("full_name") else "—"
        uname = f"@{u['username']}" if u and u.get("username") else "—"
        await _alert(bot, (
            f"🔁 <b>Мизоҷ такроран рад мешавад</b>\n\n"
            f"👤 {name} ({esc(uname)})\n"
            f"🆔 ID: <code>{r['user_id']}</code>\n"
            f"❌ Дар 7 рӯзи охир: <b>{r['n']} фармоиши радшуда</b>\n\n"
            f"Шояд ӯ чизеро нафаҳмидааст ё мушкиле дорад — "
            f"агар худатон нависед, шояд мизоҷи доимӣ шавад."
        ))


async def _watch_resellers(bot: Bot):
    """Як мизоҷ ба ID-ҳои зиёди гуногун донат мекунад — эҳтимол фурӯшанда.

    Рӯйхати «аллакай хабар додашуда» дар БАЗА нигоҳ дошта мешавад (на танҳо
    дар хотира) — вагарна баъди ҲАР рестарти сервер ҳамон огоҳиҳо аз нав
    мерафтанд ва соҳибро безор мекарданд."""
    global _alerted_reseller
    if not _alerted_reseller:
        # Бори аввал баъди оғоз — аз база бор мекунем
        raw = await db.get_setting("alerted_resellers")
        if raw:
            try:
                _alerted_reseller = set(json.loads(raw))
            except Exception:
                _alerted_reseller = set()
    changed = False
    for r in await db.get_multi_id_users():
        if r["user_id"] in _alerted_reseller:
            continue
        _alerted_reseller.add(r["user_id"])
        changed = True
        u = await db.get_user(r["user_id"])
        name = esc(u.get("full_name")) if u and u.get("full_name") else "—"
        uname = f"@{u['username']}" if u and u.get("username") else "—"
        await _alert(bot, (
            f"🏪 <b>Эҳтимол фурӯшандаи хурд</b>\n\n"
            f"👤 {name} ({esc(uname)})\n"
            f"🆔 ID: <code>{r['user_id']}</code>\n"
            f"🎮 Ба <b>{r['ids']} ID-и гуногун</b> донат кардааст\n"
            f"🛒 {r['n']} фармоиш · <b>{float(r['total']):.2f} сом</b> дар 30 рӯз\n\n"
            f"Ин мизоҷ эҳтимол худаш ба дигарон мефурӯшад. "
            f"Нархи шахсӣ пешниҳод кунед — то ба ҷои дигар наравад."
        ))
    if changed:
        try:
            await db.set_setting("alerted_resellers",
                                 json.dumps(sorted(_alerted_reseller)))
        except Exception as e:
            logger.error(f"alerted_resellers сабт нашуд: {e}")


async def _report_unknown_statuses(bot: Bot):
    """
    Агар провайдер ҳолати НАВЕ фиристад, ки бот онро намешиносад — ҳамон
    рӯз ба соҳиб хабар медиҳад.

    Чаро ин лозим шуд: FazerCards барои «Возврат» калимаи 'refund'
    фиристод, вале дар рӯйхати бот 'refunded' буд. Бот онро «ҳанӯз дар
    ҷараён» ҳисоб кард, фармоишҳо овезон монданд ва ин танҳо баъди
    шикояти мизоҷ маълум шуд. Ҳар ҳолати нав танҳо ЯК бор хабар дода
    мешавад — то ҳар 3 дақиқа такрор нашавад.
    """
    for status, rec in list(ff_api.UNKNOWN_STATUSES.items()):
        if rec.get("reported"):
            continue
        rec["reported"] = True
        text = (
            f"⚠️ <b>Провайдер ҳолати НОШИНОС фиристод!</b>\n\n"
            f"🔤 Ҳолат: <code>{esc(status)}</code>\n"
            f"🆔 Фармоиш: <code>{esc(rec.get('order') or '—')}</code>\n"
            f"🔁 Чанд бор дучор шуд: {rec.get('count', 1)}\n\n"
            f"Бот ин калимаро намешиносад, пас фармоишро «ҳанӯз дар ҷараён» "
            f"ҳисоб мекунад ва интизор мешавад.\n\n"
            f"❗️ Агар ин ҳолати НИҲОӢ бошад (мисли «Возврат»), фармоиш "
            f"абадан овезон мемонад ва «Дубора донат» кор намекунад. "
            f"Инро ба ман нависед — ман як сатр илова мекунам ва ҳал мешавад."
        )
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Огоҳии ҳолати ношинос ба {admin_id} нарасид: {e}")


async def recheck_loop(bot: Bot, interval_seconds: int = 180):
    """
    ТАФТИШГАРИ ХУДКОРИ ФАРМОИШҲОИ «ОВЕЗОН».

    Ҳар 3 дақиқа фармоишҳои 'failed'-ро, ки дар FazerCards/MooGold ID-и
    воқеӣ доранд, аз нав мепурсад:
      • completed  → худкор тасдиқ мекунад, ба мизоҷ ва админ хабар медиҳад
      • processing → ҳоло чизе намекунад, дафъаи оянда боз мепурсад
      • failed     → ЯК бор такрори худкор, баъд ба админ

    ХУДИ ҲАЛҚА ТАНҲО МЕХОНАД. Фармоиши нав танҳо дар як ҳолат сохта
    мешавад: агар FazerCards ХУДАШ "рад шуд" гуфта бошад — яъне ҳолати
    ниҳоӣ маълум бошад ва донат НАШУДА бошад.

    Фармоишҳои ПЕШ аз оғози бот сохташуда тамоман сарфи назар мешаванд —
    соҳиб онҳоро дастӣ ҳал кардааст ва бот набояд ба онҳо халал расонад.
    """
    await asyncio.sleep(45)  # то боти асосӣ пурра сар шавад
    while True:
        try:
            orders = await db.get_stuck_donate_orders()
            for order in orders:
                order_id = order["id"]
                if order_id in _recheck_settled:
                    continue
                api_order_id = (order.get("api_order_id") or "").strip()
                if not api_order_id:
                    continue

                # Фармоиши "кӯҳна" = пеш аз оғози ин версияи бот сохта шуда.
                # Соҳиб онҳоро ДАСТӢ ҳал кардааст — тафтишгар ба онҳо
                # умуман даст намерасонад: на такрор, на тасдиқ, на паём.
                # Тафтишгар ТАНҲО аз фармоишҳои НАВ сар мекунад.
                created_at = order.get("created_at")
                if created_at and created_at < _BOT_START_TS:
                    _recheck_settled.add(order_id)
                    continue

                state, cost_usd = await ff_api.peek_order_status(api_order_id)

                if state == "completed":
                    # Атомикӣ — то агар админ маҳз ҳамин лаҳза дастӣ тасдиқ
                    # кунад, ду бор паём/мукофот нашавад
                    if not await db.claim_stuck_order_confirmed(order_id):
                        _recheck_settled.add(order_id)
                        continue
                    logger.info(
                        f"recheck_loop: фармоиши #{order_id} ({api_order_id}) "
                        f"дар асл ИҶРО шудааст — худкор тасдиқ шуд"
                    )
                    _recheck_settled.add(order_id)
                    await _finish_recovered_order(bot, order, api_order_id, cost_usd)

                elif state == "failed":
                    # Ҷавоби ВОҚЕИИ "ноком". Дар ин ҳолат такрор кардан
                    # 100% бехатар аст (ҳолати ниҳоӣ маълум — донат
                    # НАШУДААСТ), пас бот ЯК бор ХУДАШ такрор мекунад.
                    _recheck_settled.add(order_id)
                    if await db.claim_failed_order_for_autoretry(order_id):
                        logger.info(
                            f"recheck_loop: #{order_id} ({api_order_id}) "
                            f"ВОҚЕАН рад шудааст — такрори ХУДКОР оғоз шуд"
                        )
                        asyncio.create_task(_auto_retry_donate(bot, order))
                    else:
                        # Аллакай як бор худкор такрор шуда буд — акнун
                        # кӯмаки админ лозим аст
                        await _report_failure_to_admin(
                            bot, order, "", api_order_id, uncertain=False)

                else:
                    # Ҳанӯз "дар ҷараён" ё ҷавоб нест — сабр мекунем.
                    # Танҳо агар хеле дер шавад, ЯК бор ба админ хабар медиҳем
                    # (вале санҷиданро бас намекунем — шояд боз ҳал шавад).
                    tries = await db.bump_recheck_tries(order_id)
                    if tries >= RECHECK_ALERT_AFTER_TRIES:
                        if await _report_failure_to_admin(
                                bot, order, "", api_order_id, uncertain=True):
                            logger.warning(
                                f"recheck_loop: #{order_id} ({api_order_id}) "
                                f"баъд аз {tries} санҷиш ҳал нашуд — админ "
                                f"хабар гирифт"
                            )

                await asyncio.sleep(1)  # ба API фишор наорем

            await _report_unknown_statuses(bot)

            # Огоҳиҳои дигар — ҳар кадом ҷудо, то хатои яке бақияро нахобонад
            for watch in (_audit_carts, _watch_losses, _watch_payment_feed,
                          _watch_problem_customers, _watch_resellers):
                try:
                    await watch(bot)
                except Exception as e:
                    logger.error(f"Огоҳии {watch.__name__} нашуд: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар recheck_loop: {e}")

        await asyncio.sleep(interval_seconds)


_STATUS_LABELS_SHORT = {
    "paid": "чек омада, интизори тасдиқ",
    "failed": "донат нашуд",
}


def _order_need_line(o: dict, now: datetime) -> str:
    """Як сатри рӯйхати «кор барои ман»."""
    created_at = o.get("created_at")
    age_min = int((now - created_at).total_seconds() / 60) if created_at else 0
    age = f"{age_min} дақ" if age_min < 60 else f"{age_min // 60} соат"
    what = "💰 пуркунии баланс" if o.get("is_balance_topup") else o.get("label") or "—"
    st = _STATUS_LABELS_SHORT.get(o.get("status"), o.get("status") or "—")
    return f"#{o['id']} — {what} — {float(o['price']):.2f} сом — {st} — {age} пеш"


async def quiet_digest_loop(bot: Bot, interval_seconds: int = 300):
    """
    ҶАМЪБАСТИ СУБҲ.

    Дар реҷаи хомӯшии шабона (00:00–08:00) огоҳиҳои «ин фармоиш кӯмак
    мехоҳад» фиристода намешаванд — то хоби соҳиб халал нашавад. Ҳамин
    ки соати 08:00 расид, ҳамаи он фармоишҳо дар ЯК паём мераванд.

    Агар шаб ҳеҷ чиз ҷамъ нашуда бошад, ҳељ паём фиристода намешавад.
    """
    await asyncio.sleep(60)
    while True:
        try:
            now = datetime.now()
            today_key = now.strftime("%Y-%m-%d")
            # Танҳо дар равзанаи субҳ (08:00–11:00) ва танҳо ЯК бор дар рӯз —
            # то агар бот нисфирӯзӣ рестарт шавад, "ҷамъбасти субҳ" беҷо наравад
            if (QUIET_END_HOUR <= now.hour < QUIET_END_HOUR + 3
                    and await _quiet_hours_on()):
                last = await db.get_setting("quiet_digest_last") or ""
                if last != today_key:
                    orders = await db.get_unalerted_admin_orders(hours=12)
                    await db.set_setting("quiet_digest_last", today_key)
                    if orders:
                        lines = [
                            f"🌅 <b>Субҳ ба хайр! Шаб {len(orders)} фармоиш "
                            f"кӯмаки шуморо мехоҳад:</b>\n"
                        ]
                        for o in orders:
                            lines.append(_order_need_line(o, now))
                            try:
                                await db.claim_admin_alert(o["id"])
                            except Exception:
                                pass
                        lines.append(
                            "\n👇 Барои кор кардан «🛠 Кор барои ман»-ро кушоед."
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛠 Кор барои ман",
                                                  callback_data="a_my_work")]
                        ])
                        for admin_id in config.ADMIN_IDS:
                            try:
                                await bot.send_message(
                                    admin_id, "\n".join(lines),
                                    reply_markup=kb, parse_mode="HTML")
                            except Exception as e:
                                logger.error(f"Ҷамъбасти субҳ ба {admin_id} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар quiet_digest_loop: {e}")

        await asyncio.sleep(interval_seconds)


AUTO_ARCHIVE_DEFAULT_DAYS = 7
ARCHIVE_HOUR = 9  # соати ҷамъбасти рӯзона


async def auto_archive_loop(bot: Bot, interval_seconds: int = 300):
    """
    ХУДКОР БАСТАНИ ФАРМОИШҲОИ ФАРОМӮШШУДА.

    Ҳар рӯз соати 09:00 фармоишҳои 'пардохтшуда'-ро, ки N рӯз (пешфарз 7)
    боз касе ба онҳо даст нарасондааст, ба ҳолати 'archived' мегузаронад
    ва ба админ рӯйхаташонро мефиристад.

    Чаро лозим: чунин фармоишҳо абадӣ ҷамъ мешаванд (дар ин сервер 800+)
    ва рақамҳои панелро вайрон мекунанд. 'archived' статуси НАВ аст —
    ҳељ як ҳисобот ва ҳисоби фоида онро намегирад, пул гум намешавад.
    """
    await asyncio.sleep(90)
    while True:
        try:
            if (await db.get_setting("auto_archive") or "1") == "1":
                now = datetime.now()
                today_key = now.strftime("%Y-%m-%d")
                last = await db.get_setting("auto_archive_last") or ""
                # Танҳо дар РАВЗАНАИ 09:00–12:00, на "ҳар вақт баъд аз 09:00".
                # Вагарна ҳангоми рестарти нимишабӣ тозакунӣ ФАВРАН иҷро
                # мешавад — на он вақте ки соҳиб интизор аст.
                if ARCHIVE_HOUR <= now.hour < ARCHIVE_HOUR + 3 and last != today_key:
                    await db.set_setting("auto_archive_last", today_key)
                    days = int(await db.get_setting("auto_archive_days")
                               or str(AUTO_ARCHIVE_DEFAULT_DAYS))
                    res = await db.archive_stale_paid_orders(days)
                    if res["count"]:
                        logger.info(
                            f"auto_archive_loop: {res['count']} фармоиши "
                            f"фаромӯшшуда (аз {days} рӯз кӯҳнатар) баста шуд"
                        )
                        ids = res["ids"]
                        shown = ", ".join(f"#{i}" for i in ids[:25])
                        more = (f" ва боз {len(ids) - 25}-то"
                                if len(ids) > 25 else "")
                        for admin_id in config.ADMIN_IDS:
                            try:
                                await bot.send_message(
                                    admin_id,
                                    f"🧹 <b>Тозакунии худкор</b>\n\n"
                                    f"<b>{res['count']}</b> фармоиш аз "
                                    f"<b>{days} рӯз</b> зиёд бе ҷавоб монда "
                                    f"буд — ба архив гузаронида шуд "
                                    f"(ҷамъан {res['sum']:.2f} сом).\n\n"
                                    f"🔒 Ҳељ чиз нест нашуд — пул, чек ва "
                                    f"таърих ҷойи худ. Онҳо танҳо аз "
                                    f"рӯйхати «Кор барои ман» баромаданд.\n\n"
                                    f"🆔 {shown}{more}",
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Хабари тозакунӣ ба {admin_id} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар auto_archive_loop: {e}")

        await asyncio.sleep(interval_seconds)


async def giveaway_loop(bot: Bot, interval_seconds: int = 60):
    """
    Ҳар дақиқа шумораи умумии фармоишҳои тасдиқшударо месанҷад. Ҳар боре,
    ки он ба каратаи N (масалан 25) нав мерасад, аз ҳамон N фармоиши охирин
    як барандаи тасодуфиро интихоб карда, маҳсулоти танзимшударо ба ӯ БЕПУЛ
    худкор донат мекунад. Агар маҳсулоти тӯҳфа ҳанӯз танзим нашуда бошад
    (аз admin), функсия хомӯш чизе намекунад.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            product_id_str = await db.get_setting("giveaway_product_id")
            if not product_id_str:
                continue

            every_n = int(await db.get_setting("giveaway_every_n") or str(GIVEAWAY_DEFAULT_EVERY_N))
            total_confirmed = await db.count_confirmed_orders()
            last_multiple = int(await db.get_setting("giveaway_last_multiple") or "0")
            current_multiple = total_confirmed // every_n

            if current_multiple <= last_multiple:
                # Ҳанӯз ба каратаи нав нарасидааст — агар наздик бошем,
                # ба харидорони ин давра огоҳии "наздикӣ" мефиристем (як
                # бор дар як давра, то флуд нашавад)
                position = total_confirmed % every_n
                remaining = every_n - position if position else every_n
                if position > 0 and remaining <= GIVEAWAY_NEAR_MISS_THRESHOLD:
                    already = await db.get_setting("giveaway_near_miss_notified_multiple")
                    if already != str(last_multiple):
                        offset = last_multiple * every_n
                        batch = await db.get_confirmed_batch_user_ids_recent(offset, position, hours=24)
                        for uid in set(batch):
                            try:
                                await bot.send_message(
                                    uid,
                                    f"🔥 <b>Тӯҳфаи навбатӣ наздик аст!</b>\n\n"
                                    f"Боз танҳо <b>{remaining} фармоиш</b> монд — ва бот "
                                    f"аз байни харидорон ЯК нафарро тасодуфан интихоб "
                                    f"мекунад.\n\n"
                                    f"✅ Шумо аллакай дар рӯйхат ҳастед — фармоиши шумо "
                                    f"худкор дохил шудааст. Ҳеҷ кор кардан лозим нест! 🍀",
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                logger.error(f"Огоҳии наздикии тӯҳфа ба {uid} нарасид: {e}")
                        await db.set_setting("giveaway_near_miss_notified_multiple", str(last_multiple))
                continue

            # Ҳимояи иловагӣ: ҳатто агар шумораи каратаҳои гузашта хеле
            # зиёд бошад (масалан ҳисобкунак хато монда буд ё бекфони
            # калон ҷамъ шуда буд), дар ЯК давра НА БЕШТАР АЗ 1 тӯҳфа
            # мефиристем (на current_multiple - last_multiple адад якбора)
            # — то флуди тӯҳфаҳо ҳељ гоҳ такрор нашавад. Агар якчанд карата
            # қафо монда бошад, дар давраҳои навбатӣ якто-якто ҷуброн мешавад.
            next_multiple = last_multiple + 1
            offset = (next_multiple - 1) * every_n
            batch = await db.get_confirmed_batch_user_ids(offset, every_n)
            if batch:
                # Индексро мегирем, на танҳо ID — то чархи тӯҳфа маҳз дар
                # ҲАМОН сектор истад, ки баранда дар он аст
                widx = random.randrange(len(batch))
                winner_id = batch[widx]
                asyncio.create_task(_send_giveaway_gift(
                    bot, winner_id, int(product_id_str), batch, widx))
            await db.set_setting("giveaway_last_multiple", str(next_multiple))
        except Exception as e:
            logger.error(f"Хатогӣ дар giveaway_loop: {e}")
