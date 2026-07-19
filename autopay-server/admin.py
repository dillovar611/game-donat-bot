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
        [InlineKeyboardButton(text="🔙 Бозгашт",      callback_data="a_back")],
    ])
    await _safe_edit(call, "💎 <b>Идоракунии маҷсулотҳо</b>\n\nХизматро интихоб кунед:", kb)


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
        success, api_order_id = await ff_api.auto_donate(
            order["game_id"], order["offer_id"], order.get("api_order_id") or ""
        )
        if api_order_id:
            await db.set_order_api_id(order_id, api_order_id)
        if success:
            await db.update_order_status(order_id, "confirmed")
            await db.set_confirmed_at(order_id)
            await _credit_referral_and_notify(call.bot, order_id)
            results.append((order, True, api_order_id))
        else:
            await db.update_order_status(order_id, "failed")
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

    for order in pending:
        await db.update_order_status(order["id"], "rejected")
        if order.get("payment_method") == "referral_balance":
            await db.add_referral_earning(order["user_id"], float(order["price"]))

    try:
        refund_note = (
            "\n💰 Маблаг ба балансаи реферралии шумо баргардонида шуд."
            if pending[0].get("payment_method") == "referral_balance" else ""
        )
        await call.bot.send_message(
            pending[0]["user_id"],
            f"❌ <b>Пардохти шумо рад карда шуд.</b>\n\n"
            f"{refund_note}\n\n"
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
    success, api_order_id = await _run_with_live_progress(
        wait_msg, header,
        ff_api.auto_donate(order["game_id"], order["offer_id"], order.get("api_order_id") or "")
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"🆔 ID FazerCards: <code>{api_order_id}</code>\n" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
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
        await _safe_edit_caption(
            wait_msg,
            f"⚠️ <b>Донати худкор нашуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}"
            f"🆔 ID: <code>{order['game_id']}</code>\n"
            f"{order['label']}\n\n"
            f"Метавонед дубора кӯшиш кунед ё дастӣ донат карда тасдиқ кунед.",
            retry_kb
        )


# ==================== ЧЕКИ МУВАФФАҚ (расм) ====================
_PM_LABELS = {
    "dushanbe_city": "🏙 Душанбе Сити",
    "alif": "💳 Алиф",
    "eskhata": "🏦 Эсхата",
    "referral_balance": "💰 Баланси рефералӣ",
}


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
    """Фармоишро рад мекунад: статус, баргардониди балансаи реферралӣ (агар лозим),
    хабар ба мизоҷ ва навсозии паёми фармоиш дар панели админ."""
    order = await db.get_order(order_id)
    if not order or order["status"] in ("confirmed", "rejected"):
        return False

    await db.update_order_status(order_id, "rejected")
    await db.set_order_reject_reason(order_id, reason_clean or "")
    if order.get("payment_method") == "referral_balance":
        await db.add_referral_earning(order["user_id"], float(order["price"]))

    try:
        refund_note = (
            "\n💰 Маблаг ба балансаи реферралии шумо баргардонида шуд."
            if order.get("payment_method") == "referral_balance" else ""
        )
        reason_line = f"\n📝 Сабаб: {esc(reason_clean)}\n" if reason_clean else ""
        await bot.send_message(
            order["user_id"],
            f"❌ <b>Пардохти шумо рад карда шуд.</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{reason_line}"
            f"{refund_note}\n\n"
            f"Агар хато бошад, бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Хабар ба корбар нашуд: {e}")

    caption = f"❌ <b>Фармоиши #{order_id} рад карда шуд.</b>"
    if reason_clean:
        caption += f"\n📝 Сабаб: {esc(reason_clean)}"
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, parse_mode="HTML")
    except Exception:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, parse_mode="HTML")
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
        "🕐 То ҳол ҲЕЧ гоҷ тоза карда нашудааст.\n\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Тоза кардани рейтинг", callback_data="a_leaderboard_reset_confirm")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_stats")]
    ])
    await _safe_edit(
        call,
        "🏆 <b>Идоракунии 'Топ харидорон'</b>\n\n"
        f"{info_line}"
        "ℹ️ Тоза кардан танҷо рейтингро аз нав мешуморад — "
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
        "Шумо мехоҷед рейтинги 'Топ харидорон'-ро тоза кунед?\n"
        "Баъд аз ин, фармоишҷои ПЕШИН дигар дар рейтинг ҷИсоб намешаванд "
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
        f"<b>{stats['best_weekday']}</b> ({stats['best_weekday_sales']:.2f} сом)"
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
        await db.add_product(amount, price, label, offer_id)
        await message.answer("✅ Маҳсулоти нав илова шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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
        data = await state.get_data()
        await db.update_product(data["edit_id"], amount, price, label, offer_id)
        await message.answer("✅ Маҳсулот навсозӣ шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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


async def _safe_edit_msg(msg: Message, text: str, kb):
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await msg.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"_safe_edit_msg хато: {e}")


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
        f"💰 Баланси рефералӣ: {ref_balance:.2f} сомонӣ\n\n"
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
            text="👥 Рефералхои ин корбар",
            callback_data=f"a_ref_subusers_{user_id}"
        )])
    kb_rows.append([InlineKeyboardButton(text="🚫 Бан/Анбан", callback_data="a_ban_unban")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="a_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


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
            f"👥 <b>Рефералхои корбари</b> <code>{user_id}</code>\n\nҲеч реферал нест.",
            kb
        )
        return
    lines = [f"👥 <b>Рефералхои корбари</b> <code>{user_id}</code>\n"]
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
        "referral_balance": "💰 Баланси рефералӣ",
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
        await call.answer(
            "❌ Чек нест — ин фармоиш нопурра мондааст (корбар чек нафиристодааст).",
            show_alert=True
        )
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
        ff_api.auto_donate_ffid(player_id, order["offer_id"], order.get("api_order_id") or "")
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
        await db.add_ffid_product(amount, price, label, offer_id)
        await message.answer("✅ Маҷсулоти нав илова шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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
        data = await state.get_data()
        await db.update_ffid_product(data["edit_id"], amount, price, label, offer_id)
        await message.answer("✅ Маҷсулот навсозӣ шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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
        ff_api.auto_donate_pubg(player_id, order["offer_id"], order.get("api_order_id") or "")
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
        await db.add_pubg_product(amount, price, label, offer_id)
        await message.answer("✅ Маҷсулоти нав илова шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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
        data = await state.get_data()
        await db.update_pubg_product(data["edit_id"], amount, price, label, offer_id)
        await message.answer("✅ Маҷсулот навсозӣ шуд!")
        await state.clear()
    except Exception:
        await message.answer(
            "⚠️ Хато! Формат:\n<code>миқдор | нарх | ном | offer_id</code>",
            parse_mode="HTML"
        )


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

    await call.answer("⏳ Дар ҷараён...", show_alert=False)

    tg_username = order["game_id"].replace("STARS:", "")

    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (Telegram Stars)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"📱 @{tg_username} — {order['label']}"
    )
    await _safe_edit_caption(call.message, header, None)

    success, api_order_id = await _run_with_live_progress(
        call.message, header,
        ff_api.buy_telegram_stars(tg_username, order["amount"])
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"\n🆔 ID FazerCards: <code>{api_order_id}</code>" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
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
        await _safe_edit_caption(
            call.message,
            f"⚠️ <b>Хато рух дод!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}\n"
            f"📱 @{tg_username} — {order['label']}\n\n"
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

    await call.answer("⏳ Дар ҷараён...", show_alert=False)

    tg_username = order["game_id"].replace("PREMIUM:", "")

    header = (
        f"⏳ <b>Автодонати шумо оғоз шуд (Telegram Premium)...</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"📱 @{tg_username} — {order['label']}"
    )
    await _safe_edit_caption(call.message, header, None)

    success, api_order_id = await _run_with_live_progress(
        call.message, header,
        ff_api.buy_telegram_premium(tg_username, order["amount"])
    )

    if api_order_id:
        await db.set_order_api_id(order_id, api_order_id)

    api_id_line = f"\n🆔 ID FazerCards: <code>{api_order_id}</code>" if api_order_id else ""

    if success:
        await db.update_order_status(order_id, "confirmed")
        await db.set_confirmed_at(order_id)
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
        await _safe_edit_caption(
            call.message,
            f"⚠️ <b>Хато рух дод!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"{api_id_line}\n"
            f"📱 @{tg_username} — {order['label']}\n\n"
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
        await db.add_stars_product(amount, price)
        await message.answer("✅ Илова шуд!")
        await state.clear()
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>миқдор | нарх</code>", parse_mode="HTML")


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
        data = await state.get_data()
        await db.update_stars_product(data["edit_id"], amount, price)
        await message.answer("✅ Навсозӣ шуд!")
        await state.clear()
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>миқдор | нарх</code>", parse_mode="HTML")


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
        await db.add_premium_product(months, price)
        await message.answer("✅ Илова шуд!")
        await state.clear()
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>моҳ | нарх</code>", parse_mode="HTML")


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
        data = await state.get_data()
        await db.update_premium_product(data["edit_id"], months, price)
        await message.answer("✅ Навсозӣ шуд!")
        await state.clear()
    except Exception:
        await message.answer("⚠️ Хато! Формат: <code>моҳ | нарх</code>", parse_mode="HTML")

