"""
Эмоҷиҳои премиуми (custom emoji) Телеграм.

Телеграм ба ботҳо иҷозат медиҳад эмоҷии тайёри худро тавассути теги
<tg-emoji emoji-id="..."> дар паёмҳои parse_mode="HTML" фиристанд.
Ҳамаи фойдабарандаҳо (ҳатто бе Премиум) онро мебинанд.

МУҲИМ: ин тегро ТАНҲО дар паёмҳое истифода бояд кард, ки бо
parse_mode="HTML" фиристода мешаванд. Дар расми чек (Pillow) ё паёми
оддӣ кор намекунад — теги хом намоиш дода мешавад.

Барои ҳар эмоҷӣ як "fallback"-и оддӣ мемонем — агар бастаи эмоҷӣ
дастнорас шавад, ҳамон эмоҷии оддӣ намоиш дода мешавад.
"""

# ── Кодҳо (custom_emoji_id) — аз санҷиши админ гирифта шуд ──
CARD_DC   = "5355246429046589391"   # 💳 Душанбе Сити (ДС)
CARD_ALIF = "5354973638493752848"   # 💳 Алиф
CHECK     = "5206607081334906820"   # ✔️
CROSS     = "5210952531676504517"   # ❌
FIRE      = "5424972470023104089"   # 🔥
DIAMOND   = "5427168083074628963"   # 💎
CROWN     = "5217822164362739968"   # 👑
SPARKLES  = "5325547803936572038"   # ✨
MONEY     = "5409048419211682843"   # 💵
PARTY     = "5461151367559141950"   # 🎉
BAG       = "5406683434124859552"   # 🛍
BELL      = "5458603043203327669"   # 🔔
HOME      = "5416041192905265756"   # 🏠
GAME      = "5361741454685256344"   # 🎮
HUNDRED   = "5341498088408234504"   # 💯
GREEN     = "5416081784641168838"   # 🟢
RED       = "5411225014148014586"   # 🔴
STAR      = "5438496463044752972"   # ⭐️
GIFT      = "5461151367559141950"   # 🎉 (ҳамчун тӯҳфа/шодӣ)
ID_ICON   = "5305474651508468012"   # 🆔
LOCK      = "5296369303661067030"   # 🔒
GEAR      = "5341715473882955310"   # ⚙️
BULB      = "5422439311196834318"   # 💡
SIREN     = "5395695537687123235"   # 🚨
GIFTBOX   = "5280558648576197139"   # 🎁
MONEYBAG  = "5278223861404421915"   # 💰
PRAY      = "5228878926306101271"   # 🙏
PHONE     = "5407025283456835913"   # 📱
CART      = "5312361253610475399"   # 🛒
PENCIL    = "5395444784611480792"   # ✏️
PERSON    = "5902335789798265487"   # 👤
SCROLL    = "6323096332579899122"   # 📜


def pe(emoji_id: str, fallback: str) -> str:
    """Теги tg-emoji-и премиум бо эмоҷии оддии эҳтиётӣ (fallback)."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


import re as _re

# Ҳамаи эмоҷиҳои оддӣ, ки бояд ба премиум табдил ёбанд.
_PREMIUM_MAP = {
    "✅": (CHECK, "✅"), "✔️": (CHECK, "✔️"), "❌": (CROSS, "❌"),
    "💎": (DIAMOND, "💎"), "🎉": (PARTY, "🎉"), "💵": (MONEY, "💵"),
    "⭐️": (STAR, "⭐️"), "⭐": (STAR, "⭐"), "🔥": (FIRE, "🔥"),
    "✨": (SPARKLES, "✨"), "👑": (CROWN, "👑"), "🛍": (BAG, "🛍"),
    "🔔": (BELL, "🔔"), "🏠": (HOME, "🏠"), "🎮": (GAME, "🎮"),
    "💯": (HUNDRED, "💯"), "🟢": (GREEN, "🟢"), "🔴": (RED, "🔴"),
    "🆔": (ID_ICON, "🆔"), "🔒": (LOCK, "🔒"), "⚙️": (GEAR, "⚙️"),
    "💡": (BULB, "💡"), "🚨": (SIREN, "🚨"),
    "🎁": (GIFTBOX, "🎁"), "💰": (MONEYBAG, "💰"), "🙏": (PRAY, "🙏"),
    "📱": (PHONE, "📱"), "🛒": (CART, "🛒"), "✏️": (PENCIL, "✏️"),
    "👤": (PERSON, "👤"), "📜": (SCROLL, "📜"),
}

_TG_SPAN = _re.compile(r"<tg-emoji\b.*?</tg-emoji>", _re.S)


def premiumize(text: str) -> str:
    """Ҳамаи эмоҷиҳои оддии дар _PREMIUM_MAP-ро ба эмоҷии премиуми
    аниматсионӣ табдил медиҳад. Эмоҷиҳое, ки аллакай <tg-emoji> шудаанд,
    даст нахӯрда мемонанд (то дубора коркард нашаванд)."""
    if not text:
        return text
    # Матнро аз рӯи тегҳои мавҷудаи tg-emoji ҷудо мекунем — то онҳоро
    # даст назанем.
    out = []
    last = 0
    for m in _TG_SPAN.finditer(text):
        seg = text[last:m.start()]
        for k, (eid, fb) in _PREMIUM_MAP.items():
            if k in seg:
                seg = seg.replace(k, f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>')
        out.append(seg)
        out.append(m.group(0))  # теги мавҷуда — бетағйир
        last = m.end()
    seg = text[last:]
    for k, (eid, fb) in _PREMIUM_MAP.items():
        if k in seg:
            seg = seg.replace(k, f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>')
    out.append(seg)
    return "".join(out)


def pm_label_html(method: str) -> str:
    """
    Нишонаи усули пардохт барои паёмҳои HTML — ДС ва Алиф бо эмоҷии
    премиуми аниматсионӣ. Барои усулҳои дигар матни оддӣ.
    """
    if method == "dushanbe_city":
        return f'{pe(CARD_DC, "💳")} Душанбе Сити'
    if method == "alif":
        return f'{pe(CARD_ALIF, "💳")} Алиф'
    if method == "eskhata":
        return "🏦 Эсхата"
    if method == "referral_balance":
        return "💰 Аз баланс"
    if method == "giveaway":
        return "🎁 Тӯҳфаи ройгон"
    return method or "—"
