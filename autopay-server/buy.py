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
import math
import random
import re
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
import pemoji
import ff_api

TJ_TZ = ZoneInfo("Asia/Dushanbe")

# Қоидаи воқеии Telegram username: танҳо ҳарф/рақам/зерхат, 5-32 аломат
_TG_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


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


async def _balance_pay_cart(call: CallbackQuery, uid: int, data: dict,
                            cart_items: list, total: float, bal_after: float = None):
    """
    Сабад аз баланс пардохта шуд — барои ҳар дона фармоиши алоҳида бо як
    group_id месозад ва ба админ бо тугмаҳои гурӯҳӣ мефиристад.

    Маблағ АЛЛАКАЙ аз баланс кам шудааст. Агар сохтани фармоиш нашавад,
    пул ХУДКОР барнамегардад (қоидаи соҳиб) — ба ҷои он админ огоҳ
    мешавад, то дастӣ ҳал кунад.
    """
    group_id = str(uuid.uuid4())
    order_ids = []
    try:
        for item in cart_items:
            oid = await db.create_order(
                user_id=uid,
                game_id=data["player_id"],
                nickname=data.get("nickname", ""),
                amount=item["amount"],
                price=item["price"],
                label=item["label"],
                offer_id=item["offer_id"],
                payment_method="referral_balance",
                order_group_id=group_id,
            )
            order_ids.append(oid)
    except Exception as e:
        logger.error(f"Сабад аз баланс сохта нашуд ({uid}): {e}")
        for admin_id in config.ADMIN_IDS:
            try:
                await call.bot.send_message(
                    admin_id,
                    f"⚠️ <b>Пул аз баланс кам шуд, вале фармоиш сохта НАШУД!</b>\n\n"
                    f"🆔 Корбар: <code>{uid}</code>\n"
                    f"💵 Кам шуд: <b>{total:.2f} сом</b>\n"
                    f"🆔 Сохта шуд: {', '.join(f'#{i}' for i in order_ids) or '—'}\n"
                    f"Хато: {esc(str(e))[:200]}\n\nДастӣ ҳал кунед.",
                    parse_mode="HTML")
            except Exception:
                pass
        await call.message.answer(
            "⚠️ Мушкили техникӣ шуд. Админ хабардор аст ва зуд ҳал мекунад 🙏")
        return

    # Баланси дақиқи лаҳзаи харид (аз транзаксияи кам кардан) — на хониши
    # алоҳида, ки фармоиши ҳамзамон онро тағйир дода метавонад
    new_balance = bal_after if bal_after is not None else await db.get_referral_balance(uid)
    ids_text = ", ".join(f"#{i}" for i in order_ids)
    items_text = "\n".join(f"  • {esc(i['label'])} — {float(i['price']):.2f} сом"
                           for i in cart_items)
    await call.message.answer(
        f"✅ <b>Пардохт аз баланс қабул шуд!</b>\n\n"
        f"🆔 Фармоишҳо: {ids_text}\n"
        f"{items_text}\n\n"
        f"💵 Кам шуд: <b>{total:.2f} сом</b>\n"
        f"💰 Баланси боқимонда: <b>{new_balance:.2f} сом</b>\n\n"
        f"🔄 Фармоишҳо ба зудӣ иҷро мешаванд. 🙏",
        parse_mode="HTML")

    # Сабади FF СНГ аз баланс — донати ХУДКОР (бе тасдиқи дастии админ).
    # Ҳамаи донаҳо худкор донат мешаванд ва як паёми ҷамъбастӣ меояд.
    # (Комбо/дастӣ ба ин ҷо намерасанд — buy.py онҳоро филтр мекунад.)
    import autopay
    orders = [await db.get_order(oid) for oid in order_ids]
    orders = [o for o in orders if o]
    asyncio.create_task(autopay.run_donate_group_from_balance(call.bot, orders))


async def _offer_game(message: Message, note: str = ""):
    """
    Баъди қабули чек ба мизоҷ бозӣ пешниҳод мекунад — интизорӣ то 10-15
    дақиқа мешавад ва дар он муддат мизоҷ асабӣ мешавад.

    ПАЁМИ АЛОҲИДА мефиристад ва тамоми бозӣ дар ҳамон як паём мегузарад
    (games.py онро нав мекунад). Бо ин, паёми «Тасдиқ шуд» дар байни
    ҳаракатҳои бозӣ гум намешавад.

    Агар чизе нашавад — хариди мизоҷ НАБОЯД халал ёбад, пас хато танҳо
    ба лог меравад.
    """
    try:
        import games
        games.start_state(message.from_user.id, note)
        await message.answer(games.menu_text(note),
                             reply_markup=games.menu_kb(), parse_mode="HTML")
    except Exception as e:
        logger.info(f"Бозӣ пешниҳод нашуд: {e}")


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


# ЭЗОҲ БАРОИ ОЯНДА — чаро ин ҷо «муқоисаи намуди расм» нест:
# Кӯшиш карда шуд, ки ғайр аз sha256 боз «нақши намуди» расм (dHash)
# ҳисоб шавад, то чеки АЗ НАВ СКРИНШОТШУДА ҳам гирифта шавад. Санҷиш
# нишон дод, ки ин барои чеки бонкӣ КОР НАМЕКУНАД: чекҳои Алиф/Эсхата
# ҳама як тарҳ доранд ва маблағ дар расм ҷои хеле хурдро мегирад. Дар
# санҷиш ду чеки ВОҚЕАН ГУНОГУН (маблағу санаи дигар) фарқи 1–34 бит
# доданд, дар ҳоле ки ҲАМОН чеки аз нав скриншотшуда 35–50 бит фарқ
# кард — яъне доираҳо ба ҳам мерасанд ва ҳадде нест, ки онҳоро ҷудо
# кунад. Гузоштани он мизоҷони ҲАЛОЛро мебаст. Барои ҳамин танҳо
# sha256 (муқоисаи дақиқи файл) кор мекунад — он хатои бардурӯғ надорад.
async def _block_if_duplicate_check(message: Message, check_hash: str):
    """
    Агар ҳамин расми чек аллакай ба ягон фармоиши дигар пайваст бошад —
    ба мизоҷ хабар медиҳад, ба соҳиб огоҳӣ мефиристад ва фармоиши кӯҳнаро
    бармегардонад (даъваткунанда бояд return кунад, то донати такрорӣ
    сохта нашавад).
    """
    if not check_hash:
        return None
    dups = await db.find_check_reuse(check_hash)
    if not dups:
        return None
    first = dups[0]
    uid = message.from_user.id
    other_user = first["user_id"] != uid
    done = first.get("status") == "confirmed"

    if other_user:
        # Ҳолати аз ҳама хатарнок: ҳамон чек ба ҳисоби ДИГАР истифода шуда.
        # Ба мизоҷ сабаби аниқро намегӯем (то маълумоти каси дигар ошкор
        # нашавад), вале соҳиб ҳама чизро мебинад.
        body = (
            "Ин расм аллакай дар система ҳаст ва ба фармоиши дигар "
            "пайваст шудааст.\n\nАгар шумо воқеан пардохти НАВ карда бошед, "
            "лутфан скриншоти ҳамон пардохти навро фиристед."
        )
    elif done:
        body = (
            f"Ҳамин расм барои фармоиши #{first['id']} ({esc(first['label'])}) "
            f"аллакай қабул ва иҷро шудааст.\n\nАгар ин пардохти ДИГАР бошад, "
            f"лутфан скриншоти ҳамон пардохтро фиристед."
        )
    else:
        body = (
            f"Ҳамин расм аллакай барои фармоиши #{first['id']} "
            f"({esc(first['label'])}) қабул шудааст ва ҳозир дар кор аст.\n\n"
            f"Лутфан каме сабр кунед — натиҷаро худам менависам."
        )
    await message.answer(
        f"⚠️ <b>Ин чек аллакай истифода шудааст</b>\n\n{body}\n\n"
        f"Савол доред? {config.SUPPORT_USERNAME}",
        parse_mode="HTML"
    )

    # ---- Огоҳии соҳиб ----
    try:
        u = await db.get_user(uid)
        uname = f"@{u['username']}" if u and u.get("username") else "—"
        full = esc(u.get("full_name")) if u and u.get("full_name") else "—"
        lines = "\n".join(
            f"   • #{d['id']} — {esc(d['label'])}, {float(d['price']):.2f} сом, "
            f"{d['status']}, корбар <code>{d['user_id']}</code>"
            for d in dups[:5]
        )
        head = ("🚨 <b>ЧЕКИ ТАКРОРӢ — АЗ ҲИСОБИ ДИГАР!</b>"
                if other_user else "⚠️ <b>Чеки такрорӣ фиристода шуд</b>")
        warn = ("\n\n❗️ Ҳамин расм пештар аз ҳисоби ДИГАР омада буд — "
                "эҳтимоли кӯшиши фиреб." if other_user else "")
        text = (
            f"{head}\n\n"
            f"👤 Фиристанда: {full} ({esc(uname)})\n"
            f"🆔 ID: <code>{uid}</code>\n\n"
            f"Ҳамин расм аллакай ба ин фармоиш(ҳо) пайваст аст:\n{lines}{warn}\n\n"
            f"🛑 Фармоиши нав сохта НАШУД."
        )
        for admin_id in config.ADMIN_IDS:
            try:
                await message.bot.send_photo(
                    admin_id, message.photo[-1].file_id,
                    caption=text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Огоҳии чеки такрорӣ ба админ {admin_id} нарасид: {e}")
    except Exception as e:
        logger.error(f"Огоҳии чеки такрорӣ сохта нашуд: {e}")
    return first


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
    has_custom = False
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        tag = "🔥 Маъмултарин — " if p.get("is_featured") else ""
        # Нархи ШАХСИИ мизоҷ (агар бошад) — ҳамон нархе, ки саҳифаи
        # маҳсулот ва сабад истифода мебаранд. Пештар ин ҷо нархи УМУМӢ
        # нишон дода мешуд: мизоҷи VIP дар рӯйхат як нарх, дар сабад
        # нархи дигар медид ва ҳисоб гӯё хато менамуд.
        custom_price = await db.get_custom_price(call.from_user.id, p["id"])
        price = custom_price if custom_price is not None else float(p["price"])
        if custom_price is not None:
            has_custom = True
            tag = "💎 " + tag
        buttons.append([InlineKeyboardButton(
            text=f"{tag}{label} — {price:.2f} сом",
            callback_data=f"prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🛒 Якчанд маҳсулот интихоб кардан", callback_data="cart_start")])
    buttons.append([InlineKeyboardButton(text="🎁 Комбоҳо", callback_data="combo_list")])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy")])

    await _safe_edit(
        call,
        "💎 <b>Алмазҳои Free Fire</b>\n\nМаҳсулотро интихоб кунед:"
        + ("\n\n💎 <b>Нархи шахсии шумо</b> — маҳсулоти нишонадор бо "
           "нархи махсуси шумо аст." if has_custom else ""),
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
        # Эсхата барои комбо нест — комбо линки ягонаи пардохт надорад
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="combo_list")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    warning = await _combo_levelup_warning(combo_id)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Комбо: <b>{combo['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n"
        f"{warning}\n"
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
        product_id=None,
        eskhata_link="",
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    is_combo = bool(data.get("combo_id"))
    product_word = "Маҳсулотҳо" if is_cart else "Маҳсулот"
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="pay_alif")],
    ]
    if not is_combo:
        kb_rows.append([InlineKeyboardButton(text="🏦 Эсхата", callback_data="pay_eskhata")])
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
        )])
    back_cb = "combo_list" if is_combo else ("cart_start" if is_cart else "id_ok")
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data=back_cb)])
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


