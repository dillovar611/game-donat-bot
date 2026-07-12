"""
Танзимоти бот — env.py переменнаҳоро сохт.
"""
import env  # noqa: F401
import json
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

# ==================== MooGold API (провайдери эҳтиётӣ — fallback) ====================
# Агар FazerCards фармоишро рад кунад ё дастрас набошад, бот худкор ба
# MooGold мегузарад (донат гум намешавад).
# Барои фаъол кардан:
#   1. Ба менеҷери MooGold муроҷиат кунед (сомонаи расмии moogold.com,
#      бахши reseller/API) — USER_ID, PARTNER_ID ва SECRET гиред.
#   2. Дар панели MooGold маҳсулоти Free Fire (СНГ)-ро ёбед — барои ҳар
#      як миқдори алмос category ва product-id мебошад.
#   3. MOOGOLD_PRODUCT_MAP-ро пур кунед: калид = offer_id-и ҳамон
#      маҳсулот дар FazerCards (ҳамон ки дар буи маҳсулот истифода
#      мешавад), қимат = {"category": "...", "product_id": "..."}.
#      Формат дар env.py: JSON-и як сатрӣ.
#      Мисол:
#      MOOGOLD_PRODUCT_MAP={"ff_110":{"category":"123","product_id":"456"}}
#   Агар барои як offer_id дар харита чизе набошад — fallback барои
#   ҳамон маҳсулот кор намекунад (танҳо FazerCards), хатогӣ дар лог сабт мешавад.
MOOGOLD_USER_ID = os.getenv("MOOGOLD_USER_ID", "")
MOOGOLD_PARTNER_ID = os.getenv("MOOGOLD_PARTNER_ID", "")
MOOGOLD_SECRET = os.getenv("MOOGOLD_SECRET", "")
try:
    MOOGOLD_PRODUCT_MAP = json.loads(os.getenv("MOOGOLD_PRODUCT_MAP", "{}") or "{}")
except Exception:
    MOOGOLD_PRODUCT_MAP = {}

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
