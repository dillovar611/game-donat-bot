"""
Файли асосии бот.
Иҷро: python3 bot.py
"""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable, Dict
from urllib.parse import quote

from aiogram import Bot, Dispatcher, BaseMiddleware, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.storage.memory import MemoryStorage

import config
import database as db
from buy import router as buy_router, esc
from games import router as minigames_router
from admin import router as admin_router
from autopay import router as autopay_router
import autopay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ==================== MIDDLEWARE: ОБУНАИ КАНАЛ ====================
class SubscriptionMiddleware(BaseMiddleware):
    """Корбар бояд ба канал обуна бошад (ба ҷуз админҳо)."""

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        bot: Bot = data.get("bot")

        # Паёмҳои гурӯҳи автопардохт (аз боти notifier) — бе тафтиши
        # обуна/бан, зеро ин на паёми корбари одист
        if isinstance(event, Message) and event.chat.id == config.NOTIFIER_CHAT_ID:
            return await handler(event, data)

        user_id = None
        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id

        # Ҳар корбаре, ки бо бот ҳамкорӣ мекунад (новобаста аз он ки бо /start
        # оғоз кардааст ё не — масалан тавассути тугмаи кӯҳна/кэшшуда) бояд
        # дар ҷадвали users сабт шавад, вагарна баъдан admin/autopay ӯро
        # "ёфт нашуд" мебинад
        if user_id:
            try:
                await db.add_user(user_id, event.from_user.username or "", event.from_user.full_name or "")
            except Exception as e:
                logger.error(f"add_user хато барои {user_id}: {e}")

        # Админҳо озод аз ҳама маҳдудиятҳо
        if user_id and user_id not in config.ADMIN_IDS:

            # 1. Тафтиши ON/OFF бот
            bot_active = await db.get_setting("bot_active")
            if bot_active != "1":
                text = "🔧 <b>Бот дар ҳолати таъмир аст!</b>\n\nБаъдтар кӯшиш кунед. 🙏"
                if isinstance(event, Message):
                    await event.answer(text, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🔧 Бот дар ҳолати таъмир аст!", show_alert=True)
                return

            # 2. Тафтиши бан
            try:
                banned, reason = await db.is_banned(user_id)
            except Exception as e:
                logger.error(f"is_banned хато барои {user_id} (fail-open, банд ҳисоб нашуд): {e}")
                banned, reason = False, ""
            if banned:
                text = f"🚫 <b>Шумо банӣ шудаед!</b>\n\n📝 Сабаб: {reason or 'Сабаб нишон дода нашуд'}"
                if isinstance(event, Message):
                    await event.answer(text, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Шумо банӣ шудаед!", show_alert=True)
                return

            # 3. Тафтиши обуна
            allow = isinstance(event, CallbackQuery) and event.data == "check_sub"
            if not allow:
                try:
                    member = await bot.get_chat_member(config.CHANNEL_ID, user_id)
                    is_sub = member.status not in ("left", "kicked")
                except Exception as e:
                    logger.error(f"Санҷиши обуна барои {user_id} хато дод (fail-open): {e}")
                    is_sub = True

                if not is_sub:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📢 Обуна шавед", url=config.CHANNEL_URL)],
                        [InlineKeyboardButton(text="✅ Обуна шудам", callback_data="check_sub")],
                    ])
                    text = (
                        "⚠️ <b>Барои истифодаи бот аввал ба канал обуна шавед!</b>\n\n"
                        f"📢 {config.CHANNEL_ID}"
                    )
                    if isinstance(event, Message):
                        await event.answer(text, reply_markup=kb, parse_mode="HTML")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⚠️ Аввал обуна шавед!", show_alert=True)
                    return

        return await handler(event, data)


# ==================== БОТ ====================
# parse_mode дар ҳар паём алоҳида гузошта мешавад (бо ҳама версияи aiogram 3 кор мекунад)
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

BOT_USERNAME = config.BOT_USERNAME  # дар main() аз bot.get_me() дуруст карда мешавад

# Middleware
dp.message.middleware(SubscriptionMiddleware())
dp.callback_query.middleware(SubscriptionMiddleware())

# Роутерҳо
dp.include_router(admin_router)
dp.include_router(buy_router)
dp.include_router(minigames_router)
dp.include_router(autopay_router)

# Роутери ОХИРИН — паёмҳое, ки ҳељ ҳандлери дигар нагирифт.
# ҲАТМАН баъд аз ҳама дохил мешавад, вагарна ҳолатҳои FSM-и buy.py-ро мегирад.
fallback_router = Router()


# ==================== МЕНЮИ АСОСӢ ====================
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Бозиҳо",      callback_data="games_menu"),
         InlineKeyboardButton(text="✈️ Telegram",    callback_data="telegram_menu")],
        [InlineKeyboardButton(text="👤 Профил",      callback_data="profile_menu"),
         InlineKeyboardButton(text="🤝 Реферал",     callback_data="referral_menu")],
        [InlineKeyboardButton(text="⭐ Отзив",       url=config.REVIEW_CHANNEL_URL),
         InlineKeyboardButton(text="🆘 Поддержка",   url=config.SUPPORT_URL)],
        [InlineKeyboardButton(text="❓ Саволҳои маъмул", callback_data="faq"),
         InlineKeyboardButton(text="ℹ️ Маълумот",    callback_data="about")],
        [InlineKeyboardButton(text="🎁 Тӯҳфаи ройгон", callback_data="giveaway_info")],
    ])