_LEVELUP_KEYWORDS = ("прокачка", "пропуск", "level", "левел", "лвл", "проп")


async def _combo_levelup_warning(combo_id: int | None) -> str:
    """Агар комбо «Пропуск прокачка» (Level-Up Pass) дошта бошад, як огоҳии
    ТАРСНОК бармегардонад — чунки прокачка дар ҳар аккаунт танҳо ЯК бор
    зада мешавад ва агар такрор харанд, пулашон месӯзад."""
    if not combo_id:
        return ""
    try:
        items = await db.get_combo_items(combo_id)
    except Exception:
        return ""
    found = False
    for it in items or []:
        label = (it.get("custom_label") or it.get("product_label") or "").lower()
        if any(k in label for k in _LEVELUP_KEYWORDS):
            found = True
            break
    if not found:
        return ""
    return (
        "\n\n⚠️❗️ <b>ДИҚҚАТИ ҶИДДӢ — Пропуск прокачка!</b>\n"
        "🔴 Пропуск прокачка дар ҳар аккаунт ФАҚАТ <b>ЯК БОР</b> зада мешавад!\n\n"
        "Агар аккаунти шумо <b>аллакай прокачка шуда бошад</b> ва боз харед — "
        "<b>ПУЛАТОН МЕСӮЗАД</b> ва баргардонида НАМЕШАВАД!\n\n"
        "✅ Пеш аз харид ҲАТМАН боварӣ ҳосил кунед, ки аккаунтатон ҳанӯз "
        "прокачка нашудааст.\n"
    )


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


def _rescale_cart_items(items: list, new_total: float) -> list:
    """
    Нархи ҳар донаи сабадро мутаносибан ба ҷамъи НАВ мутобиқ мекунад.

    Чаро лозим: тахфиф ва «сентҳои нодир» ба ҶАМЪИ УМУМӢ татбиқ мешаванд,
    вале фармоишҳо аз рӯи нархи ҲАР ДОНА сохта мешаванд. Бе ин мутобиқат
    ҷамъи фармоишҳо аз маблағи воқеан пардохтшуда фарқ мекард — ҳисобот
    даромади бештар нишон медод ва мукофоти реферал аз маблағи калонтар
    ҳисоб мешуд.

    Донаи охирин боқимондаро мегирад, то ҷамъ АЙНАН баробар шавад (агар
    ҳар донаро алоҳида гирд кунем, як-ду тин фарқ мемонад).
    """
    if not items:
        return items
    old_total = round(sum(float(i["price"]) for i in items), 2)
    new_total = round(float(new_total), 2)
    if old_total <= 0 or abs(old_total - new_total) < 0.005:
        return items
    out, acc = [], 0.0
    for i in items[:-1]:
        p = round(float(i["price"]) * new_total / old_total, 2)
        acc = round(acc + p, 2)
        out.append({**i, "price": p})
    out.append({**items[-1], "price": round(new_total - acc, 2)})
    return out


async def _dc_pay_url(dc_card: str, price: float, comment: str) -> str:
    """Линки пардохти Душанбе Сити (ExpressPay)-ро месозад.

    Асоси линк (домен) дар settings нигоҳ дошта мешавад — то агар домен боз
    иваз шавад, соҳиб онро БЕ ДЕПЛОЙ, аз панели админ иваз карда тавонад.
    Домени нав (тасдиқшуда): https://pay.dc.tj/ бо ҳарфи ХУРДИ a=."""
    base = await db.get_setting("dc_pay_base") or "https://pay.dc.tj/"
    if not base.endswith("/"):
        base += "/"
    # Диққат: параметр ҳарфи ХУРД a= аст (на A=) — сервери pay.dc.tj ҳаминро мехоҳад
    real_url = f"{base}?a={dc_card}&s={price:g}&c={comment}&f1=133"

    # Силкаи ноаён: агар домени редирект (масалан pay.wineclo.com) танзим
    # шуда бошад, ба ҷои линки воқеӣ як токени кӯтоҳ бармегардонем — мизоҷ
    # корт ва pay.dc.tj-ро намебинад. Агар танзим НАШУДА бошад ё хато диҳад,
    # линки воқеиро бармегардонем — пас пардохт ҳеҷ гоҳ вайрон намешавад.
    mask = await db.get_setting("dc_mask_base")
    if mask:
        try:
            token = await db.create_pay_token(real_url)
            m = mask if mask.endswith("/") else mask + "/"
            return f"{m}{token}"
        except Exception as e:
            logger.error(f"_dc_pay_url: токени ноаён нашуд, линки оддӣ: {e}")
    return real_url


# Силкаи тугмаи «Кушодани Алиф». Пештар силка ба пардохти ПРОВАЙДЕР бо
# рақами ТЕЛЕФОН мебурд (account=929998174) — мизоҷон иштибоҳ карда пулро
# ба ҷои дигар мефиристоданд. Акнун тугма ФАҚАТ барномаи Алифро мекушояд,
# ва мизоҷ худаш ба «На карту» рақами корт ва маблағро мезанад.
# Домени силка дар settings — то соҳиб онро БЕ ДЕПЛОЙ иваз карда тавонад.
DEFAULT_ALIF_PAY_URL = "https://alifmobi.page.link/"


async def _alif_pay_url() -> str:
    return (await db.get_setting("alif_pay_url")) or DEFAULT_ALIF_PAY_URL


async def _pay_reqs(method: str, price: float, dc_comment: str):
    """Барои DC/Alif: (силкаи пардохт, матни тугма, қадамҳо)-ро бармегардонад.
    DC → линки тайёри пардохт; Алиф → кушодани барнома + корт бо дасти мизоҷ."""
    card = await db.get_dc_card_number()
    if method == "dushanbe_city":
        pay_url = await _dc_pay_url(card, price, dc_comment)
        steps = (
            f"1️⃣ Тугмаи «💳 Пардохт»-ро пахш кунед\n"
            f"2️⃣ Маблағи <b>дақиқ {price:.2f} сом</b>-ро пардохт кунед "
            f"(на кам, на зиёд — тин ба тин!)\n"
            f"3️⃣ Расми чекро ба ҳамин чат фиристед\n\n"
        )
        return pay_url, "💳 Пардохт", steps
    # Алиф — кушодани барнома, мизоҷ худаш корт ва маблағро мезанад
    pay_url = await _alif_pay_url()
    steps = (
        f"1️⃣ Тугмаи «📲 Кушодани Алиф»-ро пахш кунед\n"
        f"2️⃣ Дар Алиф: «<b>На карту</b>»-ро интихоб кунед\n"
        f"3️⃣ Рақами кортро гузоред (пахш кунед — нусха мешавад):\n"
        f"<code>{card}</code>\n"
        f"4️⃣ Маблағи <b>дақиқ {price:.2f} сом</b>-ро занед "
        f"(на кам, на зиёд — тин ба тин!)\n"
        f"5️⃣ Пардохт кунед ва расми чекро ба ҳамин чат фиристед\n\n"
    )
    return pay_url, "📲 Кушодани Алиф", steps


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


# ==================== АВТОПАРДОХТИ МУШТАРАК ====================
# FF СНГ автопардохти пурра дошт (пардохт аз огоҳии банк худкор ёфта,
# худкор донат мешавад). Ин ду функсия ҳамон флоуро ба FFID, FFBR, PUBG,
# Stars, Premium низ медиҳанд — то онҳо ҳам бо DC/Alif ХУДКОР тасдиқ ва
# донат шаванд, на бо тасдиқи дастии админ.
async def _autopay_requisites(call: CallbackQuery, state: FSMContext, data: dict,
                              method: str, game_id_marker: str, amount,
                              title: str, back_cb: str):
    """Фармоиши 'awaiting_autopay' месозад ва экрани пардохтро нишон медиҳад.
    Танҳо барои DC/Alif даъват мешавад."""
    base_price, _winback = await _apply_winback_discount(
        call.from_user.id, round(float(data["price"]), 2))
    price = await _unique_autopay_price(base_price)
    await state.update_data(price=price, payment_method=method)

    method_name = "🏙 Душанбе Сити" if method == "dushanbe_city" else "💳 Алиф"
    await _notify_rozigiho(
        call.bot, call.from_user, title, data["label"],
        price, method_name, str(data.get("product_id", ""))
    )

    awaiting_order_id = await db.create_awaiting_order(
        user_id=call.from_user.id,
        game_id=game_id_marker,
        nickname=data.get("nickname", ""),
        amount=amount,
        price=price,
        label=data["label"],
        offer_id=data.get("offer_id", ""),
        payment_method=method,
    )
    await state.update_data(autopay_order_id=awaiting_order_id)

    pay_url, pay_btn, steps = await _pay_reqs(method, price, f"card_{awaiting_order_id}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pay_btn, url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data=back_cb)],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n"
        f"🆔 Фармоиш: #{awaiting_order_id}\n\n"
        f"{steps}"
        f"⚡ Пас аз фиристодани чек, пардохти шумо <b>худкор</b> тафтиш "
        f"мешавад ва маҳсулот худкор фиристода мешавад — интизории админ "
        f"лозим нест!\n\n"
        f"⏳ Шумо <b>20 дақиқа</b> вақт доред.",
        kb
    )


