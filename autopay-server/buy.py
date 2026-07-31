"""
Раванди харид (БЕ БАЛАНС):
  1. Корбар ID-и Free Fire-ро менависад
  2. Бот номи аккаунтро тавассути API нишон медиҳад
  3. Корбар маҳсулотро интихоб мекунад
  4. Корбар тариқи пардохтро интихоб мекунад (Душанбе Сити / Алиф)
  5. Реквизит нишон дода мешавад
  6. Корбар расми чекро мефиристад
  7. Чек ба админ меравад → админ "Тасдиқ" зад → донати худкор
"""
import logging
import asyncio
import hashlib
import random
import uuid
import html
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db
import ff_api

TJ_TZ = ZoneInfo("Asia/Dushanbe")


def esc(text) -> str:
    """
    Номи бозингар ё ҳар матни бегонаро барои паёми HTML бехатар мекунад.
    Аломатҳои < > & -ро иваз мекунад, то паёми Telegram нашиканад
    (масалан агар ном чизе монанди '<PRO>Killer' бошад).
    """
    if text is None:
        return ""
    s = "".join(
        ch for ch in str(text)
        if unicodedata.category(ch) not in ("Cf", "Cc", "Co", "Cs", "Cn")
    )
    return html.escape(s, quote=False)

logger = logging.getLogger(__name__)
router = Router()


async def _hash_photo(message: Message) -> str:
    """Sha256-и байтҳои расми чекро мебарорад — барои муайян кардани
    он ки ҳамин чек пештар истифода шудааст ё не (новобаста аз file_id,
    ки ҳар бор метавонад фарқ кунад)."""
    try:
        buf = await message.bot.download(message.photo[-1])
        return hashlib.sha256(buf.read()).hexdigest()
    except Exception as e:
        logger.warning(f"Hash-и чек ҳисоб нашуд: {e}")
        return ""


async def _block_if_duplicate_check(message: Message, check_hash: str):
    """
    Агар ҳамин чек пештар барои фармоиши ТАСДИҚШУДАИ ҳамин корбар
    истифода шуда бошад — ба корбар хабар медиҳад ва order-и кӯҳнаро
    бармегардонад (даъваткунанда бояд дар ин ҳолат return кунад, то
    фармоиши нав/донати такрорӣ сохта нашавад).
    """
    if not check_hash:
        return None
    dup = await db.find_confirmed_duplicate_check(message.from_user.id, check_hash)
    if not dup:
        return None
    await message.answer(
        f"⚠️ <b>Ин чек аллакай истифода шудааст!</b>\n\n"
        f"Ҳамин расм барои фармоиши #{dup['id']} (тасдиқшуда, "
        f"{dup['label']}) аллакай қабул шуда буд.\n\n"
        f"Агар ин фармоиши НАВ ва пардохти ДИГАР бошад, лутфан скриншоти "
        f"НАВ (тоза) фиристед. Агар савол дошта бошед: {config.SUPPORT_USERNAME}",
        parse_mode="HTML"
    )
    return dup


TERMS_TEXT_ROZIGI = (
    "📜 <b>Шартҳои хизматрасонӣ</b>\n\n"
    "Бо пахши «Қабул мекунам» шумо тасдиқ мекунед:\n\n"
    "1️⃣ Синни шумо 18 сола ё зиёдтар аст, ё бо иҷозати падару модар харид мекунед\n"
    "2️⃣ Масъулияти дурустии Player ID ба зиммаи шумост\n"
    "3️⃣ Пас аз донат, маблағ баргардонида намешавад (ба ғайр аз хатои техникии мо)\n\n"
    "✅ Бо пахши «Қабул мекунам» шумо розигии худро эълон мекунед."
)