@dp.callback_query(F.data == "giveaway_info")
async def giveaway_info(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")]
    ])
    every_n = int(await db.get_setting("giveaway_every_n") or "25")
    total = await db.count_confirmed_orders()
    position = total % every_n
    remaining = every_n - position if position else every_n
    pct = round(position / every_n * 100)
    bar = autopay._progress_bar(pct)

    product_line = ""
    product_id_str = await db.get_setting("giveaway_product_id")
    if product_id_str:
        product = await db.get_product(int(product_id_str))
        if product:
            label = product.get("label") or f"💎 {product['amount']}"
            product_line = f"🎁 Тӯҳфаи ҳозира: <b>{label}</b>\n\n"

    winner_line = ""
    winner = await db.get_last_giveaway_winner()
    if winner:
        display = f"@{winner['username']}" if winner.get("username") else esc(winner.get("full_name") or f"ID {winner['user_id']}")
        winner_line = f"🏆 Барандаи охирин: <b>{display}</b>\n"

    total_wins = await db.count_giveaway_wins()
    wins_line = f"🎉 То ҳол <b>{total_wins}</b> нафар тӯҳфа бурдаанд!\n" if total_wins else ""

    await _safe_edit(
        call,
        "🎁 <b>Тӯҳфаи ройгон — миннатдории мағоза</b>\n\n"
        "<b>Чӣ хел кор мекунад:</b>\n"
        "1️⃣ Шумо фармоиши <b>оддӣ</b> медиҳед\n"
        f"2️⃣ Мағоза <b>{every_n} фармоишро</b> ҷамъ мекунад\n"
        f"3️⃣ Бот аз ҳамон {every_n} харидор <b>ЯК нафарро тасодуфан</b> "
        "интихоб мекунад\n"
        "4️⃣ Ӯ маҳсулотро <b>БЕПУЛ</b> мегирад 🍀\n\n"
        "❗️ Дархост кардан лозим НЕСТ\n"
        "❗️ Пули иловагӣ додан лозим НЕСТ\n"
        "❗️ Ҳеҷ кас VIP нест — ҳама баробар\n\n"
        "Фақат харид кунед — боқиашро бот худаш мекунад.\n\n"
        f"{product_line}"
        f"📊 <code>{bar}</code>\n"
        f"{position} аз {every_n} фармоиш гузаштааст — боз <b>{remaining} фармоиш</b> монд!\n\n"
        f"{winner_line}"
        f"{wins_line}",
        kb
    )


# ==================== FAQ ====================
_FAQ_ITEMS = [
    ("⏱ Чанд вақт мегирад?",
     "Одатан алмоз/маҳсулот дар <b>1-5 дақиқа</b> баъд аз тасдиқи пардохт "
     "фиристода мешавад. Агар автопардохт (Душанбе Сити) бошад — то 30 сония."),
    ("✏️ Агар ID-и хато навишта бошам чӣ?",
     "Пеш аз тасдиқ бо админ тамос гиред. Баъд аз донат, маблағ бозгардонида "
     "намешавад — барои ҳамин ID-ро бодиққат санҷед."),
    ("💰 Пул баргардонида мешавад?",
     "Не, баъд аз донати муваффақ пул бозгардонида намешавад (ба ғайр аз "
     "хатои техникии мо). Агар пардохт кардед вале маҳсулот нарасид, ба "
     "дастгирӣ муроҷиат кунед."),
    ("💳 Кадом усулҳои пардохт ҳастанд?",
     "Душанбе Сити (автоматӣ), Алиф ва Эсхата. Ҳамаро дар вақти харид "
     "интихоб карда метавонед."),
    ("📸 Чек чӣ гуна фиристам?",
     "Баъд аз пардохт, скриншоти чекро (аз барномаи бонк) ба ҳамин чат "
     "фиристед. Бот худкор ба админ мефиристад."),
    ("⚠️ Пардохт кардам, вале бот тасдиқ накард — чӣ кунам?",
     "Каме сабр кунед (то 15-30 дақиқа барои фармоишҳои дастӣ). Агар боз ҳам "
     "тасдиқ нашуд, бо дастгирӣ тамос гиред ва скриншоти пардохтро нишон диҳед."),
    ("🎁 Тӯҳфаи ройгон чист?",
     "Ин тӯҳфаи миннатдории мағоза ба мизоҷон аст. Ҳар як фармоиши шумо "
     "шуморо ХУДКОР ба рӯйхат дохил мекунад — аз байни харидорони охирин "
     "як нафар тасодуфан интихоб мешавад ва тӯҳфаро БЕПУЛ мегирад.\n\n"
     "Дархост кардан, пули иловагӣ додан ё VIP шудан лозим НЕСТ — "
     "фақат харид кунед."),
]


def faq_text() -> str:
    lines = ["❓ <b>Саволҳои маъмул</b>\n"]
    for q, a in _FAQ_ITEMS:
        lines.append(f"<b>{q}</b>\n{a}\n")
    return "\n".join(lines)


@dp.callback_query(F.data == "faq")
async def show_faq(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆘 Ба дастгирӣ нависед", url=config.SUPPORT_URL)],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")],
    ])
    await _safe_edit(call, faq_text(), kb)


def profile_menu(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📋 Фармоишҳоям",  callback_data="my_orders")],
        [InlineKeyboardButton(text="🏆 Топ харидорон", callback_data="top_buyers")],
        [InlineKeyboardButton(text="🏅 Топ рефералдорон", callback_data="top_referrers")],
        [InlineKeyboardButton(text="💰 Пур кардани баланс", callback_data="topup_balance")],
        [InlineKeyboardButton(text="📜 Таърихи баланс", callback_data="balance_history")],
    ]
    rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def telegram_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars",   callback_data="buy_stars")],
        [InlineKeyboardButton(text="💎 Telegram Premium", callback_data="buy_premium")],
        [InlineKeyboardButton(text="🔙 Бозгашт",          callback_data="back_main")],
    ])


@dp.callback_query(F.data == "telegram_menu")
async def show_telegram_menu(call: CallbackQuery):
    await _safe_edit(
        call,
        "✈️ <b>Telegram</b>\n\nХизматро интихоб кунед:",
        telegram_menu()
    )


@dp.callback_query(F.data == "noop")
async def noop_callback(call: CallbackQuery):
    # Тугмаи бе амал — танҳо барои нишон додани матн дар leaderboard
    # (вақте корбар username надорад ва ЛС-кардан имконнопазир аст)
    await call.answer("Ин корбар username надорад", show_alert=False)


def games_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Free Fire СНГ",        callback_data="buy")],
        [InlineKeyboardButton(text="🔥 Free Fire Indonesia",  callback_data="buy_ffid")],
        [InlineKeyboardButton(text="🎮 PUBG Mobile",          callback_data="buy_pubg")],
        [InlineKeyboardButton(text="🔫 Standoff 2",           callback_data="buy_standoff")],
        [InlineKeyboardButton(text="🔙 Бозгашт",              callback_data="back_main")],
    ])