async def _autopay_receive_check(message: Message, data: dict) -> bool:
    """Агар фармоиши автопардохт бошад, чекро коркард мекунад ва пардохтро
    ҷустуҷӯ мекунад. True = коркард шуд (ба флоуи дастӣ гузаштан лозим нест).
    Барои ҳамаи маҳсулот муштарак — донат аз рӯи game_id-и худи фармоиш
    (FFID:/FFBR:/PUBG:/STARS:/PREMIUM:) ба хизмати дуруст мераванд."""
    autopay_order_id = data.get("autopay_order_id")
    if not autopay_order_id:
        return False
    import autopay
    order = await db.get_order(autopay_order_id)
    if not order or order["status"] not in ("awaiting_autopay", "expired"):
        await message.answer(
            "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
            f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
        return True
    file_id = message.photo[-1].file_id
    autopay_hash = await _hash_photo(message)
    # Чеки такрорӣ: агар ҳамин расм аллакай ба фармоиши ФАЪОЛИ дигар (ё аз
    # ҳисоби дигар) пайваст бошад — манъ. Пеш ин танҳо дар роҳи дастӣ буд;
    # дар автопардохт кушода монда буд ва мизоҷ метавонист чеки кӯҳнаро
    # фиристад, пул нафиристад ва баъди эскалатсия донати ройгон гирад.
    if await _block_if_duplicate_check(message, autopay_hash):
        return True
    if not await db.set_autopay_check(autopay_order_id, file_id, autopay_hash or None):
        await message.answer(
            "⚠️ Ин фармоиш дигар фаъол нест (эҳтимол аллакай коркард шудааст).\n"
            f"Агар пардохт карда бошед: {config.SUPPORT_USERNAME}",
            parse_mode="HTML"
        )
        return True
    await message.answer(
        f"✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{autopay_order_id}\n\n"
        f"🔍 Системаи мо ҳоло пардохти шуморо <b>худкор</b> ҷустуҷӯ "
        f"мекунад — одатан 5-30 сония мегирад.\n"
        f"Натиҷа ҳозир хабар дода мешавад...",
        parse_mode="HTML"
    )
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")
    kod = await db.find_kod_for_order(autopay_order_id) \
        or await db.claim_unmatched_kod(float(order["price"]), autopay_order_id, autopay.MAX_AGE_MINUTES)
    if kod:
        order = await db.get_order(autopay_order_id)
        asyncio.create_task(autopay.run_donate(message.bot, order, kod))
    return True


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
    # Сабад: нарх дар боло иваз шуда метавонад (тахфиф, сентҳои нодир) —
    # нархи ҳар донаро ҳам мутобиқ мекунем, вагарна ҷамъи фармоишҳо аз
    # маблағи воқеан пардохтшуда фарқ мекунад
    if is_cart and data.get("cart_items"):
        scaled = _rescale_cart_items(data["cart_items"], price)
        if scaled is not data["cart_items"]:
            await state.update_data(cart_items=scaled)
            data["cart_items"] = scaled

    order_id = data.get("product_id") or "cart" + str(uuid.uuid4())[:8]
    eskhata_note = ""
    alif_note = ""
    discount_note = winback_note

    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        dc_card = await db.get_dc_card_number()
        pay_url = await _dc_pay_url(dc_card, price, f"card_{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        pay_url = await _alif_pay_url()
        _alif_card = await db.get_dc_card_number()
        alif_note = (
            f"\n📲 Дар Алиф «<b>На карту</b>» → рақами корт "
            f"(пахш кунед — нусха мешавад):\n<code>{_alif_card}</code>\n"
        )

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
        # DC → линки пардохт бо коменти card_<id> (аз notification меёбем).
        # Алиф → кушодани барнома, мизоҷ худаш ба «На карту» корт+маблағ мезанад
        # (шинохт аз рӯи маблағи нодир).
        pay_url, pay_btn, steps = await _pay_reqs(method, price, f"card_{awaiting_order_id}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=pay_btn, url=pay_url)],
            [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")],
        ])
        await _safe_edit(
            call,
            f"💳 <b>{method_name}</b>\n\n"
            f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
            f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n"
            f"🆔 Фармоиш: #{awaiting_order_id}\n\n"
            f"{steps}"
            f"⚡ Пас аз фиристодани чек, системаи мо пардохти шуморо "
            f"<b>худкор</b> тафтиш мекунад ва алмазҳо худкор фиристода "
            f"мешаванд — интизории админ лозим нест!\n\n"
            f"⏳ Шумо <b>20 дақиқа</b> вақт доред.",
            kb
        )
        await state.set_state(BuyState.wait_check)
        return

    # ==== Тартиби кӯҳна (Алиф / Эсхата / сабад) — бо чек ====
    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    _btn_pay_text = "📲 Кушодани Алиф" if method == "alif" else f"💳 Пардохти {btn_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_btn_pay_text, url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{discount_note}"
        f"{eskhata_note}"
        f"{alif_note}\n"
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
        # Изи ангушти чекро низ сабт мекунем — то абзори админии
        # «Ҷустуҷӯи чек» ин фармоишро баъдан ёфта тавонад
        autopay_hash = await _hash_photo(message)
        # Чеки такрорӣ — манъ (мисли _autopay_receive_check)
        if await _block_if_duplicate_check(message, autopay_hash):
            return
        if not await db.set_autopay_check(autopay_order_id, file_id, autopay_hash or None):
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
        await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")
        # Шояд пардохт аллакай ПЕШ аз чек омада бошад — тафтиш мекунем:
        # аввал Kod-и ба ҳамин фармоиш резервшуда (аз коменти card_XXXX),
        # баъд ҳамчун эҳтиёт — аз рӯи маблағ
        kod = await db.find_kod_for_order(autopay_order_id) \
            or await db.claim_unmatched_kod(float(order["price"]), autopay_order_id, autopay.MAX_AGE_MINUTES)
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
        await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
    text = pemoji.premiumize(text)
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
    text = pemoji.premiumize(text)
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
        await call.answer("❌ Ҳозир маҳсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"FFID:{data['player_id']}",
            amount=data.get("amount", 0),
            title="🔥 Free Fire Indonesia", back_cb="ffid_id_ok")
        await state.set_state(FFIDBuyState.wait_check)
        return
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
        pay_url = await _dc_pay_url(dc_card, price, f"card_ffid{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        # Нархи каме нодир — зидди чеки такрорӣ/дуруғин (ба amount= низ мегузарад)
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = await _alif_pay_url()  # шохаи мурда — Алиф аз _autopay_requisites меравад

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

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
        await call.answer("❌ Ҳозир маҳсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"PUBG:{data['player_id']}",
            amount=data.get("amount", 0),
            title="🎮 PUBG Mobile", back_cb="pubg_id_ok")
        await state.set_state(PUBGBuyState.wait_check)
        return
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
        pay_url = await _dc_pay_url(dc_card, price, f"card_pubg{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = await _alif_pay_url()  # шохаи мурда — Алиф аз _autopay_requisites меравад

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

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
    if not _TG_USERNAME_RE.match(username):
        await message.answer(
            "⚠️ Username нодуруст! Username-и Telegram бояд танҳо аз ҳарф, рақам ва "
            "зерхат (_) иборат бошад, 5-32 аломат. Дубора нависед (бе @, бе фосила):"
        )
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
        await call.answer("❌ Ҳозир маҳсулот нест.", show_alert=True)
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"STARS:{data['tg_username']}",
            amount=data["amount"],
            title="⭐ Telegram Stars", back_cb="stars_id_ok")
        await state.set_state(StarsBuyState.wait_check)
        return
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
        pay_url = await _dc_pay_url(dc_card, price, f"card_stars{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = await _alif_pay_url()  # шохаи мурда — Алиф аз _autopay_requisites меравад

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

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
    if not _TG_USERNAME_RE.match(username):
        await message.answer(
            "⚠️ Username нодуруст! Username-и Telegram бояд танҳо аз ҳарф, рақам ва "
            "зерхат (_) иборат бошад, 5-32 аломат. Дубора нависед (бе @, бе фосила):"
        )
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
        await call.answer("❌ Ҳозир маҳсулот нест.", show_alert=True)
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
        amount=product["months"],
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
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
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"PREMIUM:{data['tg_username']}",
            amount=data["months"],
            title="💎 Telegram Premium", back_cb="premium_id_ok")
        await state.set_state(PremiumBuyState.wait_check)
        return
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
        pay_url = await _dc_pay_url(dc_card, price, f"card_premium{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = await _alif_pay_url()  # шохаи мурда — Алиф аз _autopay_requisites меравад

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

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

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
# Муҳофизат аз ду бор пахши тези "Истифода аз баланс" (то ду фармоиши
# такрорӣ ва ду донати воқеӣ насозад) — калид: user_id
_balance_pay_in_flight: set = set()


@router.callback_query(F.data == "pay_balance")
async def pay_with_balance(call: CallbackQuery, state: FSMContext):
    """
    Умумӣ барои ҲАМАИ хидматҳо (FF, FFID, PUBG, Stars, Premium). Бо
    state.get_state() муайян мекунад кадом хидмат аст, фармоишро месозад,
    аз баланси корбар маблаг кам мекунад ва ба админ бо тугмаи дурусти
    тасдиқ мефиристад (бе чек/расм).
    """
    uid = call.from_user.id
    # Санҷиш + банд кардан БЕ ягон await дар байн — то ду пахши ҳамзамон
    # ҳарду аз санҷиш нагузаранд (як-риштагии asyncio инро атомикӣ мекунад)
    if uid in _balance_pay_in_flight:
        await call.answer("⏳ Фармоиши қаблиатон дар кор аст — лутфан як лаҳза интизор шавед.", show_alert=True)
        return
    _balance_pay_in_flight.add(uid)
    try:
        current = await state.get_state()
        data = await state.get_data()
        price = data.get("price")
        if current is None or price is None:
            await call.answer("❌ State тамом шуд, аз нав сар кунед!", show_alert=True)
            return

        balance = await db.get_referral_balance(uid)
        if balance < price:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return

        # Аввал маблаГро аз баланс кам мекунем (атомикӣ — танҳо агар кофӣ бошад)
        bal_after = await db.deduct_referral_balance(uid, price)
        if bal_after is None:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return

        # ---- САБАД: барои ҳар маҳсулот фармоиши АЛОҲИДА ----
        # Бе ин шоха, аз баланс маблағи ПУРРА (масалан 4700) кам мешуд,
        # вале ЯК фармоиш сохта мешуд — бо `amount`-и маҳсулоти пештар
        # дидашуда, ки дар state мондааст. Яъне мизоҷ барои 10 баста пул
        # медод ва ЯК баста мегирифт. Ҳамон тавре чек кор мекунад, ин ҷо
        # ҳам як фармоиш ба ҳар дона сохта мешавад.
        cart_items = data.get("cart_items")
        if cart_items:
            await state.clear()
            await _balance_pay_cart(call, uid, data, cart_items, price, bal_after)
            return

        await state.clear()

        # Муайян кардани хидмат аз номи state
        if current.startswith("FFIDBuyState"):
            game_id = f"FFID:{data['player_id']}"
            confirm_prefix = "okffid"
            service_title = "🔥 Free Fire Indonesia"
            nickname = data.get("nickname", "")
            extra_line = f"🆔 ID: <code>{data['player_id']}</code>\n👤 Ном: <b>{nickname or '—'}</b>\n"
        elif current.startswith("FFBRBuyState"):
            game_id = f"FFBR:{data['player_id']}"
            confirm_prefix = "okffbr"
            service_title = "🇧🇷 Free Fire Brazil"
            nickname = data.get("nickname", "")
            extra_line = f"🆔 ID: <code>{data['player_id']}</code>\n👤 Ном: <b>{nickname or '—'}</b>\n"
        elif current.startswith("MLBuyState"):
            # ML ду майдон дорад — ҳарду дар game_id захира мешаванд
            game_id = f"ML:{data['player_id']}:{data['server_id']}"
            confirm_prefix = "okml"
            service_title = "🎯 Mobile Legends"
            nickname = data.get("nickname", "")
            extra_line = (f"🆔 Player ID: <code>{data['player_id']}</code>\n"
                          f"🌐 Server ID: <code>{data['server_id']}</code>\n")
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

        # Агар байни кам шудани баланс ва сохтани фармоиш хатои база шавад —
        # пул ба баланс ХУДКОР БАРНАМЕГАРДАД (қоидаи соҳиб). Ба ҷои он админ
        # огоҳ мешавад, то ДАСТӢ ҳал кунад.
        try:
            order_id = await db.create_order(
                user_id=uid,
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
        except Exception as e:
            logger.error(f"pay_with_balance: сохтани фармоиш нашуд ({uid}, {price}): {e}")
            await _safe_edit(
                call,
                "⚠️ <b>Хатои система рӯй дод.</b>\n\n"
                f"Лутфан бо дастгирӣ тамос гиред: {config.SUPPORT_USERNAME}",
                None
            )
            for admin_id in config.ADMIN_IDS:
                try:
                    await call.bot.send_message(
                        admin_id,
                        f"⚠️ <b>Хатои харид аз баланс — ДАСТӢ ҳал кунед!</b>\n\n"
                        f"👤 ID: <code>{uid}</code>\n"
                        f"💵 {price:.2f} сом аз баланс кам шуд, вале фармоиш сохта НАШУД.\n"
                        f"🎁 {esc(data.get('label', '—'))}\n"
                        f"Хато: {esc(str(e))[:200]}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            return

        combo_breakdown = await _combo_breakdown_text(data.get("combo_id"))
        # Баланси ДАҚИҚ аз худи транзаксияи кам кардан (на аз хониши алоҳида,
        # ки фармоиши ҳамзамони дигар онро тағйир дода метавонад)
        new_balance = bal_after
        balance = round(bal_after + price, 2)

        if data.get("combo_id"):
            # Комбо — донати худкор НЕСТ (метавонад ашёи дастӣ дошта бошад),
            # пас тартиби дастии қаблӣ бетағйир мемонад
            await _safe_edit(
                call,
                f"✅ <b>Пардохт аз баланс қабул шуд!</b>\n\n"
                f"🆔 Фармоиш: #{order_id}\n"
                f"💰 {balance:.2f} сом баланс буд → баъди фармоиш "
                f"<b>{new_balance:.2f} сом</b> шуд (-{price:.2f} сом)\n\n"
                f"🔄 Фармоиши шумо ба админ фиристода шуд, натиҷа ба зудӣ маълум мешавад.",
                None
            )

            username_val = call.from_user.username
            username = f"@{username_val}" if username_val else "—"
            caption = (
                f"💰 <b>Фармоиши нав — пардохт аз баланс!</b>\n\n"
                f"🆔 Фармоиш: <b>#{order_id}</b>\n"
                f"👤 Корбар: {esc(call.from_user.full_name)} (<code>{call.from_user.id}</code>)\n"
                f"📱 Username: {username}\n"
                f"🎮 {service_title}\n"
                f"{extra_line}"
                f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
                f"💵 Маблағ: <b>{price:.2f} сомонӣ</b> (аз баланс)"
                f"{combo_breakdown}"
            )
            admin_kb_rows = [
                [InlineKeyboardButton(text="✅ Тасдиқ — дастӣ иҷро кунед", callback_data=f"{confirm_prefix}_{order_id}")],
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
            return

        # Маҳсулоти оддӣ — донат ФАВРАН худкор, бе интизории тасдиқи админ
        await _safe_edit(
            call,
            f"✅ <b>Пардохт аз баланс қабул шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"💰 {balance:.2f} сом баланс буд → баъди фармоиш "
            f"<b>{new_balance:.2f} сом</b> шуд (-{price:.2f} сом)\n\n"
            f"🚀 Донат ҳозир иҷро мешавад...",
            None
        )
        import autopay
        order = await db.get_order(order_id)
        # Баланси дақиқи лаҳзаи харидро ба худи фармоиш (дар хотира) мегузорем,
        # то паёми «АВТОТАСДИҚ» рақами дурустро нишон диҳад — на балансе, ки
        # то лаҳзаи фиристодани паём фармоиши ҳамзамони дигар тағйир додааст
        if order:
            order["bal_after"] = bal_after
        asyncio.create_task(autopay.run_donate_from_balance(call.bot, order))
    finally:
        _balance_pay_in_flight.discard(uid)


# ════════════════════════════════════════════════════════
#         ПУРКУНИИ БАЛАНС (барои ҳамаи мизоҷон)
# ════════════════════════════════════════════════════════
class TopupState(StatesGroup):
    enter_amount  = State()  # интизори маблағ
    choose_method = State()  # интизори интихоби тариқи пардохт
    wait_check    = State()  # интизори расми чек


PRESET_TOPUP_AMOUNTS = [20, 50, 100, 200, 500]


def _purchase_intent_from_state(current_state: str | None, data: dict) -> dict | None:
    """
    Аз ҳолати FSM-и харид (пеш аз пур кардани баланс аз "норасогӣ") маълумоти
    заруриро мебарорад, то баъди пуркунӣ ин харид худкор анҷом дода шавад.
    Комбоҳо ва ҳолатҳои нопурра истисно мешаванд (None) — барои онҳо тартиби
    кӯҳна (мизоҷ худаш аз нав фармоиш медиҳад) мемонад.
    """
    if not current_state or data.get("combo_id") or not data.get("price") or not data.get("label"):
        return None
    if current_state.startswith("FFIDBuyState"):
        game_id = f"FFID:{data.get('player_id', '')}"
        nickname = data.get("nickname", "")
    elif current_state.startswith("FFBRBuyState"):
        game_id = f"FFBR:{data.get('player_id', '')}"
        nickname = data.get("nickname", "")
    elif current_state.startswith("MLBuyState"):
        game_id = f"ML:{data.get('player_id', '')}:{data.get('server_id', '')}"
        nickname = data.get("nickname", "")
    elif current_state.startswith("PUBGBuyState"):
        game_id = f"PUBG:{data.get('player_id', '')}"
        nickname = ""
    elif current_state.startswith("StarsBuyState"):
        game_id = f"STARS:{data.get('tg_username', '')}"
        nickname = ""
    elif current_state.startswith("PremiumBuyState"):
        game_id = f"PREMIUM:{data.get('tg_username', '')}"
        nickname = ""
    elif current_state.startswith("BuyState"):
        game_id = data.get("player_id", "")
        nickname = data.get("nickname", "")
    else:
        return None
    if not game_id:
        return None
    return {
        "game_id": game_id,
        "nickname": nickname,
        "amount": data.get("amount", 0),
        "price": data["price"],
        "label": data["label"],
        "offer_id": data.get("offer_id", ""),
    }


@router.callback_query(F.data == "topup_balance")
async def topup_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    max_amount = await db.get_max_balance_topup()
    preset_buttons = [
        InlineKeyboardButton(text=f"{amt} сом", callback_data=f"topup_preset_{amt}")
        for amt in PRESET_TOPUP_AMOUNTS if amt <= max_amount
    ]
    preset_rows = [preset_buttons[i:i + 3] for i in range(0, len(preset_buttons), 3)]
    kb = InlineKeyboardMarkup(inline_keyboard=preset_rows + [
        [InlineKeyboardButton(text="🔙 Бекор", callback_data="profile_menu")]
    ])
    await _safe_edit(
        call,
        f"💰 <b>Пур кардани баланс</b>\n\n"
        f"Тугмаеро пахш кунед ё маблағи дилхоҳро худатон нависед (сомонӣ).\n"
        f"Ҳадди максималӣ: <b>{max_amount:.2f} сомонӣ</b>",
        kb
    )
    await state.set_state(TopupState.enter_amount)


@router.callback_query(F.data.startswith("topup_preset_"), TopupState.enter_amount)
async def topup_preset_pick(call: CallbackQuery, state: FSMContext):
    amount = float(call.data.replace("topup_preset_", ""))
    await state.update_data(topup_amount=amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="topup_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="topup_pay_alif")],
        [InlineKeyboardButton(text="🔙 Бекор",          callback_data="profile_menu")],
    ])
    await _safe_edit(
        call,
        f"💰 <b>Пур кардани баланс</b>\n\n"
        f"💵 Маблағ: <b>{amount:.2f} сомонӣ</b>\n\n"
        f"Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(TopupState.choose_method)


@router.message(TopupState.enter_amount)
async def topup_enter_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".")
    try:
        amount = round(float(text), 2)
    except ValueError:
        await message.answer("⚠️ Лутфан рақами дуруст нависед (масалан: 50).")
        return
    if not math.isfinite(amount) or amount <= 0:
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


@router.callback_query(F.data.startswith("topup_shortfall_"))
async def topup_shortfall_pick(call: CallbackQuery, state: FSMContext):
    """
    Тугмаи "маблағ норасост" аз ягон экрани харид (FF/FFID/PUBG/Stars/
    Premium/сабад) — мустақим ба марҳилаи интихоби усули пуркунӣ мегузарад,
    бо маблағи норасо аллакай пур карда шуда. Ҳолати пешинаи харид тоза
    мешавад (state.clear) — мизоҷ бояд баъд аз пуркунӣ хариди худро аз нав
    оғоз кунад.
    """
    try:
        amount = round(float(call.data.replace("topup_shortfall_", "")), 2)
    except ValueError:
        await call.answer("❌ Хатогӣ!", show_alert=True)
        return
    if amount <= 0:
        await call.answer("❌ Хатогӣ!", show_alert=True)
        return
    # Ниятҳои хариди ҷориро ПЕШ аз тоза кардани state мегирем — то баъди
    # пуркунӣ ин харид худкор анҷом дода шавад (мизоҷ дигар кор накунад)
    current = await state.get_state()
    data = await state.get_data()
    pending = _purchase_intent_from_state(current, data)
    max_amount = await db.get_max_balance_topup()
    if amount > max_amount:
        amount = max_amount
    await state.clear()
    await state.update_data(topup_amount=amount, pending_purchase=pending)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="topup_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="topup_pay_alif")],
        [InlineKeyboardButton(text="🔙 Бекор",          callback_data="profile_menu")],
    ])
    pending_line = (
        "\n🛍 Баъди пуркунӣ, хариди шумо ХУДКОР анҷом дода мешавад — "
        "дигар кор лозим нест!\n"
        if pending else ""
    )
    await _safe_edit(
        call,
        f"💰 <b>Пур кардани баланс</b>\n\n"
        f"💵 Маблағ: <b>{amount:.2f} сомонӣ</b>\n"
        f"{pending_line}\n"
        f"Тариқи пардохтро интихоб кунед:",
        kb
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

    # Агар ин пуркунӣ аз "норасогӣ"-и харид оғоз шуда бошад — ниятҳои
    # харидро дар база ба ин фармоиши пуркунӣ мебандем, то баъди тасдиқ
    # худкор анҷом дода шавад
    pending = data.get("pending_purchase")
    if pending:
        await db.create_pending_purchase(
            awaiting_order_id, call.from_user.id,
            game_id=pending["game_id"],
            nickname=pending.get("nickname", ""),
            amount=pending.get("amount", 0),
            price=float(pending["price"]),
            label=pending["label"],
            offer_id=pending.get("offer_id", ""),
        )

    method_name = "🏙 Душанбе Сити" if method == "dushanbe_city" else "💳 Алиф"
    pay_url, pay_btn, steps = await _pay_reqs(method, price, f"card_{awaiting_order_id}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pay_btn, url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="profile_menu")],
    ])
    pending_line = (
        f"🛍 Баъди тасдиқ, «{esc(pending['label'])}» ХУДКОР харида мешавад!\n\n"
        if pending else ""
    )
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"💰 Пуркунии баланс\n"
        f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n"
        f"🆔 Фармоиш: #{awaiting_order_id}\n"
        f"{pending_line}\n"
        f"{steps}"
        f"⚡ Пас аз фиристодани чек, системаи мо пардохти шуморо "
        f"<b>худкор</b> тафтиш мекунад ва баланс худкор пур мешавад — "
        f"интизории админ лозим нест!\n\n"
        f"⏳ Шумо <b>20 дақиқа</b> вақт доред.",
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
    # Изи ангушти чек — то абзори админии «Ҷустуҷӯи чек» инро ёфта тавонад
    topup_hash = await _hash_photo(message)
    if not await db.set_autopay_check(autopay_order_id, file_id, topup_hash or None):
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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")
    kod = await db.find_kod_for_order(autopay_order_id) \
        or await db.claim_unmatched_kod(float(order["price"]), autopay_order_id, autopay.MAX_AGE_MINUTES)
    if kod:
        order = await db.get_order(autopay_order_id)
        asyncio.create_task(autopay.run_donate(message.bot, order, kod))


@router.message(TopupState.wait_check)
async def topup_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ==================== STANDOFF 2 (голд — донати ДАСТӢ) ====================
# Standoff 2 дар donatov.net аст, ки API надорад — пас донат ДАСТӢ иҷро
# мешавад: бот фармоиш, ID ва пардохтро мегирад, ба админ хабар медиҳад,
# админ дар donatov.net донат мекунад ва «Иҷро кардам»-ро мезанад.
# Барои ҳамин на автопардохт, на offer_id — мисли комбо.
class StandoffBuyState(StatesGroup):
    enter_id       = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


@router.callback_query(F.data == "buy_standoff")
async def standoff_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="games_menu")]
    ])
    await _safe_edit(
        call,
        "🔫 <b>Standoff 2 — Харидани голд</b>\n\n"
        "📝 <b>USER ID</b>-и аккаунтатонро нависед:\n"
        "Мисол: <code>263347019</code>\n\n"
        "ℹ️ ID-ро дар бозӣ → Профил ёфта метавонед.",
        kb
    )
    await state.set_state(StandoffBuyState.enter_id)


