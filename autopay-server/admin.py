"""
Панели админ:
  - Тасдиқ/Рад кардани фармоишҳо (бо донати худкор)
  - Идоракунии маҳсулотҳо (алмазҳо)
  - Омор
  - Фиристодани хабар ба ҳама
"""
import asyncio
import hashlib
import logging
import math
import html
import unicodedata
from datetime import datetime

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db
import ff_api
import receipt  # генератори расми чеки муваффақ (Pillow)

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def esc(text) -> str:
    """Матни бегона (номи корбар, username)-ро барои паёми HTML бехатар мекунад."""
    if text is None:
        return ""
    s = "".join(
        ch for ch in str(text)
        if unicodedata.category(ch) not in ("Cf", "Cc", "Co", "Cs", "Cn")
    )
    return html.escape(s, quote=False)


async def _credit_referral_and_notify(bot, order_id: int):
    """
    Пас аз ҲАР тасдиқи фармоиш (аз ҳар хидмат: FF, FFID, PUBG, Stars,
    Premium) занг зада мешавад. Агар корбар referrer дошта бошад, 5%
    аз нархи фармоишро ба балансаи referrer илова мекунад ва ба ӣ
    хабар мефиристад.
    """
    try:
        reward, referrer_id = await db.credit_referral_for_order(order_id)
        if reward and referrer_id:
            try:
                await bot.send_message(
                    referrer_id,
                    f"🤝 <b>Мукофоти реферралӣ!</b>\n\n"
                    f"💰 Дусти шумо фармоиш дод ва шумо <b>{reward:.2f} сом</b> "
                    f"ба балансаи реферралии худ гирифтед!",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Хабари мукофоти референдалӣ ба {referrer_id} нарасид: {e}")
    except Exception as e:
        logger.error(f"Хатогӣ дар credit_referral барои фармоиши #{order_id}: {e}")


def _progress_bar(current: float, threshold: float, length: int = 10) -> str:
    """Прогресс-бар мисли ███████░░░ 72%"""
    if threshold <= 0:
        pct = 100
    else:
        pct = min(100, int(current / threshold * 100))
    filled = round(length * pct / 100)
    return f"{'█' * filled}{'░' * (length - filled)} {pct}%"


async def _run_with_live_progress(wait_msg: Message, header: str, coro):
    """
    Дар вакти интизории coro (масалан ff_api.auto_donate), caption-и
    wait_msg-ро ҲАР СОНИЯ бо як progress-bar навсозӣ мекунад (то 95%,
    то 100%-ро дурӯғ нагӯяд пеш аз натиҷаи воқеӣ). Вақте coro анҷом
    ёфт, навсозӣ қатъ мешавад ва natiҷаи воқеӣ дар ҷои дигар нишон
    дода мешавад.
    """
    stop_event = asyncio.Event()

    async def _updater():
        start = asyncio.get_event_loop().time()
        est_total = 25.0  # сония — вакти тахминии як донати муваффақ
        last_pct = -1
        while not stop_event.is_set():
            elapsed = asyncio.get_event_loop().time() - start
            pct = min(95, int(elapsed / est_total * 100))
            if pct != last_pct:
                bar = _progress_bar(pct, 100)
                try:
                    await _safe_edit_caption(wait_msg, f"{header}\n\n{bar}", None)
                except Exception:
                    pass
                last_pct = pct
            if pct >= 95:
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


async def _buyer_info_line(order: dict) -> str:
    """
    Барои паёми «Донат муваффақ шуд!»-и ба админ — ном, юзер, ID-и
    Telegram-и харидор, нархи маҳсулот ва вақти харид.
    """
    user = await db.get_user(order["user_id"])
    full_name = user.get("full_name") if user else "—"
    username = f"@{user['username']}" if user and user.get("username") else "—"
    created_at = order.get("created_at")
    time_str = created_at.strftime("%H:%M") if created_at else "—"
    return (
        f"👤 Харидор: {esc(full_name)}\n"
        f"📱 Username: {esc(username)}\n"
        f"🆔 ID Telegram: <code>{order['user_id']}</code>\n"
        f"💵 Нархи маҳсулот: {order['price']:.2f} сомонӣ\n"
        f"🕒 Вақти харид: {time_str}\n"
    )




# ==================== МЕНЮИ АДМИН ====================
def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Кор барои ман",       callback_data="a_my_work")],
        [InlineKeyboardButton(text="🩺 Саломатии система",   callback_data="a_health")],
        [InlineKeyboardButton(text="💎 Маҷсулотҳо",         callback_data="a_products_menu")],
        [InlineKeyboardButton(text="📊 Омор",               callback_data="a_stats")],
        [InlineKeyboardButton(text="📋 Фармоишҳои интизорӣ", callback_data="a_pending_orders")],
        [InlineKeyboardButton(text="🔍 Ҷустуҷӯи чек (расм)", callback_data="a_check_search")],
        [InlineKeyboardButton(text="📢 Фиристодани хабар",  callback_data="a_broadcast")],
        [InlineKeyboardButton(text="🔴 ON/OFF бот",         callback_data="a_toggle_bot")],
        [InlineKeyboardButton(text="🚫 Бан/Анбан корбар",   callback_data="a_ban_unban")],
        [InlineKeyboardButton(text="🔍 Маълумоти корбар",   callback_data="a_user_info")],
        [InlineKeyboardButton(text="🔎 ҶустуҷӮи фармоиш",   callback_data="a_order_search")],
        [InlineKeyboardButton(text="💎 Нархи шахсии мизоҷ", callback_data="a_custom_price")],
        [InlineKeyboardButton(text="💳 Рақами корти ДС",     callback_data="a_dc_card")],
        [InlineKeyboardButton(text="🎁 Тӯҳфаи тасодуфӣ",      callback_data="a_giveaway")],
        [InlineKeyboardButton(text="💰 Идоракунии баланс",    callback_data="a_balance_menu")],
    ])


@router.callback_query(F.data == "a_products_menu")
async def a_products_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 FF алмазҳо",   callback_data="a_products")],
        [InlineKeyboardButton(text="💎 FF Indonesia", callback_data="a_ffid_products")],
        [InlineKeyboardButton(text="💰 PUBG UC",      callback_data="a_pubg_products")],
        [InlineKeyboardButton(text="⭐ Stars/Premium", callback_data="a_tg_products")],
        [InlineKeyboardButton(text="🎁 Комбоҳо",       callback_data="a_combos")],
        [InlineKeyboardButton(text="🔙 Бозгашт",      callback_data="a_back")],
    ])
    await _safe_edit(call, "💎 <b>Идоракунии маҳсулотҳо</b>\n\nХизматро интихоб кунед:", kb)


# ==================== РАҚАМИ КОРТИ ДУШАНБЕ СИТИ ====================
class DCCardState(StatesGroup):
    change = State()


@router.callback_query(F.data == "a_dc_card")
async def a_dc_card(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    current = await db.get_dc_card_number()
    await _safe_edit(
        call,
        f"💳 <b>Рақами корти Душанбе Сити</b>\n\n"
        f"Ҳозира: <code>{current}</code>\n\n"
        f"Рақами нави картро нависед (ин рақам дар ҲАМАИ линкҳои пардохти "
        f"Душанбе Сити — FF, FFID, PUBG, Stars, Premium — худкор иваз мешавад):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(DCCardState.change)


@router.message(DCCardState.change)
async def a_dc_card_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    card_number = message.text.strip()
    if not card_number.isdigit() or len(card_number) < 10:
        await message.answer("⚠️ Хато! Рақами картро танҳо бо рақамҳо нависед (масалан: 9762000226598802).")
        return
    await db.set_dc_card_number(card_number)
    await state.clear()
    await message.answer(
        f"✅ Рақами корти ДС иваз шуд ба: <code>{card_number}</code>\n\n"
        f"Аз ҳозир ҳамаи линкҳои пардохти нав ҳамин рақамро истифода мебаранд.",
        parse_mode="HTML"
    )


# ==================== ИДОРАКУНИИ БАЛАНС ====================
class BalanceAdjustState(StatesGroup):
    enter_user = State()
    enter_amount = State()
    enter_reason = State()


def _balance_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Ҳисоботи балансҳо", callback_data="a_balance_report")],
        [InlineKeyboardButton(text="➕➖ Дастӣ иваз кардани баланс", callback_data="a_balance_adjust")],
        [InlineKeyboardButton(text="💰 Ҳадди пуркунӣ", callback_data="a_max_topup")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ])


@router.callback_query(F.data == "a_balance_menu")
async def a_balance_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    summary = await db.get_balance_summary()
    await _safe_edit(
        call,
        f"💰 <b>Идоракунии баланс</b>\n\n"
        f"💳 Дар балансҳо ҳамагӣ: <b>{summary['total']:.2f} сом</b>\n"
        f"👥 Соҳибони баланс: <b>{summary['holders']}</b> нафар\n\n"
        f"Амалро интихоб кунед:",
        _balance_menu_kb()
    )


@router.callback_query(F.data.startswith("a_balance_report"))
async def a_balance_report(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    # callback: a_balance_report ё a_balance_report_<offset>
    parts = call.data.split("_")
    offset = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    per_page = 15

    summary = await db.get_balance_summary()
    total_holders = await db.count_users_with_balance()
    rows = await db.get_users_with_balance(offset, per_page)

    text = (
        f"📊 <b>Ҳисоботи балансҳо</b>\n\n"
        f"💳 Ҳамагӣ дар балансҳо: <b>{summary['total']:.2f} сом</b>\n"
        f"   <i>(ин пули мизоҷон аст — қарзи шумо)</i>\n"
        f"👥 Соҳибони баланс: <b>{total_holders}</b> нафар\n\n"
        f"📥 Имрӯз пур шуд: <b>{summary['topup_today']:.2f} сом</b>\n"
        f"📤 Имрӯз харҷ шуд: <b>{summary['spent_today']:.2f} сом</b>\n"
        f"🏦 Ҳамагӣ то ҳол пур шудааст: <b>{summary['topup_all']:.2f} сом</b>\n"
    )

    if not rows:
        text += "\nҲоло ҳељ кас баланс надорад."
    else:
        text += f"\n👤 <b>Рӯйхат ({offset + 1}-{offset + len(rows)} аз {total_holders}):</b>\n"
        for i, u in enumerate(rows, start=offset + 1):
            name = esc(u.get("full_name") or "—")
            uname = f"@{u['username']}" if u.get("username") else "—"
            text += f"{i}. {name} ({uname})\n   <code>{u['id']}</code> — <b>{float(u['referral_balance']):.2f} сом</b>\n"

    nav_rows = []
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️ Қафо", callback_data=f"a_balance_report_{max(0, offset - per_page)}"
        ))
    if offset + per_page < total_holders:
        nav.append(InlineKeyboardButton(
            text="Пеш ➡️", callback_data=f"a_balance_report_{offset + per_page}"
        ))
    if nav:
        nav_rows.append(nav)
    nav_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_balance_menu")])

    await _safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=nav_rows))


@router.callback_query(F.data == "a_balance_adjust")
async def a_balance_adjust(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await _safe_edit(
        call,
        "➕➖ <b>Дастӣ иваз кардани баланс</b>\n\n"
        "ID-и Telegram-и корбарро нависед (масалан: 7001198513):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_balance_menu")]
        ])
    )
    await state.set_state(BalanceAdjustState.enter_user)


@router.message(BalanceAdjustState.enter_user)
async def a_balance_adjust_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("⚠️ ID бояд танҳо аз рақамҳо бошад. Дубора нависед:")
        return
    user_id = int(txt)
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Ин корбар ёфт нашуд. ID-и дигар нависед:")
        return
    balance = await db.get_referral_balance(user_id)
    await state.update_data(adj_user_id=user_id)
    name = esc(user.get("full_name") or "—")
    uname = f"@{user['username']}" if user.get("username") else "—"
    await message.answer(
        f"👤 <b>{name}</b> ({uname})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"💰 Баланси ҳозира: <b>{balance:.2f} сом</b>\n\n"
        f"Маблағро нависед:\n"
        f"• <b>50</b> — 50 сом ИЛОВА мекунад\n"
        f"• <b>-50</b> — 50 сом КАМ мекунад",
        parse_mode="HTML"
    )
    await state.set_state(BalanceAdjustState.enter_amount)


@router.message(BalanceAdjustState.enter_amount)
async def a_balance_adjust_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        amount = round(float((message.text or "").strip().replace(",", ".")), 2)
        if not math.isfinite(amount) or amount == 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Рақами дуруст нависед (масалан: 50 ё -50):")
        return
    await state.update_data(adj_amount=amount)
    await message.answer(
        "📝 Сабабро кӯтоҳ нависед (масалан: «баргардонии фармоиши #123»).\n"
        "Агар сабаб лозим набошад, «-» нависед:"
    )
    await state.set_state(BalanceAdjustState.enter_reason)


@router.message(BalanceAdjustState.enter_reason)
async def a_balance_adjust_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    user_id = data.get("adj_user_id")
    amount = data.get("adj_amount")
    if not user_id or amount is None:
        await message.answer("⚠️ Хатогӣ — аз нав сар кунед.")
        return
    reason = (message.text or "").strip()
    if reason in ("-", "—", ""):
        reason = ""

    try:
        ok, old_balance, new_balance = await db.admin_adjust_balance(user_id, amount, reason)
    except Exception as e:
        logger.error(f"a_balance_adjust_save: хато ({user_id}, {amount}): {e}")
        await message.answer("⚠️ Хатои система — дубора кӯшиш кунед.")
        return

    if not ok:
        await message.answer(
            f"❌ Нашуд! Баланси корбар ({old_balance:.2f} сом) барои ин амал кофӣ нест "
            f"(баланс манфӣ шуда наметавонад)."
        )
        return

    sign = "+" if amount > 0 else ""
    reason_line = f"\n📝 Сабаб: {esc(reason)}" if reason else ""
    await message.answer(
        f"✅ <b>Баланс иваз шуд!</b>\n\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"💰 {old_balance:.2f} сом → <b>{new_balance:.2f} сом</b> ({sign}{amount:.2f})"
        f"{reason_line}",
        parse_mode="HTML"
    )

    # Мизоҷро низ огоҳ мекунем
    try:
        if amount > 0:
            head = f"💰 <b>Ба балансатон {amount:.2f} сом илова шуд!</b>"
        else:
            head = f"💰 <b>Аз балансатон {abs(amount):.2f} сом кам шуд.</b>"
        await message.bot.send_message(
            user_id,
            f"{head}\n\n"
            f"💳 Баланси ҳозира: <b>{new_balance:.2f} сом</b>"
            f"{reason_line}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Огоҳии ивази баланс ба {user_id} нарасид: {e}")


# ==================== ҲАДДИ ПУРКУНИИ БАЛАНС ====================
class MaxTopupState(StatesGroup):
    change = State()


@router.callback_query(F.data == "a_max_topup")
async def a_max_topup(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    current = await db.get_max_balance_topup()
    await _safe_edit(
        call,
        f"💰 <b>Ҳадди максималии пуркунии баланс</b>\n\n"
        f"Ҳозира: <b>{current:.2f} сомонӣ</b>\n\n"
        f"Маблағи нави ҳаддро нависед (сомонӣ):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_balance_menu")]
        ])
    )
    await state.set_state(MaxTopupState.change)


@router.message(MaxTopupState.change)
async def a_max_topup_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        amount = round(float(message.text.strip().replace(",", ".")), 2)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Хато! Лутфан рақами дуруст нависед (масалан: 300).")
        return
    await db.set_max_balance_topup(amount)
    await state.clear()
    await message.answer(
        f"✅ Ҳадди пуркунии баланс иваз шуд ба: <b>{amount:.2f} сомонӣ</b>",
        parse_mode="HTML"
    )


# ==================== ТӮҲФАИ ТАСОДУФӢ (ҳар N фармоиши тасдиқшуда) ====================
class GiveawayState(StatesGroup):
    change_n = State()


@router.callback_query(F.data == "a_giveaway")
async def a_giveaway(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    every_n = await db.get_setting("giveaway_every_n") or "25"
    product_id_str = await db.get_setting("giveaway_product_id")
    product_line = "❌ ҳанӯз танзим нашудааст"
    if product_id_str:
        product = await db.get_product(int(product_id_str))
        if product:
            label = product.get("label") or f"💎 {product['amount']}"
            product_line = f"{label} ({float(product['price']):.2f} сом)"
        else:
            product_line = "❌ маҳсулот нест шудааст, аз нав интихоб кунед"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ Иваз кардани миқдор (ҳозира: ҳар {every_n})", callback_data="giveaway_change_n")],
        [InlineKeyboardButton(text="🎁 Интихоби маҳсулоти тӯҳфа", callback_data="giveaway_pick_product")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ])
    await _safe_edit(
        call,
        f"🎁 <b>Тӯҳфаи тасодуфӣ</b>\n\n"
        f"Ҳар <b>{every_n}</b>-умин фармоиши тасдиқшуда, яке аз он {every_n} харидор "
        f"тасодуфан интихоб мешавад ва тӯҳфаро БЕПУЛ мегирад (худкор).\n\n"
        f"🎁 Маҳсулоти тӯҳфа: <b>{product_line}</b>\n\n"
        f"Агар маҳсулот танзим нашуда бошад, тӯҳфа фиристода намешавад.",
        kb
    )