@dp.callback_query(F.data == "games_menu")
async def show_games_menu(call: CallbackQuery):
    await _safe_edit(
        call,
        "🎮 <b>Бозиҳо</b>\n\nБозиро интихоб кунед:",
        games_menu()
    )


# ==================== ПРОФИЛ ====================
@dp.callback_query(F.data == "profile_menu")
async def show_profile_menu(call: CallbackQuery):
    stats = await db.get_user_stats(call.from_user.id)
    total_spent = stats["total_spent"]
    balance = await db.get_referral_balance(call.from_user.id)

    text = (
        f"👤 <b>Профили шумо</b>\n\n"
        f"👋 Ном: <b>{esc(call.from_user.full_name)}</b>\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\n"
        f"📱 Username: {f'@{call.from_user.username}' if call.from_user.username else '—'}\n\n"
        f"📊 <b>Омори харид:</b>\n"
        f"✅ Харидҳои муваффақ: <b>{stats['total_orders']}</b>\n"
        f"💰 Маблағи умумии харид: <b>{total_spent:.2f} сомонӣ</b>\n"
        f"👛 Баланси шумо: <b>{balance:.2f} сомонӣ</b>\n\n"
        f"Аз меню интихоб кунед:"
    )

    await _safe_edit(call, text, profile_menu(call.from_user.id))


_TX_TYPE_LABELS = {
    "topup": "💰 Пуркунӣ",
    "purchase": "🛒 Харид аз баланс",
    "referral_reward": "🤝 Мукофоти реферралӣ",
    "refund": "↩️ Баргардонӣ",
    "admin_adjust": "🛠 Ивази админ",
}


@dp.callback_query(F.data == "balance_history")
async def show_balance_history(call: CallbackQuery):
    txs = await db.get_balance_transactions(call.from_user.id, limit=15)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="profile_menu")]
    ])
    if not txs:
        await _safe_edit(
            call,
            "📜 <b>Таърихи баланс</b>\n\nҲоло ҳељ амале нест.",
            kb
        )
        return

    lines = []
    for tx in txs:
        amount = float(tx["amount"])
        sign = "+" if amount >= 0 else ""
        label = _TX_TYPE_LABELS.get(tx["tx_type"], tx["tx_type"])
        dt = tx["created_at"].strftime("%d.%m %H:%M") if tx.get("created_at") else "—"
        order_part = f" (#{tx['order_id']})" if tx.get("order_id") else ""
        lines.append(
            f"{label}{order_part}: {sign}{amount:.2f} сом → {float(tx['balance_after']):.2f} сом\n"
            f"   🕒 {dt}"
        )
    text = "📜 <b>Таърихи баланс</b> (охирин 15)\n\n" + "\n\n".join(lines)
    await _safe_edit(call, text, kb)


# ==================== РЕФЕРАЛ ====================
def referral_menu(user_id: int) -> InlineKeyboardMarkup:
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    share_text = "🎁 Ба ин бот ворид шавед ва харид кунед — фурӯши босифати алмаз ва дигар хизматҳо!"
    share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(share_text)}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Ба дӯстон фиристодан", url=share_url)],
        [InlineKeyboardButton(text="👥 Рефералҳои ман", callback_data="referral_subusers")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")],
    ])


@dp.callback_query(F.data == "referral_menu")
async def show_referral_menu(call: CallbackQuery):
    balance = await db.get_referral_balance(call.from_user.id)
    count = await db.get_referral_count(call.from_user.id)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{call.from_user.id}"
    text = (
        f"🤝 <b>Барномаи рефералӣ</b>\n\n"
        f"🔗 Линки даъвати шумо:\n{link}\n\n"
        f"👥 Даъватшудагон: <b>{count} нафар</b>\n"
        f"💰 Баланс: <b>{balance:.2f} сомонӣ</b>\n\n"
        f"🎁 Барои ҳар як дӯсте, ки тавассути линки шумо ба бот ворид шуда, "
        f"харидро анҷом медиҳад ва он аз ҷониби админ тасдиқ мешавад, шумо "
        f"<b>{config.REFERRAL_PERCENT:.0f}%</b> аз маблағи хариди ӯро ҳамчун "
        f"бонус мегиред.\n\n"
        f"💳 Бонуси ҷамъшуда ба балансатон илова мешавад ва "
        f"метавонед онро барои пардохти харидҳо дар бот истифода баред."
    )
    await _safe_edit(call, text, referral_menu(call.from_user.id))


@dp.callback_query(F.data == "referral_subusers")
async def show_referral_subusers(call: CallbackQuery):
    subusers = await db.get_referral_subusers(call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="referral_menu")],
    ])
    if not subusers:
        await _safe_edit(
            call,
            "👥 <b>Рефералҳои шумо</b>\n\n"
            "Шумо ҳанӯз ягон дустро даъват накардаед.\n\n"
            "🔗 Линки даъватро аз саҳифаи «Реферал» нусхабардорӣ карда ба "
            "дустони худ фиристед!",
            kb
        )
        return
    lines = ["👥 <b>Рефералҳои шумо</b>\n"]
    total = 0.0
    for i, u in enumerate(subusers, 1):
        name = esc(u.get("full_name") or "—")
        username = f"@{u['username']}" if u.get("username") else f"ID {u['id']}"
        earned = u["earned"]
        total += earned
        lines.append(f"{i}. {name} ({username}) — <b>{earned:.2f} сомонӣ</b>")
    lines.append(f"\n💰 Ҷамъ овардаанд: <b>{total:.2f} сомонӣ</b>")
    text = "\n".join(lines)
    await _safe_edit(call, text, kb)


# ==================== ШАРТНОМА ====================
TERMS_TEXT = (
    "📜 <b>Шартномаи корбар</b>\n\n"
    "Пеш аз истифода лутфан шартҳоро хонед:\n\n"
    "1️⃣ <b>Синну сол:</b> Хизматҳо танҳо барои <b>18+</b>.\n\n"
    "2️⃣ <b>Донат:</b> Пас аз тасдиқи пардохт алмазҳо дар <b>1-5 дақиқа</b> фиристода мешаванд.\n\n"
    "3️⃣ <b>Бозгашти пул:</b> Пас аз донат пул бозгардонида <b>намешавад</b>.\n\n"
    "4️⃣ <b>ID дуруст:</b> Масъулияти дурустии ID бар уҳдаи корбар аст.\n\n"
    "✅ Бо пахши «Қабул мекунам» шумо ба ҳама шартҳо розӣ мешавед."
)


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    # Корбарро сабт мекунем
    is_new = await db.add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or "",
    )
    # Агар корбар ВОҦЕАН нав бошад ва бо линки реферралӣ омада бошад
    # (/start ref_12345) — referrer-ро сабт мекунем
    if is_new and command.args and command.args.startswith("ref_"):
        ref_part = command.args[4:]
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            await db.set_referrer(message.from_user.id, referrer_id)
    # Шартнома қабул шудааст ё не?
    if not await db.is_terms_accepted(message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="accept_terms")],
        ])
        await message.answer(TERMS_TEXT, reply_markup=kb, parse_mode="HTML")
        return
    await _send_main(message)