@router.message(StandoffBuyState.enter_id)
async def standoff_enter_id(message: Message, state: FSMContext):
    player_id = (message.text or "").strip()
    if not player_id.isdigit() or len(player_id) < 5:
        await message.answer("⚠️ USER ID танҳо рақам аст (ҳадди ақал 5 рақам). Дубора нависед:")
        return
    await state.update_data(player_id=player_id, nickname="")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="standoff_id_ok")],
        [InlineKeyboardButton(text="✏️ ID-ро тағйир медиҳам", callback_data="buy_standoff")],
    ])
    await message.answer(
        f"🔫 <b>Standoff 2</b>\n\n"
        f"🆔 USER ID: <code>{player_id}</code>\n\n"
        f"⚠️ ID-ро бодиққат тафтиш кунед — голд ба ҳамин ID меравад!\n"
        f"Агар дуруст бошад «Давом»-ро пахш кунед:",
        reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data == "standoff_id_ok")
async def standoff_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_standoff_products()
    if not products:
        await call.answer("❌ Ҳозир голд нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return
    buttons = []
    for p in products:
        label = p.get("label") or f"🔫 {p['amount']}G"
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {float(p['price']):.2f} сом",
            callback_data=f"standoff_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_standoff")])
    await _safe_edit(
        call,
        "🔫 <b>Голд барои Standoff 2</b>\n\nМаҳсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(StandoffBuyState.choose_product)


@router.callback_query(F.data.startswith("standoff_prod_"), StandoffBuyState.choose_product)
async def standoff_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_standoff_product(product_id)
    if not product:
        await call.answer("❌ Маҳсулот ёфт нашуд!", show_alert=True)
        return
    await state.update_data(
        product_id=product_id, amount=product["amount"],
        price=float(product["price"]),
        label=product.get("label") or f"🔫 {product['amount']}G",
    )
    await _standoff_payment_menu(call, state)


async def _standoff_payment_menu(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="standoff_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="standoff_pay_alif")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="standoff_pay_balance")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="standoff_id_ok")])
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш (Standoff 2)</b>\n\n"
        f"🆔 USER ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
    await state.set_state(StandoffBuyState.choose_payment)


@router.callback_query(F.data == "standoff_pay_balance", StandoffBuyState.choose_payment)
async def standoff_pay_balance(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if uid in _balance_pay_in_flight:
        await call.answer("⏳ Фармоиши қаблиатон дар кор аст — сабр кунед.", show_alert=True)
        return
    _balance_pay_in_flight.add(uid)
    try:
        data = await state.get_data()
        price = data.get("price")
        if price is None:
            await call.answer("❌ State тамом шуд, аз нав сар кунед!", show_alert=True)
            return
        if await db.get_referral_balance(uid) < price:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return
        bal_after = await db.deduct_referral_balance(uid, price)
        if bal_after is None:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return
        await state.clear()
        # try/except — мисли pay_with_balance: агар баъди кам шудани баланс
        # сохтани фармоиш хато диҳад, пул БЕСАДО гум нашавад, балки админ
        # огоҳ шавад (қоидаи соҳиб — худкор барнамегардонем)
        try:
            order_id = await db.create_order(
                user_id=uid, game_id=f"SO2:{data['player_id']}", nickname="",
                amount=data["amount"], price=price, label=data["label"],
                offer_id="", payment_method="referral_balance",
            )
            await db.mark_order_paid_with_balance(order_id)
        except Exception as e:
            logger.error(f"standoff_pay_balance: сохтани фармоиш нашуд ({uid}): {e}")
            for admin_id in config.ADMIN_IDS:
                try:
                    await call.bot.send_message(
                        admin_id,
                        f"⚠️ <b>Хатои харид аз баланс (Standoff) — ДАСТӢ ҳал кунед!</b>\n\n"
                        f"👤 ID: <code>{uid}</code>\n"
                        f"💵 {price:.2f} сом аз баланс кам шуд, вале фармоиш сохта НАШУД.\n"
                        f"🎁 {esc(data.get('label', '—'))}\n"
                        f"Хато: {esc(str(e))[:200]}",
                        parse_mode="HTML")
                except Exception:
                    pass
            await call.message.answer(
                "⚠️ Мушкили техникӣ шуд. Админ хабардор аст ва зуд ҳал мекунад 🙏")
            return
        new_balance = bal_after
        await call.message.answer(
            f"✅ <b>Пардохт аз баланс қабул шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n🎁 {esc(data['label'])}\n"
            f"💵 Кам шуд: <b>{price:.2f} сом</b>\n"
            f"💰 Баланси боқимонда: <b>{new_balance:.2f} сом</b>\n\n"
            f"🔄 Голд ба зудӣ иҷро мешавад. 🙏", parse_mode="HTML")
        await _standoff_notify_admin(call.bot, call.from_user, order_id, data,
                                     "💰 Аз баланс", None)
    finally:
        _balance_pay_in_flight.discard(uid)


@router.callback_query(F.data.in_({"standoff_pay_dc", "standoff_pay_alif"}),
                       StandoffBuyState.choose_payment)
async def standoff_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price = round(float(data["price"]), 2)
    method = "dushanbe_city" if call.data == "standoff_pay_dc" else "alif"
    # Нархи каме нодир — то маблағи чек айнан бо ин фармоиш мувофиқ шавад
    price = round(price + random.randint(1, 99) / 100, 2)
    await state.update_data(price=price, payment_method=method)
    order_ref = "so" + str(uuid.uuid4())[:8]
    alif_note = ""
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        pay_btn = "💳 Пардохт кардан"
        dc_card = await db.get_dc_card_number()
        pay_url = await _dc_pay_url(dc_card, price, f"card_{order_ref}")
    else:
        method_name = "💳 Алиф"
        pay_btn = "📲 Кушодани Алиф"
        pay_url = await _alif_pay_url()
        _so_card = await db.get_dc_card_number()
        alif_note = (
            f"📲 Дар Алиф «<b>На карту</b>» → рақами корт "
            f"(пахш кунед — нусха мешавад):\n<code>{_so_card}</code>\n\n"
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pay_btn, url=pay_url)],
        [InlineKeyboardButton(text="📸 Чекро фиристодам", callback_data="standoff_sent")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="standoff_id_ok")],
    ])
    await _safe_edit(
        call,
        f"🔫 <b>Standoff 2 — {method_name}</b>\n\n"
        f"🆔 USER ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n\n"
        f"{alif_note}"
        f"1️⃣ Тугмаи «{pay_btn}»-ро пахш кунед\n"
        f"2️⃣ Маҳз <b>{price:.2f} сом</b>-ро пардозед\n"
        f"3️⃣ Расми чекро ин ҷо фиристед\n\n"
        f"⚠️ Маблағро АЙНАН нигоҳ доред — то фармоишатон зуд ёфт шавад.",
        kb
    )
    await state.set_state(StandoffBuyState.wait_check)