async def _notify_rozigiho(bot, user, service_title: str, label: str,
                            price: float, method_name: str, order_ref: str):
    """
    Баъд аз розигии корбар, ба канали @rozigiho хабар мефиристад.
    order_ref — рамзи муваққатии фармоиш (product_id, чун order ҳанӯз
    дар база сохта нашудааст дар ин лаҳза).
    """
    if not config.ROZIGIHO_CHANNEL:
        return
    username = f"@{user.username}" if user.username else "—"
    now_str = datetime.now(TJ_TZ).strftime("%d.%m.%Y %H:%M")
    text = (
        f"✅ <b>Розигии нав!</b>\n\n"
        f"🎮 {service_title}\n"
        f"🎁 Маҳсулот: {label}\n"
        f"👤 Ном: {esc(user.full_name)}\n"
        f"📱 Username: {username}\n"
        f"🕒 Вақт: {now_str}\n"
        f"💵 Нарх: {price:.2f} сомонӣ\n"
        f"💳 Тариқи пардохт: {method_name}\n"
        f"#order_{order_ref}"
    )
    try:
        await bot.send_message(config.ROZIGIHO_CHANNEL, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Хабар ба {config.ROZIGIHO_CHANNEL} нарасид: {e}")


class BuyState(StatesGroup):
    enter_id       = State()  # интизори ID
    choose_product = State()  # интизори интихоби маҳсулот
    choose_cart    = State()  # интизори интихоби якчанд маҳсулот (сабад)
    choose_payment = State()  # интизори интихоби тариқи пардохт
    wait_check     = State()  # интизори расми чек


# ==================== ОҒОЗ: ID НАВИШТАН ====================
@router.callback_query(F.data == "buy")
async def buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    last = await db.get_last_player_id(call.from_user.id, prefix="")
    kb_rows = []
    if last:
        label = f"✅ Истифодаи ID: {last['player_id']}"
        if last["nickname"]:
            label += f" ({last['nickname']})"
        kb_rows.append([InlineKeyboardButton(text=label, callback_data="use_last_id")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if last:
        text = (
            "🔥 <b>Free Fire — Харидани алмаз</b>\n\n"
            "Шумо пештар бо ин ID харид карда будед:\n"
            f"🆔 <code>{last['player_id']}</code>\n\n"
            "✅ Агар ҲАМИН ID-ро истифода баред — тугмаи\n"
            "   поёнро пахш кунед.\n\n"
            "✏️ Агар ID-и ДИГАР доред — онро ҳозир дар\n"
            "   чат нависед."
        )
    else:
        text = (
            "🔥 <b>Free Fire — Харидани алмаз</b>\n\n"
            "📝 ID аккаунтатонро нависед:\n"
            "Мисол: <code>3178989085</code>"
        )

    await _safe_edit(call, text, kb)
    await state.set_state(BuyState.enter_id)


@router.callback_query(F.data == "use_last_id")
async def buy_use_last_id(call: CallbackQuery, state: FSMContext):
    last = await db.get_last_player_id(call.from_user.id, prefix="")
    if not last:
        await call.answer("ID-и пештара ёфт нашуд, лутфан нав нависед.", show_alert=True)
        return
    # Бевосита ба рӯйхати маҳсулот — БЕ тасдиқи дубораи "Давом",
    # зеро пахши ин тугма худаш як амали тасдиқ буд
    player_id = last["player_id"]
    nickname = last["nickname"] or await ff_api.get_nickname(player_id)
    await state.update_data(player_id=player_id, nickname=nickname)
    await show_products(call, state)


@router.message(BuyState.enter_id)
async def buy_enter_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    # ID бояд танҳо рақам бошад
    if not player_id.isdigit():
        await message.answer("⚠️ ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return

    wait = await message.answer("⏳ ID тафтиш мешавад...")

    # Номи аккаунтро аз API мегирем
    nickname = await ff_api.get_nickname(player_id)

    await state.update_data(player_id=player_id, nickname=nickname)

    if nickname:
        text = (
            f"🔥 <b>Free Fire</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"👤 Ном: <b>{esc(nickname)}</b>\n\n"
            f"✅ Агар ин аккаунти шумо бошад «Давом»-ро пахш кунед:"
        )
    else:
        # API номро наёфт — корбар худаш тасдиқ мекунад
        text = (
            f"🔥 <b>Free Fire</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"⚠️ Номи аккаунт ёфт нашуд.\n\n"
            f"ID-ро бодиққат тафтиш кунед ва агар дуруст бошад «Давом»-ро пахш кунед:"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="id_ok")],
        [InlineKeyboardButton(text="✏️ ID-ро тағйир медиҳам", callback_data="buy")],
    ])
    await _safe_edit_msg(wait, text, kb)


# ==================== РӮЙХАТИ МАҲСУЛОТ ====================
@router.callback_query(F.data == "id_ok")
async def show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_products()
    if not products:
        await call.answer("❌ Ҳозир маҳсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return

    buttons = []
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        tag = "🔥 Маъмултарин — " if p.get("is_featured") else ""
        buttons.append([InlineKeyboardButton(
            text=f"{tag}{label} — {p['price']:.2f} сом",
            callback_data=f"prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🛒 Якчанд маҳсулот интихоб кардан", callback_data="cart_start")])
    buttons.append([InlineKeyboardButton(text="🎁 Комбоҳо", callback_data="combo_list")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy")])

    await _safe_edit(
        call,
        "💎 <b>Алмазҳои Free Fire</b>\n\nМаҳсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(BuyState.choose_product)


# ==================== КОМБОҲО ====================
@router.callback_query(F.data == "combo_list")
async def combo_list(call: CallbackQuery, state: FSMContext):
    combos = await db.get_active_combos()
    if not combos:
        await call.answer("❌ Ҳозир комбо нест.", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"🎁 {c['label']} — {float(c['price']):.2f} сом",
            callback_data=f"combo_pick_{c['id']}"
        )]
        for c in combos
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")])
    await _safe_edit(
        call,
        "🎁 <b>Комбоҳо</b>\n\nКомбои хостаатонро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(BuyState.choose_product)


@router.callback_query(F.data.startswith("combo_pick_"), BuyState.choose_product)
async def combo_pick(call: CallbackQuery, state: FSMContext):
    combo_id = int(call.data.split("_")[2])
    combo = await db.get_combo(combo_id)
    if not combo or not combo.get("is_active"):
        await call.answer("❌ Ин комбо дигар фаъол нест!", show_alert=True)
        return

    await state.update_data(
        product_id=None,
        amount=0,
        price=float(combo["price"]),
        label=f"🎁 {combo['label']}",
        offer_id="",
        eskhata_link="",
        is_custom_price=True,
        combo_id=combo_id,
        cart=None,
        cart_items=None,
    )
    data = await state.get_data()
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="combo_list")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Комбо: <b>{combo['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(BuyState.choose_payment)


# ==================== САБАД (ЯКЧАНД МАҲСУЛОТ) ====================
def _summarize_cart_labels(cart: dict, products_by_id: dict):
    """Рӯйхати матнии маҳсулотҳо бо миқдор: ['💎 520 ×3', '💎 100 ×1']"""
    out = []
    for pid, qty in cart.items():
        p = products_by_id.get(pid)
        if not p or qty <= 0:
            continue
        label = p.get("label") or f"💎 {p['amount']}"
        out.append(f"{label} ×{qty}")
    return out


async def _render_cart_text_and_kb(user_id: int, products, cart: dict):
    """
    Матн ва клавиатураи саҳифаи сабадро месозад.
    cart — dict {product_id: quantity} (миқдори ҳар маҳсулот).
    """
    buttons = []
    total = 0.0
    chosen_lines = []
    for p in products:
        pid = p["id"]
        label = p.get("label") or f"💎 {p['amount']}"
        custom_price = await db.get_custom_price(user_id, pid)
        price = custom_price if custom_price is not None else float(p["price"])
        qty = cart.get(pid, 0)

        # Сатри 1 — номи маҳсулот (пахш = +1)
        mark = "✅" if qty > 0 else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {label} — {price:.2f} сом",
            callback_data=f"cart_add_{pid}"
        )])
        # Сатри 2 — идораи миқдор (танҳо агар интихоб шуда бошад)
        if qty > 0:
            buttons.append([
                InlineKeyboardButton(text="➖", callback_data=f"cart_minus_{pid}"),
                InlineKeyboardButton(text=f"{qty} дона", callback_data="cart_noop"),
                InlineKeyboardButton(text="➕", callback_data=f"cart_add_{pid}"),
            ])
            line_sum = price * qty
            total += line_sum
            chosen_lines.append(f"  • {label} ×{qty} = {line_sum:.2f} сом")

    if chosen_lines:
        summary = "✅ <b>Интихобшуда:</b>\n" + "\n".join(chosen_lines) + f"\n\n💵 Ҷамъ: <b>{total:.2f} сомонӣ</b>"
    else:
        summary = "Ҳанӯз ҳеч чиз интихоб накардаед."

    buttons.append([InlineKeyboardButton(text="✅ Давом додан", callback_data="cart_done")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")])

    text = (
        f"🛒 <b>Якчанд маҳсулот интихоб кунед</b>\n\n"
        f"Барои илова, ба маҳсулот пахш кунед. Бо ➕ / ➖ миқдорро танзим кунед.\n\n"
        f"{summary}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "cart_start")
async def cart_start(call: CallbackQuery, state: FSMContext):
    products = await db.get_products()
    if not products:
        await call.answer("❌ Ҳозир маҳсулот нест!", show_alert=True)
        return
    await state.update_data(cart={})
    text, kb = await _render_cart_text_and_kb(call.from_user.id, products, {})
    await _safe_edit(call, text, kb)
    await state.set_state(BuyState.choose_cart)


@router.callback_query(F.data == "cart_noop", BuyState.choose_cart)
async def cart_noop(call: CallbackQuery):
    # Тугмаи "N дона" — танҳо намоишӣ, коре намекунад
    await call.answer()


@router.callback_query(F.data.startswith("cart_add_"), BuyState.choose_cart)
async def cart_add(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[-1])
    data = await state.get_data()
    # калидҳои dict баъд аз FSM метавонанд str шаванд — ба int мубаддал мекунем
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    cart[product_id] = cart.get(product_id, 0) + 1
    await state.update_data(cart=cart)

    products = await db.get_products()
    label = next((p.get("label") or f"💎 {p['amount']}" for p in products if p["id"] == product_id), "Маҳсулот")
    text, kb = await _render_cart_text_and_kb(call.from_user.id, products, cart)
    await _safe_edit(call, text, kb)
    await call.answer(f"➕ {label} (×{cart[product_id]})")


@router.callback_query(F.data.startswith("cart_minus_"), BuyState.choose_cart)
async def cart_minus(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[-1])
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    if product_id in cart:
        cart[product_id] -= 1
        if cart[product_id] <= 0:
            del cart[product_id]
    await state.update_data(cart=cart)

    products = await db.get_products()
    text, kb = await _render_cart_text_and_kb(call.from_user.id, products, cart)
    await _safe_edit(call, text, kb)
    await call.answer()


@router.callback_query(F.data == "cart_done", BuyState.choose_cart)
async def cart_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    if not cart:
        await call.answer("⚠️ Ҳеч маҳсулот интихоб накардаед!", show_alert=True)
        return

    products = await db.get_products()
    products_by_id = {p["id"]: p for p in products}

    items = []
    total = 0.0
    # Ҳар маҳсулотро вобаста ба миқдораш такрор мекунем (×3 → 3 адад)
    for pid, qty in cart.items():
        p = products_by_id.get(pid)
        if not p or qty <= 0:
            continue
        custom_price = await db.get_custom_price(call.from_user.id, pid)
        price = custom_price if custom_price is not None else float(p["price"])
        label = p.get("label") or f"💎 {p['amount']}"
        for _ in range(qty):
            items.append({
                "product_id": pid,
                "amount": p["amount"],
                "price": price,
                "label": label,
                "offer_id": p.get("offer_id") or "",
                "is_custom_price": custom_price is not None,
            })
            total += price

    if len(items) == 1:
        # Сабад бо ФАҚАТ як маҳсулот — мисли фармоиши оддии ягона рафтор
        # мекунад (на ҳамчун "сабад"), то автопардохти Душанбе Сити/Алиф
        # фаъол шавад ва дар коменти корт рақами фармоиши ВОҚЕӢ равад, на
        # placeholder-и сабад (пеш ин боиси "рақами фармоиш хато" мешуд).
        only = items[0]
        only_product = products_by_id.get(only["product_id"]) or {}
        await state.update_data(
            product_id=only["product_id"],
            amount=only["amount"],
            price=only["price"],
            label=only["label"],
            offer_id=only["offer_id"],
            is_custom_price=only["is_custom_price"],
            eskhata_link=only_product.get("eskhata_link") or "",
            cart=None,
            cart_items=None,
            combo_id=None,
        )
        await _show_payment_method_choice(call, state)
        return

    await state.update_data(
        cart_items=items,
        price=round(total, 2),
        label=" + ".join(_summarize_cart_labels(cart, products_by_id)),
        is_custom_price=any(i["is_custom_price"] for i in items),
        combo_id=None,
    )

    data = await state.get_data()
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    # Дар матн бо миқдор нишон медиҳем (×3), на 3 сатри такрорӣ
    items_text = "\n".join(
        f"  • {products_by_id[pid].get('label') or ('💎 ' + str(products_by_id[pid]['amount']))} ×{qty}"
        for pid, qty in cart.items() if pid in products_by_id and qty > 0
    )

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="cart_start")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш (сабад)</b>\n\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулотҳо:\n{items_text}\n\n"
        f"💵 Ҷамъи умумӣ: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(BuyState.choose_payment)


# ==================== ИНТИХОБИ ТАРИҚИ ПАРДОХТ ====================
async def _show_payment_method_choice(call: CallbackQuery, state: FSMContext):
    """
    Экрани «Тасдиқи фармоиш» + интихоби тариқи пардохт — барои маҳсулоти
    ягона (аз рӯйхат ё аз сабад-бо-як-маҳсулот, ки ба ин ҳамин тавр
    фурӯхта мешавад — то автопардохт фаъол бошад).
    """
    data = await state.get_data()
    label = data["label"]
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    vip_note = "\n💎 <b>Нархи шахсии шумо!</b>\n" if data.get("is_custom_price") else ""

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n"
        f"{vip_note}\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(BuyState.choose_payment)


@router.callback_query(F.data.startswith("prod_"), BuyState.choose_product)
async def choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[1])
    product = await db.get_product(product_id)
    if not product:
        await call.answer("❌ Маҳсулот ёфт нашуд!", show_alert=True)
        return

    # Нархи шахсии мизоҷ (VIP pricing) — танҳо барои FF СНГ
    custom_price = await db.get_custom_price(call.from_user.id, product_id)
    final_price = custom_price if custom_price is not None else float(product["price"])

    await state.update_data(
        product_id=product_id,
        amount=product["amount"],
        price=final_price,
        label=product.get("label") or f"💎 {product['amount']}",
        offer_id=product.get("offer_id") or "",
        eskhata_link=product.get("eskhata_link") or "",
        is_custom_price=custom_price is not None,
        cart_items=None,
        combo_id=None,
    )
    await _show_payment_method_choice(call, state)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ ====================
@router.callback_query(F.data.in_({"pay_dc", "pay_alif", "pay_eskhata"}), BuyState.choose_payment)
async def ask_terms_before_requisites(call: CallbackQuery, state: FSMContext):
    method_map = {
        "pay_dc": "dushanbe_city",
        "pay_eskhata": "eskhata",
        "pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "terms_reject", BuyState.choose_payment)
async def terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    # Бозгашт ба интихоби тариқи пардохт — choose_payment-ро дубора нишон медиҳем
    data = await state.get_data()
    label = data["label"]
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    vip_note = "\n💎 <b>Нархи шахсии шумо!</b>\n" if data.get("is_custom_price") else ""
    is_cart = bool(data.get("cart_items"))
    product_word = "Маҳсулотҳо" if is_cart else "Маҳсулот"
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(
        text="🔙 Бозгашт", callback_data="cart_start" if is_cart else "id_ok"
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 {product_word}: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n"
        f"{vip_note}\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


# ==================== НИШОН ДОДАНИ ЛИНКИ ПАРДОХТ ====================
async def _unique_autopay_price(base_price: float) -> float:
    """
    Ба нарх сентҳои хурд (0.01–0.99) илова мекунад, то бо фармоишҳои
    дигари 'дар интизории автопардохт' бархӯрд накунад — система пардохти
    воридотиро маҳз бо ҳамин маблағи нодир ба фармоиши дуруст мутобиқ
    мекунад (Kod-и DC Next танҳо БАЪД аз пардохт маълум мешавад).
    """
    active = await db.get_active_awaiting_prices("dushanbe_city")
    price = round(base_price, 2)
    if price not in active:
        return price
    for cents in range(1, 100):
        candidate = round(base_price + cents / 100, 2)
        if candidate not in active:
            return candidate
    return round(base_price + 0.99, 2)


async def _combo_breakdown_text(combo_id: int | None) -> str:
    """
    Рӯйхати ичозати комбо барои каптиони админ — то донад дар дохили
    комбо чӣ ҳаст ва бояд ба таври ДАСТӢ чӣ иҷро кунад (комбо худкор
    донат намешавад).
    """
    if not combo_id:
        return ""
    items = await db.get_combo_items(combo_id)
    if not items:
        return ""
    lines = []
    for it in items:
        qty = it.get("quantity") or 1
        if it.get("product_id"):
            label = it.get("product_label") or f"💎 {it.get('product_amount')}"
        else:
            label = it.get("custom_label") or "—"
        lines.append(f"  • {esc(label)} ×{qty}")
    return "\n\n🎁 <b>Дар дохили комбо (дастӣ иҷро кунед):</b>\n" + "\n".join(lines)


async def _apply_winback_discount(user_id: int, price: float) -> tuple[float, str]:
    """
    Агар мизоҷ тахфифи фаъоли баргардонӣ дошта бошад (мизоҷи хомӯшшуда,
    ки паёми "мо шуморо пазмон шудем" гирифтааст), -3%-ро ба нарх татбиқ
    мекунад ва тахфифро истифодашуда мешуморад (як маротиба).
    """
    pct = await db.get_winback_discount(user_id)
    if not pct:
        return price, ""
    new_price = round(price * (1 - pct / 100), 2)
    await db.clear_winback(user_id)
    note = f"🎁 <b>Тахфифи баргардонӣ -{pct:g}%</b> татбиқ шуд!\n"
    return new_price, note


@router.callback_query(F.data == "terms_accept", BuyState.choose_payment)
async def show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    method = data.get("pending_payment_method", "alif")
    is_cart = bool(data.get("cart_items"))
    # Автопардохт: ҳам Душанбе Сити, ҳам Алиф (ҳарду ба корти DC меоянд).
    # Комбоҳо ҳамеша тавассути чек (дастӣ) мераванд — на автопардохт, зеро
    # донати онҳо дастист (якчанд қисм дошта метавонанд, аз ҷумла қисмҳои
    # дастӣ мисли Level-Up Pass).
    is_autopay = (method in ("dushanbe_city", "alif") and not is_cart and not data.get("combo_id"))

    winback_note = ""
    if is_autopay:
        # Автопардохт: тахфифи сатҳи кӯҳна татбиқ намешавад, вале тахфифи
        # баргардонии мизоҷи хомӯшшуда ҳа — ба нархи АСОСӢ, пеш аз
        # беназиркунии сент (то система пардохтро аз рӯи маблағ шиносад,
        # барои DC инчунин коменти card_XXXX бо рақами фармоиш кор мекунад).
        disc_pct, disc_amt = 0.0, 0.0
        base_price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(data["price"]), 2))
        price = await _unique_autopay_price(base_price)
        await state.update_data(price=price)
    elif data.get("is_custom_price"):
        # Нархи шахсии мизоҷ — тахфиф ба ин намерасад
        price, disc_pct, disc_amt = data["price"], 0.0, 0.0
    elif method == "alif":
        # Алиф (сабад): нархи каме нодир — то маблағи дарёфтшуда АЙНАН бо
        # ин фармоиш мувофиқат кунад (зидди чеки такрорӣ/дуруғин). Ин ба
        # худи линки пардохт (amount=) низ мегузарад — мизоҷ маҳз ҳамин
        # маблағро мебинад дар барномаи Алиф.
        disc_pct, disc_amt = 0.0, 0.0
        base_price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(data["price"]), 2))
        price = base_price + round(random.randint(1, 99) / 100, 2)
        price = round(price, 2)
        await state.update_data(price=price)
    else:
        price, disc_pct, disc_amt = data["price"], 0.0, 0.0
        price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(price), 2))
        await state.update_data(price=price)
    order_id = data.get("product_id") or "cart" + str(uuid.uuid4())[:8]
    eskhata_note = ""
    discount_note = winback_note

    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_{order_id}&f1=133"
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҷсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    await state.update_data(payment_method=method)

    # Хабар ба @rozigiho — баъд аз розигӣ, пеш аз чек
    await _notify_rozigiho(
        call.bot, call.from_user, "🔥 Free Fire", data["label"],
        price, method_name, str(order_id)
    )

    # ==== АВТОПАРДОХТ: Душанбе Сити / Алиф, як маҳсулот (на сабад) ====
    if is_autopay:
        awaiting_order_id = await db.create_awaiting_order(
            user_id=call.from_user.id,
            game_id=data["player_id"],
            nickname=data.get("nickname", ""),
            amount=data.get("amount", 0),
            price=price,
            label=data["label"],
            offer_id=data.get("offer_id", ""),
            payment_method=method,
        )
        await state.update_data(autopay_order_id=awaiting_order_id)
        if method == "dushanbe_city":
            # Линки пардохт бо РАҚАМИ ФАРМОИШИ ВОҚЕӢ дар комент — DC онро
            # дар notification бармегардонад (card§8848) ва бот фармоишро
            # мустақим аз рӯи он меёбад
            dc_card = await db.get_dc_card_number()
            pay_url = (
                f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}"
                f"&c=card_{awaiting_order_id}&f1=133"
            )
        else:
            # Алиф — комент надорад, шинохт аз рӯи маблағи нодир
            pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пардохт", url=pay_url)],
            [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")],
        ])
        await _safe_edit(
            call,
            f"💳 <b>{method_name}</b>\n\n"
            f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
            f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n"
            f"🆔 Фармоиш: #{awaiting_order_id}\n\n"
            f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
            f"2️⃣ Маблағи <b>дақиқ {price:.2f} сом</b>-ро пардохт кунед "
            f"(на кам, на зиёд — тин ба тин!)\n"
            f"3️⃣ Расми чекро ба ҳамин чат фиристед\n\n"
            f"⚡ Пас аз фиристодани чек, системаи мо пардохти шуморо "
            f"<b>худкор</b> тафтиш мекунад ва алмазҳо худкор фиристода "
            f"мешаванд — интизории админ лозим нест!\n\n"
            f"⏳ Шумо <b>15 дақиқа</b> вақт доред.",
            kb
        )
        await state.set_state(BuyState.wait_check)
        return

    # ==== Тартиби кӯҳна (Алиф / Эсхата / сабад) — бо чек ====
    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(BuyState.wait_check)