def _welcome_text(user, greeted: bool = True) -> str:
    """Матни хушомадгуи кӯтоҳ — тафсилоти пурра дар тугмаи «ℹ️ Маълумот»."""
    hello = f"👋 Хуш омадед, <b>{esc(user.full_name)}</b>!\n\n" if greeted else f"👋 <b>{esc(user.full_name)}</b>\n\n"
    return (
        f"{hello}"
        f"🆔 ID-и шумо: <code>{user.id}</code>\n\n"
        f"⚡ Донати худкор — то 1 дақиқа!\n"
        f"🔒 Бехатар 100% · 💳 Пардохти осон\n\n"
        f"🎁 Баъзе харидорон ТАСОДУФАН тӯҳфаи ройгон мегиранд!\n\n"
        f"👇 Аз меню интихоб кунед:"
    )


_banner_cache = {"file_id": None, "bytes": None}


async def _send_main(message: Message):
    """Банер + матни хушомадгӯӣ + менюро дар ЯК паём мефиристад
    (банер як бор месозад, баъд file_id-ро такроран истифода мебарад —
    фавран, бе аз нав сохтан/боркунӣ)."""
    text = _welcome_text(message.from_user)
    kb = main_menu()
    try:
        from aiogram.types import BufferedInputFile
        if _banner_cache["file_id"]:
            await message.answer_photo(_banner_cache["file_id"], caption=text, reply_markup=kb, parse_mode="HTML")
            return
        if _banner_cache["bytes"] is None:
            import banner as _banner_mod
            _banner_cache["bytes"] = _banner_mod.generate_welcome_banner().read()
        photo = BufferedInputFile(_banner_cache["bytes"], filename="welcome.png")
        sent = await message.answer_photo(photo, caption=text, reply_markup=kb, parse_mode="HTML")
        if sent.photo:
            _banner_cache["file_id"] = sent.photo[-1].file_id
    except Exception as e:
        logger.error(f"Банер нафиристод: {e}")
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "accept_terms")
async def accept_terms(call: CallbackQuery):
    await db.accept_terms(call.from_user.id)
    # Ба канали лог (агар бошад)
    if config.LOG_CHANNEL_ID:
        try:
            username = f"@{call.from_user.username}" if call.from_user.username else "—"
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            ls_kb_rows = []
            if call.from_user.username:
                ls_kb_rows.append(
                    [InlineKeyboardButton(text="💬 ЛС ба корбар", url=f"https://t.me/{call.from_user.username}")]
                )
            ls_kb = InlineKeyboardMarkup(inline_keyboard=ls_kb_rows) if ls_kb_rows else None
            await call.bot.send_message(
                config.LOG_CHANNEL_ID,
                f"✅ <b>Корбари нав</b>\n\n"
                f"👤 {esc(call.from_user.full_name)}\n"
                f"🆔 <code>{call.from_user.id}</code>\n"
                f"📱 {username}\n"
                f"📜 Розигӣ: дод ✅",
                reply_markup=ls_kb,
                parse_mode="HTML"
            )
        except Exception:
            pass
    text = (
        f"✅ <b>Ташаккур! Шартнома қабул шуд.</b>\n\n"
        f"{_welcome_text(call.from_user)}"
    )
    await _safe_edit(call, text, main_menu())


# ==================== БОЗГАШТ БА МЕНЮ ====================
@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    await _safe_edit(call, _welcome_text(call.from_user, greeted=False), main_menu())


# ==================== ТАФТИШИ ОБУНА ====================
@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    try:
        member = await call.bot.get_chat_member(config.CHANNEL_ID, call.from_user.id)
        is_sub = member.status not in ("left", "kicked")
    except Exception:
        is_sub = True
    if is_sub:
        try:
            await call.message.delete()
        except Exception:
            pass
        await _send_main_from_call(call)
    else:
        await call.answer("❌ Шумо ҳанӯз обуна нашудед!", show_alert=True)


async def _send_main_from_call(call: CallbackQuery):
    text = f"✅ <b>Обуна тасдиқ шуд!</b>\n\n{_welcome_text(call.from_user)}"
    await call.bot.send_message(call.from_user.id, text, reply_markup=main_menu(), parse_mode="HTML")


# ==================== ФАРМОИШҲОЯМ ====================
@dp.callback_query(F.data == "my_orders")
async def my_orders(call: CallbackQuery):
    orders = await db.get_user_orders(call.from_user.id, limit=10)
    if not orders:
        await call.answer("❌ Шумо ҳанӯз фармоиш надоред!", show_alert=True)
        return
    status_emoji = {
        "confirmed": "✅",
        "rejected": "❌",
        "failed": "⚠️",
        "paid": "💳",
        "donating": "🚀",
        "pending": "⏳",
    }
    text = "📋 <b>Фармоишҳои охирини шумо:</b>\n\n"
    for o in orders:
        emoji = status_emoji.get(o["status"], "❓")
        text += f"{emoji} #{o['id']} | {o['label']} | {o['price']:.2f} сом\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")]
    ])
    await _safe_edit(call, text, kb)