@router.callback_query(F.data == "giveaway_change_n")
async def a_giveaway_change_n(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "✏️ Ҳар чанд фармоиши тасдиқшуда як тӯҳфа диҳем? Рақамро нависед (масалан: 25):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_giveaway")]
        ])
    )
    await state.set_state(GiveawayState.change_n)


@router.message(GiveawayState.change_n)
async def a_giveaway_change_n_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        n = int(message.text.strip())
        if n < 2:
            raise ValueError
    except Exception:
        await message.answer("⚠️ Хато! Рақами бутун нависед (ҳадди ақал 2), масалан: 25")
        return
    await db.set_setting("giveaway_every_n", str(n))
    # База кардани ҳисобкунак ба ҲОЗИРА — вагарна системаи ба тамоми
    # фармоишҳои ТО ҲОЗИР (шояд ҳазорон) нигоҳ карда, якбора даҳҳо тӯҳфа
    # мефиристад ба ҷои интизори фармоишҳои НАВ
    total = await db.count_confirmed_orders()
    await db.set_setting("giveaway_last_multiple", str(total // n))
    await state.clear()
    await message.answer(f"✅ Ҳоло ҳар {n} фармоиши тасдиқшудаи НАВ (аз ҳозир) як тӯҳфа дода мешавад.")


@router.callback_query(F.data == "giveaway_pick_product")
async def a_giveaway_pick_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_products()
    if not products:
        await call.answer("❌ Ҳозир маҳсулот нест!", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"{p.get('label') or ('💎 ' + str(p['amount']))} — {float(p['price']):.2f} сом",
            callback_data=f"giveaway_set_{p['id']}"
        )]
        for p in products
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_giveaway")])
    await _safe_edit(
        call,
        "🎁 <b>Кадом маҳсулот ҳамчун тӯҳфа дода шавад?</b>\n\n"
        "(Тавсия: маҳсулоти арзон, масалан ваучери лайт — то хароҷот кам бошад)",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("giveaway_set_"))
async def a_giveaway_set_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[2])
    await db.set_setting("giveaway_product_id", str(product_id))
    # База кардани ҳисобкунак ба ҲОЗИРА — то фармоишҳои кӯҳна ҳисоб нашаванд
    # (бинг. изоҳи болотар дар a_giveaway_change_n_save)
    every_n = int(await db.get_setting("giveaway_every_n") or "25")
    total = await db.count_confirmed_orders()
    await db.set_setting("giveaway_last_multiple", str(total // every_n))
    await call.answer("✅ Маҳсулоти тӯҳфа танзим шуд! (Танҳо фармоишҳои НАВ ҳисоб мешаванд)")
    await a_giveaway(call)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔐 <b>Панели Админ</b>\n\nАз меню интихоб кунед:",
        reply_markup=admin_menu(), parse_mode="HTML"
    )


@router.callback_query(F.data == "a_back")
async def a_back(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call, "🔐 <b>Панели Админ</b>\n\nАз меню интихоб кунед:", admin_menu()
    )


# ==================== ФАРМОИШҲОИ ИНТИЗОРӢ ====================
# ==================== 🛠 КОР БАРОИ МАН ====================
# Филтри чуқурӣ: a_my_work (пешфарз 3 рӯз) ё a_my_work_d<рӯз>
_WORK_RANGES = [(1, "Имрӯз"), (3, "3 рӯз"), (7, "7 рӯз"), (3650, "Ҳама")]


@router.callback_query(F.data == "a_my_work")
@router.callback_query(F.data.startswith("a_my_work_d"))
async def a_my_work(call: CallbackQuery):
    """
    Ҳамаи фармоишҳое, ки ВОҚЕАН кӯмаки админро мехоҳанд — дар ЯК рӯйхат.
    Дигар лозим нест дар байни садҳо паём кофтуков кардан.
    """
    if not is_admin(call.from_user.id):
        return
    try:
        days = int(call.data.rsplit("_d", 1)[1]) if "_d" in call.data else 3
    except (ValueError, IndexError):
        days = 3
    # Маҳдудият то ба ҳадди 4096 аломати Telegram нарасем
    limit = 30 if days <= 7 else 40
    orders = await db.get_orders_needing_admin(days=days, limit=limit)
    quiet_on = (await db.get_setting("quiet_hours") or "1") == "1"
    quiet_label = "🌙 Хомӯшии шабона: ФАЪОЛ" if quiet_on else "🔔 Хомӯшии шабона: ХОМӮШ"
    filter_row = [
        InlineKeyboardButton(
            text=(f"▪️{lbl}" if d == days else lbl),
            callback_data=f"a_my_work_d{d}")
        for d, lbl in _WORK_RANGES
    ]
    tail_rows = [
        filter_row,
        [InlineKeyboardButton(text=quiet_label, callback_data="a_toggle_quiet")],
        [InlineKeyboardButton(text="🧹 Бастани фармоишҳои кӯҳна",
                              callback_data="a_archive_menu")],
        [InlineKeyboardButton(text="🔄 Навсозӣ", callback_data=f"a_my_work_d{days}")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ]
    period = dict(_WORK_RANGES).get(days, f"{days} рӯз")

    if not orders:
        await _safe_edit(
            call,
            f"🛠 <b>Кор барои ман</b> — <i>{period}</i>\n\n"
            f"✅ Ҳеҷ кор нест — ҳама чиз ҳал шудааст!\n\n"
            f"ℹ️ Фармоишҳое, ки бот ҳоло худаш пайгирӣ мекунад, ин ҷо "
            f"нишон дода намешаванд — онҳо кори шумо нестанд.",
            InlineKeyboardMarkup(inline_keyboard=tail_rows)
        )
        return

    now = datetime.now()
    waiting = [o for o in orders if o["status"] == "paid"]
    broken = [o for o in orders if o["status"] == "failed"]

    lines = [f"🛠 <b>Кор барои ман ({len(orders)})</b> — <i>{period}</i>"]
    if waiting:
        lines.append(f"\n📥 <b>Интизори тасдиқи шумо ({len(waiting)}):</b>")
        for o in waiting:
            lines.append(_work_line(o, now))
    if broken:
        lines.append(f"\n❌ <b>Донат нашуд ({len(broken)}):</b>")
        for o in broken:
            lines.append(_work_line(o, now))
    lines.append(
        "\nℹ️ Барои ҳар фармоиш тугмаашро пахш кунед — расми чек ва "
        "тугмаҳои Тасдиқ/Рад мебарояд."
    )
    if len(orders) >= limit:
        lines.append(
            f"📌 Ҳадди аксар <b>{limit}</b>-то нишон дода мешавад — "
            f"эҳтимол боз ҳам ҳаст."
        )

    kb_rows = [
        [InlineKeyboardButton(
            text=f"{'📥' if o['status'] == 'paid' else '❌'} #{o['id']} — {float(o['price']):.2f} сом",
            callback_data=f"a_order_view_{o['id']}")]
        for o in orders[:20]
    ] + tail_rows
    if len(orders) > 20:
        lines.append(
            f"\n⬇️ Тугмаҳо танҳо барои 20-тои аввал — бақияро дар "
            f"«🔎 Ҷустуҷӯи фармоиш» аз рӯи рақам кушоед."
        )
    await _safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))


def _work_line(o: dict, now: datetime) -> str:
    created_at = o.get("created_at")
    age_min = int((now - created_at).total_seconds() / 60) if created_at else 0
    age = f"{age_min} дақ" if age_min < 60 else f"{age_min // 60} соат"
    what = "💰 пуркунии баланс" if o.get("is_balance_topup") else (o.get("label") or "—")
    warn = "🔥 " if age_min >= 60 else ""
    return f"{warn}#{o['id']} — {what} — {float(o['price']):.2f} сом — {age} пеш"


@router.callback_query(F.data == "a_toggle_quiet")
async def a_toggle_quiet(call: CallbackQuery):
    """Реҷаи хомӯшии шабона (00:00–08:00)-ро фаъол/хомӯш мекунад."""
    if not is_admin(call.from_user.id):
        return
    now_on = (await db.get_setting("quiet_hours") or "1") == "1"
    await db.set_setting("quiet_hours", "0" if now_on else "1")
    if now_on:
        await call.answer(
            "🔔 Хомӯшии шабона ХОМӮШ шуд — огоҳиҳо шабона ҳам фавран меоянд.",
            show_alert=True)
    else:
        await call.answer(
            "🌙 Хомӯшии шабона ФАЪОЛ шуд — аз 00:00 то 08:00 огоҳиҳо ҷамъ "
            "мешаванд ва субҳ дар як паём меоянд.",
            show_alert=True)
    await a_my_work(call)


# ==================== 🧹 БАСТАНИ ФАРМОИШҲОИ КӮҲНА ====================
# «Бастан» = ба ҳолати 'archived' гузаронидан. Пул, таърих ва расми чек
# ГУМ НАМЕШАВАД — фармоиш танҳо аз рӯйхати «кор» бароварда мешавад, то
# рақамҳо ҳақиқиро нишон диҳанд.
_ARCHIVE_CHOICES = [7, 30, 90]
AUTO_ARCHIVE_DEFAULT_DAYS = 7


@router.callback_query(F.data == "a_archive_menu")
async def a_archive_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    auto_on = (await db.get_setting("auto_archive") or "1") == "1"
    auto_days = int(await db.get_setting("auto_archive_days")
                    or str(AUTO_ARCHIVE_DEFAULT_DAYS))

    lines = [
        "🧹 <b>Бастани фармоишҳои кӯҳна</b>\n",
        "Фармоишҳое, ки мизоҷ чек фиристодааст, вале ҳељ гоҳ дар бот "
        "«Тасдиқ» ё «Рад» пахш нашудааст, абадӣ дар рӯйхат мемонанд ва "
        "рақамҳоро вайрон мекунанд.\n",
        "🔒 <b>Пул ва таърих ГУМ НАМЕШАВАД</b> — фармоиш танҳо ба архив "
        "мегузарад ва аз рӯйхати «кор» мебарояд. Дар ҷустуҷӯи фармоиш "
        "аз рӯи рақам ҳамеша ёфт мешавад.\n",
        "<b>Ҳозир дар навбат:</b>",
    ]
    rows = []
    for d in _ARCHIVE_CHOICES:
        st = await db.count_stale_paid_orders(d)
        lines.append(f"• аз <b>{d} рӯз</b> кӯҳнатар — "
                     f"<b>{st['count']}</b> дона ({st['sum']:.0f} сом)")
        if st["count"]:
            rows.append([InlineKeyboardButton(
                text=f"🧹 Бастани {st['count']}-тои аз {d} рӯз кӯҳнатар",
                callback_data=f"a_archive_ask_{d}")])

    if not rows:
        lines.append("\n✅ Ҳеҷ фармоиши кӯҳна нест — ҳама тоза аст!")

    auto_label = (f"🤖 Худкор бастан: ФАЪОЛ ({auto_days} рӯз)"
                  if auto_on else "🤖 Худкор бастан: ХОМӮШ")
    lines.append(
        f"\n{'✅' if auto_on else '⛔️'} <b>Худкор бастан:</b> "
        + (f"ҳар рӯз фармоишҳои аз <b>{auto_days} рӯз</b> кӯҳнатар худкор "
           f"баста мешаванд ва ба шумо рӯйхаташон меояд."
           if auto_on else "хомӯш аст — фармоишҳо худашон ҷамъ мешаванд.")
    )
    rows += [
        [InlineKeyboardButton(text=auto_label, callback_data="a_toggle_autoarchive")],
        [InlineKeyboardButton(text="⏱ Иваз кардани мӯҳлат", callback_data="a_archive_days")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_my_work")],
    ]
    await _safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("a_archive_ask_"))
async def a_archive_ask(call: CallbackQuery):
    """Тасдиқи охирин пеш аз бастан."""
    if not is_admin(call.from_user.id):
        return
    days = int(call.data.rsplit("_", 1)[1])
    st = await db.count_stale_paid_orders(days)
    if not st["count"]:
        await call.answer("✅ Аллакай ҳеҷ чиз нест.", show_alert=True)
        return await a_archive_menu(call)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Ҳа, {st['count']}-таро бас",
                              callback_data=f"a_archive_go_{days}")],
        [InlineKeyboardButton(text="❌ Не, бекор", callback_data="a_archive_menu")],
    ])
    await _safe_edit(
        call,
        f"⚠️ <b>Тасдиқ кунед</b>\n\n"
        f"<b>{st['count']}</b> фармоиши аз <b>{days} рӯз</b> кӯҳнатар "
        f"(ҷамъан {st['sum']:.2f} сом) ба архив мегузаранд.\n\n"
        f"🔒 Ҳељ чиз нест намешавад — на пул, на чек, на таърих. Онҳо "
        f"танҳо аз рӯйхати «Кор барои ман» мебароянд.\n\n"
        f"Давом диҳем?",
        kb
    )


@router.callback_query(F.data.startswith("a_archive_go_"))
async def a_archive_go(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    days = int(call.data.rsplit("_", 1)[1])
    await call.answer("⏳ Кор рафта истодааст...")
    try:
        res = await db.archive_stale_paid_orders(days)
    except Exception as e:
        logger.error(f"archive_stale_paid_orders({days}) хато: {e}")
        await call.answer("❌ Хатогӣ шуд — логро бинед.", show_alert=True)
        return
    logger.info(f"Админ {call.from_user.id} {res['count']} фармоиши кӯҳнаро баст")
    await call.answer(
        f"✅ {res['count']} фармоиш ба архив гузашт.", show_alert=True)
    await a_archive_menu(call)


@router.callback_query(F.data == "a_toggle_autoarchive")
async def a_toggle_autoarchive(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    now_on = (await db.get_setting("auto_archive") or "1") == "1"
    await db.set_setting("auto_archive", "0" if now_on else "1")
    await call.answer(
        "⛔️ Худкор бастан ХОМӮШ шуд." if now_on
        else "✅ Худкор бастан ФАЪОЛ шуд.", show_alert=True)
    await a_archive_menu(call)


class ArchiveDaysState(StatesGroup):
    enter = State()


@router.callback_query(F.data == "a_archive_days")
async def a_archive_days(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    cur = int(await db.get_setting("auto_archive_days")
              or str(AUTO_ARCHIVE_DEFAULT_DAYS))
    await state.set_state(ArchiveDaysState.enter)
    await _safe_edit(
        call,
        f"⏱ <b>Мӯҳлати худкор бастан</b>\n\n"
        f"Ҳозир: <b>{cur} рӯз</b>\n\n"
        f"Рақами нави рӯзҳоро нависед (аз 2 то 365).\n"
        f"Масалан: <code>14</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_archive_menu")]])
    )


@router.message(ArchiveDaysState.enter)
async def a_archive_days_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        days = int((message.text or "").strip())
        if not (2 <= days <= 365):
            raise ValueError
    except ValueError:
        await message.answer("❌ Рақами дуруст нависед (аз 2 то 365).")
        return
    await state.clear()
    await db.set_setting("auto_archive_days", str(days))
    await message.answer(
        f"✅ Акнун фармоишҳои аз <b>{days} рӯз</b> кӯҳнатар худкор "
        f"баста мешаванд.", parse_mode="HTML")