@router.message(BuyState.wait_check, F.photo)
async def receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    # State-ро холӣ мекунем то такрор нашавад
    await state.clear()

    file_id = message.photo[-1].file_id

    # ==== АВТОПАРДОХТ (Душанбе Сити): чек омад → ҷустуҷӯи пардохт ====
    autopay_order_id = data.get("autopay_order_id")
    if autopay_order_id:
        import autopay
        order = await db.get_order(autopay_order_id)
        # 'expired' низ иҷозат дода мешавад — агар мизоҷ дер карда чек фиристад ҳам,
        # донати худкор кӯшиш карда мешавад, на радди фаврӣ
        if not order or order["status"] not in ("awaiting_autopay", "expired"):
            await message.answer(
                "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
                f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
                parse_mode="HTML"
            )
            return
        if not await db.set_autopay_check(autopay_order_id, file_id):
            await message.answer(
                "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
                f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
                parse_mode="HTML"
            )
            return
        await message.answer(
            f"✅ <b>Чек қабул шуд!</b>\n\n"
            f"🆔 Фармоиш: #{autopay_order_id}\n\n"
            f"🔍 Системаи мо ҳоло пардохти шуморо <b>худкор</b> ҷустуҷӯ "
            f"мекунад — одатан 5-30 сония мегирад.\n"
            f"Натиҷа ҳозир хабар дода мешавад...",
            parse_mode="HTML"
        )
        # Шояд пардохт аллакай ПЕШ аз чек омада бошад — тафтиш мекунем:
        # аввал Kod-и ба ҳамин фармоиш резервшуда (аз коменти card_XXXX),
        # баъд ҳамчун эҳтиёт — аз рӯи маблағ
        kod = await db.find_kod_for_order(autopay_order_id) \
            or await db.find_unmatched_kod(float(order["price"]), autopay.MAX_AGE_MINUTES)
        if kod:
            order = await db.get_order(autopay_order_id)
            asyncio.create_task(autopay.run_donate(message.bot, order, kod))
        return

    # ==== Пешгирии донати такрорӣ: агар ҳамин чек пештар тасдиқ шуда буд ====
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return

    _pm = data.get("payment_method")
    if _pm == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
    elif _pm == "eskhata":
        method_name = "🏦 Эсхата"
    else:
        method_name = "💳 Алиф"

    cart_items = data.get("cart_items")

    if cart_items:
        # ---- САБАД: барои ҳар маҳсулот order-и алоҳида, бо як group_id ----
        group_id = str(uuid.uuid4())
        order_ids = []
        for item in cart_items:
            oid = await db.create_order(
                user_id=message.from_user.id,
                game_id=data["player_id"],
                nickname=data.get("nickname", ""),
                amount=item["amount"],
                price=item["price"],
                label=item["label"],
                offer_id=item["offer_id"],
                payment_method=data.get("payment_method", ""),
                order_group_id=group_id,
            )
            await db.set_order_check(oid, file_id, check_hash)
            order_ids.append(oid)

        ids_text = ", ".join(f"#{i}" for i in order_ids)
        await message.answer(
            "✅ <b>Чек қабул шуд!</b>\n\n"
            f"🆔 Фармоишҳо: {ids_text}\n\n"
            "🔄 Пардохти шумо тафтиш мешавад.\n"
            "Натиҷа ба зудӣ фиристода мешавад. 🙏",
            parse_mode="HTML"
        )

        nickname = data.get("nickname", "") or "—"
        username = f"@{message.from_user.username}" if message.from_user.username else "—"
        items_text = "\n".join(
            f"  🎁 {item['label']} — {item['price']:.2f} сом (#{oid})"
            for item, oid in zip(cart_items, order_ids)
        )
        caption = (
            f"📸 <b>Фармоиши нав (сабад) — чек омад!</b>\n\n"
            f"🆔 Фармоишҳо: <b>{ids_text}</b>\n"
            f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
            f"📱 Username: {username}\n"
            f"💳 Тариқ: {method_name}\n\n"
            f"🎮 Free Fire\n"
            f"🆔 ID: <code>{data['player_id']}</code>\n"
            f"👤 Ном: <b>{esc(nickname)}</b>\n\n"
            f"{items_text}\n\n"
            f"💵 Ҷамъи умумӣ: <b>{data['price']:.2f} сомонӣ</b>"
        )
        username_val = message.from_user.username
        admin_kb_rows = [
            [InlineKeyboardButton(text="✅ Тасдиқи ҳамаи гурӯҳ — донат кун", callback_data=f"okgroup_{group_id}")],
            [InlineKeyboardButton(text="❌ Рад кардани ҳамаи гурӯҳ",         callback_data=f"nogroup_{group_id}")],
        ]
        if username_val:
            admin_kb_rows.append(
                [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
            )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
        for admin_id in config.ADMIN_IDS:
            try:
                await message.bot.send_photo(
                    admin_id, file_id, caption=caption,
                    reply_markup=admin_kb, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")
        return

    # ---- ЯГОНА МАҲСУЛОТ (мисли пеш) ----
    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=data["player_id"],
        nickname=data.get("nickname", ""),
        amount=data["amount"],
        price=data["price"],
        label=data["label"],
        offer_id=data.get("offer_id", ""),
        payment_method=data.get("payment_method", ""),
        combo_id=data.get("combo_id"),
    )
    await db.set_order_check(order_id, file_id, check_hash)
    combo_breakdown = await _combo_breakdown_text(data.get("combo_id"))

    # Ба корбар
    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )

    # Ба ҳамаи админҳо — расм + тугмаҳо
    nickname = data.get("nickname", "") or "—"
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"🎮 Free Fire\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"👤 Ном: <b>{esc(nickname)}</b>\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
        f"{combo_breakdown}"
    )
    # ЛС тугма — танҳо агар username бошад (tg://user?id= боиси
    # BUTTON_USER_PRIVACY_RESTRICTED ва рад шудани тамоми паём мешавад,
    # агар танзимоти privacy-и корбар маҳдуд бошад)
    username_val = message.from_user.username
    confirm_text = "✅ Тасдиқ — дастӣ иҷро кунед" if data.get("combo_id") else "✅ Тасдиқ — донат кун"
    admin_kb_rows = [
        [InlineKeyboardButton(text=confirm_text, callback_data=f"ok_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан",          callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id, file_id, caption=caption,
                reply_markup=admin_kb, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(BuyState.wait_check)
async def wrong_check(message: Message, state: FSMContext):
    """Агар корбар ба ҷои расм матн фиристад."""
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ==================== ОТЗИВ ====================
class ReviewState(StatesGroup):
    enter_text = State()


@router.callback_query(F.data.startswith("review_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    order_id = call.data.split("_", 1)[1]
    await state.update_data(review_order_id=order_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бекор", callback_data="back_main")]
    ])
    await _safe_edit(
        call,
        "⭐ <b>Отзив гузоред!</b>\n\nФикри худро нависед 👇",
        kb
    )
    await state.set_state(ReviewState.enter_text)


@router.message(ReviewState.enter_text)
async def review_save(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("review_order_id", "")
    await state.clear()

    # Маълумоти фармоишро аз база мегирем (агар order_id дода шуда бошад ва аз они ҳамин корбар бошад)
    order = await db.get_order(int(order_id)) if order_id and order_id.isdigit() else None
    if order and order.get("user_id") != message.from_user.id:
        order = None
    label = order.get("label", "—") if order else "—"

    stats = await db.get_user_stats(message.from_user.id)
    total_orders = stats["total_orders"]

    sent = False
    if config.REVIEW_CHANNEL_ID:
        try:
            review_number = await db.increment_review_count()
            loyalty_line = ""
            if total_orders > 1:
                loyalty_line = (
                    f"\n🔥 Ин муштарӣ аллакай <b>{total_orders}</b>-умин хариди худро "
                    f"анҷом дод. Ташаккур барои эътимод ва ҳамкории доимӣ! ❤️\n"
                )
            await message.bot.send_message(
                config.REVIEW_CHANNEL_ID,
                f"🏅 <b>ОТЗИВИ МУШТАРӢ #{review_number}</b>\n\n"
                f"👤 Муштарӣ: {esc(message.from_user.full_name)}\n\n"
                f"💬 <b>Назари муштарӣ:</b>\n«{esc(message.text)}»\n\n"
                f"🎁 Маҳсулот: {label}\n"
                f"🆔 ID фармоиш: #{order_id if order_id else '—'}\n"
                f"{loyalty_line}\n"
                f"🤖 @{config.BOT_USERNAME}",
                parse_mode="HTML"
            )
            sent = True
        except Exception as e:
            logger.error(f"Отзив ба канал нашуд: {e}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Асосӣ", callback_data="back_main")]
    ])
    if sent:
        await message.answer(
            "✅ <b>Ташаккур барои отзив!</b>\n\nОтзиви шумо нашр шуд 🙏",
            reply_markup=kb, parse_mode="HTML"
        )
    else:
        await message.answer(
            "✅ <b>Ташаккур барои отзив!</b>",
            reply_markup=kb, parse_mode="HTML"
        )


# ==================== ЁРИРАСОНҲО ====================
async def _safe_edit(call: CallbackQuery, text: str, kb):
    """edit_text бо муҳофизат — агар нашавад, паёми нав мефиристад."""
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
    """Барои таҳрири паёми 'Тафтиш...'."""
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await msg.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"_safe_edit_msg хато: {e}")


# ════════════════════════════════════════════════════════
#                FREE FIRE INDONESIA
# ════════════════════════════════════════════════════════

class FFIDBuyState(StatesGroup):
    enter_id       = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


# ==================== ОҒОЗ: ID НАВИШТАН ====================
@router.callback_query(F.data == "buy_ffid")
async def ffid_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="games_menu")]
    ])
    await _safe_edit(
        call,
        "🔥 <b>Free Fire Indonesia — Харидани алмаз</b>\n\n"
        "📝 ID аккаунтатонро нависед:\n"
        "Мисол: <code>123456789</code>",
        kb
    )
    await state.set_state(FFIDBuyState.enter_id)