# ==================== МАЪЛУМОТ ====================
@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    stats = await db.get_stats()
    text = (
        "ℹ️ <b>Дар бораи Dilovar FF Bot</b>\n\n"
        "🤖 Боти расмии фурӯши хизматҳои рақамӣ дар Тоҷикистон\n\n"
        "🎮 <b>Хизматҳо:</b> Free Fire, FF Indonesia, PUBG Mobile, Telegram Stars/Premium\n\n"
        "🚀 <b>Афзалиятҳо:</b> донати худкор, суръати баланд (то 1 дақ.), бехатар 100%, рейтинги харидорон\n\n"
        "💳 <b>Пардохт:</b> Душанбе Сити, Алиф, Эсхата\n\n"
        f"📊 Корбарон: <b>{stats['users']}</b> | Фармоишҳо: <b>{stats['orders']}</b>\n\n"
        f"📞 {config.SUPPORT_USERNAME} | 📢 {config.CHANNEL_URL}\n"
        f"⭐ Отзивҳо: https://t.me/otziv_dilovar"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")]
    ])
    await _safe_edit(call, text, kb)


# ==================== ЁРИРАСОН ====================
async def _safe_edit(call: CallbackQuery, text: str, kb):
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await call.bot.send_message(call.from_user.id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"_safe_edit хато: {e}")



# ==================== ТОП ХАРИДОРОН ====================
@dp.callback_query(F.data == "top_buyers")
async def top_buyers(call: CallbackQuery):
    admin_ids = config.ADMIN_IDS or [0]
    placeholders = ",".join(["%s"] * len(admin_ids))
    reset_at = await db.get_leaderboard_reset_at()
    date_filter = "AND o.created_at >= %s" if reset_at else ""
    date_params = (reset_at,) if reset_at else ()

    async with db.pool.acquire() as conn:
        async with conn.cursor(db.aiomysql.DictCursor) as cur:
            await cur.execute(f"""
                SELECT o.user_id, u.username, u.full_name,
                       COUNT(*) as total_orders,
                       SUM(o.price) as total_spent
                FROM orders o
                LEFT JOIN users u ON u.id = o.user_id
                WHERE o.status = 'confirmed' AND o.is_balance_topup = 0
                AND o.user_id NOT IN ({placeholders})
                {date_filter}
                GROUP BY o.user_id
                ORDER BY total_spent DESC
                LIMIT 10
            """, tuple(admin_ids) + date_params)
            rows = await cur.fetchall()

    if not rows:
        await call.answer("Ҳоло харидор нест!", show_alert=True)
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 <b>Топ харидорон</b>\n\n"

    user_place = None
    for i, row in enumerate(rows):
        username = f"@{row['username']}" if row.get('username') else esc(row.get('full_name', '—'))
        spent = float(row['total_spent'] or 0)
        orders = row['total_orders']
        text += f"{medals[i]} {username} — {orders} харид · {spent:.0f} сом\n"
        if row['user_id'] == call.from_user.id:
            user_place = i + 1

    # Ҷои корбар (танҳо барои ғайри-админ)
    if call.from_user.id in config.ADMIN_IDS:
        pass  # Админ дар рейтинг нест — ҳеҷ чиз нависем
    elif user_place:
        text += f"\n👤 <b>Шумо {medals[user_place-1]} ҷой</b>"
    else:
        # Ҷои корбарро ҳисоб кунем (бе префикси 'o.' дар ин query)
        plain_date_filter = "AND created_at >= %s" if reset_at else ""
        async with db.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    SELECT COUNT(DISTINCT user_id) + 1 as place
                    FROM (
                        SELECT user_id, SUM(price) as total
                        FROM orders
                        WHERE status='confirmed' AND is_balance_topup=0 AND user_id NOT IN ({placeholders})
                        {plain_date_filter}
                        GROUP BY user_id
                        HAVING total > (
                            SELECT COALESCE(SUM(price), 0)
                            FROM orders
                            WHERE status='confirmed' AND is_balance_topup=0 AND user_id=%s
                            {plain_date_filter}
                        )
                    ) t
                """, tuple(admin_ids) + date_params + (call.from_user.id,) + date_params)
                row = await cur.fetchone()
                place = row[0] if row else "?"
        text += f"\n👤 <b>Шумо {place}-ҷой</b>"

    kb_buttons = []
    # Танҳо барои админ — тугмаҳои ЛС ба ҳар харидор
    if call.from_user.id in config.ADMIN_IDS:
        for i, row in enumerate(rows):
            username_val = row.get('username')
            display = f"@{username_val}" if username_val else row.get('full_name', f"ID {row['user_id']}")
            if username_val:
                kb_buttons.append([InlineKeyboardButton(text=f"{medals[i]} {display}", url=f"https://t.me/{username_val}")])
            else:
                # Бе username, тугмаи ЛС намегузорем (tg://user?id= боиси
                # BUTTON_USER_PRIVACY_RESTRICTED мешавад) — танҳо матн нишон медиҳем
                kb_buttons.append([InlineKeyboardButton(text=f"{medals[i]} {display}", callback_data="noop")])

    kb_buttons.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await _safe_edit(call, text, kb)


@dp.callback_query(F.data == "top_referrers")
async def top_referrers(call: CallbackQuery):
    async with db.pool.acquire() as conn:
        async with conn.cursor(db.aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT id as user_id, username, full_name,
                       (SELECT COUNT(*) FROM users u2 WHERE u2.referrer_id = u1.id) as ref_count,
                       referral_balance
                FROM users u1
                WHERE (SELECT COUNT(*) FROM users u2 WHERE u2.referrer_id = u1.id) > 0
                ORDER BY ref_count DESC, referral_balance DESC
                LIMIT 10
            """)
            rows = await cur.fetchall()

    if not rows:
        await call.answer("Ҳоло рефералдор нест!", show_alert=True)
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏅 <b>Топ рефералдорон</b>\n\n"

    user_place = None
    for i, row in enumerate(rows):
        username = f"@{row['username']}" if row.get('username') else esc(row.get('full_name', '—'))
        ref_count = row['ref_count']
        text += f"{medals[i]} {username} — {ref_count} дӯст даъват кардааст\n"
        if row['user_id'] == call.from_user.id:
            user_place = i + 1

    if user_place:
        text += f"\n👤 <b>Шумо {medals[user_place-1]} ҷой</b>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="back_main")],
    ])
    await _safe_edit(call, text, kb)


# ==================== ГУЗОРИШИ РӾЗОНА (соати 00:00) ====================
_PM_LABELS_REPORT = {
    "dushanbe_city": "🏙 Душанбе Сити",
    "alif": "💳 Алиф",
    "eskhata": "🏦 Эсхата",
    "referral_balance": "💰 Аз баланс",
    "giveaway": "🎁 Тӯҳфаи ройгон",
}