# ==================== 🩺 САЛОМАТИИ СИСТЕМА ====================
@router.callback_query(F.data == "a_health")
async def a_health(call: CallbackQuery):
    """Дар як нигоҳ: система хуб кор мекунад ё не."""
    if not is_admin(call.from_user.id):
        return
    import autopay
    h = await db.get_system_health(hours=24, watch_since=autopay._BOT_START_TS)

    rate = h["success_rate"]
    if rate is None:
        verdict, bar = "ℹ️ Дар 24 соат фармоиш набуд", "—"
    elif rate >= 95:
        verdict, bar = "🟢 Система хуб кор мекунад", "🟢🟢🟢🟢🟢"
    elif rate >= 85:
        verdict, bar = "🟡 Каме мушкилӣ ҳаст", "🟢🟢🟢🟢⚪"
    elif rate >= 60:
        verdict, bar = "🟠 Мушкилии ҷиддӣ — FazerCards-ро санҷед", "🟢🟢🟢⚪⚪"
    else:
        verdict, bar = "🔴 Система бад кор мекунад!", "🔴⚪⚪⚪⚪"

    avg = f"{h['avg_minutes']} дақиқа" if h["avg_minutes"] is not None else "—"
    rate_txt = f"{rate}%" if rate is not None else "—"

    srate = h.get("service_rate")
    if srate is None:
        service_block = ""
    else:
        gap = (rate - srate) if rate is not None else 0
        service_block = (
            f"🤝 <b>Фоизи хизматрасонӣ: {srate}%</b>\n"
            f"<i>аз ҳар 100 мизоҷе, ки пул дод, чандто алмосашро гирифт "
            f"(фармоишҳои ҳанӯз ҳалнашуда низ ҳисоб мешаванд)</i>\n"
        )
        if gap >= 5:
            service_block += (
                f"⚠️ Фарқи {gap:.1f}% байни ду фоиз маънои онро дорад, ки "
                f"фармоишҳо интизори тасдиқи ДАСТИИ шумо мемонанд.\n"
            )
        service_block += "\n"

    text = (
        f"🩺 <b>Саломатии система</b>\n"
        f"<i>24 соати охир</i>\n\n"
        f"{verdict}\n{bar}  <b>{rate_txt}</b>\n"
        f"<i>фоизи техникӣ — мошини донат чӣ хел кор мекунад</i>\n\n"
        f"{service_block}"
        f"✅ Муваффақ: <b>{h['confirmed']}</b>\n"
        f"❌ Ноком: <b>{h['failed']}</b>\n"
        f"🚫 Радшуда: <b>{h['rejected']}</b>\n"
        f"⚠️ Ҳолати номаълум (таймаут): <b>{h['uncertain']}</b>\n"
        f"⏱ Аз фармоиш то тасдиқ: <b>{avg}</b>\n"
        f"<i>(вақти интизории мизоҷ то пардохт низ дохил аст)</i>\n\n"
        f"<b>Ҳозир дар кор:</b>\n"
        f"🔄 Дар ҷараёни донат: <b>{h['donating_now']}</b>\n"
        f"🤖 Тафтишгар пайгирӣ мекунад: <b>{h['stuck_now']}</b>\n"
        f"📥 Интизори тасдиқи шумо: <b>{h['waiting_admin']}</b> "
        f"<i>(3 рӯзи охир)</i>\n"
    )
    if h["waiting_admin_old"]:
        text += (
            f"\n🗄 <b>{h['waiting_admin_old']}</b> фармоиши КӮҲНА (аз 3 рӯз "
            f"пештар) ҳанӯз дар ҳолати «пардохтшуда» мондаанд.\n"
            f"<i>Инҳо кори имрӯза нестанд — эҳтимол аллакай дастӣ ҳал "
            f"шудаанд, вале дар бот пӯшида нашудаанд.</i>\n"
            f"👇 Бо тугмаи «🧹 Бастани фармоишҳои кӯҳна» тоза кардан мумкин."
        )
    if h["uncertain"] >= 3:
        text += (
            f"\n\n💡 Шумораи зиёди «номаълум» одатан маънои сусти алоқа бо "
            f"FazerCards-ро дорад — на хатогии боти шумо."
        )

    rows = [[InlineKeyboardButton(text="🛠 Кор барои ман", callback_data="a_my_work")]]
    if h["waiting_admin_old"]:
        rows.append([InlineKeyboardButton(text="🧹 Бастани фармоишҳои кӯҳна",
                                          callback_data="a_archive_menu")])
    rows += [
        [InlineKeyboardButton(text="🔄 Навсозӣ", callback_data="a_health")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ]
    await _safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "a_pending_orders")
