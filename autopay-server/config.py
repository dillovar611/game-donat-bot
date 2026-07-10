"""
Танзимоти бот — env.py переменнаҳоро сохт.
"""
import env  # noqa: F401
import os

# ==================== TELEGRAM ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Dilovarffbot")

# ID-ҳои админ (бо вергул ҷудо: 123,456)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

# Канали обуна (корбар бояд обуна бошад)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@kanali_dilovar")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/kanali_dilovar")

# Username-и дастгирӣ
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@dilovar612")
SUPPORT_URL = f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"

# Канали хабарҳо (фармоиши нав, шартнома ва ғ.) — холӣ бошад, фиристода намешавад
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "")

# Гурӯҳи хусусии Telegram барои автопардохти DC Next (барномаи телефон
# notification-ро тавассути боти notifier ба ин гурӯҳ мефиристад)
NOTIFIER_CHAT_ID = int(os.getenv("NOTIFIER_CHAT_ID", "0") or 0)

# Канали отзив — холӣ бошад, фиристода намешавад
REVIEW_CHANNEL_ID = os.getenv("REVIEW_CHANNEL_ID", "")
REVIEW_CHANNEL_URL = os.getenv("REVIEW_CHANNEL_URL", "https://t.me/otziv_dilovar")
ROZIGIHO_CHANNEL = os.getenv("ROZIGIHO_CHANNEL", "@rozigiho")

# ==================== БАЗА ====================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")

# ==================== FazerCards API (донати худкор) ====================
FAZER_KEY = os.getenv("FAZER_KEY", "")
FAZER_BASE = os.getenv("FAZER_BASE", "https://api.fzr.cards/api/v2")

# Категорияҳои Free Fire CIS дар FazerCards
FF_CATEGORY_ORDER = os.getenv("FF_CATEGORY_ORDER", "free_fire_cis")      # барои фармоиш
FF_CATEGORY_VALIDATE = os.getenv("FF_CATEGORY_VALIDATE", "free_fire")    # барои тафтиши ID

# Free Fire Indonesia (FFID) — category-и фармоиш ва тафтиши ID метавонанд фарқ кунанд
FFID_CATEGORY_ORDER = os.getenv("FFID_CATEGORY_ORDER", "free_fire_id")
FFID_CATEGORY_VALIDATE = os.getenv("FFID_CATEGORY_VALIDATE", "free_fire_id")

# ==================== RapidAPI (номи аккаунти FF) ====================
# Калидҳо бо вергул ҷудо мешаванд
RAPIDAPI_KEYS = [k.strip() for k in os.getenv("RAPIDAPI_KEYS", "").split(",") if k.strip()]

# ==================== РЕКВИЗИТҲОИ ПАРДОХТ ====================
# Душанбе Сити
DC_NUMBER = os.getenv("DC_NUMBER", "")   # рақами корт ё ҳамён
DC_NAME = os.getenv("DC_NAME", "")       # номи соҳиб

# Алиф
ALIF_NUMBER = os.getenv("ALIF_NUMBER", "")
ALIF_NAME = os.getenv("ALIF_NAME", "")

# ==================== РЕФЕРРАЛ ====================
REFERRAL_PERCENT = float(os.getenv("REFERRAL_PERCENT", "5.0"))

# ==================== САТҲИ ХАРИДОР (ЛЕВЕЛ/ТАХФИФ) ====================
# (левел, ном, ҍадди ҍаҷми умумии хариди лозим, фоизи тахфиф)
LEVELS = [
    (1, "🥉 Харидори нав",     100,  1.0),
    (2, "🥈 Харидори фаъол",   300,  2.0),
    (3, "🥇 Харидори пешсаф",  700,  3.0),
    (4, "💎 Харидори моҍир",   1500, 4.0),
    (5, "👑 Харидори VIP",     3000, 5.0),
]


def get_level_for_spend(total_spent: float):
    """
    Бо ҷамъи харидҍои корбар, левели ҍозираро бармегардонад.
    Натиҷа: dict бо level, name, discount_percent, threshold,
    next_level (ё None агар левели охирин бошад), next_threshold,
    remaining (чанд сом то левели навбатӣ лозим аст).
    """
    current = None
    for level, name, threshold, discount in LEVELS:
        if total_spent >= threshold:
            current = (level, name, threshold, discount)
        else:
            break

    next_info = None
    for level, name, threshold, discount in LEVELS:
        if total_spent < threshold:
            next_info = (level, name, threshold, discount)
            break

    if current is None:
        return {
            "level": 0,
            "name": "Бе сатҳ",
            "discount_percent": 0.0,
            "threshold": 0,
            "next_level": next_info[0] if next_info else None,
            "next_name": next_info[1] if next_info else None,
            "next_threshold": next_info[2] if next_info else None,
            "remaining": (next_info[2] - total_spent) if next_info else 0,
        }

    return {
        "level": current[0],
        "name": current[1],
        "discount_percent": current[3],
        "threshold": current[2],
        "next_level": next_info[0] if next_info else None,
        "next_name": next_info[1] if next_info else None,
        "next_threshold": next_info[2] if next_info else None,
        "remaining": (next_info[2] - total_spent) if next_info else 0,
    }