_WEEKDAY_SHORT_TJ = ["Дш", "Сш", "Чш", "Пш", "Ҷм", "Шн", "Яш"]


def _sparkline(values):
    blocks = "▁▂▃▄▅▆▇█"
    max_v = max(values) if values else 0
    if max_v <= 0:
        return blocks[0] * len(values)
    return "".join(blocks[min(7, int(v / max_v * 7))] for v in values)


def _format_daily_report(stats: dict) -> str:
    def _fmt_change(pct):
        if pct > 0:
            return f"📈 +{pct:.1f}%"
        elif pct < 0:
            return f"📉 {pct:.1f}%"
        return "➖ 0%"

    change_str = _fmt_change(stats["change_percent"])
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
        f"   {_PM_LABELS_REPORT.get(p['method'], p['method'])}: {p['confirmed']} ✅ / {p['rejected']} ❌"
        for p in stats.get("payment_breakdown", [])
    ) or "   —"

    confirmed_today = stats["confirmed_today"]
    with_cost = stats.get("orders_with_cost_today", 0)
    coverage = f" (аз {with_cost}/{confirmed_today} фармоиш)" if confirmed_today else ""
    margin_pct = stats.get("profit_margin_percent")
    margin_line = f" — <b>{margin_pct:.1f}%</b> аз арзиши харид" if margin_pct is not None else ""

    return (
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
        f"👤 <b>Гурӯҳбандии харидорон (ҳама вақт):</b>\n"
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


def _format_weekly_report(stats: dict) -> str:
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

    return (
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


TJ_TZ = ZoneInfo("Asia/Dushanbe")


async def _daily_report_loop(bot: Bot):
    """
    Ҳар шаб дар соати 23:59 (вақти Тоҷикистон) гузоришро ба админ мефиристад.
    ДИҚҚАТ: маҳз 23:59, НА 00:00 — агар дар 00:00 фиристода шавад, рӯз аллакай
    иваз шудааст ва get_daily_report() омори РӮЗИ НАВРО (ҳанӯз холӣ, ҳама 0)
    мешуморад, на рӯзи гузаштаро.
    """
    while True:
        now = datetime.now(TJ_TZ)
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            stats = await db.get_daily_report()
            text = _format_daily_report(stats)
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Гузориши шабона ба админ {admin_id} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар сохтани гузориши шабона: {e}")


async def _weekly_report_loop(bot: Bot):
    """Ҳар якшанбе дар соати 23:59 (вақти Тоҷикистон) гузориши ҳафтаинаро мефиристад."""
    while True:
        now = datetime.now(TJ_TZ)
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        days_until_sunday = (6 - target.weekday()) % 7
        target += timedelta(days=days_until_sunday)
        if target <= now:
            target += timedelta(days=7)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            stats = await db.get_weekly_report()
            text = _format_weekly_report(stats)
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Гузориши ҳафтаина ба админ {admin_id} нарасид: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар сохтани гузориши ҳафтаина: {e}")


# ==================== BACKUP-И ШАБОНАИ БАЗА ====================
async def _run_backup_and_send(bot: Bot):
    """mysqldump мегирад, фишурда (gzip) мекунад ва ба ҳамаи админҳо
    ҳамчун файл мефиристад. Файлҳои муваққатӣ дар охир нест мешаванд."""
    import subprocess
    import gzip
    import tempfile
    import os

    date_str = datetime.now(TJ_TZ).strftime("%Y-%m-%d")
    fd, sql_path = tempfile.mkstemp(suffix=".sql")
    os.close(fd)
    gz_path = sql_path + ".gz"
    try:
        env = os.environ.copy()
        env["MYSQL_PWD"] = config.DB_PASSWORD  # то parolь дар "ps aux" намоён нашавад
        cmd = [
            "mysqldump",
            f"-h{config.DB_HOST}",
            f"-P{config.DB_PORT}",
            f"-u{config.DB_USER}",
            "--single-transaction",
            config.DB_NAME,
        ]
        with open(sql_path, "wb") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=180, env=env)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="ignore")[:500])

        with open(sql_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            f_out.writelines(f_in)

        size_mb = os.path.getsize(gz_path) / (1024 * 1024)
        if size_mb > 45:
            raise RuntimeError(f"Файли backup хеле калон аст: {size_mb:.1f}MB (лимити Telegram 50MB)")

        doc = FSInputFile(gz_path, filename=f"backup_{date_str}.sql.gz")
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_document(
                    admin_id, doc,
                    caption=f"🗄 <b>Backup-и база — {date_str}</b>\n({size_mb:.2f} MB)",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Backup ба админ {admin_id} нарасид: {e}")
    finally:
        for p in (sql_path, gz_path):
            try:
                os.remove(p)
            except Exception:
                pass


async def _backup_loop(bot: Bot):
    """Ҳар шаб дар соати 00:30 (вақти Тоҷикистон, баъд аз гузориши
    шабона) backup-и пурраи базаро ба ҳамаи админҳо мефиристад."""
    while True:
        now = datetime.now(TJ_TZ)
        next_time = (now + timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)
        wait_seconds = (next_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        try:
            await _run_backup_and_send(bot)
        except Exception as e:
            logger.error(f"Backup-и шабонаи база нашуд: {e}")
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ <b>Backup-и шабонаи база НАШУД!</b>\n\nХато: {esc_err(e)}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass


def esc_err(e) -> str:
    import html
    return html.escape(str(e)[:300])


async def _notify_admins_summary(bot: Bot, text: str):
    """Ба ADMIN_IDS ЯК хабари хулосавӣ мефиристад (на алоҳида барои ҳар мизоҷ — то флуд нашавад)."""
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"🔔 {text}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Хабари хулосавӣ ба админ {admin_id} нарасид: {e}")


# Ҳар давра (15 дақ) аз ҳар гурӯҳ на бештар аз ин миқдор коркард мешавад —
# то агар лупа муддате қатъ буда бошад (масалан хостинг афтод) ва
# бекфони калон ҷамъ шуда бошад, ҳама якбора нашаванд (флуди огоҳиҳо ба
# садҳо мизоҷ якбора), балки дар якчанд давра паҳн шаванд.
REENGAGEMENT_BATCH_LIMIT = 15


async def _reengagement_loop(bot: Bot):
    """
    Ҳар 15 дақиқа корбаронеро санҷад, ки бот тарк кардаанд (ё харидро
    тамом накардаанд), ва ёдоварии мувофиқ мефиристад. Ҳар намуди
    ёдоварӣ барои ҲАР корбар фақат 1 БОР фиристода мешавад. Ба админ як
    ХУЛОСАИ ягона барои ҳар давра фиристода мешавад (на паёми алоҳида
    барои ҳар мизоҷ — то флуд нашавад).
    """
    while True:
        await asyncio.sleep(15 * 60)  # 15 дақиқа

        # ---- Гурӯҳи 1: /start зад, 1 соат ҲЕЧ order ----
        try:
            users = (await db.get_users_no_order_1h())[:REENGAGEMENT_BATCH_LIMIT]
            sent = 0
            for u in users:
                try:
                    await db.mark_reminder_noorder_sent(u["id"])
                    await bot.send_message(
                        u["id"],
                        "👋 <b>Шумо ботро кушодед, аммо то ҳол ягон фармоиш надодаед.</b>\n\n"
                        "❓ Агар чизе нофаҳмо бошад ё ҳангоми истифодаи бот мушкилоте "
                        "пайдо шуда бошад, метавонед ба админ муроҷиат кунед.\n\n"
                        f"📩 Админ: {config.SUPPORT_USERNAME}\n"
                        f"📢 Канали хабарҳои бот: {config.CHANNEL_URL}\n"
                        "⭐️ Отзив ва фикру мулоҳизаҳои муштариён: https://t.me/otziv_dilovar\n\n"
                        "😊 Мо омодаем ба ҳамаи саволҳои шумо ҷавоб диҳем ва дар "
                        "ҳалли мушкилот кумак расонем.",
                        parse_mode="HTML"
                    )
                    sent += 1
                except Exception as e:
                    logger.error(f"Ёдоварии бе-order ба {u['id']} нарасид: {e}")
            if sent:
                await _notify_admins_summary(bot, f"👋 Ёдоварии бе-фармоиш ба {sent} мизоҷ фиристода шуд")
        except Exception as e:
            logger.error(f"Хатогӣ дар санҷиши гурӯҳи бе-order: {e}")

        # ---- Гурӯҳи 2: мизоҷони хомӯшшуда (14+ рӯз бе харид) — тахфифи баргардонӣ -3% ----
        try:
            users = (await db.get_dormant_customers_for_winback())[:REENGAGEMENT_BATCH_LIMIT]
            sent = 0
            for u in users:
                try:
                    await db.mark_winback_sent(u["id"])
                    await bot.send_message(
                        u["id"],
                        "🎁 <b>Мо шуморо пазмон шудем!</b>\n\n"
                        "Шумо дер боз назди мо наомадед. Барои шумо як "
                        "тӯҳфаи хурд гузоштем — ба хариди навбатии шумо "
                        "<b>-3% тахфиф</b> худкор ҳисоб карда мешавад "
                        "(як маротибагӣ).\n\n"
                        "🛒 Барои истифода — танҳо фармоиш диҳед, тахфиф "
                        "худаш дар нархи пардохт ҳисоб мешавад.",
                        parse_mode="HTML"
                    )
                    sent += 1
                except Exception as e:
                    logger.error(f"Паёми баргардонӣ ба {u['id']} нарасид: {e}")
            if sent:
                await _notify_admins_summary(bot, f"🎁 Тахфифи баргардонии -3% ба {sent} мизоҷи хомӯшшуда фиристода шуд")
        except Exception as e:
            logger.error(f"Хатогӣ дар санҷиши гурӯҳи мизоҷони хомӯшшуда: {e}")


# ==================== ЁДОВАРӢ БАРОИ ФАРМОИШҲОИ ДАСТИИ ДЕРМОНДА ====================
async def _stale_paid_orders_loop(bot: Bot):
    """
    Ҳар 5 дақиқа фармоишҳои дастиро (Алиф/Эсхата) санҷад, ки чек фиристодаанд
    вале зиёда аз 20 дақиқа админ тасдиқ/рад накардааст. Ба мизоҷ узр
    мефиристад. Ба админ ЧИЗЕ ФИРИСТОДА НАМЕШАВАД — фармоиш танҳо дар
    "📋 Фармоишҳои интизорӣ" (a_pending_orders) намоён аст, то худи
    админ тугмаро пахш кунад (бе спам дар чат). Ҳар фармоиш фақат ЯК
    бор ёдоварӣ ба мизоҷ мегирад.
    """
    while True:
        await asyncio.sleep(5 * 60)
        try:
            for order in await db.get_stale_paid_orders(minutes=20):
                order_id = order["id"]
                try:
                    await db.mark_stale_reminder_sent(order_id)
                    try:
                        await bot.send_message(
                            order["user_id"],
                            f"⏳ <b>Узр мехоҳем!</b>\n\n"
                            f"Фармоиши шумо #{order_id} ҳанӯз дар ҷараёни тасдиқ аст — "
                            f"каме дертар шуд. Мо дар ҳоли ҳали он ҳастем, лутфан сабр кунед.\n\n"
                            f"Агар савол дошта бошед: {config.SUPPORT_USERNAME}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Ёдоварии дермондагӣ ба мизоҷи {order['user_id']} нарасид: {e}")
                except Exception as e:
                    logger.error(f"Коркарди ёдоварии фармоиши #{order_id} нашуд: {e}")
        except Exception as e:
            logger.error(f"Хатогӣ дар давраи ёдоварии фармоишҳои дермонда: {e}")


# ==================== ОҒОЗ ====================
# ==================== ҶАВОБИ ХУДКОР БА САВОЛИ МИЗОҶ ====================
# Вақте мизоҷ дар вақти интизорӣ чизе менависад ("пулам чӣ шуд?", "алмос
# наомад" ва ғ.), бот ХУДАШ ҳолати фармоиши охиринашро мегӯяд — то соҳиб
# ба ҳар як савол ҷавоб надиҳад.
_ORDER_STATUS_REPLY = {
    "awaiting_autopay": (
        "⏳ Мо ҳанӯз <b>расми чеки</b> шуморо нагирифтем.\n"
        "Пулро фиристодед? Пас расми чекро ба ҳамин ҷо фиристед — "
        "фармоиш худкор идома меёбад."
    ),
    "autopay_search": (
        "🔍 Чеки шумо қабул шуд — ҳоло пардохти шуморо меҷӯем.\n"
        "Одатан 1-5 дақиқа мегирад. Ҳамин ки ёфт шавад, донат "
        "ХУДКОР оғоз мешавад."
    ),
    "paid": (
        "✅ Пардохти шумо қабул шудааст — фармоиш дар навбати "
        "тасдиқ аст.\nБа зудӣ иҷро мешавад."
    ),
    "donating": (
        "🚀 Донати шумо АЙНИ ҲОЛ дар ҷараён аст!\n"
        "Одатан 1-3 дақиқа мегирад — каме сабр кунед."
    ),
    "failed": (
        "⏳ Фармоиши шумо каме дертар мерасад.\n"
        "Бот ҳар чанд дақиқа ҳолати онро месанҷад ва ҳамин ки тайёр "
        "шавад, ба шумо хабар медиҳад. <b>Пулатон бехатар аст</b> — "
        "ҳељ ҷо гум намешавад. 🙏"
    ),
}


# Пуркунии баланс донат нест — матни худро дорад
_TOPUP_STATUS_REPLY = {
    "awaiting_autopay": (
        "⏳ Мо ҳанӯз <b>расми чеки</b> шуморо нагирифтем.\n"
        "Пулро фиристодед? Пас расми чекро ба ҳамин ҷо фиристед — "
        "баланс худкор пур мешавад."
    ),
    "autopay_search": (
        "🔍 Чеки шумо қабул шуд — ҳоло пардохти шуморо меҷӯем.\n"
        "Одатан 1-5 дақиқа мегирад. Ҳамин ки ёфт шавад, маблағ "
        "ХУДКОР ба балансатон гузошта мешавад."
    ),
    "paid": (
        "✅ Пардохти шумо қабул шудааст — дар навбати тасдиқ аст.\n"
        "Ба зудӣ ба балансатон гузошта мешавад."
    ),
    "donating": "🔄 Пуркунии баланси шумо айни ҳол коркард шуда истодааст.",
    "failed": (
        "⏳ Пуркунии баланси шумо каме дертар анҷом меёбад.\n"
        "<b>Пулатон бехатар аст</b> — ҳељ ҷо гум намешавад. 🙏"
    ),
}


@fallback_router.message(F.chat.type == "private", F.text)
async def auto_answer_status(message: Message):
    """
    Паёме, ки ҳељ ҳандлери дигар нагирифт. Агар корбар фармоиши «зинда»
    дошта бошад — ҳолати онро худкор мегӯем. Вагарна ба менюи асосӣ
    равона мекунем.
    """
    try:
        order = await db.get_active_order_for_user(message.from_user.id)
    except Exception as e:
        logger.error(f"auto_answer_status: {e}")
        order = None

    if not order:
        await message.answer(
            "👋 Барои харид тугмаҳои поёнро истифода баред:",
            reply_markup=main_menu(), parse_mode="HTML"
        )
        return

    table = _TOPUP_STATUS_REPLY if order.get("is_balance_topup") else _ORDER_STATUS_REPLY
    status_text = table.get(
        order["status"],
        "ℹ️ Фармоиши шумо дар коркард аст."
    )
    created_at = order.get("created_at")
    age_line = ""
    if created_at:
        age_min = int((datetime.now() - created_at).total_seconds() / 60)
        age_line = f"⏱ {age_min} дақиқа пеш сохта шудааст\n"

    what = "💰 Пуркунии баланс" if order.get("is_balance_topup") else (order.get("label") or "—")
    await message.answer(
        f"📦 <b>Фармоиши охирини шумо: #{order['id']}</b>\n"
        f"🎁 {what}\n"
        f"💵 {float(order['price']):.2f} сомонӣ\n"
        f"{age_line}\n"
        f"{status_text}\n\n"
        f"❓ Агар савол дошта бошед, ба дастгирӣ нависед:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Фармоишҳоям", callback_data="my_orders")],
            [InlineKeyboardButton(text="🆘 Дастгирӣ", url=config.SUPPORT_URL)],
            [InlineKeyboardButton(text="🏠 Менюи асосӣ", callback_data="back_main")],
        ]),
        parse_mode="HTML"
    )


