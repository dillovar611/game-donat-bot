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
import random
import time
import unicodedata
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
# Коменти пардохт: мо ба линк c=card_8848 мегузорем, DC онро ҳамчун
# "card§8848" (ё card_8848) дар notification нишон медиҳад
_CARD_RE = re.compile(r"card\D{0,3}(\d{1,10})", re.IGNORECASE)

MAX_AGE_MINUTES = 20      # мӯҳлати умумии фармоиши автопардохт
SEARCH_TIMEOUT_MIN = 10   # чек омад, вале пардохт то ин дақиқа ёфт нашуд → ба админ
EXPIRY_WARN_BEFORE_MIN = 3  # чанд дақиқа пеш аз итмоми мӯҳлат огоҳ кунем

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
    parsed = _parse_notification(text)
    if not parsed:
        return
    summa, kod, order_ref = parsed

    if await db.is_kod_seen(kod):
        logger.info(f"Autopay: Kod {kod} такрорист — нодида гирифта шуд")
        return
    await db.record_kod(kod, summa)

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
        if await db.is_kod_seen(synth_kod):
            continue
        await db.record_kod(synth_kod, summa)

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
    _gid = order.get("game_id") or ""
    if _gid.startswith("FFID:"):
        retry_cb = f"okffid_{order['id']}"
    elif _gid.startswith("PUBG:"):
        retry_cb = f"okpubg_{order['id']}"
    elif _gid.startswith("STARS:"):
        retry_cb = f"okstars_{order['id']}"
    elif _gid.startswith("PREMIUM:"):
        retry_cb = f"okpremium_{order['id']}"
    else:
        retry_cb = f"ok_{order['id']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=retry_cb)],
        [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order['id']}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order['id']}")],
    ])
    if uncertain:
        warning_line = (
            f"\n⚠️⚠️ <b>ДИҚҚАТ: ин на радди воқеӣ аст — шабака ба FazerCards "
            f"такроран таймаут задааст ва мо ҲОЛАТИ НИҲОИИ ВОҚЕИРО намедонем!</b>\n"
            f"Фармоиш дар FazerCards (ID боло) шояд АЛЛАКАЙ иҷро шуда бошад. "
            f"Пеш аз «Дубора донат», ҳатман дар FazerCards санҷед — вагарна "
            f"ду бор донат мешавад!\n"
        )
    else:
        warning_line = ""
    text = (
        f"⚠️ <b>ПАРДОХТ ОМАД, вале донати худкор НАШУД!</b>\n\n"
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

            donate_coro = ff_api.auto_donate(
                fresh_order["game_id"], fresh_order["offer_id"], fresh_order.get("api_order_id") or "",
                order_id
            )
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
                f"🎁 Шумо ҳоло дар қуръакашии тӯҳфаи ройгон ҳастед — шояд навбати шумо расад! 🍀\n\n"
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
        await db.update_order_status(order_id, "failed")
        try:
            await bot.send_message(
                user_id,
                f"⚠️ <b>Пардохти шумо қабул шуд, вале донат каме ба таъхир афтод.</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"Хавотир нашавед — админ огоҳ карда шуд ва ба зудӣ "
                f"дастӣ ҳал мекунад. Пулатон бехатар аст. 🙏",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми таъхир ба {user_id} нарасид: {e}")
        if uncertain:
            await db.flag_order_uncertain(order_id)
        await _admin_report_failure(bot, order, kod, api_order_id, uncertain)


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
            success, api_order_id, uncertain, cost_usd = await ff_api.auto_donate(
                fresh_order["game_id"], fresh_order["offer_id"], fresh_order.get("api_order_id") or "",
                order_id
            )
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
            await db.update_order_status(order_id, "failed")
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>Пардохти шумо қабул шуд, вале донат каме ба таъхир афтод.</b>\n\n"
                    f"🆔 Фармоиш: #{order_id}\n\n"
                    f"Хавотир нашавед — админ огоҳ карда шуд ва ба зудӣ "
                    f"дастӣ ҳал мекунад. Пулатон бехатар аст. 🙏",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Паёми таъхир (эскалатсия) ба {user_id} нарасид: {e}")
            if uncertain:
                await db.flag_order_uncertain(order_id)
            await _admin_report_failure(bot, order, kod, api_order_id, uncertain)
    finally:
        _in_flight_orders.discard(order_id)


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
    game_id = order["game_id"] or ""

    await db.update_order_status(order_id, "donating")

    async with _donate_semaphore:
        # Ҳар хизмат API-и худро дорад (game_id бо префикс фарқ мекунад —
        # мисли admin.py-и order_confirm_ffid/_pubg/_stars/_premium аллакай
        # мекунанд). FFID/PUBG 2-tuple бармегардонанд (uncertain/cost_usd надоранд).
        # Даъвати API дар try — то агар exception партояд, фармоиш дар 'donating'
        # гир намонад (баланс аллакай кам шудааст) — онро ҳамчун ноком коркард
        # мекунем ва админ огоҳ мешавад (мисли уncertain).
        try:
            if game_id.startswith("FFID:"):
                player_id = game_id.replace("FFID:", "")
                success, api_order_id = await ff_api.auto_donate_ffid(
                    player_id, order["offer_id"], order.get("api_order_id") or "", order_id
                )
                uncertain, cost_usd = False, None
            elif game_id.startswith("PUBG:"):
                player_id = game_id.replace("PUBG:", "")
                success, api_order_id = await ff_api.auto_donate_pubg(
                    player_id, order["offer_id"], order.get("api_order_id") or "", order_id
                )
                uncertain, cost_usd = False, None
            elif game_id.startswith("STARS:"):
                tg_username = game_id.replace("STARS:", "")
                success, api_order_id, uncertain, cost_usd = await ff_api.buy_telegram_stars(
                    tg_username, order["amount"], order_id
                )
            elif game_id.startswith("PREMIUM:"):
                tg_username = game_id.replace("PREMIUM:", "")
                success, api_order_id, uncertain, cost_usd = await ff_api.buy_telegram_premium(
                    tg_username, order["amount"], order_id
                )
            else:
                success, api_order_id, uncertain, cost_usd = await ff_api.auto_donate(
                    game_id, order["offer_id"], order.get("api_order_id") or "",
                    order_id
                )
        except Exception as e:
            logger.error(f"run_donate_from_balance: ff_api хато барои #{order_id}: {e}")
            success, api_order_id, uncertain, cost_usd = False, "", True, None
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
        await db.update_order_status(order_id, "failed")
        try:
            await bot.send_message(
                user_id,
                f"⚠️ <b>Пардохти шумо қабул шуд, вале донат каме ба таъхир афтод.</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n\n"
                f"Хавотир нашавед — админ огоҳ карда шуд ва ба зудӣ "
                f"дастӣ ҳал мекунад. Пулатон бехатар аст. 🙏",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми таъхир (аз баланс) ба {user_id} нарасид: {e}")
        if uncertain:
            await db.flag_order_uncertain(order_id)
        await _admin_report_failure(bot, order, "", api_order_id, uncertain)


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
            await db.recover_stuck_donating_orders(3)
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

        # ---- Чек наомада, мӯҳлат гузашт ----
        try:
            stale = await db.expire_stale_awaiting_orders(MAX_AGE_MINUTES)
            for order in stale:
                try:
                    await bot.send_message(
                        order["user_id"],
                        f"⏳ <b>Вақти пардохти фармоиши #{order['id']} гузашт.</b>\n\n"
                        f"Агар аллакай пардохт карда бошед, расми чекро ҳозир ҳам ба ин "
                        f"чат фиристед — системаи мо боз ҳам кӯшиш мекунад худкор донат кунад.\n"
                        f"Агар пардохт накарда бошед, метавонед фармоиши нав созед.",
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
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Тасдиқ — донат кун", callback_data=f"ok_{order['id']}")],
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


async def _send_giveaway_gift(bot: Bot, winner_id: int, product_id: int):
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
        try:
            await bot.send_message(
                winner_id,
                f"🎉🎁 <b>Муборак! Шумо барандаи тӯҳфаи ройгон шудед!</b>\n\n"
                f"Ҳамчун ташаккур барои харидатон, системаи мо тасодуфан шуморо "
                f"интихоб кард ва <b>{label}</b> ба ҳисоби шумо БЕПУЛ фиристод! 🎊\n\n"
                f"🙏 Ташаккур, ки бо мо ҳастед!",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Паёми тӯҳфа ба {winner_id} нарасид: {e}")

        total_wins = await db.count_giveaway_wins()

        # Эълони ҷамъиятӣ дар канали асосӣ — бо НОМ, вале БЕ юзернейм.
        # Ҳар бор шарҳи механизм илова мешавад — то мизоҷон "чӣ хел дигарон
        # ройгон гирифтанд?" напурсанд (қуръаи возеҳ, на дархост/VIP)
        try:
            every_n = int(await db.get_setting("giveaway_every_n") or str(GIVEAWAY_DEFAULT_EVERY_N))
            display_name = winner_name if winner_name != "—" else "Яке аз мизоҷони мо"
            await bot.send_message(
                config.CHANNEL_ID,
                f"🎉🎁 <b>Тӯҳфаи ройгон дода шуд!</b>\n\n"
                f"{display_name} тасодуфан интихоб шуд ва <b>{label}</b>-ро "
                f"БЕПУЛ гирифт! 🍀\n\n"
                f"🏆 Ин <b>{total_wins}-умин</b> барандаи мо аст!\n\n"
                f"🎲 <b>Чӣ гуна кор мекунад?</b> Ҳар <b>{every_n}-умин</b> фармоиши "
                f"муваффақ — як БАРАНДАИ ТАСОДУФӢ аз ҳамон {every_n} харидор интихоб "
                f"мешавад ва тӯҳфаи БЕПУЛ мегирад. Ин қуръа аст — на дархост, на "
                f"VIP; ҳар кас имкони баробар дорад!\n"
                f"Шумо низ фармоиш диҳед — шояд навбати шумо расад! 💎",
                parse_mode="HTML"
            )
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
                                    f"Боз танҳо <b>{remaining} фармоиш</b> монд — шумо ҳам "
                                    f"дар қуръа ҳастед! 🍀",
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
                winner_id = random.choice(batch)
                asyncio.create_task(_send_giveaway_gift(bot, winner_id, int(product_id_str)))
            await db.set_setting("giveaway_last_multiple", str(next_multiple))
        except Exception as e:
            logger.error(f"Хатогӣ дар giveaway_loop: {e}")