@router.message(FFIDBuyState.enter_id)
async def ffid_enter_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    if not player_id.isdigit():
        await message.answer("⚠️ ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return

    wait = await message.answer("⏳ ID тафтиш мешавад...")
    nickname = await ff_api.get_nickname_ffid(player_id)
    await state.update_data(player_id=player_id, nickname=nickname)

    if nickname:
        text = (
            f"🔥 <b>Free Fire Indonesia</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"👤 Ном: <b>{esc(nickname)}</b>\n\n"
            f"✅ Агар ин аккаунти шумо бошад «Давом»-ро пахш кунед:"
        )
    else:
        text = (
            f"🔥 <b>Free Fire Indonesia</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"⚠️ Номи аккаунт ёфт нашуд.\n\n"
            f"ID-ро бодиққат тафтиш кунед ва агар дуруст бошад «Давом»-ро пахш кунед:"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="ffid_id_ok")],
        [InlineKeyboardButton(text="✏️ ID-ро тағйир медиҳам", callback_data="buy_ffid")],
    ])
    await _safe_edit_msg(wait, text, kb)


# ==================== РӮЙХАТИ МАҲСУЛОТ ====================
@router.callback_query(F.data == "ffid_id_ok")
async def ffid_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_ffid_products()
    if not products:
        await call.answer("❌ Ҳозир маҷсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return

    buttons = []
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {p['price']:.2f} сом",
            callback_data=f"ffid_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_ffid")])

    await _safe_edit(
        call,
        "💎 <b>Алмазҳои Free Fire Indonesia</b>\n\nМаҷсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(FFIDBuyState.choose_product)


# ==================== ИНТИХОБИ ТАРИҚИ ПАРДОХТ ====================
@router.callback_query(F.data.startswith("ffid_prod_"), FFIDBuyState.choose_product)
async def ffid_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_ffid_product(product_id)
    if not product:
        await call.answer("❌ Маҷсулот ёфт нашуд!", show_alert=True)
        return

    await state.update_data(
        product_id=product_id,
        amount=product["amount"],
        price=float(product["price"]),
        label=product.get("label") or f"💎 {product['amount']}",
        offer_id=product.get("offer_id") or "",
        eskhata_link=product.get("eskhata_link") or "",
        combo_id=None,
    )

    data = await state.get_data()
    label = data["label"]
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ffid_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ffid_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="ffid_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffid_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🔥 Free Fire Indonesia\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(FFIDBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ (FFID) ====================
@router.callback_query(F.data.in_({"ffid_pay_dc", "ffid_pay_alif", "ffid_pay_eskhata"}), FFIDBuyState.choose_payment)
async def ffid_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "ffid_pay_dc": "dushanbe_city",
        "ffid_pay_eskhata": "eskhata",
        "ffid_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="ffid_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="ffid_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "ffid_terms_reject", FFIDBuyState.choose_payment)
async def ffid_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    label = data["label"]
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ffid_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ffid_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="ffid_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffid_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🔥 Free Fire Indonesia\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


# ==================== НИШОН ДОДАНИ РЕКВИЗИТ ====================
@router.callback_query(F.data == "ffid_terms_accept", FFIDBuyState.choose_payment)
async def ffid_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price, disc_pct, disc_amt = data["price"], 0.0, 0.0
    price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(price), 2))
    await state.update_data(price=price)
    order_id = data.get("product_id", 0)
    eskhata_note = ""
    discount_note = winback_note

    method = data.get("pending_payment_method", "alif")
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_ffid{order_id}&f1=133"
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҷсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        # Нархи каме нодир — зидди чеки такрорӣ/дуруғин (ба amount= низ мегузарад)
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    await state.update_data(payment_method=method)

    await _notify_rozigiho(
        call.bot, call.from_user, "🔥 Free Fire Indonesia", data["label"],
        price, method_name, f"ffid{order_id}"
    )

    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffid_id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(FFIDBuyState.wait_check)