@router.callback_query(F.data == "standoff_sent", StandoffBuyState.wait_check)
async def standoff_ask_check(call: CallbackQuery, state: FSMContext):
    await call.answer("📸 Расми чекро фиристед 👇", show_alert=True)


@router.message(StandoffBuyState.wait_check, F.photo)
async def standoff_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    method_name = "🏙 Душанбе Сити" if data.get("payment_method") == "dushanbe_city" else "💳 Алиф"
    order_id = await db.create_order(
        user_id=message.from_user.id, game_id=f"SO2:{data['player_id']}",
        nickname="", amount=data["amount"], price=data["price"],
        label=data["label"], offer_id="",
        payment_method=data.get("payment_method", ""),
    )
    await db.set_order_check(order_id, file_id, check_hash)
    await message.answer(
        f"✅ <b>Чек қабул шуд!</b>\n\n🆔 Фармоиш: #{order_id}\n\n"
        f"🔄 Пардохтатон тафтиш мешавад, голд ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML")
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")
    await _standoff_notify_admin(message.bot, message.from_user, order_id, data,
                                 method_name, file_id)


@router.message(StandoffBuyState.wait_check)
async def standoff_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


async def _standoff_notify_admin(bot, user, order_id, data, method_name, file_id):
    """Ба админ хабар — Standoff донати ДАСТӢ дорад, пас тугмаи «Иҷро кардам»."""
    username = f"@{user.username}" if user.username else "—"
    caption = (
        f"🔫 <b>Фармоиши нав — Standoff 2 (ДАСТӢ иҷро кунед!)</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(user.full_name)} (<code>{user.id}</code>)\n"
        f"📱 Username: {esc(username)}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"🆔 USER ID: <code>{data['player_id']}</code>\n"
        f"🎁 Маҳсулот: <b>{esc(data['label'])}</b>\n"
        f"💵 Маблағ: <b>{float(data['price']):.2f} сомонӣ</b>\n\n"
        f"👉 Дар donatov.net ба ин ID донат кунед, баъд «Иҷро кардам»-ро пахш кунед."
    )
    rows = [
        [InlineKeyboardButton(text="✅ Иҷро кардам — тасдиқ",
                              callback_data=f"sdok_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if user.username:
        rows.append([InlineKeyboardButton(text="💬 ЛС ба клент",
                                          url=f"https://t.me/{user.username}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    for admin_id in config.ADMIN_IDS:
        try:
            if file_id:
                await bot.send_photo(admin_id, file_id, caption=caption,
                                     reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb,
                                       parse_mode="HTML")
        except Exception as e:
            logger.error(f"Standoff: ба админ {admin_id} нарасид: {e}")


@router.callback_query(F.data.startswith("sdok_"))
async def standoff_admin_done(call: CallbackQuery):
    if call.from_user.id not in config.ADMIN_IDS:
        return
    order_id = int(call.data.split("_")[1])
    order = await db.get_order(order_id)
    if not order:
        await call.answer("❌ Фармоиш ёфт нашуд!", show_alert=True)
        return
    if order.get("status") == "confirmed":
        await call.answer("ℹ️ Ин фармоиш аллакай тасдиқ шудааст.", show_alert=True)
        return
    await db.update_order_status(order_id, "confirmed")
    try:
        await db.set_confirmed_at(order_id)
    except Exception:
        pass
    try:
        await call.bot.send_message(
            order["user_id"],
            f"🎉 <b>Голди Standoff 2 фиристода шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"🎁 {esc(order.get('label') or '—')}\n"
            f"🆔 ба ID: <code>{str(order.get('game_id','')).replace('SO2:','')}</code>\n\n"
            f"📲 Аккаунтатонро санҷед. Раҳмат барои харид! 🙏",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"Standoff: хабар ба мизоҷ нарасид: {e}")
    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + "\n\n✅ ИҶРО ШУД",
            reply_markup=None)
    except Exception:
        try:
            await call.message.edit_text(
                (call.message.text or "") + "\n\n✅ ИҶРО ШУД", reply_markup=None)
        except Exception:
            pass
    await call.answer("✅ Тасдиқ шуд, мизоҷ хабар гирифт.")



# ==================== FREE FIRE BRAZIL (донати ХУДКОР) ====================
class FFBRBuyState(StatesGroup):
    enter_id       = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


# ==================== ОҒОЗ: ID НАВИШТАН ====================
@router.callback_query(F.data == "buy_ffbr")
async def ffbr_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="games_menu")]
    ])
    await _safe_edit(
        call,
        "🔥 <b>Free Fire Brazil — Харидани алмаз</b>\n\n"
        "📝 ID аккаунтатонро нависед:\n"
        "Мисол: <code>123456789</code>",
        kb
    )
    await state.set_state(FFBRBuyState.enter_id)


@router.message(FFBRBuyState.enter_id)
async def ffbr_enter_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    if not player_id.isdigit():
        await message.answer("⚠️ ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return

    wait = await message.answer("⏳ ID тафтиш мешавад...")
    nickname = await ff_api.get_nickname_ffbr(player_id)
    await state.update_data(player_id=player_id, nickname=nickname)

    if nickname:
        text = (
            f"🔥 <b>Free Fire Brazil</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"👤 Ном: <b>{esc(nickname)}</b>\n\n"
            f"✅ Агар ин аккаунти шумо бошад «Давом»-ро пахш кунед:"
        )
    else:
        text = (
            f"🔥 <b>Free Fire Brazil</b>\n\n"
            f"🆔 ID: <code>{player_id}</code>\n"
            f"⚠️ Номи аккаунт ёфт нашуд.\n\n"
            f"ID-ро бодиққат тафтиш кунед ва агар дуруст бошад «Давом»-ро пахш кунед:"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="ffbr_id_ok")],
        [InlineKeyboardButton(text="✏️ ID-ро тағйир медиҳам", callback_data="buy_ffbr")],
    ])
    await _safe_edit_msg(wait, text, kb)


