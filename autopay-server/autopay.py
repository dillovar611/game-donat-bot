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

MAX_AGE_MINUTES = 15      # мӯҳлати умумии фармоиши автопардохт
SEARCH_TIMEOUT_MIN = 10   # чек омад, вале пардохт то ин дақиқа ёфт нашуд → ба админ

# Навбати автодонат — донатҳо паси ҳам иҷро мешаванд
_donate_lock = asyncio.Lock()
_queue_count = 0  # чанд фармоиш ҳоло дар навбат/кор аст

# Монеаи иловагӣ (дар хотираи барнома, на база) — зидди он ки run_donate
# ду бор ҳамзамон барои ҲАМОН фармоиш сар шавад (пеш аз он ки дархости
# claim_order_for_donate ба база расад). Хеле тезтар аз DB-claim, пас
# race-ро дар ҳамон лаҳза мебандад.
_in_flight_orders: set[int] = set()


def esc(text) -> str:
    if text is None:
        return ""
    return html.escape(str(text), quote=False)


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
        if (order and order.get("payment_method") in ("dushanbe_city", "alif")
                and order.get("status") in ("autopay_search", "awaiting_autopay")):
            # Маблағро месанҷем — бояд бо нархи фармоиш баробар бошад
            if abs(float(order["price"]) - summa) > 0.011:
                await _notify_admins_wrong_amount(message.bot, order, summa, kod)
                return
            # Kod-ро ба ин фармоиш мебандем (резерв, зидди такрор)
            await db.mark_kod_matched(kod, order_ref)
            if order["status"] == "autopay_search":
                # Чек аллакай омадааст → фавран донат
                asyncio.create_task(run_donate(message.bot, order, kod))
            else:
                # Чек ҳанӯз наомадааст → интизор; вақте чек ояд,
                # buy.py ҳамин Kod-и резервшударо меёбад
                logger.info(f"Autopay: пардохти #{order_ref} омад, чек интизор")
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
            if order.get("status") not in ("autopay_search", "awaiting_autopay"):
                continue
            if abs(float(order["price"]) - summa) > 0.011:
                await _notify_admins_wrong_amount(message.bot, order, summa, synth_kod)
                continue
            await db.mark_kod_matched(synth_kod, int(order_ref))
            if order["status"] == "autopay_search":
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
        f"Эҳтимол: мизоҷ маблағи ГАЛАТ фиристод, дер фиристод (мӯҳлат гузашт), "
        f"ё ин пардохти шахсист. Дастӣ тафтиш кунед."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Огоҳии пардохти ношинос ба {admin_id} нарасид: {e}")


async def _admin_report_success(bot: Bot, order: dict, kod: str, api_order_id: str):
    """Ҳисоботи муфассал ба админ баъд аз донати муваффақ."""
    user = await db.get_user(order["user_id"])
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    created_at = order.get("created_at")
    time_str = created_at.strftime("%H:%M") if created_at else "—"
    api_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""
    text = (
        f"⚡ <b>АВТОТАСДИҚ — Донат муваффақ шуд!</b>\n\n"
        f"👤 Харидор: {esc(full_name)}\n"
        f"📱 Username: {esc(username)}\n"
        f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
        f"💵 Нархи маҳсулот: {float(order['price']):.2f} сомонӣ\n"
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


async def _admin_report_failure(bot: Bot, order: dict, kod: str, api_order_id: str):
    """Пардохт омад, вале донат нашуд — админ бо тугмаҳо огоҳ мешавад."""
    user = await db.get_user(order["user_id"])
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    api_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=f"ok_{order['id']}")],
        [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order['id']}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order['id']}")],
    ])
    text = (
        f"⚠️ <b>ПАРДОХТ ОМАД, вале донати худкор НАШУД!</b>\n\n"
        f"👤 Харидор: {esc(full_name)} ({esc(username)})\n"
        f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
        f"💵 Маблағ: {float(order['price']):.2f} сомонӣ\n\n"
        f"🆔 Фармоиш: #{order['id']}\n"
        f"{api_line}"
        f"🎁 {order['label']} → <code>{order['game_id']}</code>\n\n"
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
        if ahead > 0:
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
        async with _donate_lock:
            # Бехатарии иловагӣ: пеш аз фиристодан ба API, аз база маълумоти
            # ТОЗАРО мехонем (на он чи дар аввали функсия дошта будем) — то
            # агар ин фармоиш аллакай ба FazerCards/MooGold фиристода шуда
            # бошад (масалан бо даъвати параллели дигар), ID-и мавҷударо
            # истифода барем, на фармоиши комилан НАВ созем (зидди донати
            # дукарата — зарари молиявӣ).
            fresh_order = await db.get_order(order_id) or order
            if fresh_order.get("status") not in ("paid",):
                logger.warning(
                    f"Autopay: фармоиши #{order_id} дигар 'paid' нест "
                    f"(ҳозир: {fresh_order.get('status')}) — Марҳилаи 3 гузаронида шуд"
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
                fresh_order["game_id"], fresh_order["offer_id"], fresh_order.get("api_order_id") or ""
            )
            if progress_msg:
                success, api_order_id = await _run_with_live_progress_text(progress_msg, header, donate_coro)
            else:
                success, api_order_id = await donate_coro

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
                f"🙏 Ташаккур барои харид!\n\n"
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
        await _admin_report_failure(bot, order, kod, api_order_id)


async def expiry_loop(bot: Bot, interval_seconds: int = 60):
    """
    Ҳар дақиқа:
      - фармоишҳои 'awaiting_autopay' (чек наомада) > 15 дақ → 'expired' + хабар
      - фармоишҳои 'autopay_search' (чек омада, пардохт ёфт нашуда) > 10 дақ →
        чек ба админ барои тафтиши ДАСТӢ (бо тугмаҳои Тасдиқ/Рад)
    """
    while True:
        await asyncio.sleep(interval_seconds)

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