# ==================== ИНТИЗОРИ ЧЕК ====================
@router.message(FFIDBuyState.wait_check, F.photo)
async def ffid_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    _pm = data.get("payment_method")
    if _pm == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
    elif _pm == "eskhata":
        method_name = "🏦 Эсхата"
    else:
        method_name = "💳 Алиф"

    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=data["player_id"],
        nickname=data.get("nickname", ""),
        amount=data["amount"],
        price=data["price"],
        label=data["label"],
        offer_id=data.get("offer_id", ""),
        payment_method=data.get("payment_method", ""),
    )
    await db.set_order_check(order_id, file_id, check_hash)
    # Маркер барои FF Indonesia
    async with db.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET game_id=%s WHERE id=%s",
                               (f"FFID:{data['player_id']}", order_id))

    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )

    nickname = data.get("nickname", "") or "—"
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"🎮 Free Fire Indonesia\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"👤 Ном: <b>{esc(nickname)}</b>\n"
        f"🎁 Маҷсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    username_val = message.from_user.username
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (FFID)", callback_data=f"okffid_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id, file_id, caption=caption,
                reply_markup=admin_kb, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(FFIDBuyState.wait_check)
async def ffid_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ════════════════════════════════════════════════════════
#                   PUBG MOBILE
# ════════════════════════════════════════════════════════