# ==================== РӮЙХАТИ МАҲСУЛОТ ====================
@router.callback_query(F.data == "ffbr_id_ok")
async def ffbr_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_ffbr_products()
    if not products:
        await call.answer("❌ Ҳозир маҳсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return

    buttons = []
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {p['price']:.2f} сом",
            callback_data=f"ffbr_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_ffbr")])

    await _safe_edit(
        call,
        "💎 <b>Алмазҳои Free Fire Brazil</b>\n\nМаҷсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(FFBRBuyState.choose_product)


# ==================== ИНТИХОБИ ТАРИҚИ ПАРДОХТ ====================
@router.callback_query(F.data.startswith("ffbr_prod_"), FFBRBuyState.choose_product)
async def ffbr_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_ffbr_product(product_id)
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
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ffbr_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ffbr_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="ffbr_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffbr_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🔥 Free Fire Brazil\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )
    await state.set_state(FFBRBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ (FFBR) ====================
@router.callback_query(F.data.in_({"ffbr_pay_dc", "ffbr_pay_alif", "ffbr_pay_eskhata"}), FFBRBuyState.choose_payment)
async def ffbr_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "ffbr_pay_dc": "dushanbe_city",
        "ffbr_pay_eskhata": "eskhata",
        "ffbr_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="ffbr_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="ffbr_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "ffbr_terms_reject", FFBRBuyState.choose_payment)
async def ffbr_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    label = data["label"]
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ffbr_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ffbr_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="ffbr_pay_eskhata")],
    ]
    balance = await db.get_referral_balance(call.from_user.id)
    if balance >= data["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    else:
        shortfall = round(data["price"] - balance, 2)
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffbr_id_ok")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(
        call,
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🔥 Free Fire Brazil\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{label}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:",
        kb
    )


# ==================== НИШОН ДОДАНИ РЕКВИЗИТ ====================
@router.callback_query(F.data == "ffbr_terms_accept", FFBRBuyState.choose_payment)
async def ffbr_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"FFBR:{data['player_id']}",
            amount=data.get("amount", 0),
            title="🇧🇷 Free Fire Brazil", back_cb="ffbr_id_ok")
        await state.set_state(FFBRBuyState.wait_check)
        return
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
        pay_url = await _dc_pay_url(dc_card, price, f"card_ffbr{order_id}")
    elif method == "eskhata":
        method_name = "🏦 Эсхата"
        pay_url = data.get("eskhata_link") or ""
        eskhata_note = "\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n"
        if not pay_url:
            await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
            return
    else:
        method_name = "💳 Алиф"
        # Нархи каме нодир — зидди чеки такрорӣ/дуруғин (ба amount= низ мегузарад)
        price = round(round(float(price), 2) + round(random.randint(1, 99) / 100, 2), 2)
        await state.update_data(price=price)
        pay_url = await _alif_pay_url()  # шохаи мурда — Алиф аз _autopay_requisites меравад

    await state.update_data(payment_method=method)

    await _notify_rozigiho(
        call.bot, call.from_user, "🔥 Free Fire Brazil", data["label"],
        price, method_name, f"ffbr{order_id}"
    )

    btn_text = method_name.replace("🏙 ", "").replace("💳 ", "").replace("🏦 ", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Пардохти {btn_text}", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffbr_id_ok")],
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
    await state.set_state(FFBRBuyState.wait_check)


# ==================== ИНТИЗОРИ ЧЕК ====================
@router.message(FFBRBuyState.wait_check, F.photo)
async def ffbr_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

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
    # Маркер барои FF Brazil
    async with db.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET game_id=%s WHERE id=%s",
                               (f"FFBR:{data['player_id']}", order_id))

    await message.answer(
        "✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        "🔄 Пардохти шумо тафтиш мешавад.\n"
        "Натиҷа ба зудӣ фиристода мешавад. 🙏",
        parse_mode="HTML"
    )
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

    nickname = data.get("nickname", "") or "—"
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"💳 Тариқ: {method_name}\n\n"
        f"🎮 Free Fire Brazil\n"
        f"🆔 ID: <code>{data['player_id']}</code>\n"
        f"👤 Ном: <b>{esc(nickname)}</b>\n"
        f"🎁 Маҷсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    username_val = message.from_user.username
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (FFBR)", callback_data=f"okffbr_{order_id}")],
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


@router.message(FFBRBuyState.wait_check)
async def ffbr_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ════════════════════════════════════════════════════════
#                   PUBG MOBILE


# ════════════════════════════════════════════════════════
#                   MOBILE LEGENDS
# ════════════════════════════════════════════════════════
# ML аз бозиҳои дигар ФАРҚ мекунад: мизоҷ ДУ рақам менависад —
# Player ID ва Server (Zone) ID. Ҳарду дар game_id захира мешаванд:
# "ML:<player_id>:<server_id>".
class MLBuyState(StatesGroup):
    enter_id       = State()
    enter_server   = State()
    choose_product = State()
    choose_payment = State()
    wait_check     = State()


# ==================== ҚАДАМИ 1: PLAYER ID ====================
@router.callback_query(F.data == "buy_ml")
async def ml_buy_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit(
        call,
        "🎯 <b>Mobile Legends</b>\n\n"
        "1️⃣ Аввал <b>Player ID</b>-и худро нависед:\n\n"
        "📌 ID-ро дар бозӣ: Профил → зери ном мебинед\n"
        "Мисол: <code>123456789</code>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="games_menu")],
        ])
    )
    await state.set_state(MLBuyState.enter_id)