async def a_pending_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    orders = await db.get_pending_orders(limit=20)
    kb_rows = [[InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")]]
    if not orders:
        await _safe_edit(call, "📋 <b>Фармоишҳои интизорӣ</b>\n\nҲоло чизе нест — ҳама коркард шудааст! ✅", InlineKeyboardMarkup(inline_keyboard=kb_rows))
        return

    now = datetime.now()
    lines = [f"📋 <b>Фармоишҳои интизорӣ ({len(orders)})</b>\n"]
    for o in orders:
        created_at = o.get("created_at")
        age_min = int((now - created_at).total_seconds() / 60) if created_at else 0
        warn = "⚠️ " if age_min >= 20 else ""
        lines.append(
            f"{warn}#{o['id']} — {o['label']} — {o['price']:.2f} сом — {age_min} дақ. пеш"
        )
    kb_rows = [
        [InlineKeyboardButton(text=f"#{o['id']} — {o['price']:.2f} сом", callback_data=f"a_order_view_{o['id']}")]
        for o in orders
    ] + kb_rows
    await _safe_edit(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("a_order_view_"))
async def a_order_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    order_id = int(call.data.rsplit("_", 1)[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    user = await db.get_user(order["user_id"])
    username = f"@{user['username']}" if user and user.get("username") else "—"
    text = (
        f"📦 <b>Фармоиши #{order_id}</b>\n\n"
        f"👤 {esc(user.get('full_name') if user else '—')} ({username})\n"
        f"🎁 {order['label']} → <code>{order['game_id']}</code>\n"
        f"💵 {order['price']:.2f} сомонӣ\n"
        f"📊 Ҳолат: {order['status']}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тасдиқ (донат)", callback_data=f"ok_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан",     callback_data=f"no_{order_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",         callback_data="a_pending_orders")],
    ])
    await call.answer()
    try:
        if order.get("check_file_id"):
            await call.bot.send_photo(
                call.from_user.id, order["check_file_id"],
                caption=text, reply_markup=kb, parse_mode="HTML"
            )
        else:
            await call.bot.send_message(call.from_user.id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"a_order_view нашуд барои #{order_id}: {e}")


# ==================== ҶУСТУҶӮИ ЧЕК БО РАСМ (REVERSE LOOKUP) ====================
class CheckSearchState(StatesGroup):
    wait_photo = State()


@router.callback_query(F.data == "a_check_search")
async def a_check_search_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(CheckSearchState.wait_photo)
    await _safe_edit(
        call,
        "🔍 <b>Ҷустуҷӯи чек</b>\n\n"
        "Расми чекеро, ки мехоҳед донед аллакай истифода шудааст ё не, "
        "фиристед:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )


@router.message(CheckSearchState.wait_photo, F.photo)
async def a_check_search_photo(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    try:
        buf = await message.bot.download(message.photo[-1])
        check_hash = hashlib.sha256(buf.read()).hexdigest()
    except Exception as e:
        logger.error(f"Hash-и чек ҳисоб нашуд: {e}")
        await message.answer("❌ Хатогӣ рух дод. Дубора кӯшиш кунед.")
        return

    matches = await db.find_orders_by_check_hash(check_hash)
    if not matches:
        await message.answer("✅ Ин чек дар система ЁФТ НАШУД — то ҳол истифода нашудааст.")
        return

    lines = [f"⚠️ <b>Ин чек {len(matches)} бор дар система ёфт шуд:</b>\n"]
    for o in matches:
        user = await db.get_user(o["user_id"])
        username = f"@{user['username']}" if user and user.get("username") else "—"
        created_at = o.get("created_at")
        time_str = created_at.strftime("%d.%m.%Y %H:%M") if created_at else "—"
        lines.append(
            f"#{o['id']} — {o['label']} — {o['price']:.2f} сом — {o['status']} "
            f"— {username} — {time_str}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _combo_confirm(call: CallbackQuery, order: dict):
    """
    Тасдиқи фармоиши комбо — донати худкор НЕСТ. Фармоиш тасдиқшуда
    қайд карда мешавад ва ба админ рӯйхати пурраи он чи бояд дастӣ иҷро
    шавад нишон дода мешавад.
    """
    order_id = order["id"]
    await db.update_order_status(order_id, "confirmed")
    await db.set_confirmed_at(order_id)
    await _credit_referral_and_notify(call.bot, order_id)

    items = await db.get_combo_items(order["combo_id"])
    lines = []
    for it in items:
        qty = it.get("quantity") or 1
        if it.get("product_id"):
            label = it.get("product_label") or f"💎 {it.get('product_amount')}"
        else:
            label = it.get("custom_label") or "—"
        lines.append(f"  • {esc(label)} ×{qty}")
    checklist = "\n".join(lines) or "  —"

    await call.answer("✅ Тасдиқ шуд — дастӣ иҷро кунед!", show_alert=False)
    await _safe_edit_caption(
        call.message,
        f"✅ <b>Комбо тасдиқ шуд — ДАСТӢ иҷро кунед!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"🆔 ID: <code>{order['game_id']}</code>\n\n"
        f"🎁 <b>Чиро иҷро кардан лозим:</b>\n{checklist}",
        None
    )

    try:
        await call.bot.send_message(
            order["user_id"],
            f"✅ <b>Комбои шумо тасдиқ шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n\n"
            f"🎁 Дар наздиктарин вақт иҷро карда мешавад — интизор бошед. 🙏",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Хабари тасдиқи комбо ба {order['user_id']} нарасид: {e}")


# ==================== ТАСДИҚИ ФАРМОИШ → ДОНАТИ ХУДКОР ====================
@router.callback_query(F.data.startswith("ok_"))
async def order_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return

    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return

    # Пешгирии такрор — агар аллакай коркард шуда бошад
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return

    # Комбо — донати худкор НЕСТ (қисмҳояш омехта аз маҳсулоти автопардохт ва
    # ашёи дастӣ буда метавонанд), пас админ ҳамаашро дастӣ иҷро мекунад
    if order.get("combo_id"):
        await _combo_confirm(call, order)
        return

    # Пуркунии баланс — ҲЕЉ ГОҲ донат нест (game_id/offer_id холист, ff_api
    # табиист нокоме мекунад). Ин шоха ҳимоя мекунад агар топуп ба ин ҷо
    # аз ягон роҳи ғайримустақим бирасад (масалан эскалатсияи expiry_loop-и
    # "чек омада, пардохт ёфт нашуд" — ки бо ҳамин тугмаи умумии "ok_" кор мекунад)
    if order.get("is_balance_topup"):
        import autopay
        await call.answer("⏳ Пуркунии баланс тафтиш карда истодааст...", show_alert=False)
        await autopay._credit_balance_topup(call.bot, order)
        await _safe_edit_caption(
            call.message, f"✅ Баланси мизоҷ пур карда шуд (фармоиш #{order_id}).", None
        )
        return

    # Агар статус 'paid' ё 'donating' бошад, атомикӣ банд мекунем — то агар
    # дар ҳамин лаҳза DCSCAN/DCNOTIF низ ҳамин пардохтро ёфта, худкор
    # коркард карда истода бошад (ё аллакай оғоз кардааст), ду бор донат
    # нашавад (яке аз ду тараф claim-ро мебарад)
    if order["status"] in ("paid", "donating") and not await db.claim_paid_order_for_autodonate(order_id):
        await call.answer("ℹ️ Ин фармоиш ҳозир аллакай худкор коркард шуда истодааст (DCSCAN)!", show_alert=True)
        return

    await call.answer("⏳ Донат оғоз шуд...", show_alert=False)

    # Дар ҲАМОН паёми чек/расм — caption тавсия мешавад (на паёми нав)
    await _safe_edit_caption(
        call.message,
        f"⏳ <b>Донати худкор оғоз шуд...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"{order['label']} → <code>{order['game_id']}</code>",
        None
    )

    # Донати худкорро дар таски алоҳида иҷро мекунем (то бот қулф нашавад)
    asyncio.create_task(_do_donate(call, order, call.message))


# ==================== ТАСДИҚИ ГУРУҲ (САБАД) ====================
@router.callback_query(F.data.startswith("okgroup_"))
async def order_group_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return
    group_id = call.data.split("_", 1)[1]
    orders = await db.get_orders_by_group(group_id)
    pending = [o for o in orders if o["status"] not in ("confirmed", "rejected")]
    if not pending:
        await call.answer("ℹ️ Ин гурӯҳ аллакай коркард шудааст!", show_alert=True)
        return

    await call.answer(f"⏳ Донати {len(pending)} маҳсулот оғоз шуд...", show_alert=False)
    await _safe_edit_caption(
        call.message,
        f"⏳ <b>Донати гурӯҳ оғоз шуд...</b>\n\n"
        f"🎁 {len(pending)} маҳсулот дар навбат аст.",
        None
    )
    asyncio.create_task(_do_donate_group(call, pending))


async def _do_donate_group(call: CallbackQuery, orders: list):
    """Ҳар фармоиши гурӯҳро (сабад) бо навбат донат мекунад."""
    results = []
    for order in orders:
        order_id = order["id"]
        success, api_order_id, uncertain, cost_usd = await ff_api.auto_donate(
            order["game_id"], order["offer_id"], order.get("api_order_id") or "",
            order_id
        )
        if api_order_id:
            await db.set_order_api_id(order_id, api_order_id)
        if success:
            await db.update_order_status(order_id, "confirmed")
            await db.set_confirmed_at(order_id)
            if cost_usd:
                await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
            await _credit_referral_and_notify(call.bot, order_id)
            results.append((order, True, api_order_id))
        else:
            await db.update_order_status(order_id, "failed")
            if uncertain:
                await db.flag_order_uncertain(order_id)
            # Тафтишгари худкор инро низ пайгирӣ кунад
            import autopay
            autopay._recheck_settled.discard(order_id)
            try:
                await db.reset_recheck_state(order_id)
            except Exception as e:
                logger.error(f"reset_recheck_state #{order_id} нашуд: {e}")
            results.append((order, False, api_order_id))

    ok_items = [r for r in results if r[1]]
    failed_items = [r for r in results if not r[1]]

    # Хабар ба харидор — як паём барои ҳама
    if ok_items:
        lines = "\n".join(f"  {o['label']} → <code>{o['game_id']}</code>" for o, _, _ in ok_items)
        # Тугмаҳо: чеки муваффақ барои ҳар маҳсулоти FF СНГ + отзив
        _kb_rows = []
        for o, _, _ in ok_items:
            if str(o.get("game_id", "")).isdigit():  # танҳо FF СНГ
                _kb_rows.append([InlineKeyboardButton(
                    text=f"🧾 Чеки #{o['id']}", callback_data=f"receipt_{o['id']}"
                )])
        _kb_rows.append([InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{ok_items[0][0]['id']}")])
        try:
            await call.bot.send_message(
                orders[0]["user_id"],
                f"✅ <b>Муваффақ! Маҳсулотҳо фиристода шуданд!</b>\n\n"
                f"{lines}\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_kb_rows),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")
    if failed_items:
        lines = "\n".join(f"  ⚠️ {o['label']} (#{o['id']})" for o, _, _ in failed_items)
        try:
            await call.bot.send_message(
                orders[0]["user_id"],
                f"⚠️ <b>Баъзе маҳсулотҳо нашуданд:</b>\n\n{lines}\n\n"
                f"Админ дар ҷараёни ҳал кардан аст.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")

    # Натиҷа ба админ
    result_lines = []
    for o, ok, api_id in results:
        mark = "✅" if ok else "❌"
        api_line = f" (ord:{api_id})" if api_id else ""
        result_lines.append(f"{mark} {o['label']} — #{o['id']}{api_line}")
    await call.message.answer(
        "📊 <b>Натиҷаи донати гурӯҳ:</b>\n\n" + "\n".join(result_lines),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("nogroup_"))
async def order_group_reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    group_id = call.data.split("_", 1)[1]
    orders = await db.get_orders_by_group(group_id)
    pending = [o for o in orders if o["status"] not in ("confirmed", "rejected")]
    if not pending:
        await call.answer("ℹ️ Ин гурӯҳ аллакай коркард шудааст!", show_alert=True)
        return

    # Банди АТОМИКӢ барои ҳар фармоиш — то агар ду админ ҳамзамон рад кунанд,
    # такрор нашавад. ДИҚҚАТ: пул ба баланс ХУДКОР БАРГАРДОНИДА НАМЕШАВАД
    # (қоидаи соҳиб) — агар лозим бошад, соҳиб дастӣ ҳал мекунад.
    rejected_any = False
    for order in pending:
        if not await db.claim_order_for_reject(order["id"]):
            continue
        rejected_any = True
    if not rejected_any:
        await call.answer("ℹ️ Ин гурӯҳ аллакай коркард шудааст!", show_alert=True)
        return

    try:
        await call.bot.send_message(
            pending[0]["user_id"],
            f"❌ <b>Пардохти шумо рад карда шуд.</b>\n\n"
            f"Агар хато бошад, бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Хабар ба корбар нашуд: {e}")

    await _safe_edit_caption(
        call.message,
        f"❌ <b>Ҳамаи фармоишҳои гурӯҳ рад карда шуданд.</b>",
        None
    )


async def _do_donate(call: CallbackQuery, order: dict, wait_msg: Message):
    """Донати худкорро иҷро мекунад ва натиҷаро хабар медиҳад."""
    order_id = order["id"]
    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"{order['label']} → <code>{order['game_id']}</code>"
    )
    success, api_order_id, uncertain, cost_usd = await _run_with_live_progress(
        wait_msg, header,
        ff_api.auto_donate(order["game_id"], order["offer_id"], order.get("api_order_id") or "", order_id)
    )
    logger.info(f"[COST-DEBUG] _do_donate: order={order_id} success={success} cost_usd={cost_usd!r}")

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
        await _credit_referral_and_notify(call.bot, order_id)
        # Ба корбар
        try:
            await call.bot.send_message(
                order["user_id"],
                f"✅ <b>Муваффақ! Алмазҳо фиристода шуданд!</b>\n\n"
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
            logger.error(f"Хабар ба корбар нашуд: {e}")
        # Дар ҳамон паёми чек — caption тавсия мешавад
        buyer_line = await _buyer_info_line(order)
        await _safe_edit_caption(
            wait_msg,
            f"✅ <b>Донат муваффақ шуд!</b>\n\n"
            f"{buyer_line}\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"{order['label']} → <code>{order['game_id']}</code>",
            None
        )
    else:
        # Донат нашуд — статусро 'failed' мегузорем
        await db.update_order_status(order_id, "failed")
        if uncertain:
            await db.flag_order_uncertain(order_id)
        # Кӯшиши НАВ буд — ҳисоби тафтишгари худкор аз сифр сар шавад
        import autopay
        autopay._recheck_settled.discard(order_id)
        try:
            await db.reset_recheck_state(order_id)
        except Exception as e:
            logger.error(f"reset_recheck_state #{order_id} нашуд: {e}")
        # ЛС линки клент
        try:
            user_chat = await call.bot.get_chat(order["user_id"])
            ls_url = f"https://t.me/{user_chat.username}" if user_chat.username else f"tg://user?id={order['user_id']}"
        except Exception:
            ls_url = f"tg://user?id={order['user_id']}"

        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=f"ok_{order_id}")],
            [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=ls_url)],
        ])
        if api_order_id:
            # Фармоиш дар FazerCards ВУҶУД дорад — тафтишгари худкор
            # (recheck_loop) ҳар 3 дақиқа онро мепурсад ва аксаран худаш
            # ҳал мекунад. Админ бояд ҳозир ҳеҷ коре накунад.
            head = "⏳ <b>Донат ҳанӯз тамом нашуд — бот худаш пайгирӣ мекунад</b>"
            tail = (
                f"\n🤖 <b>ЧИЗЕ НАКУНЕД.</b> Тафтишгари худкор ҳар 3 дақиқа "
                f"ҳолати ин фармоишро аз FazerCards мепурсад:\n"
                f"• иҷро шуда бошад → бот худаш тасдиқ мекунад ва ба шумо "
                f"ва ба мизоҷ хабар медиҳад\n"
                f"• воқеан рад шуда бошад → бот ба шумо хабар медиҳад ва "
                f"«Дубора донат» бехатар мешавад\n\n"
                f"Агар ҳудуди 30 дақиқа ҳеҷ хабар наояд, боз як паём мегиред."
            )
        else:
            head = "⚠️ <b>Донати худкор нашуд!</b>"
            tail = (
                f"\n⚠️ Фармоиш умуман ба FazerCards нарасид (ID нест) — "
                f"пас «🔄 Дубора донат» бехатар аст, дучандон харҷ намешавад."
            )
        await _safe_edit_caption(
            wait_msg,
            f"{head}\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"🆔 ID: <code>{order['game_id']}</code>\n"
            f"{order['label']}\n"
            f"{tail}",
            retry_kb
        )


# ==================== ЧЕКИ МУВАФФАҚ (расм) ====================
_PM_LABELS = {
    "dushanbe_city": "🏙 Душанбе Сити",
    "alif": "💳 Алиф",
    "eskhata": "🏦 Эсхата",
    "referral_balance": "💰 Аз баланс",
    "giveaway": "🎁 Тӯҳфаи ройгон",
}


@router.callback_query(F.data.startswith("topupcheck_"))
async def topup_view_check(call: CallbackQuery):
    """Ба админ расми чеки пуркунии баланс (агар мизоҷ фиристода бошад)-ро нишон медиҳад."""
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return
    try:
        order_id = int(call.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await call.answer("❌ Хатои ID фармоиш!", show_alert=True)
        return
    order = await db.get_order(order_id)
    if not order or not order.get("check_file_id"):
        await call.answer("⚠️ Чек ёфт нашуд.", show_alert=True)
        return
    try:
        await call.bot.send_photo(
            call.from_user.id, order["check_file_id"],
            caption=f"🧾 Чеки пуркунии баланс — фармоиш #{order_id}"
        )
        await call.answer()
    except Exception as e:
        logger.error(f"Фиристодани чеки пуркунӣ #{order_id} нашуд: {e}")
        await call.answer("❌ Хатогӣ дар фиристодани чек.", show_alert=True)


@router.callback_query(F.data.startswith("receipt_"))
async def send_success_receipt(call: CallbackQuery):
    """
    Расми чеки муваффақро месозад ва ба корбар мефиристад.
    Ҳам корбар ва ҳам админ метавонанд пахш кунанд.
    """
    try:
        order_id = int(call.data.split("_")[1])
    except (IndexError, ValueError):
        await call.answer("❌ Хатои ID фармоиш!", show_alert=True)
        return

    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return

    if order.get("user_id") != call.from_user.id and not is_admin(call.from_user.id):
        await call.answer("❌ Ин фармоиши шумо нест!", show_alert=True)
        return

    if order.get("status") != "confirmed":
        await call.answer("ℹ️ Ин фармоиш ҳанӯз тасдиқ нашудааст.", show_alert=True)
        return

    await call.answer("🧾 Чек омода мешавад...", show_alert=False)

    payment_label = _PM_LABELS.get(order.get("payment_method"), order.get("payment_method") or "—")
    created_at = order.get("created_at")

    try:
        buf = receipt.generate_success_receipt(
            order_id=order_id,
            product_label=order.get("label") or "Маҳсулот",
            game_id=str(order.get("game_id") or ""),
            payment_label=payment_label,
            nickname=order.get("nickname") or None,
            price=float(order["price"]) if order.get("price") is not None else None,
            dt=created_at,
        )
    except Exception as e:
        logger.error(f"Сохтани чек нашуд (#{order_id}): {e}")
        await call.answer("❌ Сохтани чек нашуд. Бо дастгирӣ тамос гиред.", show_alert=True)
        return

    photo = BufferedInputFile(buf.read(), filename=f"receipt_{order_id}.png")
    try:
        await call.bot.send_photo(
            call.from_user.id,
            photo,
            caption=f"🧾 <b>Чеки фармоиши #{order_id}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Фиристодани чек нашуд (#{order_id}): {e}")
        await call.answer("❌ Фиристодани чек нашуд.", show_alert=True)



@router.callback_query(F.data.startswith("manual_"))
async def order_manual(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return

    # Пешгирии такрор — агар аллакай коркард шуда бошад (масалан админи
    # дигар аллакай пахш кардааст, ё шумо иштибоҳан дубора пахш кардед)
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return

    await db.update_order_status(order_id, "confirmed")
    await db.set_confirmed_at(order_id)
    await _credit_referral_and_notify(call.bot, order_id)

    # Ба корбар
    try:
        # Тугмаи «Чеки муваффақ» танҳо барои Free Fire СНГ (game_id танҳо рақам,
        # бе префиксҳои FFID:/PUBG:/STARS:/PREMIUM:)
        _gid = str(order.get("game_id", ""))
        _is_ff_cis = _gid.isdigit()
        _kb_rows = []
        if _is_ff_cis:
            _kb_rows.append([InlineKeyboardButton(text="🧾 Чеки муваффақ", callback_data=f"receipt_{order_id}")])
        _kb_rows.append([InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")])
        await call.bot.send_message(
            order["user_id"],
            f"✅ <b>Алмазҳо фиристода шуданд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{order['label']} → <code>{order['game_id']}</code>\n\n"
            f"🙏 Ташаккур барои харид!\n\n"
            f"⭐ Лутфан отзив гузоред:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_kb_rows),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Хабар ба корбар нашуд: {e}")

    api_id_line = f"🆔 ID FazerCards: <code>{order['api_order_id']}</code>\n" if order.get("api_order_id") else ""
    await _safe_edit_caption(
        call.message,
        f"✅ <b>Фармоиши #{order_id} дастӣ тасдиқ шуд.</b>\n\n"
        f"{api_id_line}"
        f"{order['label']} → <code>{order['game_id']}</code>",
        None
    )


# ==================== РАД КАРДАН ====================
class RejectState(StatesGroup):
    enter_reason = State()


_REJECT_REASONS = {
    "amt": "💵 Маблағ нодуруст",
    "chk": "🖼 Чек норавшан/қалбакӣ",
    "gid": "🆔 ID-и бозӣ нодуруст",
    "dup": "🔁 Чек такрорӣ (истифодашуда)",
    "img": "🚫 Расми номуносиб фиристода шуд",
}


async def _heal_stale_order_message(bot, order_id: int, status: str, chat_id: int, msg_id: int):
    """Агар паёми кӯҳна (аз пеш аз ислоҳи хатогии тугмаҳо) ҳанӯз тугмаҳои
    фаъол дошта бошад, ҳангоми зер кардани онҳо инҷо тоза мекунем — то
    паёмҳои қаблан 'гир' мондаро низ ислоҳ кунад."""
    emoji = "✅" if status == "confirmed" else "❌"
    label = "тасдиқ шуд" if status == "confirmed" else "рад шуд"
    caption = f"{emoji} <b>Фармоиши #{order_id} {label} (қаблан).</b>"
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=None, parse_mode="HTML")
    except Exception:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Тозакунии паёми кӯҳнаи фармоиши #{order_id} нашуд: {e}")


@router.callback_query(F.data.startswith("no_"))
async def order_reject(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        await _heal_stale_order_message(call.bot, order_id, order["status"], call.message.chat.id, call.message.message_id)
        return

    await state.update_data(
        reject_order_id=order_id,
        reject_chat_id=call.message.chat.id,
        reject_msg_id=call.message.message_id,
        reject_kb=call.message.reply_markup,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=_REJECT_REASONS["amt"], callback_data=f"rjr_{order_id}_amt"),
            InlineKeyboardButton(text=_REJECT_REASONS["chk"], callback_data=f"rjr_{order_id}_chk"),
        ],
        [
            InlineKeyboardButton(text=_REJECT_REASONS["gid"], callback_data=f"rjr_{order_id}_gid"),
            InlineKeyboardButton(text=_REJECT_REASONS["dup"], callback_data=f"rjr_{order_id}_dup"),
        ],
        [
            InlineKeyboardButton(text=_REJECT_REASONS["img"], callback_data=f"rjr_{order_id}_img"),
            InlineKeyboardButton(text="⛔ Рад + Бан кардан", callback_data=f"rjr_{order_id}_ban"),
        ],
        [InlineKeyboardButton(text="✏️ Сабаби дигар (навиштан)", callback_data=f"rjr_{order_id}_custom")],
        [InlineKeyboardButton(text="🔙 Бекор кардан", callback_data=f"rjr_{order_id}_cancel")],
    ])
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except Exception as e:
        logger.error(f"Навсозии тугмаҳои рад кардани #{order_id} нашуд: {e}")
    await call.answer()


async def _finalize_reject(bot, order_id: int, reason_clean: str, chat_id: int, msg_id: int) -> bool:
    """Фармоишро рад мекунад: статус, хабар ба мизоҷ ва навсозии паёми
    фармоиш дар панели админ. ДИҚҚАТ: пул ба баланс ХУДКОР БАРГАРДОНИДА
    НАМЕШАВАД (қоидаи соҳиб) — агар лозим бошад, соҳиб дастӣ ҳал мекунад."""
    order = await db.get_order(order_id)
    if not order:
        return False
    # Банди АТОМИКӢ ба 'rejected' — 'donating' низ манъ аст (донати худкор
    # дар ҷараён). Агар False барорад, аллакай коркард шудааст.
    if not await db.claim_order_for_reject(order_id):
        return False

    await db.set_order_reject_reason(order_id, reason_clean or "")

    try:
        reason_line = f"\n📝 Сабаб: {esc(reason_clean)}\n" if reason_clean else ""
        await bot.send_message(
            order["user_id"],
            f"❌ <b>Пардохти шумо рад карда шуд.</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{reason_line}\n"
            f"Агар хато бошад, бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Хабар ба корбар нашуд: {e}")

    caption = f"❌ <b>Фармоиши #{order_id} рад карда шуд.</b>"
    if reason_clean:
        caption += f"\n📝 Сабаб: {esc(reason_clean)}"
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=None, parse_mode="HTML")
    except Exception:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Навсозии паёми фармоиши #{order_id} нашуд: {e}")
    return True


@router.callback_query(F.data.startswith("rjr_"))
async def order_reject_reason_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split("_")
    order_id, code = int(parts[1]), parts[2]
    data = await state.get_data()

    if code == "cancel":
        original_kb = data.get("reject_kb")
        try:
            await call.message.edit_reply_markup(reply_markup=original_kb)
        except Exception as e:
            logger.error(f"Барқарорсозии тугмаҳои фармоиши #{order_id} нашуд: {e}")
        await call.answer("Бекор карда шуд.")
        return

    if code == "custom":
        await state.set_state(RejectState.enter_reason)
        await call.answer()
        await call.bot.send_message(
            call.from_user.id,
            f"📝 <b>Сабаби радди фармоиши #{order_id}-ро нависед:</b>\n\n"
            f"(Ё нависед «—» агар сабаб ба мизоҷ гуфтан нахоҳед)",
            parse_mode="HTML"
        )
        return

    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        await _heal_stale_order_message(call.bot, order_id, order["status"], call.message.chat.id, call.message.message_id)
        return

    if code == "ban":
        reason_clean = "Сӯиистифода / вайрон кардани қоидаҳо"
        ok = await _finalize_reject(call.bot, order_id, reason_clean, call.message.chat.id, call.message.message_id)
        if not ok:
            await call.answer("❌ Фармоиш ёфт нашуд ё аллакай коркард шудааст!", show_alert=True)
            return
        await db.ban_user(order["user_id"], reason_clean)
        try:
            await call.bot.send_message(
                order["user_id"],
                f"🚫 <b>Шумо банӣ шудаед!</b>\n\n📝 Сабаб: {esc(reason_clean)}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабари бан ба {order['user_id']} нарасид: {e}")
        await call.answer(f"✅ Фармоиши #{order_id} рад шуд, корбар бан шуд.", show_alert=True)
        return

    reason_clean = _REJECT_REASONS.get(code, "")
    ok = await _finalize_reject(call.bot, order_id, reason_clean, call.message.chat.id, call.message.message_id)
    if not ok:
        await call.answer("❌ Фармоиш ёфт нашуд ё аллакай коркард шудааст!", show_alert=True)
        return
    await call.answer(f"✅ Фармоиши #{order_id} рад карда шуд.")


@router.message(RejectState.enter_reason)
async def order_reject_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    order_id = data.get("reject_order_id")
    if not order_id:
        return

    reason = (message.text or "").strip()
    reason_clean = "" if reason in ("—", "-", "") else reason

    ok = await _finalize_reject(message.bot, order_id, reason_clean, data["reject_chat_id"], data["reject_msg_id"])
    if not ok:
        await message.answer("❌ Фармоиш ёфт нашуд ё аллакай коркард шудааст!")
        return
    await message.answer(f"✅ Фармоиши #{order_id} рад карда шуд.")


# ==================== ОМОР ====================
@router.callback_query(F.data == "a_stats")
async def a_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    stats = await db.get_stats()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌙 Гузориши шабона", callback_data="a_daily_report")],
        [InlineKeyboardButton(text="📅 Гузориши ҳафтаина", callback_data="a_weekly_report")],
        [InlineKeyboardButton(text="🔁 Омори баргардонидан", callback_data="a_reengagement_stats")],
        [InlineKeyboardButton(text="🏆 Топ харидорон", callback_data="a_leaderboard_menu")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")]
    ])
    await _safe_edit(
        call,
        f"📊 <b>Омор</b>\n\n"
        f"👥 Корбарон: <b>{stats['users']}</b>\n"
        f"✅ Фармоишҳои анҷомёфта: <b>{stats['orders']}</b>\n"
        f"💰 Фурӯши умумӣ: <b>{stats['total']:.2f} сом</b>",
        kb
    )


@router.callback_query(F.data == "a_leaderboard_menu")
async def a_leaderboard_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    reset_at = await db.get_leaderboard_reset_at()
    info_line = (
        f"🕐 Охирин тоза кардан: <b>{reset_at}</b>\n\n"
        if reset_at else
        "🕐 То ҳол ҲЕЧ гоҳ тоза карда нашудааст.\n\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Тоза кардани рейтинг", callback_data="a_leaderboard_reset_confirm")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_stats")]
    ])
    await _safe_edit(
        call,
        "🏆 <b>Идоракунии 'Топ харидорон'</b>\n\n"
        f"{info_line}"
        "ℹ️ Тоза кардан танҳо рейтингро аз нав мешуморад — "
        "ягон фармоиш аз база нест карда намешавад.",
        kb
    )