async def main():
    global BOT_USERNAME
    await db.create_pool()
    await db.init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    # Роутери охирин — баъд аз ҳама, то ҳолатҳои FSM-ро нагирад
    dp.include_router(fallback_router)
    logger.info(f"✅ Бот омода аст! @{BOT_USERNAME}")
    # Webhook-ро тоза мекунем (то TelegramConflictError нашавад)
    await bot.delete_webhook(drop_pending_updates=True)
    # Гузориши шабона дар соати 00:00 — дар background, бе халал ба polling
    asyncio.create_task(_daily_report_loop(bot))
    # Гузориши ҳафтаина — ҳар якшанбе соати 23:59
    asyncio.create_task(_weekly_report_loop(bot))
    # Ёдоварии баргардонидани мизоҷ — ҳар 15 дақиқа
    asyncio.create_task(_reengagement_loop(bot))
    # Автопардохт — бастани фармоишҳои мӯҳлаташон гузашта
    asyncio.create_task(autopay.expiry_loop(bot))
    # Backup-и шабонаи база — соати 00:30
    asyncio.create_task(_backup_loop(bot))
    # Ёдоварӣ барои фармоишҳои дастии дермонда — ҳар 5 дақиқа
    asyncio.create_task(_stale_paid_orders_loop(bot))
    # Тӯҳфаи тасодуфӣ — ҳар N фармоиши тасдиқшуда
    asyncio.create_task(autopay.giveaway_loop(bot))
    # Тафтишгари худкори фармоишҳои "овезон" — ҳар 3 дақиқа (танҳо мехонад)
    asyncio.create_task(autopay.recheck_loop(bot))
    # Ҷамъбасти субҳ баъд аз реҷаи хомӯшии шабона
    asyncio.create_task(autopay.quiet_digest_loop(bot))
    # Худкор бастани фармоишҳои фаромӯшшуда — ҳар рӯз соати 09:00
    asyncio.create_task(autopay.auto_archive_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