@router.message(MLBuyState.enter_id)
async def ml_enter_id(message: Message, state: FSMContext):
    player_id = message.text.strip()
    if not player_id.isdigit():
        await message.answer("⚠️ Player ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return
    await state.update_data(player_id=player_id)
    await message.answer(
        f"✅ Player ID: <code>{player_id}</code>\n\n"
        f"2️⃣ Акнун <b>Server ID</b> (Zone)-ро нависед:\n\n"
        f"📌 Дар бозӣ он дар қавс паҳлӯи ID навишта шудааст\n"
        f"Мисол: агар <code>123456789 (2001)</code> бошад → <code>2001</code>",
        parse_mode="HTML"
    )
    await state.set_state(MLBuyState.enter_server)


# ==================== ҚАДАМИ 2: SERVER ID ====================
@router.message(MLBuyState.enter_server)
async def ml_enter_server(message: Message, state: FSMContext):
    server_id = message.text.strip()
    if not server_id.isdigit():
        await message.answer("⚠️ Server ID танҳо аз рақамҳо иборат аст! Дубора нависед:")
        return
    data = await state.get_data()
    player_id = data["player_id"]

    wait = await message.answer("⏳ Аккаунт тафтиш мешавад...")
    nickname = await ff_api.get_nickname_ml(player_id, server_id)
    await state.update_data(server_id=server_id, nickname=nickname)

    if nickname:
        text = (
            f"🎯 <b>Mobile Legends</b>\n\n"
            f"🆔 Player ID: <code>{player_id}</code>\n"
            f"🌐 Server ID: <code>{server_id}</code>\n"
            f"👤 Ном: <b>{esc(nickname)}</b>\n\n"
            f"✅ Агар ин аккаунти шумо бошад «Давом»-ро пахш кунед:"
        )
    else:
        # Барои Mobile Legends FazerCards санҷиши номро УМУМАН дастгирӣ
        # намекунад — пас «ёфт нашуд» ҳар дафъа мебарояд ва мизоҷро беҳуда
        # метарсонад. Ба ҷои огоҳӣ, танҳо хоҳиши тафтиши рақамҳо.
        text = (
            f"🎯 <b>Mobile Legends</b>\n\n"
            f"🆔 Player ID: <code>{player_id}</code>\n"
            f"🌐 Server ID: <code>{server_id}</code>\n\n"
            f"ℹ️ Дар Mobile Legends номи аккаунтро пешакӣ нишон додан "
            f"мумкин нест.\n\n"
            f"❗️ Лутфан ҳарду рақамро бо диққат тафтиш кунед — алмазҳо "
            f"маҳз ба ҳамин аккаунт мераванд ва баргардонида намешаванд."
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Давом", callback_data="ml_id_ok")],
        [InlineKeyboardButton(text="✏️ Аз нав нависам", callback_data="buy_ml")],
    ])
    await _safe_edit_msg(wait, text, kb)


# ==================== РӮЙХАТИ МАҲСУЛОТ ====================
@router.callback_query(F.data == "ml_id_ok")
async def ml_show_products(call: CallbackQuery, state: FSMContext):
    products = await db.get_ml_products()
    if not products:
        await call.answer("❌ Ҳозир маҳсулот нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return
    buttons = []
    for p in products:
        label = p.get("label") or f"💎 {p['amount']}"
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {p['price']:.2f} сом",
            callback_data=f"ml_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="buy_ml")])
    await _safe_edit(
        call,
        "💎 <b>Алмазҳои Mobile Legends</b>\n\nМаҳсулотро интихоб кунед:",
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(MLBuyState.choose_product)


def _ml_confirm_kb(balance: float, price: float) -> list:
    """Тугмаҳои экрани тасдиқи фармоиши ML (пардохт/баланс/бозгашт)."""
    rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ml_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ml_pay_alif")],
        [InlineKeyboardButton(text="🏦 Эсхата",        callback_data="ml_pay_eskhata")],
    ]
    if balance >= price:
        rows.append([InlineKeyboardButton(
            text=f"💰 Истифода аз баланс ({balance:.2f} сом)",
            callback_data="pay_balance"
        )])
    else:
        shortfall = round(price - balance, 2)
        rows.append([InlineKeyboardButton(
            text=f"💰 {shortfall:.2f} сом норасост — Пур кунед",
            callback_data=f"topup_shortfall_{shortfall:.2f}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ml_id_ok")])
    return rows


def _ml_confirm_text(data: dict) -> str:
    nickname = data.get("nickname", "")
    nick_line = f"👤 Ном: <b>{esc(nickname)}</b>\n" if nickname else ""
    return (
        f"🛒 <b>Тасдиқи фармоиш</b>\n\n"
        f"🎮 Бозӣ: 🎯 Mobile Legends\n"
        f"🆔 Player ID: <code>{data['player_id']}</code>\n"
        f"🌐 Server ID: <code>{data['server_id']}</code>\n"
        f"{nick_line}"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Нарх: <b>{data['price']:.2f} сомонӣ</b>\n\n"
        f"💰 Тариқи пардохтро интихоб кунед:"
    )


# ==================== ИНТИХОБИ ТАРИҚИ ПАРДОХТ ====================
@router.callback_query(F.data.startswith("ml_prod_"), MLBuyState.choose_product)
async def ml_choose_payment(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split("_")[2])
    product = await db.get_ml_product(product_id)
    if not product:
        await call.answer("❌ Маҳсулот ёфт нашуд!", show_alert=True)
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
    balance = await db.get_referral_balance(call.from_user.id)
    await _safe_edit(
        call, _ml_confirm_text(data),
        InlineKeyboardMarkup(inline_keyboard=_ml_confirm_kb(balance, data["price"]))
    )
    await state.set_state(MLBuyState.choose_payment)


# ==================== РОЗИГӢ ПЕШ АЗ РЕКВИЗИТ ====================
@router.callback_query(F.data.in_({"ml_pay_dc", "ml_pay_alif", "ml_pay_eskhata"}), MLBuyState.choose_payment)
async def ml_ask_terms(call: CallbackQuery, state: FSMContext):
    method_map = {
        "ml_pay_dc": "dushanbe_city",
        "ml_pay_eskhata": "eskhata",
        "ml_pay_alif": "alif",
    }
    await state.update_data(pending_payment_method=method_map[call.data])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="ml_terms_accept")],
        [InlineKeyboardButton(text="❌ Рад кунам",      callback_data="ml_terms_reject")],
    ])
    await _safe_edit(call, TERMS_TEXT_ROZIGI, kb)


@router.callback_query(F.data == "ml_terms_reject", MLBuyState.choose_payment)
async def ml_terms_reject(call: CallbackQuery, state: FSMContext):
    await call.answer("Бекор карда шуд.")
    data = await state.get_data()
    balance = await db.get_referral_balance(call.from_user.id)
    await _safe_edit(
        call, _ml_confirm_text(data),
        InlineKeyboardMarkup(inline_keyboard=_ml_confirm_kb(balance, data["price"]))
    )


# ==================== РЕКВИЗИТ ====================
@router.callback_query(F.data == "ml_terms_accept", MLBuyState.choose_payment)
async def ml_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    method = data.get("pending_payment_method", "alif")
    # DC/Alif → автопардохти пурра (худкор тасдиқ + худкор донат). Эсхата дастӣ.
    if method in ("dushanbe_city", "alif"):
        await _autopay_requisites(
            call, state, data, method,
            game_id_marker=f"ML:{data['player_id']}:{data['server_id']}",
            amount=data.get("amount", 0),
            title="🎯 Mobile Legends", back_cb="ml_id_ok")
        await state.set_state(MLBuyState.wait_check)
        return

    price, winback_note = await _apply_winback_discount(
        call.from_user.id, round(float(data["price"]), 2))
    await state.update_data(price=price, payment_method=method)
    pay_url = data.get("eskhata_link") or ""
    if not pay_url:
        await call.answer("⚠️ Барои ин маҳсулот линки Эсхата ҷойгир нашудааст!", show_alert=True)
        return

    await _notify_rozigiho(
        call.bot, call.from_user, "🎯 Mobile Legends", data["label"],
        price, "🏦 Эсхата", str(data.get("product_id", ""))
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пардохти Эсхата", url=pay_url)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ml_id_ok")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>🏦 Эсхата</b>\n\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b>\n"
        f"{winback_note}"
        f"\n⚠️ <b>Эсхата +5% комиссия мегирад</b>\n\n"
        f"1️⃣ Тугмаи «Пардохт»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ <b>{price:.2f} сом</b>-ро пардохт кунед\n"
        f"3️⃣ Расми чекро ба ин чат фиристед\n\n"
        f"⏳ Шумо <b>10 дақиқа</b> вақт доред барои фиристодани чек!\n"
        f"⚠️ Маблағ бояд дақиқ бошад!",
        kb
    )
    await state.set_state(MLBuyState.wait_check)