@router.callback_query(F.data == "a_leaderboard_reset_confirm")
async def a_leaderboard_reset_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Бале, тоза кун", callback_data="a_leaderboard_reset_do"),
            InlineKeyboardButton(text="❌ На, бекор", callback_data="a_leaderboard_menu"),
        ]
    ])
    await _safe_edit(
        call,
        "⚠️ <b>Диққат!</b>\n\n"
        "Шумо мехоҳед рейтинги 'Топ харидорон'-ро тоза кунед?\n"
        "Баъд аз ин, фармоишҳои ПЕШИН дигар дар рейтинг ҳисоб намешаванд "
        "(ягон чиз аз база нест карда намешавад, фақат рейтинг аз нав сар мешавад).\n\n"
        "Ин амалро бекор кардан мумкин НЕСТ.",
        kb
    )


@router.callback_query(F.data == "a_leaderboard_reset_do")
async def a_leaderboard_reset_do(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await db.reset_leaderboard()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_stats")]
    ])
    await _safe_edit(
        call,
        "✅ Рейтинги 'Топ харидорон' тоза шуд!\n\n"
        "Аз ин лаҷза, рейтинг бар асоси фармоишҷои НАВ ҷИсоб мешавад.",
        kb
    )


@router.callback_query(F.data == "a_reengagement_stats")
async def a_reengagement_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    s = await db.get_reengagement_stats()

    def _rate(sent, converted):
        return f"{(converted / sent * 100):.0f}%" if sent else "—"

    text = (
        f"🔁 <b>Омори баргардонидани мизоҷ</b>\n\n"
        f"👋 <b>Ёдоварии бе-фармоиш:</b>\n"
        f"   Фиристода шуд: <b>{s['noorder_sent']}</b>\n"
        f"   Баъд харид кард: <b>{s['noorder_converted']}</b> "
        f"({_rate(s['noorder_sent'], s['noorder_converted'])})"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_stats")]
    ])
    await _safe_edit(call, text, kb)


_WEEKDAY_SHORT_TJ = ["Дш", "Сш", "Чш", "Пш", "Ҷм", "Шн", "Яш"]


def _sparkline(values):
    blocks = "▁▂▃▄▅▆▇█"
    max_v = max(values) if values else 0
    if max_v <= 0:
        return blocks[0] * len(values)
    return "".join(blocks[min(7, int(v / max_v * 7))] for v in values)


@router.callback_query(F.data == "a_daily_report")
async def a_daily_report(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    stats = await db.get_daily_report()
    change = stats["change_percent"]
    if change > 0:
        change_str = f"📈 +{change:.1f}%"
    elif change < 0:
        change_str = f"📉 {change:.1f}%"
    else:
        change_str = "➖ 0%"

    def _fmt_change(pct):
        if pct > 0:
            return f"📈 +{pct:.1f}%"
        elif pct < 0:
            return f"📉 {pct:.1f}%"
        return "➖ 0%"

    change_7d_str = _fmt_change(stats["change_7d"])
    change_30d_str = _fmt_change(stats["change_30d"])
    peak_hour_str = f"{stats['peak_hour']:02d}:00" if stats.get("peak_hour") is not None else "—"

    sparkline = _sparkline([d["sales"] for d in stats.get("last_7_days_sales", [])])
    weekday_labels = " ".join(_WEEKDAY_SHORT_TJ[d["date"].weekday()] for d in stats.get("last_7_days_sales", []))

    top_products_lines = "\n".join(
        f"   {i + 1}. {esc(p['label'])} — {p['count']} фармоиш, {p['revenue']:.2f} сом"
        + (f" (фоида {p['margin_percent']:.1f}%)" if p.get("margin_percent") is not None else "")
        for i, p in enumerate(stats.get("top_products", []))
    ) or "   —"

    payment_lines = "\n".join(
        f"   {_PM_LABELS.get(p['method'], p['method'])}: {p['confirmed']} ✅ / {p['rejected']} ❌"
        for p in stats.get("payment_breakdown", [])
    ) or "   —"

    confirmed_today = stats["confirmed_today"]
    with_cost = stats.get("orders_with_cost_today", 0)
    coverage = f" (аз {with_cost}/{confirmed_today} фармоиш)" if confirmed_today else ""
    margin_pct = stats.get("profit_margin_percent")
    margin_line = f" — <b>{margin_pct:.1f}%</b> аз арзиши харид" if margin_pct is not None else ""

    text = (
        f"🌙 <b>Гузориши шабона</b>\n\n"
        f"👥 <b>Корбарони нав:</b>\n"
        f"   Имрӯз: <b>{stats['new_today']}</b>\n"
        f"   3 рӯзи охир: <b>{stats['new_3d']}</b>\n"
        f"   7 рӯзи охир: <b>{stats['new_7d']}</b>\n"
        f"   1 моҳи охир: <b>{stats['new_30d']}</b>\n\n"
        f"💰 <b>Савдо:</b>\n"
        f"   Имрӯз: <b>{stats['sales_today']:.2f} сом</b>\n"
        f"   Дина: <b>{stats['sales_yesterday']:.2f} сом</b>\n"
        f"   Тағйир нисбат ба дина: {change_str}\n"
        f"   7 рӯзи охир: <b>{stats['sales_7d']:.2f} сом</b> ({change_7d_str})\n"
        f"   30 рӯзи охир: <b>{stats['sales_30d']:.2f} сом</b> ({change_30d_str})\n\n"
        f"📦 <b>Фармоишҳои имрӯз:</b>\n"
        f"   ✅ Тасдиқшуда: <b>{stats['confirmed_today']}</b>\n"
        f"   ❌ Радшуда: <b>{stats['rejected_today']}</b>\n"
        f"   📊 Фоизи радкунӣ: <b>{stats['rejection_rate']:.1f}%</b>\n"
        f"   💵 Миёнаи арзиши фармоиш: <b>{stats['avg_order_value']:.2f} сом</b>\n\n"
        f"🔁 <b>Харидорон имрӯз:</b>\n"
        f"   Такрорӣ: <b>{stats['repeat_customers_today']}</b>\n"
        f"   Нав: <b>{stats['new_customers_today']}</b>\n\n"
        f"👤 <b>ГурӴҳбандии харидорон (ҳама вақт):</b>\n"
        f"   1 харид: <b>{stats['buyers_1']}</b> нафар\n"
        f"   2–5 харид: <b>{stats['buyers_2_5']}</b> нафар\n"
        f"   5+ харид (VIP): <b>{stats['buyers_5plus']}</b> нафар\n"
        f"   💵 Миёнаи харид ба як корбар: <b>{stats['avg_spent_per_buyer']:.2f} сом</b>\n\n"
        f"⏰ <b>Соати пик (30 рӯзи охир):</b> "
        f"<b>{peak_hour_str}</b> ({stats['peak_hour_count']} фармоиш)\n"
        f"📅 <b>Рӯзи беҳтарин (30 рӯзи охир):</b> "
        f"<b>{stats['best_weekday']}</b> ({stats['best_weekday_sales']:.2f} сом)\n\n"
        f"📊 <b>Тамоюли 7 рӯз:</b> <code>{sparkline}</code>\n"
        f"   <code>{weekday_labels}</code>\n\n"
        f"🏆 <b>Топ-5 маҳсулот (7 рӯз):</b>\n"
        f"{top_products_lines}\n\n"
        f"💳 <b>Пардохт аз рӯи усул (имрӯз):</b>\n"
        f"{payment_lines}\n\n"
        f"⚠️ <b>Фармоишҳои \"номуайян\" (таймаути FazerCards) имрӯз:</b> <b>{stats['uncertain_today']}</b>\n"
        f"🔄 <b>Пардохти дерина наҷотёфта (имрӯз):</b> <b>{stats['late_recovered_today']}</b>\n"
        f"😴 <b>Мизоҷони хомӯшшуда (14+ рӯз бе харид):</b> <b>{stats['dormant_customers']}</b>\n"
        f"💵 <b>Фоидаи холис имрӯз:</b> <b>~{stats['profit_today']:.2f} сом</b>{coverage}{margin_line}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")]
    ])
    await _safe_edit(call, text, kb)


@router.callback_query(F.data == "a_weekly_report")
async def a_weekly_report(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    stats = await db.get_weekly_report()

    def _fmt_change(pct):
        if pct > 0:
            return f"📈 +{pct:.1f}%"
        elif pct < 0:
            return f"📉 {pct:.1f}%"
        return "➖ 0%"

    change_str = _fmt_change(stats["change_pct"])
    top_products_lines = "\n".join(
        f"   {i + 1}. {esc(p['label'])} — {p['count']} фармоиш, {p['revenue']:.2f} сом"
        + (f" (фоида {p['margin_percent']:.1f}%)" if p.get("margin_percent") is not None else "")
        for i, p in enumerate(stats.get("top_products", []))
    ) or "   —"

    with_cost = stats.get("orders_with_cost_week", 0)
    confirmed = stats["confirmed_week"]
    coverage = f" (аз {with_cost}/{confirmed} фармоиш)" if confirmed else ""
    margin_pct = stats.get("profit_margin_percent")
    margin_line = f" — <b>{margin_pct:.1f}%</b> аз арзиши харид" if margin_pct is not None else ""

    week_start = stats["week_start"].strftime("%d.%m")
    week_end = stats["week_end"].strftime("%d.%m")

    text = (
        f"📅 <b>Гузориши ҳафтаина</b> ({week_start} – {week_end})\n\n"
        f"💰 <b>Савдо:</b>\n"
        f"   Ин ҳафта: <b>{stats['sales_week']:.2f} сом</b>\n"
        f"   Ҳафтаи гузашта: <b>{stats['sales_prev_week']:.2f} сом</b>\n"
        f"   Тағйир: {change_str}\n\n"
        f"📦 <b>Фармоишҳо:</b>\n"
        f"   ✅ Тасдиқшуда: <b>{stats['confirmed_week']}</b>\n"
        f"   ❌ Радшуда: <b>{stats['rejected_week']}</b>\n"
        f"   💵 Миёнаи арзиши фармоиш: <b>{stats['avg_order_value']:.2f} сом</b>\n\n"
        f"👥 <b>Мизоҷони нав ин ҳафта:</b> <b>{stats['new_customers_week']}</b>\n"
        f"📅 <b>Рӯзи беҳтарини ҳафта:</b> <b>{stats['best_weekday']}</b> "
        f"({stats['best_weekday_sales']:.2f} сом)\n\n"
        f"🏆 <b>Топ-5 маҳсулот:</b>\n"
        f"{top_products_lines}\n\n"
        f"💵 <b>Фоидаи холис ин ҳафта:</b> <b>~{stats['profit_week']:.2f} сом</b>{coverage}{margin_line}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")]
    ])
    await _safe_edit(call, text, kb)


# ==================== ИДОРАКУНИИ МАҲСУЛОТҲО ====================
@router.callback_query(F.data == "a_products")
async def a_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_products()
    buttons = []
    for p in products:
        active = "🟢" if p["is_active"] else "🔴"
        featured = "🔥" if p.get("is_featured") else ""
        label = p.get("label") or f"💎 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{active}{featured} {label} — {p['price']:.2f} сом",
            callback_data=f"pedit_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Маҳсулоти нав", callback_data="padd")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_products_menu")])
    await _safe_edit(
        call,
        "💎 <b>Идоракунии алмазҳо</b>\n\nБарои таҳрир интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("pedit_"))
async def a_product_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    p = await db.get_product(product_id)
    if not p:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    featured_btn = "⚪ Бекор кардани 🔥 Маъмултарин" if p.get("is_featured") else "🔥 Гузоштан ҳамчун Маъмултарин"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Тағйир додан", callback_data=f"pchange_{product_id}")],
        [InlineKeyboardButton(text=featured_btn,      callback_data=f"pfeat_{product_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан",   callback_data=f"pdel_{product_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",       callback_data="a_products")],
    ])
    featured_line = "\n🔥 <b>Ҳозир Маъмултарин аст</b>" if p.get("is_featured") else ""
    await _safe_edit(
        call,
        f"💎 <b>Маҳсулот #{product_id}</b>\n\n"
        f"🔢 Миқдор: <b>{p['amount']}</b>\n"
        f"💵 Нарх: <b>{p['price']:.2f} сом</b>\n"
        f"🏷 Ном: <b>{p.get('label') or '—'}</b>\n"
        f"🔑 Offer ID: <code>{p.get('offer_id') or '—'}</code>"
        f"{featured_line}",
        kb
    )


@router.callback_query(F.data.startswith("pfeat_"))
async def a_product_toggle_featured(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.toggle_product_featured(product_id)
    await call.answer("✅ Навсозӣ шуд!")
    await a_product_edit(call)


@router.callback_query(F.data.startswith("pdel_"))
async def a_product_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.delete_product(product_id)
    await call.answer("✅ Нест карда шуд!")
    await a_products(call)


class ProductState(StatesGroup):
    add_data    = State()
    change_data = State()


@router.callback_query(F.data == "padd")
async def a_product_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "➕ <b>Маҳсулоти нав</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>\n\n"
        "Мисол:\n"
        "<code>520 | 46.00 | 💎 520 (+52) | 572_diamonds</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_products")]
        ])
    )
    await state.set_state(ProductState.add_data)


@router.message(ProductState.add_data)
async def a_product_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"💎 {amount}"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        await db.add_product(amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_product_add_save: db.add_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулоти нав илова шуд!")
    await state.clear()


@router.callback_query(F.data.startswith("pchange_"))
async def a_product_change(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=product_id)
    await _safe_edit(
        call,
        "✏️ <b>Тағйир додан</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>\n\n"
        "Мисол:\n"
        "<code>520 | 49.00 | 💎 520 (+52) | 572_diamonds</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_products")]
        ])
    )
    await state.set_state(ProductState.change_data)


@router.message(ProductState.change_data)
async def a_product_change_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"💎 {amount}"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        data = await state.get_data()
        await db.update_product(data["edit_id"], amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_product_change_save: db.update_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулот навсозӣ шуд!")
    await state.clear()


# ==================== ФИРИСТОДАНИ ХАБАР ====================
class BroadcastState(StatesGroup):
    text = State()


@router.callback_query(F.data == "a_broadcast")
async def a_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "📢 <b>Матни хабарро нависед:</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(BroadcastState.text)


@router.message(BroadcastState.text)
async def a_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    users = await db.get_all_users()
    sent = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(user["id"], message.text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # то flood-limit нашавад
    await message.answer(
        f"✅ Хабар фиристода шуд!\n\n"
        f"📤 Расид: {sent}\n"
        f"❌ Нашуд: {failed}"
    )


# ==================== ЁРИРАСОНҲО ====================
async def _safe_edit(call: CallbackQuery, text: str, kb):
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await call.bot.send_message(
                call.from_user.id, text, reply_markup=kb, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"_safe_edit хато: {e}")


async def _safe_edit_caption(msg: Message, caption: str, kb):
    """
    Caption-и паёми расм (чек)-ро тавсия мекунад. Агар паём расм набошад
    (масалан фармоиши пардохти баланс — бе чек), ба matn-edit мегузарад.
    """
    try:
        await msg.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await msg.edit_text(caption, reply_markup=kb, parse_mode="HTML")
        except Exception:
            try:
                await msg.answer(caption, reply_markup=kb, parse_mode="HTML")
            except Exception as e:
                logger.error(f"_safe_edit_caption хато: {e}")


# ==================== ON/OFF БОТ ====================
@router.callback_query(F.data == "a_toggle_bot")
async def a_toggle_bot(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    current = await db.get_setting("bot_active")
    if current == "1":
        await db.set_setting("bot_active", "0")
        status = "🔴 Бот ХОМӮШ шуд!"
        btn = "🟢 Бот-ро фаъол кун"
    else:
        await db.set_setting("bot_active", "1")
        status = "🟢 Бот ФАЪОЛ шуд!"
        btn = "🔴 Бот-ро хомӯш кун"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn, callback_data="a_toggle_bot")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ])
    await _safe_edit(call, f"⚙️ <b>{status}</b>", kb)


# ==================== БАН/АНБАН (ЯКЧО) ====================
class BanState(StatesGroup):
    enter_id     = State()
    enter_reason = State()


@router.callback_query(F.data == "a_ban_unban")
async def a_ban_unban_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "🚫 <b>Бан/Анбан кардан</b>\n\nID-и Telegram корбарро нависед:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(BanState.enter_id)