class PUBGBuyState(StatesGroup):
    enter_id       = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


# ==================== ОҒОЗ: ID НАВИШТАН ====================
@router.callback_query(F.data == "buy_pubg")
async def pubg_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="games_menu")]
    ])
    await _safe_edit(
        call,
        "🎮 <b>PUBG Mobile — Харидани UC</b>\n\n"
        "📝 ID аккаунтатонро нависед:\n"
        "Мисол: <code>5123456789</code>",
        kb
    )
    await state.set_state(PUBGBuyState.enter_id)


@router.message(PUBGBuyState.enter_id)
async def pubg_enter_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    if not player_id.isdigit():
        await message.answer("⚠️ ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return

    await state.update_data(player_id=player_id, nickname="")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="pubg_id_ok")],
        [InlineKeyboardButton(text="✏️ ID-ро тағйир медиҳам", callback_data="buy_pubg")],
    ])
    await message.answer(
        f"🎮 <b>PUBG Mobile</b>\n\n"
        f"🆔 ID: <code>{player_id}</code>\n\n"
        f"⚠️ ID-ро бодиққат тафтиш кунед!\n"
        f"Агар дуруст бошад «Давом»-ро пахш кунед:",
        reply_markup=kb, parse_mode="HTML"
    )


# ==================== РӮЙХАТИ МАҲСУЛОТ ====================
@router.callback_query(F.data == "pubg_id_ok")
async def pubg_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_pubg_products()
    if not products:
        await call.answer("❌ Ҳозир маҷсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return

    buttons = []
    for p in products:
        label = p.get("label") or f"💰 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {p['price']:.2f} сом",
            callback_data=f"pubg_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_pubg")])

    await _safe_edit(
        call,
        "💰 <b>UC барои PUBG Mobile</b>\n\nМаҷсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(PUBGBuyState.choose_product)


# ==================== ИНТИХОБИ ТАРИҲИ ПАРДОХТ ====================
@router.callback_query(F.data.startswith("pubg_prod_"), PUBGBuyState.choose_product)
async def pubg_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_pubg_product(product_id)
    if not product:
        await call.answer("❌ Маҷсулот ёфт нашуд!", show_alert=True)
        return

    await state.update_data(
        product_id=product_id,
        amount=product["amount"],
        price=float(product["price"]),
        label=product.get("label") or f"💰 {product['amount']}",
        offer_id=product.get("offer_id") or "",
        eskhata_link=product.get("eskhata_link") or "",
        combo_id=None,
    )

    data = await state.get_data()
    label = data["label"]

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pubg_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pubg_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pubg_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="pubg_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🎮 PUBG Mobile\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҷсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(PUBGBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ (PUBG) ====================
@router.callback_query(F.data.in_({"pubg_pay_dc", "pubg_pay_alif", "pubg_pay_eskhata"}), PUBGBuyState.choose_payment)
async def pubg_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "pubg_pay_dc": "dushanbe_city",
        "pubg_pay_eskhata": "eskhata",
        "pubg_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="pubg_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="pubg_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "pubg_terms_reject", PUBGBuyState.choose_payment)
async def pubg_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    label = data["label"]
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pubg_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pubg_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="pubg_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="pubg_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🎮 PUBG Mobile\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҷсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


# ==================== НИШОН ДОДАНИ РЕКВИЗИТ ====================
@router.callback_query(F.data == "pubg_terms_accept", PUBGBuyState.choose_payment)
async def pubg_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price, disc_pct, disc_amt = data["price"], 0.0, 0.0
    price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(price), 2))
    await state.update_data(price=price)
    order_id = data.get("product_id", 0)
    eskhata_note = ""
    discount_note = winback_note

    method = data.get("pending_payment_method", "alif")
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_pubg{order_id}&f1=133"
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҷсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    await state.update_data(payment_method=method)

    await _notify_rozigiho(
        call.bot, call.from_user, "🎮 PUBG Mobile", data["label"],
        price, method_name, f"pubg{order_id}"
    )

    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="pubg_id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 Маҷсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(PUBGBuyState.wait_check)


# ==================== ИНТИЗОРИ ЧЕК ====================
@router.message(PUBGBuyState.wait_check, F.photo)
async def pubg_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    _pm = data.get("payment_method")
    if _pm == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
    elif _pm == "eskhata":
        method_name = "🏦 Эсхата"
    else:
        method_name = "💳 Алиф"

    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=f"PUBG:{data['player_id']}",
        nickname="",
        amount=data["amount"],
        price=data["price"],
        label=data["label"],
        offer_id=data.get("offer_id", ""),
        payment_method=data.get("payment_method", ""),
    )
    await db.set_order_check(order_id, file_id, check_hash)

    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )

    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"🎮 PUBG Mobile\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҷсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    username_val = message.from_user.username
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (PUBG)", callback_data=f"okpubg_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id, file_id, caption=caption,
                reply_markup=admin_kb, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(PUBGBuyState.wait_check)
async def pubg_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")



# ════════════════════════════════════════════════════════
#              TELEGRAM STARS / PREMIUM
# ════════════════════════════════════════════════════════

class StarsBuyState(StatesGroup):
    enter_username = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


class PremiumBuyState(StatesGroup):
    enter_username = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


# -------------------- STARS --------------------
@router.callback_query(F.data == "buy_stars")
async def stars_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="telegram_menu")]
    ])
    await _safe_edit(
        call,
        "⭐ <b>Telegram Stars</b>\n\n"
        "📝 Username-и Telegram-ро нависед (бе @):\n"
        "Мисол: <code>username</code>\n\n"
        "ℹ️ Метавонед барои худатон ё дигар каси фиристед.",
        kb
    )
    await state.set_state(StarsBuyState.enter_username)


@router.message(StarsBuyState.enter_username)
async def stars_enter_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    if not username or " " in username:
        await message.answer("⚠️ Username нодуруст! Дубора нависед (бе @, бе фосила):")
        return

    await state.update_data(tg_username=username)
    text = (
        f"⭐ <b>Telegram Stars</b>\n\n"
        f"📱 Username: <code>@{username}</code>\n\n"
        f"✅ Агар дуруст бошад «Давом»-ро пахш кунед:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="stars_id_ok")],
        [InlineKeyboardButton(text="✏️ Тағйир медиҳам", callback_data="buy_stars")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "stars_id_ok")
async def stars_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_stars_products()
    if not products:
        await call.answer("❌ Ҳозир маҷсулот нест.", show_alert=True)
        return
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(
            text=f"⭐ {p['amount']} — {p['price']:.2f} сом",
            callback_data=f"stars_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_stars")])
    await _safe_edit(
        call,
        "⭐ <b>Telegram Stars</b>\n\nМиқдорро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(StarsBuyState.choose_product)


@router.callback_query(F.data.startswith("stars_prod_"), StarsBuyState.choose_product)
async def stars_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_stars_product(product_id)
    if not product:
        await call.answer("❌ Маҷсулот ёфт нашуд!", show_alert=True)
        return

    await state.update_data(
        product_id=product_id,
        amount=product["amount"],
        price=float(product["price"]),
        label=f"⭐ {product['amount']} Stars",
        eskhata_link=product.get("eskhata_link") or "",
        combo_id=None,
    )
    data = await state.get_data()

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="stars_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="stars_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="stars_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="stars_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"⭐ Telegram Stars\n"
        f"📱 Username: <code>@{data['tg_username']}</code>\n"
        f"🎁 Миқдор: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(StarsBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ (STARS) ====================
@router.callback_query(F.data.in_({"stars_pay_dc", "stars_pay_alif", "stars_pay_eskhata"}), StarsBuyState.choose_payment)
async def stars_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "stars_pay_dc": "dushanbe_city",
        "stars_pay_eskhata": "eskhata",
        "stars_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="stars_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="stars_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "stars_terms_reject", StarsBuyState.choose_payment)
async def stars_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="stars_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="stars_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="stars_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="stars_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"⭐ Telegram Stars\n"
        f"📱 Username: <code>@{data['tg_username']}</code>\n"
        f"🎁 Миқдор: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


@router.callback_query(F.data == "stars_terms_accept", StarsBuyState.choose_payment)
async def stars_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price, disc_pct, disc_amt = data["price"], 0.0, 0.0
    price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(price), 2))
    await state.update_data(price=price)
    order_id = data.get("product_id", 0)
    eskhata_note = ""
    discount_note = winback_note

    method = data.get("pending_payment_method", "alif")
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_stars{order_id}&f1=133"
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҷсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    await state.update_data(payment_method=method)

    await _notify_rozigiho(
        call.bot, call.from_user, "⭐ Telegram Stars", data["label"],
        price, method_name, f"stars{order_id}"
    )

    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="stars_id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 {data['label']}\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(StarsBuyState.wait_check)