# ==================== ИНТИЗОРИ ЧЕК ====================
@router.message(MLBuyState.wait_check, F.photo)
async def ml_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    # ==== АВТОПАРДОХТ (DC/Alif): чек омад → ҷустуҷӯи худкори пардохт ====
    if await _autopay_receive_check(message, data):
        return

    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return

    order_id = await db.create_order(
        user_id=message.from_user.id,
        game_id=f"ML:{data['player_id']}:{data['server_id']}",
        nickname=data.get("nickname", ""),
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
    await _offer_game(message, "⏳ Пардохти шумо ҳозир тафтиш шуда истодааст...")

    nickname = data.get("nickname", "") or "—"
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    caption = (
        f"📸 <b>Фармоиши нав — чек омад!</b>\n\n"
        f"🆔 Фармоиш: <b>#{order_id}</b>\n"
        f"👤 Корбар: {esc(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"📱 Username: {username}\n"
        f"💳 Тариқ: 🏦 Эсхата\n\n"
        f"🎯 Mobile Legends\n"
        f"🆔 Player ID: <code>{data['player_id']}</code>\n"
        f"🌐 Server ID: <code>{data['server_id']}</code>\n"
        f"👤 Ном: <b>{esc(nickname)}</b>\n"
        f"🎁 Маҳсулот: <b>{data['label']}</b>\n"
        f"💵 Маблағ: <b>{data['price']:.2f} сомонӣ</b>"
    )
    admin_kb_rows = [
        [InlineKeyboardButton(text="✅ Тасдиқ — донат кун (ML)", callback_data=f"okml_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if message.from_user.username:
        admin_kb_rows.append([InlineKeyboardButton(
            text="💬 ЛС ба клент", url=f"https://t.me/{message.from_user.username}")])
    admin_kb = InlineKeyboardMarkup(inline_keyboard=admin_kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id, file_id, caption=caption,
                reply_markup=admin_kb, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ба админ {admin_id} фиристода нашуд: {e}")


@router.message(MLBuyState.wait_check)
async def ml_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")


# ════════════════════════════════════════════════════════
#         НАСТРОЙКАИ FF (маҳсулоти рақамӣ — видео + силка)
# ════════════════════════════════════════════════════════
# Мизоҷ платформаро интихоб мекунад → видеои намоишӣ мебинад → пул медиҳад
# → админ огоҳ мешавад ва бо тугма силкаи каналро мефиристад. То админ
# тугмаро напахшад, силка ба мизоҷ НАМЕРАВАД (маҳсулот баргардонашаванда
# нест, пас озод кардани дастӣ бехатар аст).
_FFSET_PLATFORMS = {
    "ios":     ("📱 iPhone (iOS)", "ios"),
    "android": ("🤖 Android",      "android"),
}


async def _ffset_cfg(platform: str) -> dict:
    """Танзимоти платформаро аз settings мегирад: видео (file_id), нарх, силка."""
    video = await db.get_setting(f"ffset_{platform}_video")
    price = await db.get_setting(f"ffset_{platform}_price")
    link = await db.get_setting(f"ffset_{platform}_link")
    try:
        price_f = float(price) if price else 0.0
    except ValueError:
        price_f = 0.0
    return {"video": video or "", "price": price_f, "link": link or ""}


class FFSetupState(StatesGroup):
    wait_check = State()


@router.callback_query(F.data == "ffset_menu")
async def ffset_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"ffset_plat_{key}")]
        for key, (name, _) in _FFSET_PLATFORMS.items()
    ] + [[InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")]])
    await _safe_edit(
        call,
        "⚙️ <b>Настройкаи Free Fire</b>\n\n"
        "Тайёркунии беҳтарини ҳассосият (чувствительность) барои бозии беҳтар.\n\n"
        "Дастгоҳатонро интихоб кунед:",
        kb
    )


@router.callback_query(F.data.startswith("ffset_plat_"))
async def ffset_platform(call: CallbackQuery, state: FSMContext):
    platform = call.data.rsplit("_", 1)[1]
    if platform not in _FFSET_PLATFORMS:
        await call.answer("❌ Номаълум", show_alert=True)
        return
    cfg = await _ffset_cfg(platform)
    name = _FFSET_PLATFORMS[platform][0]
    if cfg["price"] <= 0 or not cfg["link"]:
        await call.answer("⚠️ Ин маҳсулот ҳоло тайёр нест. Баъдтар кӯшиш кунед.", show_alert=True)
        return
    await state.update_data(ffset_platform=platform, price=cfg["price"],
                            label=f"⚙️ Настройкаи FF — {name}")
    caption = (
        f"⚙️ <b>Настройкаи FF — {name}</b>\n\n"
        f"🎥 Дар видео тарзи кор нишон дода шудааст.\n"
        f"💵 Нарх: <b>{cfg['price']:.2f} сомонӣ</b>\n\n"
        f"Пас аз пардохт, силкаи канали пӯшида ба шумо фиристода мешавад "
        f"(баъди тасдиқи админ — одатан чанд дақиқа).\n\n"
        f"Тариқи пардохтро интихоб кунед:"
    )
    balance = await db.get_referral_balance(call.from_user.id)
    kb_rows = [
        [InlineKeyboardButton(text="🏙 Душанбе Сити", callback_data="ffset_pay_dc")],
        [InlineKeyboardButton(text="💳 Алиф",          callback_data="ffset_pay_alif")],
    ]
    if balance >= cfg["price"]:
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Аз баланс ({balance:.2f} сом)", callback_data="ffset_pay_balance")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffset_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    # Видеоро мефиристем (агар танзим шуда бошад), вагарна танҳо матн
    try:
        if cfg["video"]:
            await call.message.answer_video(cfg["video"], caption=caption,
                                            reply_markup=kb, parse_mode="HTML")
            await call.answer()
            return
    except Exception as e:
        logger.error(f"ffset видео нашуд: {e}")
    await _safe_edit(call, caption, kb)


async def _ffset_notify_admin(bot, user, order_id: int, platform: str,
                              price: float, method_name: str, check_file_id=None):
    """Ба админ огоҳӣ бо тугмаи «Додани силка» мефиристад."""
    name = _FFSET_PLATFORMS.get(platform, (platform,))[0]
    uname = f"@{user.username}" if user.username else "—"
    caption = (
        f"⚙️ <b>Настройкаи FF — пардохт омад!</b>\n\n"
        f"👤 Харидор: {esc(user.full_name)} ({esc(uname)})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📱 Дастгоҳ: {name}\n"
        f"💵 Маблағ: <b>{price:.2f} сомонӣ</b> · {method_name}\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        f"👇 Барои фиристодани силка ба мизоҷ, тугмаро пахш кунед. "
        f"То напахшед, силка НАМЕРАВАД."
    )
    kb_rows = [
        [InlineKeyboardButton(text="🔗 Додани силка ба мизоҷ", callback_data=f"ffsetrel_{order_id}")],
        [InlineKeyboardButton(text="❌ Рад кардан", callback_data=f"no_{order_id}")],
    ]
    if user.username:
        kb_rows.append([InlineKeyboardButton(text="💬 ЛС ба клент", url=f"https://t.me/{user.username}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    for admin_id in config.ADMIN_IDS:
        try:
            if check_file_id:
                await bot.send_photo(admin_id, check_file_id, caption=caption,
                                     reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, caption, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"ffset огоҳӣ ба админ {admin_id} нарасид: {e}")


@router.callback_query(F.data == "ffset_pay_balance")
async def ffset_pay_balance(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if uid in _balance_pay_in_flight:
        await call.answer("⏳ Фармоиши қаблиатон дар кор аст — сабр кунед.", show_alert=True)
        return
    _balance_pay_in_flight.add(uid)
    try:
        data = await state.get_data()
        platform = data.get("ffset_platform")
        price = data.get("price")
        if not platform or price is None:
            await call.answer("❌ State тамом шуд, аз нав сар кунед!", show_alert=True)
            return
        if await db.get_referral_balance(uid) < price:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return
        bal_after = await db.deduct_referral_balance(uid, price)
        if bal_after is None:
            await call.answer("❌ Балансатон кофӣ нест!", show_alert=True)
            return
        await state.clear()
        try:
            order_id = await db.create_order(
                user_id=uid, game_id=f"FFSETUP:{platform}", nickname="",
                amount=0, price=price, label=data["label"],
                offer_id="", payment_method="referral_balance",
            )
            await db.mark_order_paid_with_balance(order_id)
        except Exception as e:
            logger.error(f"ffset_pay_balance: фармоиш нашуд ({uid}): {e}")
            for admin_id in config.ADMIN_IDS:
                try:
                    await call.bot.send_message(
                        admin_id,
                        f"⚠️ <b>Хатои настройкаи FF аз баланс — ДАСТӢ ҳал кунед!</b>\n\n"
                        f"👤 ID: <code>{uid}</code>\n"
                        f"💵 {price:.2f} сом кам шуд, вале фармоиш сохта НАШУД.",
                        parse_mode="HTML")
                except Exception:
                    pass
            await call.message.answer("⚠️ Мушкили техникӣ. Админ хабардор аст 🙏")
            return
        await call.message.answer(
            f"✅ <b>Пардохт аз баланс қабул шуд!</b>\n\n"
            f"🆔 Фармоиш: #{order_id}\n"
            f"💰 Баланси боқимонда: <b>{bal_after:.2f} сом</b>\n\n"
            f"🔗 Силкаи канал ба зудӣ фиристода мешавад (баъди тасдиқи админ). 🙏",
            parse_mode="HTML")
        await _ffset_notify_admin(call.bot, call.from_user, order_id, platform,
                                  price, "💰 Аз баланс", None)
    finally:
        _balance_pay_in_flight.discard(uid)


@router.callback_query(F.data.in_({"ffset_pay_dc", "ffset_pay_alif"}))
async def ffset_show_requisites(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    platform = data.get("ffset_platform")
    price = data.get("price")
    if not platform or price is None:
        await call.answer("❌ State тамом шуд, аз нав сар кунед!", show_alert=True)
        return
    method = "dushanbe_city" if call.data == "ffset_pay_dc" else "alif"
    # Нархи каме нодир — то админ пардохтро осон мувофиқ кунад
    price = round(float(price) + random.randint(1, 99) / 100, 2)
    await state.update_data(price=price, payment_method=method)
    alif_note = ""
    if method == "dushanbe_city":
        method_name = "🏙 Душанбе Сити"
        pay_btn = "💳 Пардохт"
        dc_card = await db.get_dc_card_number()
        pay_url = await _dc_pay_url(dc_card, price, f"card_ffset")
    else:
        method_name = "💳 Алиф"
        pay_btn = "📲 Кушодани Алиф"
        pay_url = await _alif_pay_url()
        _fs_card = await db.get_dc_card_number()
        alif_note = (
            f"📲 Дар Алиф «<b>На карту</b>» → рақами корт "
            f"(пахш кунед — нусха мешавад):\n<code>{_fs_card}</code>\n\n"
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pay_btn, url=pay_url)],
        [InlineKeyboardButton(text="📸 Чекро фиристодам", callback_data="ffset_sent")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="ffset_menu")],
    ])
    await _safe_edit(
        call,
        f"💳 <b>{method_name}</b>\n\n"
        f"🎁 {esc(data['label'])}\n"
        f"💵 Маблағи ДАҚИҚ: <b>{price:.2f} сомонӣ</b>\n\n"
        f"{alif_note}"
        f"1️⃣ Тугмаи «{pay_btn}»-ро пахш кунед\n"
        f"2️⃣ Маблағи дақиқ пардохт кунед\n"
        f"3️⃣ «📸 Чекро фиристодам»-ро пахш карда, расми чекро фиристед\n\n"
        f"🔗 Баъди тасдиқ, силкаи канал ба шумо меравад.",
        kb
    )


@router.callback_query(F.data == "ffset_sent")
async def ffset_ask_check(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer("📸 Лутфан расми чекро фиристед:")
    await state.set_state(FFSetupState.wait_check)


@router.message(FFSetupState.wait_check, F.photo)
async def ffset_receive_check(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    platform = data.get("ffset_platform")
    price = data.get("price")
    if not platform or price is None:
        await message.answer("⚠️ Фармоиш фаъол нест, аз нав сар кунед.")
        return
    file_id = message.photo[-1].file_id
    check_hash = await _hash_photo(message)
    if await _block_if_duplicate_check(message, check_hash):
        return
    try:
        order_id = await db.create_order(
            user_id=message.from_user.id, game_id=f"FFSETUP:{platform}", nickname="",
            amount=0, price=price, label=data["label"],
            offer_id="", payment_method=data.get("payment_method", ""),
        )
        await db.set_order_check(order_id, file_id, check_hash)
    except Exception as e:
        logger.error(f"ffset_receive_check: фармоиш нашуд: {e}")
        await message.answer("⚠️ Хатои система. Бо дастгирӣ тамос гиред.")
        return
    await message.answer(
        f"✅ <b>Чек қабул шуд!</b>\n\n"
        f"🆔 Фармоиш: #{order_id}\n\n"
        f"🔗 Пас аз тасдиқи админ, силкаи канал ба шумо фиристода мешавад. 🙏",
        parse_mode="HTML")
    _pm = data.get("payment_method")
    method_name = "🏙 Душанбе Сити" if _pm == "dushanbe_city" else "💳 Алиф"
    await _ffset_notify_admin(message.bot, message.from_user, order_id, platform,
                              price, method_name, file_id)


@router.message(FFSetupState.wait_check)
async def ffset_wrong_check(message: Message, state: FSMContext):
    await message.answer("⚠️ Лутфан <b>расми</b> чекро фиристед (на матн).", parse_mode="HTML")