@router.message(BanState.enter_id)
async def a_ban_unban_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.strip().isdigit():
        await message.answer("⚠️ Танҳо рақам нависед!")
        return
    user_id = int(message.text.strip())
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Чунин корбар дар база нест!")
        return

    is_banned = bool(user.get("is_banned"))
    if is_banned:
        # Корбар АЛЛАКАЙ банист — фавран анбан мекунем, пурсиши сабаб лозим нест
        await state.clear()
        await db.unban_user(user_id)
        try:
            await message.bot.send_message(
                user_id,
                "✅ <b>Банӣ шумо бардошта шуд!</b>\n\nАкнун ботро истифода карда метавонед.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await message.answer(
            f"✅ Корбар <code>{user_id}</code> анбан шуд!", parse_mode="HTML"
        )
    else:
        # Корбар банист НЕСТ — банидан мехостаем, сабабро мепурсем
        await state.update_data(ban_user_id=user_id)
        await message.answer(
            "📝 Сабаби банро нависед:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
            ])
        )
        await state.set_state(BanState.enter_reason)


@router.message(BanState.enter_reason)
async def a_ban_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    user_id = data["ban_user_id"]
    reason = message.text.strip()
    await state.clear()
    await db.ban_user(user_id, reason)
    # Ба корбар хабар фиристонем
    try:
        await message.bot.send_message(
            user_id,
            f"🚫 <b>Шумо банӣ шудаед!</b>\n\n📝 Сабаб: {reason}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await message.answer(
        f"✅ Корбар <code>{user_id}</code> банӣ шуд!\n📝 Сабаб: {reason}",
        parse_mode="HTML"
    )


# ==================== МАЪЛУМОТИ КОРБАР ====================
class UserInfoState(StatesGroup):
    enter_id = State()


class OrderSearchState(StatesGroup):
    enter_id = State()


@router.callback_query(F.data == "a_user_info")
async def a_user_info_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "🔍 <b>Маълумоти корбар</b>\n\nID-и Telegram корбарро нависед:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(UserInfoState.enter_id)


@router.message(UserInfoState.enter_id)
async def a_user_info_show(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.strip().isdigit():
        await message.answer("⚠️ Танҳо рақам нависед!")
        return
    user_id = int(message.text.strip())
    await state.clear()

    user = await db.get_user(user_id)
    stats = await db.get_user_stats(user_id)
    banned, ban_reason = await db.is_banned(user_id)

    if not user:
        await message.answer(f"❌ Корбар <code>{user_id}</code> ёфт нашуд!", parse_mode="HTML")
        return

    username = f"@{user['username']}" if user.get('username') else "—"
    ban_status = f"🚫 Банӣ ({ban_reason})" if banned else "✅ Озод"

    ref_balance = await db.get_referral_balance(user_id)
    ref_count = await db.get_referral_count(user_id)
    referrer_id = await db.get_referrer_id(user_id)
    referrer_line = f"🔝 Даъватшуда аз: <code>{referrer_id}</code>\n" if referrer_id else ""

    text = (
        f"👤 <b>Маълумоти корбар</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📱 Username: {esc(username)}\n"
        f"👤 Ном: {esc(user.get('full_name', '—'))}\n"
        f"🔒 Статус: {ban_status}\n"
        f"{referrer_line}\n"
        f"📊 <b>Омор:</b>\n"
        f"✅ Харидҳо: {stats['total_orders']}\n"
        f"💰 Умумӣ: {stats['total_spent']:.2f} сом\n\n"
        f"🤝 <b>Реферал:</b>\n"
        f"👥 Зердастон: {ref_count} нафар\n"
        f"💰 Баланс: {ref_balance:.2f} сомонӣ\n\n"
    )

    if stats['orders']:
        text += "📋 <b>Фармоишҳои охир:</b>\n"
        status_emoji = {"confirmed": "✅", "rejected": "❌", "failed": "⚠️", "paid": "💳", "pending": "⏳", "donating": "🚀"}
        for o in stats['orders'][:10]:
            emoji = status_emoji.get(o['status'], "❓")
            text += f"{emoji} #{o['id']} | {o['label']} | {o['price']:.2f} сом | ID: <code>{o['game_id']}</code>\n"

    kb_rows = []
    if user.get('username'):
        kb_rows.append([InlineKeyboardButton(text="💬 ЛС ба корбар", url=f"https://t.me/{user['username']}")])
    if ref_count:
        kb_rows.append([InlineKeyboardButton(
            text="👥 Рефералҳои ин корбар",
            callback_data=f"a_ref_subusers_{user_id}"
        )])
    kb_rows.append([InlineKeyboardButton(
        text="📜 Таърихи баланс",
        callback_data=f"a_bal_hist_{user_id}"
    )])
    kb_rows.append([InlineKeyboardButton(text="🚫 Бан/Анбан", callback_data="a_ban_unban")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


_ADMIN_TX_LABELS = {
    "topup": "💰 Пуркунӣ",
    "purchase": "🛒 Харид",
    "referral_reward": "🤝 Мукофоти реферралӣ",
    "refund": "↩️ Баргардонӣ",
    "admin_adjust": "🛠 Ивази админ",
}


@router.callback_query(F.data.startswith("a_bal_hist_"))
async def a_balance_history_admin(call: CallbackQuery):
    """Таърихи баланси як мизоҷ — барои админ (аз экрани 'Маълумоти корбар')."""
    if not is_admin(call.from_user.id):
        return
    user_id = int(call.data.split("_")[-1])
    balance = await db.get_referral_balance(user_id)
    txs = await db.get_balance_transactions(user_id, limit=20)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")]
    ])
    if not txs:
        await _safe_edit(
            call,
            f"📜 <b>Таърихи баланс</b>\n\n"
            f"🆔 <code>{user_id}</code>\n"
            f"💰 Баланси ҳозира: <b>{balance:.2f} сом</b>\n\n"
            f"Ҳоло ҳељ амале нест.",
            kb
        )
        return
    lines = []
    for tx in txs:
        amount = float(tx["amount"])
        sign = "+" if amount >= 0 else ""
        label = _ADMIN_TX_LABELS.get(tx["tx_type"], tx["tx_type"])
        dt = tx["created_at"].strftime("%d.%m %H:%M") if tx.get("created_at") else "—"
        order_part = f" (#{tx['order_id']})" if tx.get("order_id") else ""
        lines.append(
            f"{label}{order_part}: {sign}{amount:.2f} → {float(tx['balance_after']):.2f} сом\n"
            f"   🕒 {dt}"
        )
    await _safe_edit(
        call,
        f"📜 <b>Таърихи баланс</b> (охирин 20)\n\n"
        f"🆔 <code>{user_id}</code>\n"
        f"💰 Баланси ҳозира: <b>{balance:.2f} сом</b>\n\n"
        + "\n\n".join(lines),
        kb
    )


@router.callback_query(F.data.startswith("a_ref_subusers_"))
async def a_ref_subusers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    user_id = int(call.data.split("_")[-1])
    subusers = await db.get_referral_subusers(user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ])
    if not subusers:
        await _safe_edit(
            call,
            f"👥 <b>Рефералҳои корбари</b> <code>{user_id}</code>\n\nҲеч реферал нест.",
            kb
        )
        return
    lines = [f"👥 <b>Рефералҳои корбари</b> <code>{user_id}</code>\n"]
    total = 0.0
    for i, u in enumerate(subusers, 1):
        name = u.get("full_name") or "—"
        username = f"@{u['username']}" if u.get("username") else f"ID {u['id']}"
        earned = u["earned"]
        total += earned
        lines.append(f"{i}. {esc(name)} ({esc(username)}, <code>{u['id']}</code>) — <b>{earned:.2f} сомонӣ</b>")
    lines.append(f"\n💰 Ҷамъ овардаанд: <b>{total:.2f} сомонӣ</b>")
    await _safe_edit(call, "\n".join(lines), kb)


# ==================== ҶУСТУҶӮИ ФАРМОИШ ====================
_STATUS_LABELS = {
    "pending": "⏳ Дар интизорӣ (чек нафиристодааст)",
    "paid": "📸 Чек фиристода шуд (интизори тасдиқи админ)",
    "donating": "🚀 Донат ҳозир иҷро мешавад...",
    "confirmed": "✅ Тасдиқшуда",
    "rejected": "❌ Радшуда",
    "failed": "⚠️ Хато (донати худкор нашуд)",
}


@router.callback_query(F.data == "a_order_search")
async def a_order_search_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "🔎 <b>ҶустуҷӮи фармоиш</b>\n\nID-и фармоишро нависед (масалан 1773):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(OrderSearchState.enter_id)


@router.message(OrderSearchState.enter_id)
async def a_order_search_show(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = message.text.strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("⚠️ Танҳо рақами фармоишро нависед (масалан 1773)!")
        return
    order_id = int(raw)
    await state.clear()

    order = await db.get_order(order_id)
    if not order:
        await message.answer(f"❌ Фармоиши #{order_id} ёфт нашуд!")
        return

    user = await db.get_user(order["user_id"])
    username = f"@{user['username']}" if user and user.get("username") else "—"
    full_name = user.get("full_name") if user else "—"
    status_text = _STATUS_LABELS.get(order["status"], order["status"])
    api_id_line = (
        f"🆔 ID FazerCards: <code>{order['api_order_id']}</code>\n"
        if order.get("api_order_id") else ""
    )
    created_at = order.get("created_at")
    time_line = f"🕒 Вақт: {created_at.strftime('%d.%m.%Y %H:%M')}\n" if created_at else ""

    pm_labels = {
        "dushanbe_city": "🏙 Душанбе Сити",
        "alif": "💳 Алиф",
        "eskhata": "🏦 Эсхата",
        "referral_balance": "💰 Аз баланс",
        "giveaway": "🎁 Тӯҳфаи ройгон",
    }
    pm_text = pm_labels.get(order.get("payment_method"), order.get("payment_method") or "—")

    text = (
        f"📦 <b>Фармоиши #{order_id}</b>\n\n"
        f"👤 Корбар: {esc(full_name)} (<code>{order['user_id']}</code>)\n"
        f"📱 Username: {esc(username)}\n\n"
        f"🎮 ID/Username дар бозӣ: <code>{order['game_id']}</code>\n"
        f"🎁 Маҳсулот: {order['label']}\n"
        f"💵 Маблағ: {order['price']:.2f} сомонӣ\n"
        f"💳 Тариқи пардохт: {pm_text}\n"
        f"{api_id_line}"
        f"{time_line}\n"
        f"📊 Ҳолат: {status_text}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Чеки ин фармоиш", callback_data=f"a_order_check_{order_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("a_order_check_"))
async def a_order_check(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    order_id = int(call.data.split("_")[-1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return

    file_id = order.get("check_file_id")
    if not file_id:
        if order.get("payment_method") == "referral_balance":
            msg = "ℹ️ Ин фармоиш АЗ БАЛАНС пардохт шудааст — чеки расм надорад (пул аллакай дар балансаш буд)."
        elif order.get("status") == "confirmed":
            msg = "ℹ️ Ин фармоиш тавассути автопардохт тасдиқ шуд — чеки расм захира нашудааст."
        else:
            msg = "❌ Чек нест — ин фармоиш нопурра мондааст (корбар чек нафиристодааст)."
        await call.answer(msg, show_alert=True)
        return

    try:
        await call.bot.send_photo(
            call.from_user.id,
            file_id,
            caption=f"📸 Чеки фармоиши #{order_id}"
        )
        await call.answer()
    except Exception as e:
        logger.error(f"Чеки фармоиши #{order_id} фиристода нашуд: {e}")
        await call.answer("⚠️ Хатогӣ дар фиристодани чек!", show_alert=True)


# ==================== НАРХИ ШАХСИИ МИЗОҶ (VIP PRICING, ФАҦАТ FF СНГ) ====================
class CustomPriceState(StatesGroup):
    enter_user_id = State()
    enter_price = State()


@router.callback_query(F.data == "a_custom_price")
async def a_custom_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "💎 <b>Нархи шахсии мизоҷ (танҳо Free Fire СНГ)</b>\n\n"
        "ID-и Telegram-и мизоҷро нависед:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_back")]
        ])
    )
    await state.set_state(CustomPriceState.enter_user_id)


@router.message(CustomPriceState.enter_user_id)
async def a_custom_price_show_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("⚠️ Танҳо ID-и рақамиро нависед!")
        return
    user_id = int(raw)
    await state.update_data(cp_user_id=user_id)

    user = await db.get_user(user_id)
    if not user:
        await message.answer(f"⚠️ Корбари бо ID <code>{user_id}</code> ёфт нашуд!", parse_mode="HTML")
        return

    await _show_custom_price_menu(message, user_id, is_callback=False)


async def _show_custom_price_menu(target, user_id: int, is_callback: bool):
    prices = await db.get_custom_prices_for_user(user_id)
    lines = [f"💎 <b>Нархи шахсии мизоҷи</b> <code>{user_id}</code> <b>(FF СНГ)</b>\n"]
    kb_rows = []
    if not prices:
        lines.append("Ҳоло нархи шахсӣ гузошта нашудааст.")
    else:
        for p in prices:
            lines.append(
                f"🎁 {p['label']} — <s>{p['default_price']:.2f}</s> → "
                f"<b>{p['custom_price']:.2f} сомонӣ</b>"
            )
            kb_rows.append([InlineKeyboardButton(
                text=f"🗑 Дур кардани: {p['label']}",
                callback_data=f"a_cp_del_{user_id}_{p['product_id']}"
            )])
    kb_rows.append([InlineKeyboardButton(
        text="➕ Илова/Тавсиа кардани нарх",
        callback_data=f"a_cp_add_{user_id}"
    )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    text = "\n".join(lines)
    if is_callback:
        await _safe_edit(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("a_cp_add_"))
async def a_custom_price_choose_product(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    user_id = int(call.data.split("_")[-1])
    products = await db.get_products()
    if not products:
        await call.answer("❌ Ҳеч маҳсулот нест!", show_alert=True)
        return
    kb_rows = []
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        kb_rows.append([InlineKeyboardButton(
            text=f"{label} ({p['price']:.2f} сом)",
            callback_data=f"a_cp_pick_{user_id}_{p['id']}"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_custom_price")])
    await _safe_edit(
        call,
        f"💎 <b>Маҳсулотро интихоб кунед</b> (барои мизоҷи <code>{user_id}</code>):",
        InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )


@router.callback_query(F.data.startswith("a_cp_pick_"))
async def a_custom_price_enter_amount(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split("_")
    user_id = int(parts[3])
    product_id = int(parts[4])
    product = await db.get_product(product_id)
    if not product:
        await call.answer("❌ Маҳсулот ёфт нашуд!", show_alert=True)
        return
    await state.update_data(cp_user_id=user_id, cp_product_id=product_id)
    label = product.get("label") or f"💎 {product['amount']}"
    await _safe_edit(
        call,
        f"💎 <b>{label}</b>\n"
        f"Нархи стандартӣ: {product['price']:.2f} сомонӣ\n\n"
        f"Нархи шахсии навро нависед (ба сомонӣ, масалан 25 ё 25.50):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_custom_price")]
        ])
    )
    await state.set_state(CustomPriceState.enter_price)


@router.message(CustomPriceState.enter_price)
async def a_custom_price_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    user_id = data.get("cp_user_id")
    product_id = data.get("cp_product_id")
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Нархи дуруст нависед (масалан 25 ё 25.50)!")
        return

    await db.set_custom_price(user_id, product_id, price)
    await state.clear()
    await message.answer(
        f"✅ Нархи шахсӣ гузошта шуд: <b>{price:.2f} сомонӣ</b> "
        f"барои мизоҷи <code>{user_id}</code>.",
        parse_mode="HTML"
    )
    await _show_custom_price_menu(message, user_id, is_callback=False)


@router.callback_query(F.data.startswith("a_cp_del_"))
async def a_custom_price_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split("_")
    user_id = int(parts[3])
    product_id = int(parts[4])
    await db.delete_custom_price(user_id, product_id)
    await call.answer("✅ Дур карда шуд!")
    await _show_custom_price_menu(call, user_id, is_callback=True)


# ==================== ТАСДИҲ FF INDONESIA ====================
@router.callback_query(F.data.startswith("okffid_"))
async def order_confirm_ffid(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return

    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return
    if order["status"] in ("paid", "donating") and not await db.claim_paid_order_for_autodonate(order_id):
        await call.answer("ℹ️ Ин фармоиш ҳозир аллакай худкор коркард шуда истодааст!", show_alert=True)
        return

    await call.answer("⏳ Донат оғоз шуд...", show_alert=False)

    # game_id формат: "FFID:123456789"
    player_id = order["game_id"].replace("FFID:", "") if order["game_id"].startswith("FFID:") else order["game_id"]

    await _safe_edit_caption(
        call.message,
        f"⏳ <b>Донати худкор оғоз шуд (FF Indonesia)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"{order['label']} → <code>{player_id}</code>",
        None
    )

    import asyncio as _asyncio
    _asyncio.create_task(_do_donate_ffid(call, order, player_id, call.message))


async def _do_donate_ffid(call: CallbackQuery, order: dict, player_id: str, wait_msg: Message):
    order_id = order["id"]
    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (FF Indonesia)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"{order['label']} → <code>{player_id}</code>"
    )
    success, api_order_id = await _run_with_live_progress(
        wait_msg, header,
        ff_api.auto_donate_ffid(player_id, order["offer_id"], order.get("api_order_id") or "", order_id)
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        await _credit_referral_and_notify(call.bot, order_id)
        try:
            await call.bot.send_message(
                order["user_id"],
                f"✅ <b>Муваффақ! Алмазҳо фиристода шуданд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"{order['label']} → <code>{player_id}</code>\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")
        buyer_line = await _buyer_info_line(order)
        await _safe_edit_caption(
            wait_msg,
            f"✅ <b>Донат муваффақ шуд!</b>\n\n"
            f"{buyer_line}\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"{order['label']} → <code>{player_id}</code>",
            None
        )
    else:
        await db.update_order_status(order_id, "failed")
        try:
            user_chat = await call.bot.get_chat(order["user_id"])
            ls_url = f"https://t.me/{user_chat.username}" if user_chat.username else f"tg://user?id={order['user_id']}"
        except Exception:
            ls_url = f"tg://user?id={order['user_id']}"
        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=f"okffid_{order_id}")],
            [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=ls_url)],
        ])
        await _safe_edit_caption(
            wait_msg,
            f"⚠️ <b>Донати худкор нашуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"{order['label']}\n\n"
            f"Метавонед дубора кӯшиш кунед ё дастӣ донат карда тасдиқ кунед.",
            retry_kb
        )