@router.message(StarsBuyState.wait_check, F.photo)
async def stars_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    _pm = data.get("payment_method")
    if _pm == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
    elif _pm == "eskhata":
        method_name = "🏦 Эсхата"
    else:
        method_name = "💳 Алиф"

    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=f"STARS:{data['tg_username']}",
        nickname="",
        amount=data["amount"],
        price=data["price"],
        label=data["label"],
        offer_id="",
        payment_method=data.get("payment_method", ""),
    )
    await db.set_order_check(order_id, file_id, check_hash)

    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )

    username_caller = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username_caller}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"⭐ Telegram Stars\n"
        f"📱 Барои: <code>@{data['tg_username']}</code>\n"
        f"🎁 Миқдор: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    username_val = message.from_user.username
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (Stars)", callback_data=f"okstars_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(admin_id, file_id, caption=caption, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(StarsBuyState.wait_check)
async def stars_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# -------------------- PREMIUM --------------------
@router.callback_query(F.data == "buy_premium")
async def premium_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="telegram_menu")]
    ])
    await _safe_edit(
        call,
        "💎 <b>Telegram Premium</b>\n\n"
        "📝 Username-и Telegram-ро нависед (бе @):\n"
        "Мисол: <code>username</code>\n\n"
        "ℹ️ Метавонед барои худатон ё дигар каси фиристед.",
        kb
    )
    await state.set_state(PremiumBuyState.enter_username)


@router.message(PremiumBuyState.enter_username)
async def premium_enter_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    if not username or " " in username:
        await message.answer("⚠️ Username нодуруст! Дубора нависед (бе @, бе фосила):")
        return

    await state.update_data(tg_username=username)
    text = (
        f"💎 <b>Telegram Premium</b>\n\n"
        f"📱 Username: <code>@{username}</code>\n\n"
        f"✅ Агар дуруст бошад «Давом»-ро пахш кунед:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="premium_id_ok")],
        [InlineKeyboardButton(text="✏️ Тағйир медиҳам", callback_data="buy_premium")],
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "premium_id_ok")
async def premium_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_premium_products()
    if not products:
        await call.answer("❌ Ҳозир маҷсулот нест.", show_alert=True)
        return
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(
            text=f"💎 {p['months']} моҳ — {p['price']:.2f} сом",
            callback_data=f"premium_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_premium")])
    await _safe_edit(
        call,
        "💎 <b>Telegram Premium</b>\n\nМуддатро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(PremiumBuyState.choose_product)


@router.callback_query(F.data.startswith("premium_prod_"), PremiumBuyState.choose_product)
async def premium_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_premium_product(product_id)
    if not product:
        await call.answer("❌ Маҷсулот ёфт нашуд!", show_alert=True)
        return

    await state.update_data(
        product_id=product_id,
        months=product["months"],
        price=float(product["price"]),
        label=f"💎 Premium {product['months']} моҳ",
        eskhata_link=product.get("eskhata_link") or "",
        combo_id=None,
    )
    data = await state.get_data()

    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="premium_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="premium_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="premium_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="premium_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"💎 Telegram Premium\n"
        f"📱 Username: <code>@{data['tg_username']}</code>\n"
        f"🎁 Муддат: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(PremiumBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ (PREMIUM) ====================
@router.callback_query(F.data.in_({"premium_pay_dc", "premium_pay_alif", "premium_pay_eskhata"}), PremiumBuyState.choose_payment)
async def premium_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "premium_pay_dc": "dushanbe_city",
        "premium_pay_eskhata": "eskhata",
        "premium_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="premium_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="premium_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "premium_terms_reject", PremiumBuyState.choose_payment)
async def premium_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="premium_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="premium_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="premium_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="premium_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"💎 Telegram Premium\n"
        f"📱 Username: <code>@{data['tg_username']}</code>\n"
        f"🎁 Муддат: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


@router.callback_query(F.data == "premium_terms_accept", PremiumBuyState.choose_payment)
async def premium_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price, disc_pct, disc_amt = data["price"], 0.0, 0.0
    price, winback_note = await _apply_winback_discount(call.from_user.id, round(float(price), 2))
    await state.update_data(price=price)
    order_id = data.get("product_id", 0)
    eskhata_note = ""
    discount_note = winback_note

    method = data.get("pending_payment_method", "alif")
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_premium{order_id}&f1=133"
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҷсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    await state.update_data(payment_method=method)

    await _notify_rozigiho(
        call.bot, call.from_user, "💎 Telegram Premium", data["label"],
        price, method_name, f"premium{order_id}"
    )

    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="premium_id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 {data['label']}\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(PremiumBuyState.wait_check)


@router.message(PremiumBuyState.wait_check, F.photo)
async def premium_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    _pm = data.get("payment_method")
    if _pm == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
    elif _pm == "eskhata":
        method_name = "🏦 Эсхата"
    else:
        method_name = "💳 Алиф"

    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=f"PREMIUM:{data['tg_username']}",
        nickname="",
        amount=data["months"],
        price=data["price"],
        label=data["label"],
        offer_id="",
        payment_method=data.get("payment_method", ""),
    )
    await db.set_order_check(order_id, file_id, check_hash)

    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )

    username_caller = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username_caller}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"💎 Telegram Premium\n"
        f"📱 Барои: <code>@{data['tg_username']}</code>\n"
        f"🎁 Муддат: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    username_val = message.from_user.username
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (Premium)", callback_data=f"okpremium_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(admin_id, file_id, caption=caption, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(PremiumBuyState.wait_check)
async def premium_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ==================== ПАРДОХТ АЗ БАЛАНСИ РЕФЕРРАЛӢ ====================
@router.callback_query(F.data == "pay_balance")
async def pay_with_balance(call: CallbackQuery, state: FSMContext):
    """
    Умумӣ барои ҲАМАИ хидматҳо (FF, FFID, PUBG, Stars, Premium). Бо
    state.get_state() муайян мекунад кадом хидмат аст, фармоишро месозад,
    аз баланси корбар маблаг кам мекунад ва ба админ бо тугмаи дурусти
    тасдиқ мефиристад (бе чек/расм).
    """
    current = await state.get_state()
    data = await state.get_data()
    price = data.get("price")
    if current is None or price is None:
        await call.answer("❌ State тамом шуд, аз нав сар кунед!", show_alert=True)
        return

    balance = await db.get_referral_balance(call.from_user.id)
    if balance < price:
        await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
        return

    # Аввал маблаГро аз баланс кам мекунем (атомикӣ — танҳо агар кофӣ бошад)
    ok = await db.deduct_referral_balance(call.from_user.id, price)
    if not ok:
        await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
        return

    await state.clear()

    # Муайян кардани хидмат аз номи state
    if current.startswith("FFIDBuyState"):
        game_id = f"FFID:{data['player_id']}"
        confirm_prefix = "okffid"
        service_title = "🔥 Free Fire Indonesia"
        nickname = data.get("nickname", "")
        extra_line = f"🆔 ID: <code>{data['player_id']}</code>\n👤 Ном: <b>{nickname or '—'}</b>\n"
    elif current.startswith("PUBGBuyState"):
        game_id = f"PUBG:{data['player_id']}"
        confirm_prefix = "okpubg"
        service_title = "🎮 PUBG Mobile"
        nickname = ""
        extra_line = f"🆔 ID: <code>{data['player_id']}</code>\n"
    elif current.startswith("StarsBuyState"):
        game_id = f"STARS:{data['tg_username']}"
        confirm_prefix = "okstars"
        service_title = "⭐ Telegram Stars"
        nickname = ""
        extra_line = f"📱 Username: <code>@{data['tg_username']}</code>\n"
    elif current.startswith("PremiumBuyState"):
        game_id = f"PREMIUM:{data['tg_username']}"
        confirm_prefix = "okpremium"
        service_title = "💎 Telegram Premium"
        nickname = ""
        extra_line = f"📱 Username: <code>@{data['tg_username']}</code>\n"
    else:  # BuyState — Free Fire СНГ
        game_id = data["player_id"]
        confirm_prefix = "ok"
        service_title = "🔥 Free Fire"
        nickname = data.get("nickname", "")
        extra_line = f"🆔 ID: <code>{data['player_id']}</code>\n👤 Ном: <b>{nickname or '—'}</b>\n"

    order_id = await db.create_order(
        user_id=call.from_user.id,
        game_id=game_id,
        nickname=nickname,
        amount=data.get("amount", 0),
        price=price,
        label=data["label"],
        offer_id=data.get("offer_id", ""),
        payment_method="referral_balance",
        combo_id=data.get("combo_id"),
    )
    await db.mark_order_paid_with_balance(order_id)
    combo_breakdown = await _combo_breakdown_text(data.get("combo_id"))

    await _safe_edit(
        call,
        f"✅ <b>Пардохт аз баланси реферралӣ қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n"
        f"💰 {price:.2f} сом аз балансатон кам шуд.\n\n"
        f"🔄 Фармоиши шумо ба админ фиристода шуд, натиҷа ба зудӣ маълум мешавад.",
        None
    )

    # Ба ҳамаи админҳо — БЕ расм (чун чек нест)
    username_val = call.from_user.username
    username = f"@{username_val}" if username_val else "—"
    caption = (
        f"💰 <b>Фармоиши нав — пардохт аз баланси реферралӣ!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(call.from_user.full_name)} (<code>{call.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"🎮 {service_title}\n"
        f"{extra_line}"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b> (аз баланси реферралӣ)"
        f"{combo_breakdown}"
    )
    confirm_text = "✅ Тасдиқ — дастӣ иҷро кунед" if data.get("combo_id") else "✅ Тасдиқ — донат кун"
    admin_kb_rows = [
        [InlineKeyboardButton(text=confirm_text, callback_data=f"{confirm_prefix}_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан",          callback_data=f"no_{order_id}")],
    ]
    if username_val:
        admin_kb_rows.append(
            [InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{username_val}")]
        )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await call.bot.send_message(admin_id, caption, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


# ════════════════════════════════════════════════════════
#         ПУРКУНИИ БАЛАНС (ҳозира танҳо барои админ)
# ════════════════════════════════════════════════════════
class TopupState(StatesGroup):
    enter_amount  = State()  # интизори маблағ
    choose_method = State()  # интизори интихоби тариқи пардохт
    wait_check    = State()  # интизори расми чек


@router.callback_query(F.data == "topup_balance")
async def topup_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in config.ADMIN_IDS:
        await call.answer("⚠️ Ин функсия ҳоло дар марҳилаи озмоишист.", show_alert=True)
        return
    await state.clear()
    max_amount = await db.get_max_balance_topup()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бекор", callback_data="profile_menu")]
    ])
    await _safe_edit(
        call,
        f"💰 <b>Пур кардани баланс</b>\n\n"
        f"Маблағеро, ки мехоҳед ба баланс илова кунед, нависед (сомонӣ).\n"
        f"Ҳадди максималӣ: <b>{max_amount:.2f} сомонӣ</b>",
        kb
    )
    await state.set_state(TopupState.enter_amount)


@router.message(TopupState.enter_amount)
async def topup_enter_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".")
    try:
        amount = round(float(text), 2)
    except ValueError:
        await message.answer("⚠️ Лутфан рақами дуруст нависед (масалан: 50).")
        return
    if amount <= 0:
        await message.answer("⚠️ Маблағ бояд аз сифр зиёд бошад.")
        return
    max_amount = await db.get_max_balance_topup()
    if amount > max_amount:
        await message.answer(
            f"⚠️ Маблағи максималӣ барои як пуркунӣ: <b>{max_amount:.2f} сомонӣ</b>.\n"
            f"Лутфан маблағи камтар нависед.",
            parse_mode="HTML"
        )
        return

    await state.update_data(topup_amount=amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="topup_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="topup_pay_alif")],
        [InlineKeyboardButton(text="🔙 Бекор",          callback_data="profile_menu")],
    ])
    await message.answer(
        f"💰 <b>Пур кардани баланс</b>\n\n"
        f"💵 Маблағ: <b>{amount:.2f} сомонӣ</b>\n\n"
        f"Тариқи пардохтро интихоб кунед:",
        reply_markup=kb, parse_mode="HTML"
    )
    await state.set_state(TopupState.choose_method)


@router.callback_query(F.data.in_({"topup_pay_dc", "topup_pay_alif"}), TopupState.choose_method)
async def topup_choose_method(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("topup_amount")
    if not amount:
        await call.answer("⚠️ Хатогӣ — аз нав кӯшиш кунед.", show_alert=True)
        await state.clear()
        return

    method = "dushanbe_city" if call.data == "topup_pay_dc" else "alif"
    price = await _unique_autopay_price(amount)

    awaiting_order_id = await db.create_awaiting_order(
        user_id=call.from_user.id,
        game_id="",
        nickname="",
        amount=0,
        price=price,
        label="💰 Пуркунии баланс",
        offer_id="",
        payment_method=method,
        is_balance_topup=1,
    )
    await state.update_data(autopay_order_id=awaiting_order_id)

    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = f"http://pay.expresspay.tj/?A={dc_card}&s={price:g}&c=card_{awaiting_order_id}&f1=133"
    else:
        method_name = "💳 Алиф"
        pay_url = f"https://alifmobi.page.link/providers?id=124&amount={price:.2f}&account=929998174"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пардохт", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="profile_menu")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"💰 Пуркунии баланс\n"
        f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n"
        f"🆔 Фармоиш: #{awaiting_order_id}\n\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи <b>дақиқ {price:.2f} сом</b>-ро пардохт кунед "
        f"(на кам, на зиёд — тин ба тин!)\n"
        f"3️⃣ Расми чекро ба ҳамин чат фиристед\n\n"
        f"⚡ Пас аз фиристодани чек, системаи мо пардохти шуморо "
        f"<b>худкор</b> тафтиш мекунад ва баланс худкор пур мешавад — "
        f"интизории админ лозим нест!\n\n"
        f"⏳ Шумо <b>15 дақиқа</b> вақт доред.",
        kb
    )
    await state.set_state(TopupState.wait_check)


@router.message(TopupState.wait_check, F.photo)
async def topup_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    autopay_order_id = data.get("autopay_order_id")
    if not autopay_order_id:
        await message.answer("⚠️ Хатогӣ — аз нав кӯшиш кунед.")
        return

    file_id = message.photo[-1].file_id
    import autopay
    order = await db.get_order(autopay_order_id)
    if not order or order["status"] not in ("awaiting_autopay", "expired"):
        await message.answer(
            "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
            f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
        return
    if not await db.set_autopay_check(autopay_order_id, file_id):
        await message.answer(
            "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
            f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
        return
    await message.answer(
        f"✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{autopay_order_id}\n\n"
        f"🔍 Системаи мо ҳоло пардохти шуморо <b>худкор</b> ҷустуҷӯ "
        f"мекунад — одатан 5-30 сония мегирад.\n"
        f"Натиҷа ҳозир хабар дода мешавад...",
        parse_mode="HTML"
    )
    kod = await db.find_kod_for_order(autopay_order_id) \
        or await db.find_unmatched_kod(float(order["price"]), autopay.MAX_AGE_MINUTES)
    if kod:
        order = await db.get_order(autopay_order_id)
        asyncio.create_task(autopay.run_donate(message.bot, order, kod))


@router.message(TopupState.wait_check)
async def topup_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")