# ==================== ИДОРАКУНИИ КОМБОҲО ====================
@router.callback_query(F.data == "a_combos")
async def a_combos(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    combos = await db.get_combos()
    buttons = []
    for c in combos:
        active = "🟢" if c["is_active"] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{active} {c['label']} — {float(c['price']):.2f} сом",
            callback_data=f"combo_view_{c['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Комбои нав", callback_data="combo_add")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_products_menu")])
    await _safe_edit(
        call,
        "🎁 <b>Идоракунии комбоҳо</b>\n\nБарои дидан/таҳрир интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("combo_view_"))
async def a_combo_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    combo_id = int(call.data.split("_")[2])
    combo = await db.get_combo(combo_id)
    if not combo:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    items = await db.get_combo_items(combo_id)
    lines = []
    for it in items:
        qty = it.get("quantity") or 1
        if it.get("product_id"):
            label = it.get("product_label") or f"💎 {it.get('product_amount')}"
        else:
            label = it.get("custom_label") or "—"
        lines.append(f"  • {esc(label)} ×{qty}")
    items_text = "\n".join(lines) or "  — холӣ —"

    toggle_btn = "🔴 Хомӯш кардан" if combo["is_active"] else "🟢 Фаъол кардан"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Таҳрир кардани таркиб", callback_data=f"combo_edit_{combo_id}")],
        [InlineKeyboardButton(text=toggle_btn, callback_data=f"combo_toggle_{combo_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан", callback_data=f"combo_del_{combo_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_combos")],
    ])
    await _safe_edit(
        call,
        f"🎁 <b>{esc(combo['label'])}</b>\n\n"
        f"💵 Нарх: <b>{float(combo['price']):.2f} сом</b>\n\n"
        f"📦 <b>Таркиб:</b>\n{items_text}",
        kb
    )


@router.callback_query(F.data.startswith("combo_toggle_"))
async def a_combo_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    combo_id = int(call.data.split("_")[2])
    await db.toggle_combo_active(combo_id)
    await call.answer("✅ Навсозӣ шуд!")
    await a_combo_view(call)


@router.callback_query(F.data.startswith("combo_del_"))
async def a_combo_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    combo_id = int(call.data.split("_")[2])
    await db.delete_combo(combo_id)
    await call.answer("✅ Нест карда шуд!")
    await a_combos(call)


@router.callback_query(F.data.startswith("combo_edit_"))
async def a_combo_edit(call: CallbackQuery, state: FSMContext):
    """Таркиби комбои МАВҶУДАро бо ҳамон интерфейси интихоб дубора кушода
    метавонад иваз кунад — то лозим набошад ҳама чизро аз нав созед."""
    if not is_admin(call.from_user.id):
        return
    combo_id = int(call.data.split("_")[2])
    combo = await db.get_combo(combo_id)
    if not combo:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    items = await db.get_combo_items(combo_id)
    qty_map = {}
    customs = []
    for it in items:
        qty = it.get("quantity") or 1
        if it.get("product_id"):
            qty_map[str(it["product_id"])] = qty
        else:
            customs.append(it.get("custom_label") or "—")

    await state.update_data(
        combo_edit_id=combo_id,
        combo_label=combo["label"],
        combo_price=float(combo["price"]),
        combo_qty=qty_map,
        combo_customs=customs,
    )
    text, kb = await _render_combo_items_kb(state)
    await _safe_edit(call, text, kb)
    await state.set_state(ComboState.add_items)


class ComboState(StatesGroup):
    add_name = State()
    add_price = State()
    add_items = State()
    add_custom_label = State()


async def _render_combo_items_kb(state: FSMContext) -> tuple[str, InlineKeyboardMarkup]:
    data = await state.get_data()
    products = await db.get_all_products()
    qty_map = data.get("combo_qty", {})  # {product_id_str: qty}
    customs = data.get("combo_customs", [])

    buttons = []
    for p in products:
        pid = p["id"]
        label = p.get("label") or f"💎 {p['amount']}"
        qty = qty_map.get(str(pid), 0)
        mark = "✅" if qty > 0 else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"combo_ptoggle_{pid}"
        )])
        if qty > 0:
            buttons.append([
                InlineKeyboardButton(text="➖", callback_data=f"combo_pminus_{pid}"),
                InlineKeyboardButton(text=f"{qty} дона", callback_data="combo_noop"),
                InlineKeyboardButton(text="➕", callback_data=f"combo_padd_{pid}"),
            ])
    buttons.append([InlineKeyboardButton(text="✍️ Илова кардани дастӣ (масалан Level-Up)", callback_data="combo_custom_add")])
    buttons.append([InlineKeyboardButton(text="✅ Тамом — Сабт кардан", callback_data="combo_finish")])
    buttons.append([InlineKeyboardButton(text="❌ Бекор кардан", callback_data="a_combos")])

    chosen_lines = []
    for p in products:
        qty = qty_map.get(str(p["id"]), 0)
        if qty > 0:
            label = p.get("label") or f"💎 {p['amount']}"
            chosen_lines.append(f"  • {label} ×{qty}")
    for c in customs:
        chosen_lines.append(f"  • {esc(c)} ×1 (дастӣ)")
    chosen_text = "\n".join(chosen_lines) or "  — ҳанӯз холӣ —"

    text = (
        f"🎁 <b>Комбо: {esc(data.get('combo_label',''))}</b>\n"
        f"💵 Нарх: <b>{float(data.get('combo_price', 0)):.2f} сом</b>\n\n"
        f"Маҳсулотҳоро интихоб кунед (пахш = илова, ➖/➕ = миқдор):\n\n"
        f"{chosen_text}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "combo_add")
async def a_combo_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    # Тоза кардани ҳар боқимондаи як таҳрир/сохтани қаблӣ (масалан агар
    # админ бекор карда буд) — то он ба ин комбои НАВ омехта нашавад
    await state.clear()
    await _safe_edit(
        call,
        "➕ <b>Комбои нав</b>\n\nНоми комборо нависед (масалан: VIP Combo):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_combos")]
        ])
    )
    await state.set_state(ComboState.add_name)


@router.message(ComboState.add_name)
async def a_combo_add_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(combo_label=message.text.strip())
    await message.answer(
        "💵 Ҳоло нархи умумии комбо (сомонӣ)-ро нависед (масалан: 150.00):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_combos")]
        ])
    )
    await state.set_state(ComboState.add_price)


@router.message(ComboState.add_price)
async def a_combo_add_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        price = float(message.text.strip().replace(",", "."))
    except Exception:
        await message.answer("⚠️ Хато! Рақами нархро дуруст нависед (масалан: 150.00)")
        return
    await state.update_data(combo_price=price, combo_qty={}, combo_customs=[])
    text, kb = await _render_combo_items_kb(state)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(ComboState.add_items)


@router.callback_query(F.data == "combo_noop")
async def a_combo_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("combo_ptoggle_"), ComboState.add_items)
async def a_combo_item_toggle(call: CallbackQuery, state: FSMContext):
    """
    Пахши АСОСИИ сатри маҳсулот — интихоб/бекор кардан (0 ↔ 1), на илова
    кардани беохир. Барои миқдори бештар аз 1, тугмаи ➕ алоҳида ҳаст.
    Ин аз он ҷилавгирӣ мекунад, ки як пахши тасодуфӣ маҳсулоти нохостаро
    ба комбо ҳамеша илова кунад бе роҳи осони бозгашт.
    """
    pid = call.data.split("_")[2]
    data = await state.get_data()
    qty_map = dict(data.get("combo_qty", {}))
    if qty_map.get(pid, 0) > 0:
        qty_map.pop(pid, None)
    else:
        qty_map[pid] = 1
    await state.update_data(combo_qty=qty_map)
    text, kb = await _render_combo_items_kb(state)
    await _safe_edit(call, text, kb)


@router.callback_query(F.data.startswith("combo_padd_"), ComboState.add_items)
async def a_combo_item_add(call: CallbackQuery, state: FSMContext):
    pid = call.data.split("_")[2]
    data = await state.get_data()
    qty_map = dict(data.get("combo_qty", {}))
    qty_map[pid] = qty_map.get(pid, 0) + 1
    await state.update_data(combo_qty=qty_map)
    text, kb = await _render_combo_items_kb(state)
    await _safe_edit(call, text, kb)


@router.callback_query(F.data.startswith("combo_pminus_"), ComboState.add_items)
async def a_combo_item_minus(call: CallbackQuery, state: FSMContext):
    pid = call.data.split("_")[2]
    data = await state.get_data()
    qty_map = dict(data.get("combo_qty", {}))
    if qty_map.get(pid, 0) > 0:
        qty_map[pid] -= 1
        if qty_map[pid] <= 0:
            qty_map.pop(pid, None)
    await state.update_data(combo_qty=qty_map)
    text, kb = await _render_combo_items_kb(state)
    await _safe_edit(call, text, kb)


@router.callback_query(F.data == "combo_custom_add", ComboState.add_items)
async def a_combo_custom_add(call: CallbackQuery, state: FSMContext):
    await _safe_edit(
        call,
        "✍️ Номи ашёи дастиро нависед (масалан: Пропуски прокачка):",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="combo_custom_cancel")]
        ])
    )
    await state.set_state(ComboState.add_custom_label)


@router.callback_query(F.data == "combo_custom_cancel", ComboState.add_custom_label)
async def a_combo_custom_cancel(call: CallbackQuery, state: FSMContext):
    await state.set_state(ComboState.add_items)
    text, kb = await _render_combo_items_kb(state)
    await _safe_edit(call, text, kb)


@router.message(ComboState.add_custom_label)
async def a_combo_custom_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    customs = list(data.get("combo_customs", []))
    customs.append(message.text.strip())
    await state.update_data(combo_customs=customs)
    await state.set_state(ComboState.add_items)
    text, kb = await _render_combo_items_kb(state)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "combo_finish", ComboState.add_items)
async def a_combo_finish(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    data = await state.get_data()
    qty_map = data.get("combo_qty", {})
    customs = data.get("combo_customs", [])
    if not qty_map and not customs:
        await call.answer("⚠️ Ҳадди ақал як маҳсулот/ашё илова кунед!", show_alert=True)
        return

    edit_id = data.get("combo_edit_id")
    if edit_id:
        await db.update_combo(edit_id, data.get("combo_label", "Комбо"), float(data.get("combo_price", 0)))
        await db.clear_combo_items(edit_id)
        combo_id = edit_id
    else:
        combo_id = await db.create_combo(data.get("combo_label", "Комбо"), float(data.get("combo_price", 0)))
    for pid_str, qty in qty_map.items():
        if qty > 0:
            await db.add_combo_item(combo_id, product_id=int(pid_str), custom_label=None, quantity=qty)
    for label in customs:
        await db.add_combo_item(combo_id, product_id=None, custom_label=label, quantity=1)

    await state.clear()
    await call.answer("✅ Таркиб навсозӣ шуд!" if edit_id else "✅ Комбо сабт шуд!")
    await a_combos(call)


# ==================== ИДОРАКУНИИ FF INDONESIA МАҲСУЛОТ ====================
@router.callback_query(F.data == "a_ffid_products")
async def a_ffid_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_ffid_products()
    buttons = []
    for p in products:
        active = "🟢" if p["is_active"] else "🔴"
        label = p.get("label") or f"💎 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{active} {label} — {p['price']:.2f} сом",
            callback_data=f"ffidedit_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Маҷсулоти нав", callback_data="ffidadd")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_products_menu")])
    await _safe_edit(
        call,
        "💎 <b>Идоракунии FF Indonesia</b>\n\nБарои таҳрир интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("ffidedit_"))
async def a_ffid_product_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    p = await db.get_ffid_product(product_id)
    if not p:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Тағйир додан", callback_data=f"ffidchange_{product_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан",   callback_data=f"ffiddel_{product_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",       callback_data="a_ffid_products")],
    ])
    await _safe_edit(
        call,
        f"💎 <b>Маҷсулот #{product_id}</b>\n\n"
        f"🔢 Миқдор: <b>{p['amount']}</b>\n"
        f"💵 Нарх: <b>{p['price']:.2f} сом</b>\n"
        f"🏷 Ном: <b>{p.get('label') or '—'}</b>\n"
        f"🔑 Offer ID: <code>{p.get('offer_id') or '—'}</code>",
        kb
    )


@router.callback_query(F.data.startswith("ffiddel_"))
async def a_ffid_product_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.delete_ffid_product(product_id)
    await call.answer("✅ Нест карда шуд!")
    await a_ffid_products(call)


class FFIDProductState(StatesGroup):
    add_data    = State()
    change_data = State()


@router.callback_query(F.data == "ffidadd")
async def a_ffid_product_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "➕ <b>Маҷсулоти нав (FF Indonesia)</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>\n\n"
        "Мисол:\n"
        "<code>500 | 45.00 | 💎 500 | 500_diamonds</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_ffid_products")]
        ])
    )
    await state.set_state(FFIDProductState.add_data)


@router.message(FFIDProductState.add_data)
async def a_ffid_product_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"💎 {amount}"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        await db.add_ffid_product(amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_ffid_product_add_save: db.add_ffid_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулоти нав илова шуд!")
    await state.clear()


@router.callback_query(F.data.startswith("ffidchange_"))
async def a_ffid_product_change(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=product_id)
    await _safe_edit(
        call,
        "✏️ <b>Тағйир додан</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_ffid_products")]
        ])
    )
    await state.set_state(FFIDProductState.change_data)


@router.message(FFIDProductState.change_data)
async def a_ffid_product_change_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"💎 {amount}"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        data = await state.get_data()
        await db.update_ffid_product(data["edit_id"], amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_ffid_product_change_save: db.update_ffid_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулот навсозӣ шуд!")
    await state.clear()


# ==================== ТАСДИҲ PUBG ====================
@router.callback_query(F.data.startswith("okpubg_"))
async def order_confirm_pubg(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return

    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return
    if order["status"] in ("paid", "donating") and not await db.claim_paid_order_for_autodonate(order_id):
        await call.answer("ℹ️ Ин фармоиш ҳозир аллакай худкор коркард шуда истодааст!", show_alert=True)
        return

    await call.answer("⏳ Донат оғоз шуд...", show_alert=False)

    player_id = order["game_id"].replace("PUBG:", "") if order["game_id"].startswith("PUBG:") else order["game_id"]

    await _safe_edit_caption(
        call.message,
        f"⏳ <b>Донати худкор оғоз шуд (PUBG Mobile)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"🎁 {order['label']} → <code>{player_id}</code>",
        None
    )

    import asyncio as _asyncio
    _asyncio.create_task(_do_donate_pubg(call, order, player_id, call.message))


async def _do_donate_pubg(call: CallbackQuery, order: dict, player_id: str, wait_msg: Message):
    order_id = order["id"]
    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (PUBG Mobile)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"🎁 {order['label']} → <code>{player_id}</code>"
    )
    success, api_order_id = await _run_with_live_progress(
        wait_msg, header,
        ff_api.auto_donate_pubg(player_id, order["offer_id"], order.get("api_order_id") or "", order_id)
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        await _credit_referral_and_notify(call.bot, order_id)
        try:
            await call.bot.send_message(
                order["user_id"],
                f"✅ <b>Муваффақ! UC фиристода шуд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"🎁 {order['label']} → <code>{player_id}</code>\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")
        buyer_line = await _buyer_info_line(order)
        await _safe_edit_caption(
            wait_msg,
            f"✅ <b>Донат муваффақ шуд!</b>\n\n"
            f"{buyer_line}\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"🎁 {order['label']} → <code>{player_id}</code>",
            None
        )
    else:
        await db.update_order_status(order_id, "failed")
        try:
            user_chat = await call.bot.get_chat(order["user_id"])
            ls_url = f"https://t.me/{user_chat.username}" if user_chat.username else f"tg://user?id={order['user_id']}"
        except Exception:
            ls_url = f"tg://user?id={order['user_id']}"
        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Дубора донат", callback_data=f"okpubg_{order_id}")],
            [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=ls_url)],
        ])
        await _safe_edit_caption(
            wait_msg,
            f"⚠️ <b>Донати худкор нашуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"🆔 Player ID: <code>{player_id}</code>\n"
            f"🎁 {order['label']}\n\n"
            f"Метавонед дубора кӯшиш кунед ё дастӣ донат карда тасдиқ кунед.",
            retry_kb
        )


# ==================== ИДОРАКУНИИ PUBG МАҶСУЛОТ ====================
@router.callback_query(F.data == "a_pubg_products")
async def a_pubg_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_pubg_products()
    buttons = []
    for p in products:
        active = "🟢" if p["is_active"] else "🔴"
        label = p.get("label") or f"💰 {p['amount']} UC"
        buttons.append([InlineKeyboardButton(
            text=f"{active} {label} — {p['price']:.2f} сом",
            callback_data=f"pubgedit_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Маҷсулоти нав", callback_data="pubgadd")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_products_menu")])
    await _safe_edit(
        call,
        "💰 <b>Идоракунии PUBG UC</b>\n\nБарои таҳрир интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("pubgedit_"))
async def a_pubg_product_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    p = await db.get_pubg_product(product_id)
    if not p:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Тағйир додан", callback_data=f"pubgchange_{product_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан",   callback_data=f"pubgdel_{product_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",       callback_data="a_pubg_products")],
    ])
    await _safe_edit(
        call,
        f"💰 <b>Маҷсулот #{product_id}</b>\n\n"
        f"🔢 Миқдор: <b>{p['amount']} UC</b>\n"
        f"💵 Нарх: <b>{p['price']:.2f} сом</b>\n"
        f"🏷 Ном: <b>{p.get('label') or '—'}</b>\n"
        f"🔑 Offer ID: <code>{p.get('offer_id') or '—'}</code>",
        kb
    )


@router.callback_query(F.data.startswith("pubgdel_"))
async def a_pubg_product_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.delete_pubg_product(product_id)
    await call.answer("✅ Нест карда шуд!")
    await a_pubg_products(call)


class PUBGProductState(StatesGroup):
    add_data    = State()
    change_data = State()


@router.callback_query(F.data == "pubgadd")
async def a_pubg_product_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "➕ <b>Маҷсулоти нав (PUBG)</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>\n\n"
        "Мисол:\n"
        "<code>60 | 8.00 | 60 UC | 60_uc</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_pubg_products")]
        ])
    )
    await state.set_state(PUBGProductState.add_data)


@router.message(PUBGProductState.add_data)
async def a_pubg_product_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"{amount} UC"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        await db.add_pubg_product(amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_pubg_product_add_save: db.add_pubg_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулоти нав илова шуд!")
    await state.clear()


@router.callback_query(F.data.startswith("pubgchange_"))
async def a_pubg_product_change(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=product_id)
    await _safe_edit(
        call,
        "✏️ <b>Тағйир додан</b>\n\n"
        "Бо ин формат нависед (бо | ҷудо):\n"
        "<code>миқдор | нарх | ном | offer_id</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бекор", callback_data="a_pubg_products")]
        ])
    )
    await state.set_state(PUBGProductState.change_data)


@router.message(PUBGProductState.change_data)
async def a_pubg_product_change_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
        label = parts[2] if len(parts) > 2 else f"{amount} UC"
        offer_id = parts[3] if len(parts) > 3 else ""
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )
        return
    try:
        data = await state.get_data()
        await db.update_pubg_product(data["edit_id"], amount, price, label, offer_id)
    except Exception as e:
        logger.error(f"a_pubg_product_change_save: db.update_pubg_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Маҳсулот навсозӣ шуд!")
    await state.clear()


# ==================== ТАСДИҲ TELEGRAM STARS ====================
@router.callback_query(F.data.startswith("okstars_"))
async def order_confirm_stars(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return
    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return
    if order["status"] in ("paid", "donating") and not await db.claim_paid_order_for_autodonate(order_id):
        await call.answer("ℹ️ Ин фармоиш ҳозир аллакай худкор коркард шуда истодааст!", show_alert=True)
        return

    await call.answer("⏳ Дар ҷараён...", show_alert=False)

    tg_username = order["game_id"].replace("STARS:", "")

    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (Telegram Stars)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"📱 @{tg_username} — {order['label']}"
    )
    await _safe_edit_caption(call.message, header, None)

    success, api_order_id, uncertain, cost_usd = await _run_with_live_progress(
        call.message, header,
        ff_api.buy_telegram_stars(tg_username, order["amount"], order_id, order.get("api_order_id") or "")
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"\n🆔 ID FazerCards: <code>{api_order_id}</code>" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
        await _credit_referral_and_notify(call.bot, order_id)
        try:
            await call.bot.send_message(
                order["user_id"],
                f"✅ <b>Муваффақ! {order['label']} фиристода шуд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"📱 Барои: @{tg_username}\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")
        await _safe_edit_caption(
            call.message,
            f"✅ <b>Муваффақ шуд!</b>\n\n🆔 Фармоиш: #{order_id}{api_id_line}",
            None
        )
    else:
        await db.update_order_status(order_id, "failed")
        if uncertain:
            await db.flag_order_uncertain(order_id)
        try:
            user_chat = await call.bot.get_chat(order["user_id"])
            ls_url = f"https://t.me/{user_chat.username}" if user_chat.username else f"tg://user?id={order['user_id']}"
        except Exception:
            ls_url = f"tg://user?id={order['user_id']}"
        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Дубора кӯшиш", callback_data=f"okstars_{order_id}")],
            [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=ls_url)],
        ])
        uncertain_line = (
            f"\n⚠️⚠️ <b>ДИҚҚАТ: шабака ба FazerCards таймаут задааст — мо "
            f"НАФАҲМИДЕМ фармоиш дар тарафи онҳо сохта шуд ё не!</b>\n"
            f"Пеш аз «Дубора кӯшиш», дар FazerCards санҷед, вагарна дучандон "
            f"харҷ мешавад!\n"
            if uncertain else ""
        )
        await _safe_edit_caption(
            call.message,
            f"⚠️ <b>Хато рух дод!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}\n"
            f"📱 @{tg_username} — {order['label']}\n"
            f"{uncertain_line}\n"
            f"Метавонед дубора кӯшиш кунед ё дастӣ тасдиқ кунед.",
            retry_kb
        )


# ==================== ТАСДИҲ TELEGRAM PREMIUM ====================
@router.callback_query(F.data.startswith("okpremium_"))
async def order_confirm_premium(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Иҷозат нест!", show_alert=True)
        return
    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order["status"] in ("confirmed", "rejected"):
        await call.answer(f"ℹ️ Ин фармоиш аллакай: {order['status']}", show_alert=True)
        return
    if order["status"] in ("paid", "donating") and not await db.claim_paid_order_for_autodonate(order_id):
        await call.answer("ℹ️ Ин фармоиш ҳозир аллакай худкор коркард шуда истодааст!", show_alert=True)
        return

    await call.answer("⏳ Дар ҷараён...", show_alert=False)

    tg_username = order["game_id"].replace("PREMIUM:", "")

    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (Telegram Premium)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"📱 @{tg_username} — {order['label']}"
    )
    await _safe_edit_caption(call.message, header, None)

    success, api_order_id, uncertain, cost_usd = await _run_with_live_progress(
        call.message, header,
        ff_api.buy_telegram_premium(tg_username, order["amount"], order_id, order.get("api_order_id") or "")
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"\n🆔 ID FazerCards: <code>{api_order_id}</code>" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
        if cost_usd:
            await db.set_order_cost(order_id, round(cost_usd * config.USD_TO_TJS_RATE, 2))
        await _credit_referral_and_notify(call.bot, order_id)
        try:
            await call.bot.send_message(
                order["user_id"],
                f"✅ <b>Муваффақ! {order['label']} фиристода шуд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"📱 Барои: @{tg_username}\n\n"
                f"🙏 Ташаккур барои харид!\n\n"
                f"⭐ Лутфан отзив гузоред:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Отзив гузоштан", callback_data=f"review_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Хабар ба корбар нашуд: {e}")
        await _safe_edit_caption(
            call.message,
            f"✅ <b>Муваффақ шуд!</b>\n\n🆔 Фармоиш: #{order_id}{api_id_line}",
            None
        )
    else:
        await db.update_order_status(order_id, "failed")
        if uncertain:
            await db.flag_order_uncertain(order_id)
        try:
            user_chat = await call.bot.get_chat(order["user_id"])
            ls_url = f"https://t.me/{user_chat.username}" if user_chat.username else f"tg://user?id={order['user_id']}"
        except Exception:
            ls_url = f"tg://user?id={order['user_id']}"
        retry_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Дубора кӯшиш", callback_data=f"okpremium_{order_id}")],
            [InlineKeyboardButton(text="✅ Дастӣ тасдиқ кардам", callback_data=f"manual_{order_id}")],
            [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=ls_url)],
        ])
        uncertain_line = (
            f"\n⚠️⚠️ <b>ДИҚҚАТ: шабака ба FazerCards таймаут задааст — мо "
            f"НАФАҲМИДЕМ фармоиш дар тарафи онҳо сохта шуд ё не!</b>\n"
            f"Пеш аз «Дубора кӯшиш», дар FazerCards санҷед, вагарна дучандон "
            f"харҷ мешавад!\n"
            if uncertain else ""
        )
        await _safe_edit_caption(
            call.message,
            f"⚠️ <b>Хато рух дод!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}\n"
            f"📱 @{tg_username} — {order['label']}\n"
            f"{uncertain_line}\n"
            f"Метавонед дубора кӯшиш кунед ё дастӣ тасдиқ кунед.",
            retry_kb
        )


# ==================== ИДОРАКУНИИ STARS/PREMIUM ====================
@router.callback_query(F.data == "a_tg_products")
async def a_tg_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars нархҳо", callback_data="a_stars_products")],
        [InlineKeyboardButton(text="💎 Premium нархҳо", callback_data="a_premium_products")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_products_menu")],
    ])
    await _safe_edit(call, "⭐ <b>Telegram Stars/Premium</b>\n\nИнтихоб кунед:", kb)


@router.callback_query(F.data == "a_stars_products")
async def a_stars_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_stars_products()
    buttons = []
    for p in products:
        active = "🟢" if p["is_active"] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{active} ⭐ {p['amount']} — {p['price']:.2f} сом",
            callback_data=f"starsedit_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Нав", callback_data="starsadd")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_tg_products")])
    await _safe_edit(call, "⭐ <b>Идоракунии Stars</b>", InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("starsedit_"))
async def a_stars_product_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    p = await db.get_stars_product(product_id)
    if not p:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Тағйир додан", callback_data=f"starschange_{product_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан",   callback_data=f"starsdel_{product_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",       callback_data="a_stars_products")],
    ])
    await _safe_edit(
        call,
        f"⭐ <b>Stars #{product_id}</b>\n\nМиқдор: <b>{p['amount']}</b>\nНарх: <b>{p['price']:.2f} сом</b>",
        kb
    )


@router.callback_query(F.data.startswith("starsdel_"))
async def a_stars_product_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.delete_stars_product(product_id)
    await call.answer("✅ Нест карда шуд!")
    await a_stars_products(call)


class StarsProductState(StatesGroup):
    add_data    = State()
    change_data = State()


@router.callback_query(F.data == "starsadd")
async def a_stars_product_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "➕ <b>Stars нав</b>\n\nФормат: <code>миқдор | нарх</code>\nМисол: <code>100 | 20.00</code>",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Бекор", callback_data="a_stars_products")]])
    )
    await state.set_state(StarsProductState.add_data)


@router.message(StarsProductState.add_data)
async def a_stars_product_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>миқдор | нарх</code>", parse_mode="HTML")
        return
    try:
        await db.add_stars_product(amount, price)
    except Exception as e:
        logger.error(f"a_stars_product_add_save: db.add_stars_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Илова шуд!")
    await state.clear()


@router.callback_query(F.data.startswith("starschange_"))
async def a_stars_product_change(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=product_id)
    await _safe_edit(
        call,
        "✏️ Формат: <code>миқдор | нарх</code>",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Бекор", callback_data="a_stars_products")]])
    )
    await state.set_state(StarsProductState.change_data)


@router.message(StarsProductState.change_data)
async def a_stars_product_change_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        amount = int(parts[0])
        price = float(parts[1])
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>миқдор | нарх</code>", parse_mode="HTML")
        return
    try:
        data = await state.get_data()
        await db.update_stars_product(data["edit_id"], amount, price)
    except Exception as e:
        logger.error(f"a_stars_product_change_save: db.update_stars_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Навсозӣ шуд!")
    await state.clear()


# -------------------- PREMIUM ИДОРАКУНӢ --------------------
@router.callback_query(F.data == "a_premium_products")
async def a_premium_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = await db.get_all_premium_products()
    buttons = []
    for p in products:
        active = "🟢" if p["is_active"] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{active} 💎 {p['months']} моҳ — {p['price']:.2f} сом",
            callback_data=f"premiumedit_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Нав", callback_data="premiumadd")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_tg_products")])
    await _safe_edit(call, "💎 <b>Идоракунии Premium</b>", InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("premiumedit_"))
async def a_premium_product_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    p = await db.get_premium_product(product_id)
    if not p:
        await call.answer("❌ Ёфт нашуд!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Тағйир додан", callback_data=f"premiumchange_{product_id}")],
        [InlineKeyboardButton(text="🗑 Нест кардан",   callback_data=f"premiumdel_{product_id}")],
        [InlineKeyboardButton(text="🔙 Бозгашт",       callback_data="a_premium_products")],
    ])
    await _safe_edit(
        call,
        f"💎 <b>Premium #{product_id}</b>\n\nМуддат: <b>{p['months']} моҳ</b>\nНарх: <b>{p['price']:.2f} сом</b>",
        kb
    )


@router.callback_query(F.data.startswith("premiumdel_"))
async def a_premium_product_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await db.delete_premium_product(product_id)
    await call.answer("✅ Нест карда шуд!")
    await a_premium_products(call)


class PremiumProductState(StatesGroup):
    add_data    = State()
    change_data = State()


@router.callback_query(F.data == "premiumadd")
async def a_premium_product_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await _safe_edit(
        call,
        "➕ <b>Premium нав</b>\n\nФормат: <code>моҳ | нарх</code>\nМисол: <code>3 | 150.00</code>",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Бекор", callback_data="a_premium_products")]])
    )
    await state.set_state(PremiumProductState.add_data)


@router.message(PremiumProductState.add_data)
async def a_premium_product_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        months = int(parts[0])
        price = float(parts[1])
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>моҳ | нарх</code>", parse_mode="HTML")
        return
    try:
        await db.add_premium_product(months, price)
    except Exception as e:
        logger.error(f"a_premium_product_add_save: db.add_premium_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Илова шуд!")
    await state.clear()


@router.callback_query(F.data.startswith("premiumchange_"))
async def a_premium_product_change(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=product_id)
    await _safe_edit(
        call,
        "✏️ Формат: <code>моҳ | нарх</code>",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Бекор", callback_data="a_premium_products")]])
    )
    await state.set_state(PremiumProductState.change_data)


@router.message(PremiumProductState.change_data)
async def a_premium_product_change_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = [x.strip() for x in message.text.split("|")]
        months = int(parts[0])
        price = float(parts[1])
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>моҳ | нарх</code>", parse_mode="HTML")
        return
    try:
        data = await state.get_data()
        await db.update_premium_product(data["edit_id"], months, price)
    except Exception as e:
        logger.error(f"a_premium_product_change_save: db.update_premium_product хато: {e}")
        await message.answer("⚠️ Хатои система — бо админи техникӣ тамос гиред.")
        return
    await message.answer("✅ Навсозӣ шуд!")
    await state.clear()

