"""
API-и Free Fire:
  1. get_nickname()  — номи аккаунтро аз ID мегирад (FazerCards)
  2. auto_donate()   — донати худкор тавассути FazerCards, бо fallback ба
                        MooGold агар FazerCards ноком шавад
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid

import aiohttp

import config

logger = logging.getLogger(__name__)


def _extract_cost_usd(data):
    """Арзиши воқеии USD-ро аз ҷавоби FazerCards мебарорад (агар мавҷуд бошад)."""
    if not isinstance(data, dict):
        return None
    order_block = data.get("order") or {}
    for src in (order_block, data):
        for key in ("total_usd", "price_usd", "chargedUsd"):
            v = src.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    return None


# ==================== НОМИ АККАУНТ ====================
async def get_nickname(player_id: str) -> str:
    """
    Номи аккаунти Free Fire (СНГ)-ро бармегардонад.
    Танҳо FazerCards validate истифода мешавад (category: free_fire = СНГ),
    зеро RapidAPI endpoint ff-global номи сервери Глобалро бармегардонад
    ки метавонад бо сервери СНГ фарқ кунад.
    """
    return await _nickname_fazer(player_id)


async def _nickname_fazer(player_id: str) -> str:
    """Аз FazerCards validate-id номро мегирад."""
    if not config.FAZER_KEY:
        return ""
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    payload = {
        "category_id": config.FF_CATEGORY_VALIDATE,
        "fields": {"player_id": player_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/validate-id",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json(content_type=None)
        logger.info(f"FazerCards validate: {data}")
        # FazerCards аслан "player_name"-ро дар сатҳи болоӣ бармегардонад
        if isinstance(data.get("player_name"), str) and data["player_name"]:
            return data["player_name"].strip()
        # Номро аз ҷойҳои гуногуни эҳтимолӣ ҷустуҷӯ мекунем
        for path in (
            ("account", "username"),
            ("account", "nickname"),
            ("data", "username"),
            ("data", "nickname"),
            ("result", "username"),
        ):
            node = data
            ok = True
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    ok = False
                    break
            if ok and node:
                return str(node).strip()
        # Майдони username дар сатҳи болоӣ
        if isinstance(data.get("username"), str):
            return data["username"].strip()
    except Exception as e:
        logger.warning(f"FazerCards validate хато: {e}")
    return ""


# ==================== ДОНАТИ ХУДКОР (FazerCards) ====================
# Ҳолатҳое, ки ВОҚЕАН ноком буданашонро ТАСДИҚ мекунанд. Танҳо дар ин
# ҳолатҳо иҷозат аст фармоиши НАВ созем. Агар ҳолат НОМАЪЛУМ бошад
# (масалан шабака хато дод ва мо ҷавоб нагирифтем), фармоиши нав
# САХТАН манъ аст — вагарна фармоиши аллакай иҷрошуда дучандон мешавад.
# «refund» — маҳз ҳамин калимаро FazerCards барои «Возврат» мефиристад
# (дар панел «Возврат», дар API 'refund'). Дар рӯйхат танҳо 'refunded'
# буд — як ҳарф фарқ, вале оқибаташ вазнин: возврат ҳамчун «ҳанӯз дар
# ҷараён» шинохта мешуд, тафтишгар абадан интизор мешуд ва тугмаи
# «Дубора донат» кор намекард (ҳимояи зидди харҷи дучанд онро мебаст).
# Возврат ҳолати НИҲОӢ аст: пул баргашт, донат нашуд — интизорӣ бефоида.
_FAZER_FAILED = {"failed", "cancelled", "canceled", "error", "rejected",
                 "refund", "refunded", "returned", "reversed", "chargeback"}

# Ҳолатҳои ОДДИИ «ҳанӯз дар ҷараён». Ҳар ҳолате, ки на дар ин рӯйхат
# аст, на дар _FAZER_FAILED ва на "completed" — НОШИНОС ҳисоб мешавад.
_FAZER_PROCESSING = {"processing", "pending", "created", "new", "queued",
                     "waiting", "in_progress", "in progress", "accepted"}

# Ҳолатҳои ношиноси то ҳол дидашуда: {ҳолат: {"count", "order", "reported"}}
# Ин ҷо ҷамъ мешаванд, вале ХАБАР аз autopay.py меравад — ин файл боти
# Telegram надорад ва набояд дошта бошад.
#
# Сабаби пайдоиш: як бор FazerCards барои «Возврат» калимаи 'refund'
# фиристод, вале дар рӯйхат 'refunded' буд. Бот онро нашинохт, фармоишҳо
# овезон монданд ва ин танҳо баъди шикояти мизоҷ маълум шуд. Акнун ҳар
# калимаи нави ношинос ҲАМОН РӮЗ ба соҳиб хабар медиҳад.
UNKNOWN_STATUSES: dict = {}


def note_unknown_status(status: str, order_id: str = ""):
    """Ҳолати ношиносро сабт мекунад, то autopay.py ба соҳиб хабар диҳад."""
    s = (status or "").strip().lower()
    if not s or s == "completed" or s in _FAZER_FAILED or s in _FAZER_PROCESSING:
        return
    rec = UNKNOWN_STATUSES.setdefault(
        s, {"count": 0, "order": order_id, "reported": False})
    rec["count"] += 1
    if order_id:
        rec["order"] = order_id
    logger.warning(f"ҲОЛАТИ НОШИНОСИ провайдер: {s!r} (фармоиш {order_id})")


# ---- Сабаби ноком шудани донат (то admin.py/autopay.py нишон диҳанд) ----
# Пеш сабаб танҳо дар лог мемонд ва соҳиб онро дида наметавонист. Акнун ҳар
# фармоиши ноком сабаби ХОНДАШАВАНДАро нигоҳ медорад, то дар ҳисоботи админ пайдо шавад.
_LAST_DONATE_ERROR: dict = {}


def note_donate_error(order_id, reason: str):
    """Сабаби ноком шудани донати як фармоишро сабт мекунад."""
    if order_id is None or order_id == "":
        return
    try:
        _LAST_DONATE_ERROR[str(order_id)] = (str(reason) or "")[:300]
    except Exception:
        return
    # ҷилавгирӣ аз варами хотира
    if len(_LAST_DONATE_ERROR) > 500:
        for k in list(_LAST_DONATE_ERROR)[:250]:
            _LAST_DONATE_ERROR.pop(k, None)


def pop_donate_error(order_id) -> str:
    """Сабаби нокомро мегирад ва аз хотира тоза мекунад (як бор истифода)."""
    if order_id is None:
        return ""
    return _LAST_DONATE_ERROR.pop(str(order_id), "")


def _fazer_err_text(result: dict) -> str:
    """Аз ҷавоби FazerCards матни хатои хонданбобро мекашад."""
    if not isinstance(result, dict):
        return str(result)[:200]
    for key in ("error", "message", "error_message", "detail"):
        v = result.get(key)
        if v:
            return str(v)[:200]
    return str(result)[:200]


def _idem_key(prefix: str, order_id, retry_tag: str = "") -> str:
    """
    Калиди Idempotency месозад. Барои ҳар фармоиш собит аст (то такрори
    шабакавӣ фармоиши дуюм насозад), вале агар кӯшиши қаблӣ ноком шуда
    бошад ва мо кӯшиши НАВ кунем (retry_tag = ID-и кӯшиши қаблӣ),
    калид фарқ мекунад — то FazerCards воқеан фармоиши нав созад.
    """
    if not order_id:
        return str(uuid.uuid4())
    base = f"{prefix}-{order_id}"
    return f"{base}-r{retry_tag}" if retry_tag else base


async def _fazer_order(offer_id: str, player_id: str, order_id: int | str = "",
                       retry_tag: str = "") -> dict:
    """Фармоиш ба FazerCards мефиристад."""
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит (аз рӯи order_id-и худамон), на тасодуфӣ — то агар
        # дархост дар шабака гум шавад ва бот такрор фиристад, FazerCards
        # онро такрорӣ шинохта, фармоиши ДУЮМ насозад (зидди дучандон харҷ).
        # retry_tag: вақте фармоиши пешина ВОҚЕАН ноком шуд ва мо кӯшиши
        # НАВ мекунем, калид бояд ФАРҚ кунад — вагарна FazerCards ҳамон
        # натиҷаи кӯҳнаи нокомро бармегардонад ва "Дубора донат" кор
        # намекунад. Калид ҳанӯз собит аст (аз рӯи ID-и кӯшиши қаблӣ),
        # пас такрори шабакавии ҲАМИН кӯшиш бехатар мемонад.
        "Idempotency-Key": _idem_key("donate", order_id, retry_tag),
    }
    payload = {
        "category_id": config.FF_CATEGORY_ORDER,
        "offer_id": offer_id,
        "fields": {"player_id": player_id},
    }
    # То 3 кӯшиш бо ҳамон калиди собит — агар дархост дар роҳи шабака гум
    # шавад/таймаут кунад (на хатои воқеии FazerCards), такрор бехатар аст
    # (FazerCards ҳамон фармоишро бармегардонад, дуюм насозад)
    last_error = ""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{config.FAZER_BASE}/topups/order",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    data = await r.json(content_type=None)
            logger.info(f"FazerCards order: {data}")
            return data
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"FazerCards order кӯшиши {attempt + 1}/3 ноком "
                f"(хато: {last_error or 'таймаут/шабака'})"
            )
            if attempt < 2:
                await asyncio.sleep(3)
    logger.error(f"FazerCards order хато (баъд аз 3 кӯшиш): {last_error}")
    return {"ok": False, "error": last_error}


async def _fazer_status(order_id: str) -> dict:
    """Ҳолати фармоишро мепурсад."""
    headers = {"X-API-Key": config.FAZER_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{config.FAZER_BASE}/orders/{order_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                return await r.json(content_type=None)
    except Exception as e:
        logger.error(f"FazerCards status хато: {e}")
        return {"ok": False, "error": str(e)}


# ==================== ДОНАТИ ЭҲТИЁТӢ (MooGold) ====================
# Агар FazerCards фармоишро рад кунад (ё бо хатогӣ бирӯяд), auto_donate()
# худкор ба MooGold мегузарад — то мизоҷ бе алмос намонад. ID-ҳои
# фармоиши MooGold бо префикси "moo:" нигоҳ дошта мешаванд, то дар
# санҷиши такрорӣ бот донад кадом провайдерро пурсад.
_MOOGOLD_DONE = {"completed", "complete", "delivered", "success", "done"}
_MOOGOLD_FAILED = {"failed", "cancelled", "canceled", "error", "rejected",
                   "refund", "refunded", "returned", "reversed", "chargeback"}


async def _moogold_request(api_route: str, payload: dict) -> dict:
    if not (config.MOOGOLD_PARTNER_ID and config.MOOGOLD_SECRET and config.MOOGOLD_USER_ID):
        return {}
    body = dict(payload)
    body["path"] = api_route
    payload_json = json.dumps(body)
    timestamp = str(int(time.time()))
    string_to_sign = payload_json + timestamp + api_route
    auth = hmac.new(
        config.MOOGOLD_SECRET.encode("utf-8"),
        msg=string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    headers = {
        "timestamp": timestamp,
        "auth": auth,
        "Content-Type": "application/json",
    }
    basic_auth = aiohttp.BasicAuth(login=config.MOOGOLD_PARTNER_ID, password=config.MOOGOLD_SECRET)
    url = f"https://moogold.com/wp-json/v1/api/{api_route}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url, data=payload_json, headers=headers, auth=basic_auth,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                data = await r.json(content_type=None)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"MooGold {api_route} хато: {e}")
        return {}


async def _moogold_order(offer_id: str, player_id: str) -> dict:
    """Фармоиш ба MooGold мефиристад (агар барои ин offer_id харита мавҷуд бошад)."""
    mapping = config.MOOGOLD_PRODUCT_MAP.get(offer_id)
    if not mapping:
        logger.warning(f"MooGold: барои offer_id={offer_id} дар MOOGOLD_PRODUCT_MAP чизе нест")
        return {}
    payload = {
        "category": mapping.get("category", ""),
        "product-id": mapping.get("product_id", ""),
        "quantity": "1",
        "User ID": player_id,
    }
    if mapping.get("server"):
        payload["Server"] = mapping["server"]
    result = await _moogold_request("order/create_order", payload)
    logger.info(f"MooGold order: {result}")
    return result


async def _moogold_status(order_id: str) -> dict:
    result = await _moogold_request("order/order_detail", {"order_id": order_id})
    logger.info(f"MooGold status: {result}")
    return result


async def _moogold_check(order_id: str):
    """Бармегардонад: (True/False/None, "moo:<order_id>"). None = ҳанӯз дар ҷараён."""
    data = await _moogold_status(order_id)
    status = str(data.get("order_status") or "").strip().lower()
    tagged = f"moo:{order_id}"
    if status in _MOOGOLD_DONE:
        return True, tagged
    if status in _MOOGOLD_FAILED:
        return False, tagged
    return None, tagged


async def _moogold_fallback(offer_id: str, player_id: str):
    """Фармоиши нав ба MooGold мефиристад ва то анҷом мунтазир мемонад."""
    order_result = await _moogold_order(offer_id, player_id)
    account = order_result.get("account_details") or {}
    moo_order_id = str(account.get("order_id") or "")
    status = str(order_result.get("status") or "").strip().lower()
    if status in _MOOGOLD_FAILED or not moo_order_id:
        logger.error(f"MooGold ҳам ноком шуд: {order_result}")
        return False, ""

    for _ in range(60):
        await asyncio.sleep(10)
        ok, tagged = await _moogold_check(moo_order_id)
        if ok is True:
            return True, tagged
        if ok is False:
            return False, tagged
    # Вақт тамом шуд, аммо ҳанӯз дар ҷараён — ID-ро нигоҳ медорем, санҷиши
    # навбатӣ (existing_order_id) идомаро тафтиш мекунад.
    return False, f"moo:{moo_order_id}"


async def auto_donate(player_id: str, offer_id: str, existing_order_id: str = "", order_id: int | str = ""):
    """
    Донати худкор: аввал FazerCards, агар ноком шавад — MooGold (fallback).
    Агар existing_order_id дода шавад — аввал ҳолати ОНРО тафтиш мекунад
    (то дучандон фармоиш фиристода нашавад).
    Бармегардонад: (success: bool, api_order_id: str, uncertain: bool, cost_usd: float | None)
    uncertain=True маънояш: мо ҳељ бор ҷавоби ВОҚЕИИ FazerCards-ро дар бораи
    ҳолати ниҳоӣ нагирифтем (ҳамеша таймаути шабака) — фармоиш шояд ВОҚЕАН
    иҷро шуда бошад, пеш аз "Дубора донат" дар FazerCards санҷед!
    cost_usd — арзиши воқеии USD-и FazerCards барои ин фармоиш (агар
    маълум бошад) — барои ҳисоби фоидаи холис.
    """
    # retry_tag — ID-и кӯшиши ҚАБЛӢ (агар он воқеан ноком шуда бошад).
    # Ба калиди Idempotency илова мешавад, то "Дубора донат" воқеан
    # фармоиши нав созад, на натиҷаи кӯҳнаи нокомро баргардонад.
    retry_tag = ""

    # Агар фармоиши пешина ба MooGold тааллуқ дошта бошад
    if existing_order_id.startswith("moo:"):
        ok, tagged = await _moogold_check(existing_order_id[4:])
        if ok is True:
            return True, tagged, False, None
        if ok is None:
            return False, tagged, False, None  # ҳанӯз дар ҷараён — мунтазир мемонем
        retry_tag = existing_order_id[4:]
        existing_order_id = ""  # ноком — аз нав кӯшиш мекунем

    # Агар фармоиши пешина ба FazerCards тааллуқ дошта бошад
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id, False, _extract_cost_usd(status_data)
        if status == "processing":
            return False, existing_order_id, False, None
        if status not in _FAZER_FAILED:
            # Ҳолати ВОҚЕӢ номаълум (шабака ҷавоб надод) — фармоиши НАВ
            # НАМЕСОЗЕМ, вагарна агар он воқеан иҷро шуда бошад, дучандон
            # харҷ мешавад. Ба админ ҳамчун "номуайян" бармегардонем.
            note_unknown_status(status, existing_order_id)
            logger.warning(
                f"auto_donate: ҳолати фармоиши {existing_order_id} номаълум "
                f"({status!r}) — кӯшиши нав НАШУД (зидди дучандон харҷ)"
            )
            return False, existing_order_id, True, None
        # ВОҚЕАН ноком — поён фармоиши НАВ месозем (бо калиди нав)
        retry_tag = existing_order_id

    if not offer_id:
        logger.error("auto_donate: offer_id холист")
        note_donate_error(order_id, "offer_id (танзими маҳсулот) холӣ аст")
        return False, "", False, None

    # ---- Кӯшиши 1: FazerCards ----
    if config.FAZER_KEY:
        result = await _fazer_order(offer_id, player_id, order_id, retry_tag)
        api_order_id = ""
        if isinstance(result, dict):
            order_block = result.get("order") or {}
            api_order_id = str(order_block.get("id") or result.get("id") or "")
        cost_usd = _extract_cost_usd(result)
        logger.info(f"[COST-DEBUG] auto_donate: cost_usd={cost_usd!r} extracted from order-creation result for offer={offer_id}")

        if result.get("ok") and api_order_id:
            ever_confirmed = False  # оё ягон бор ҷавоби воқеии FazerCards гирифтем
            last_status = ""
            unknown_logged = 0
            for _ in range(60):
                await asyncio.sleep(10)
                status_data = await _fazer_status(api_order_id)
                status = ""
                if isinstance(status_data, dict):
                    status = (status_data.get("order") or {}).get("status") \
                        or status_data.get("status") or ""
                    # Ҷавоби ВОҚЕИИ FazerCards гирифтем, агар ё "ok": true
                    # омада бошад, ё ҳолати хондашаванда. Баъзе версияҳои API
                    # дар GET /orders/{id} калиди "ok"-ро НАМЕфиристанд —
                    # пештар аз ҳамин сабаб ҲАР фармоиш "номаълум" эълон
                    # мешуд, ҳарчанд FazerCards дуруст ҷавоб медод.
                    if status_data.get("ok") is True or status:
                        ever_confirmed = True
                    if status:
                        last_status = status
                    elif unknown_logged < 3:
                        unknown_logged += 1
                        logger.warning(
                            f"[STATUS-DEBUG] {api_order_id}: ҳолат хонда нашуд, "
                            f"ҷавоби хом: {status_data}"
                        )
                if status == "completed":
                    final_cost = cost_usd or _extract_cost_usd(status_data)
                    logger.info(f"[COST-DEBUG] auto_donate: completed, cost_usd={cost_usd!r} status_data_cost={_extract_cost_usd(status_data)!r} final_cost={final_cost!r}")
                    return True, api_order_id, False, final_cost
                if status in ("failed", "cancelled", "error", "refunded"):
                    note_donate_error(order_id, f"FazerCards рад кард (ҳолат: {status})")
                    break  # ба MooGold мегузарем
            else:
                # 10 дақиқа гузашт, ҳанӯз "processing" (ё ҳамеша таймаут) —
                # мунтазир мемонем, ба MooGold нагузарем (то дучандон
                # фармоиш нашавад)
                logger.warning(
                    f"auto_donate: {api_order_id} баъд аз 10 дақиқа тамом нашуд "
                    f"(ҳолати охирин: {last_status or 'ҷавоб нест'}, "
                    f"алоқа бо FazerCards: {'ҲА' if ever_confirmed else 'НЕ'})"
                )
                return False, api_order_id, not ever_confirmed, cost_usd
        else:
            err = _fazer_err_text(result)
            logger.warning(f"FazerCards фармоиш нашуд, MooGold-ро санҷем: {result}")
            note_donate_error(order_id, f"FazerCards фармоиш насохт: {err}")
    else:
        logger.warning("FAZER_KEY нест — рост ба MooGold мегузарем")

    # ---- Кӯшиши 2: MooGold (fallback) — арзиши воқеӣ маълум нест ----
    success, tagged = await _moogold_fallback(offer_id, player_id)
    if not success:
        # Ҳарду провайдер ноком — сабаби FazerCards-ро нигоҳ медорем (агар бошад),
        # вагарна умумӣ. (MooGold сабаби ҷудогона намедиҳад.)
        if order_id is not None and str(order_id) not in _LAST_DONATE_ERROR:
            note_donate_error(order_id, "FazerCards ва MooGold ҳарду фармоиш насохтанд")
    return success, tagged, False, None


async def peek_order_status(api_order_id: str):
    """
    ТАНҲО ҳолати фармоиши мавҷударо мехонад — ҲЕҶ ГОҲ фармоиши нав
    намесозад ва ҳеҷ пул харҷ намекунад. Барои тафтишгари худкори
    фармоишҳои "овезон" (recheck_loop) сохта шудааст.

    Бармегардонад: (state, cost_usd)
      state: "completed" | "processing" | "failed" | "" (ҷавоб нест)
    """
    if not api_order_id:
        return "", None

    # Фармоиши MooGold
    if api_order_id.startswith("moo:"):
        ok, _tagged = await _moogold_check(api_order_id[4:])
        if ok is True:
            return "completed", None
        if ok is None:
            return "processing", None
        return "failed", None

    if not config.FAZER_KEY:
        return "", None

    status_data = await _fazer_status(api_order_id)
    status = ""
    if isinstance(status_data, dict):
        status = (status_data.get("order") or {}).get("status") \
            or status_data.get("status") or ""
    if not status:
        return "", None
    if status == "completed":
        return "completed", _extract_cost_usd(status_data)
    if status in _FAZER_FAILED:
        return "failed", None
    # Ҳолати ношинос — интизор мешавем (бехатартар аз донати такрорӣ),
    # вале соҳиб бояд ҲАМИН РӮЗ бидонад, на баъди шикояти мизоҷ
    note_unknown_status(status, api_order_id)
    return "processing", None


# ==================== FREE FIRE INDONESIA ====================
async def get_nickname_ffid(player_id: str) -> str:
    """
    Номи аккаунти Free Fire Indonesia.

    FazerCards барои категорияи фармоиши FFID санҷиши ID-ро ДАСТГИРӢ
    НАМЕКУНАД — ҷавобаш: «ID validation is not available for this
    category_id». Барои ҳамин агар категорияи FFID нашавад, ҳамон
    категорияи санҷишро мекӯшем, ки барои СНГ кор мекунад: ID-и Free
    Fire ҷаҳонӣ аст ва як ID ҳамон як аккаунт аст, новобаста аз он ки
    пуркунӣ ба кадом сервер меравад.

    Санҷиш танҳо ХОНДАН аст — на пул мехӯрад, на чизе месозад. Агар
    ҳарду нашаванд, сатри холӣ бармегардад ва бот мисли пештара
    «Номи аккаунт ёфт нашуд» мегӯяд.
    """
    cats = [config.FFID_CATEGORY_VALIDATE]
    if config.FF_CATEGORY_VALIDATE not in cats:
        cats.append(config.FF_CATEGORY_VALIDATE)
    for cat in cats:
        name = await _ffid_validate_one(player_id, cat)
        if name:
            return name
    return ""


async def _ffid_validate_one(player_id: str, category_id: str) -> str:
    if not config.FAZER_KEY:
        return ""
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    payload = {"category_id": category_id, "fields": {"player_id": player_id}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/validate-id",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json(content_type=None)
        logger.info(f"FazerCards FFID validate ({category_id}, id={player_id}): {data}")
        if not data.get("ok"):
            logger.info(f"FazerCards FFID validate ({category_id}) нашуд: "
                        f"{data.get('error') or data}")
            return ""
        if data.get("valid") is False:
            return ""
        # FazerCards аслан "player_name"-ро дар сатҳи болоӣ бармегардонад
        if isinstance(data.get("player_name"), str) and data["player_name"]:
            return data["player_name"].strip()
        for path in (("account", "username"), ("account", "nickname"),
                     ("data", "username"), ("data", "nickname")):
            node = data
            ok = True
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    ok = False
                    break
            if ok and node:
                return str(node).strip()
    except Exception as e:
        logger.warning(f"FazerCards FFID validate ({category_id}) хато: {e}")
    return ""


async def auto_donate_ffid(player_id: str, offer_id: str, existing_order_id: str = "", order_id: int | str = ""):
    """Донати худкор барои Free Fire Indonesia."""
    retry_tag = ""
    # Агар фармоиши пешина мавҷуд бошад — аввал ҳолатро тафтиш кунем
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id
        if status == "processing":
            return False, existing_order_id
        if status not in _FAZER_FAILED:
            # Ҳолат номаълум — фармоиши НАВ намесозем (зидди дучандон харҷ)
            logger.warning(
                f"auto_donate_ffid: ҳолати {existing_order_id} номаълум "
                f"({status!r}) — кӯшиши нав НАШУД"
            )
            return False, existing_order_id
        # ВОҚЕАН ноком — кӯшиши НАВ бо калиди дигар (ниг. изоҳи _idem_key)
        retry_tag = existing_order_id

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит (аз order_id-и худамон), на тасодуфӣ — то агар
        # даъвати такрорӣ шавад (масалан такроран пас аз таймаути шабака),
        # FazerCards онро ҳамон дархост шиносад, на фармоиши дуюм насозад
        "Idempotency-Key": _idem_key("ffid", order_id, retry_tag),
    }
    payload = {
        "category_id": config.FFID_CATEGORY_ORDER,
        "offer_id": offer_id,
        "fields": {"player_id": player_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/order",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                result = await r.json(content_type=None)
        logger.info(f"FazerCards FFID order: {result}")
    except Exception as e:
        logger.error(f"FazerCards FFID order хато: {e}")
        return False, ""

    api_order_id = ""
    if isinstance(result, dict):
        order_block = result.get("order") or {}
        api_order_id = str(order_block.get("id") or result.get("id") or "")

    if not result.get("ok") or not api_order_id:
        logger.error(f"FazerCards FFID фармоиш нашуд: {result}")
        return False, api_order_id

    for _ in range(60):
        await asyncio.sleep(10)
        status_data = await _fazer_status(api_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, api_order_id
        if status in ("failed", "cancelled", "error", "refunded"):
            return False, api_order_id
    return False, api_order_id


# ==================== PUBG MOBILE ====================
async def auto_donate_pubg(player_id: str, offer_id: str, existing_order_id: str = "", order_id: int | str = ""):
    """Донати худкор барои PUBG Mobile (category: pubg_mobile_auto)."""
    retry_tag = ""
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id
        if status == "processing":
            return False, existing_order_id
        if status not in _FAZER_FAILED:
            # Ҳолат номаълум — фармоиши НАВ намесозем (зидди дучандон харҷ)
            logger.warning(
                f"auto_donate_pubg: ҳолати {existing_order_id} номаълум "
                f"({status!r}) — кӯшиши нав НАШУД"
            )
            return False, existing_order_id
        # ВОҚЕАН ноком — кӯшиши НАВ бо калиди дигар (ниг. изоҳи _idem_key)
        retry_tag = existing_order_id

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит — ниг. изоҳи _idem_key
        "Idempotency-Key": _idem_key("pubg", order_id, retry_tag),
    }
    payload = {
        "category_id": "pubg_mobile_auto",
        "offer_id": offer_id,
        "fields": {"player_id": player_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/order",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                result = await r.json(content_type=None)
        logger.info(f"FazerCards PUBG order: {result}")
    except Exception as e:
        logger.error(f"FazerCards PUBG order хато: {e}")
        return False, ""

    api_order_id = ""
    if isinstance(result, dict):
        order_block = result.get("order") or {}
        api_order_id = str(order_block.get("id") or result.get("id") or "")

    if not result.get("ok") or not api_order_id:
        logger.error(f"FazerCards PUBG фармоиш нашуд: {result}")
        return False, api_order_id

    for _ in range(60):
        await asyncio.sleep(10)
        status_data = await _fazer_status(api_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, api_order_id
        if status in ("failed", "cancelled", "error", "refunded"):
            return False, api_order_id
    return False, api_order_id


# ==================== FREE FIRE BRAZIL (free_fire_br) ====================
# FF BR дар FazerCards аст — донати ХУДКОР дорад, мисли FF СНГ/FFID.
# Танҳо фарқ дар category_id: 'free_fire_br'. ID-и Free Fire ҷаҳонӣ аст,
# пас санҷиши ном ҳамон категорияи кориро мекӯшад (мисли FFID).
FFBR_CATEGORY = "free_fire_br"


async def list_offers(category_id: str) -> list:
    """Рӯйхати офферҳои як категорияро аз FazerCards мегирад.
    Ҳар элемент: {id, name, price_usd}. Барои он ки соҳиб offer_id-ро
    (масалан ваучери ҳафта/моҳона) дар панели админ бинад ва нусха гирад."""
    if not config.FAZER_KEY:
        return []
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    base = config.FAZER_BASE
    # Роҳи дурусти FazerCards маълум нест — якчандтоашро месанҷем ва аввалин
    # ҷавоби кориро мегирем (ҷои кории мо POST аст, вале рӯйхат шояд GET бошад)
    attempts = [
        ("GET",  f"{base}/topups/offers?category_id={category_id}", None),
        ("POST", f"{base}/topups/offers", {"category_id": category_id}),
        ("GET",  f"{base}/offers?category_id={category_id}", None),
    ]
    for method, url, payload in attempts:
        try:
            async with aiohttp.ClientSession() as s:
                req = s.get(url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=20)) \
                    if method == "GET" else \
                    s.post(url, json=payload, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=20))
                async with req as r:
                    data = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"list_offers {method} {url} хато: {e}")
            continue
        # Ҷавоб метавонад {ok, offers:[...]} ё худи рӯйхат бошад
        raw = []
        if isinstance(data, dict):
            raw = data.get("offers") or data.get("data") or data.get("result") or []
        elif isinstance(data, list):
            raw = data
        out = []
        for o in raw:
            if not isinstance(o, dict):
                continue
            oid = o.get("id") or o.get("offer_id") or o.get("sku") or ""
            name = o.get("name") or o.get("title") or o.get("label") or ""
            price = o.get("price_usd") or o.get("price") or o.get("total_usd") or ""
            out.append({"id": str(oid), "name": str(name), "price_usd": price})
        if out:
            logger.info(f"FazerCards offers ({category_id}) аз {method} {url}: "
                        f"{len(out)} дона")
            return out
    logger.error(f"FazerCards list_offers({category_id}): ягон роҳ кор накард")
    return []


# Номҳои эҳтимолии category_id-и Mobile Legends дар FazerCards.
# Аз рӯи услуби номгузории худи FazerCards сохта шудаанд: бозиҳои дигар
# free_fire_cis / free_fire_id / free_fire_br / pubg_mobile_auto ном доранд —
# яъне snake_case + пасванди минтақа/навъ.
ML_CANDIDATES = [
    "mobile_legends", "mobile_legends_global", "mobile_legends_auto",
    "mobile_legends_cis", "mobile_legends_bang_bang", "mobilelegends",
    "mlbb", "mlbb_global", "mlbb_auto", "ml_global", "ml",
    "mobile_legends_gl", "mobile_legend", "moba_mobile_legends",
]


# Ҷуфтҳои эҳтимолии номи майдонҳо барои бозиҳои ДУ-майдона (ML).
# Номи дақиқи FazerCards маълум нест — сайт танҳо «Player ID»/«Server ID»
# нишон медиҳад, ки ин нишонаи UI аст, на калиди API.
ML_FIELD_PAIRS = [
    ("player_id", "server_id"),
    ("user_id", "zone_id"),
    ("player_id", "zone_id"),
    ("user_id", "server_id"),
    ("uid", "zone"),
    ("userid", "zoneid"),
    ("account_id", "server_id"),
    ("id", "server"),
]


async def find_ml_fields(player_id: str, server_id: str,
                         category_id: str = "") -> list:
    """Ҷуфтҳои номи майдонҳоро месанҷад ва онҳоеро бармегардонад, ки
    FazerCards ҚАБУЛ кард (номи аккаунт баргардонд).

    Ин санҷиш ТАНҲО validate-id-ро истифода мебарад — яъне ҳељ пул сарф
    намешавад ва ҳељ донат намешавад. Лозим аст, чунки бо номи НОДУРУСТИ
    майдон донат ноком мешавад, вале пули мизоҷ аллакай гирифта шудааст.
    Ҳар элемент: {fields, name}."""
    if not config.FAZER_KEY:
        return []
    if not category_id:
        category_id, _, _ = await _ml_cfg()
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    found = []
    async with aiohttp.ClientSession() as s:
        for f_player, f_server in ML_FIELD_PAIRS:
            payload = {
                "category_id": category_id,
                "fields": {f_player: player_id, f_server: server_id},
            }
            try:
                async with s.post(
                    f"{config.FAZER_BASE}/topups/validate-id",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    data = await r.json(content_type=None)
            except Exception as e:
                logger.warning(f"find_ml_fields({f_player}/{f_server}) хато: {e}")
                continue
            if not isinstance(data, dict):
                continue
            name = ""
            for src in (data, data.get("data") or {}, data.get("result") or {}):
                if not isinstance(src, dict):
                    continue
                for key in ("player_name", "username", "nickname", "name"):
                    v = src.get(key)
                    if isinstance(v, str) and v.strip():
                        name = v.strip()
                        break
                if name:
                    break
            if name:
                found.append({"fields": f"{f_player} | {f_server}", "name": name})
                logger.info(f"find_ml_fields: {f_player}/{f_server} → {name}")
    return found


async def find_category(candidates: list) -> list:
    """Ҳар номи эҳтимолиро месанҷад ва онҳоеро бармегардонад, ки ВОҚЕАН
    оффер доранд. Барои ёфтани category_id-и бозии нав, вақте рӯйхати
    категорияҳои FazerCards дастрас нест.
    Ҳар элемент: {id, count, sample}."""
    found = []
    for cid in candidates:
        try:
            offers = await list_offers(cid)
        except Exception as e:
            logger.warning(f"find_category({cid}) хато: {e}")
            continue
        if offers:
            found.append({
                "id": cid,
                "count": len(offers),
                "sample": offers[0].get("name", ""),
            })
            logger.info(f"find_category: {cid} → {len(offers)} оффер")
    return found


async def probe_api(category_id: str = "") -> list:
    """ТАШХИС: якчанд роҳи гирифтани рӯйхати офферҳо/категорияҳоро месанҷад
    ва ҷавоби ХОМИ ҳар яке (код + матн)-ро бармегардонад.

    Лозим шуд, чунки роҳи дурусти FazerCards маълум нест: ҷои кории мо
    (order/validate-id) POST аст, вале рӯйхат шояд GET бошад ё роҳи дигар.
    Ҳар элемент: {method, url, status, body}."""
    if not config.FAZER_KEY:
        return [{"method": "-", "url": "-", "status": "-",
                 "body": "FAZER_KEY холӣ аст!"}]
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    base = config.FAZER_BASE
    attempts = [
        ("GET",  f"{base}/topups/offers?category_id={category_id}", None),
        ("GET",  f"{base}/topups/offers", None),
        ("POST", f"{base}/topups/offers", {"category_id": category_id}),
        ("GET",  f"{base}/topups/categories", None),
        ("POST", f"{base}/topups/categories", {}),
    ]
    out = []
    async with aiohttp.ClientSession() as s:
        for method, url, payload in attempts:
            try:
                req = s.get(url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)) \
                    if method == "GET" else \
                    s.post(url, json=payload, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=15))
                async with req as r:
                    status = r.status
                    body = (await r.text())[:400]
            except Exception as e:
                status, body = "ХАТО", str(e)[:400]
            out.append({"method": method, "url": url.replace(base, ""),
                        "status": status, "body": body})
            logger.info(f"probe_api {method} {url} → {status}: {body[:200]}")
    return out


async def list_categories() -> list:
    """Ҳамаи категорияҳои FazerCards-ро рӯйхат мекунад — то соҳиб category_id-и
    бозии наверо (масалан Mobile Legends) ёбад. Ҳар элемент: {id, name}.

    Endpoint-и дақиқро намедонем, пас якчанд роҳи эҳтимолиро санҷида, аввалин
    ҷавоби кориро бармегардонем (дар лог қайд мешавад ки кадомаш кор кард)."""
    if not config.FAZER_KEY:
        return []
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    candidates = [
        f"{config.FAZER_BASE}/topups/categories",
        f"{config.FAZER_BASE}/categories",
        f"{config.FAZER_BASE}/topups/category",
    ]
    for url in candidates:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    data = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"FazerCards list_categories {url} хато: {e}")
            continue
        raw = []
        if isinstance(data, dict):
            raw = data.get("categories") or data.get("data") \
                or data.get("result") or []
        elif isinstance(data, list):
            raw = data
        if not raw:
            continue
        out = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") or c.get("category_id") or c.get("slug") or ""
            name = c.get("name") or c.get("title") or c.get("label") or ""
            out.append({"id": str(cid), "name": str(name)})
        if out:
            logger.info(f"FazerCards categories аз {url}: {len(out)} дона")
            return out
    logger.error("FazerCards list_categories: ягон endpoint кор накард")
    return []


# ==================== MOBILE LEGENDS ====================
# ML аз бозиҳои дигар ФАРҚ мекунад: ба ҷуз Player ID боз Server (Zone) ID
# лозим аст — пас payload ДУ майдон дорад, на як.
#
# Ному category_id-и дақиқи FazerCards ҳанӯз тасдиқ нашудааст, барои ҳамин
# ҳар се қиматро соҳиб аз панели админ иваз карда метавонад (бе деплой).
# «mobile_legends_global» бо санҷиши воқеӣ тасдиқ шуд (47 оффер дошт).
ML_CATEGORY = "mobile_legends_global"
ML_FIELD_PLAYER = "player_id"
ML_FIELD_SERVER = "server_id"


async def _ml_cfg():
    """Танзимоти ML-ро аз база мегирад (агар соҳиб ивазашон карда бошад)."""
    try:
        import database as _db
        cat = await _db.get_setting("ml_category_id")
        fp = await _db.get_setting("ml_field_player")
        fs = await _db.get_setting("ml_field_server")
    except Exception as e:
        logger.warning(f"_ml_cfg: танзимот аз база хонда нашуд ({e}) — пешфарз")
        cat = fp = fs = ""
    return (cat or ML_CATEGORY, fp or ML_FIELD_PLAYER, fs or ML_FIELD_SERVER)


async def get_nickname_ml(player_id: str, server_id: str) -> str:
    """Номи аккаунти Mobile Legends (Player ID + Server ID)."""
    if not config.FAZER_KEY:
        return ""
    category_id, f_player, f_server = await _ml_cfg()
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    payload = {
        "category_id": category_id,
        "fields": {f_player: player_id, f_server: server_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/validate-id",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"FazerCards ML validate хато: {e}")
        return ""
    logger.info(f"FazerCards ML validate ({player_id}/{server_id}): {data}")
    if not isinstance(data, dict):
        return ""
    for key in ("player_name", "username", "nickname", "name"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    inner = data.get("data") or data.get("result") or {}
    if isinstance(inner, dict):
        for key in ("player_name", "username", "nickname", "name"):
            v = inner.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


async def auto_donate_ml(player_id: str, server_id: str, offer_id: str,
                         existing_order_id: str = "", order_id: int | str = ""):
    """Донати худкор барои Mobile Legends (ду майдон: Player ID + Server ID)."""
    retry_tag = ""
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id
        if status == "processing":
            return False, existing_order_id
        if status not in _FAZER_FAILED:
            note_unknown_status(status, existing_order_id)
            logger.warning(f"auto_donate_ml: ҳолати {existing_order_id} "
                           f"номаълум ({status!r}) — кӯшиши нав НАШУД")
            return False, existing_order_id
        retry_tag = existing_order_id

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    category_id, f_player, f_server = await _ml_cfg()
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        "Idempotency-Key": _idem_key("ml", order_id, retry_tag),
    }
    payload = {
        "category_id": category_id,
        "offer_id": offer_id,
        "fields": {f_player: player_id, f_server: server_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/order",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                result = await r.json(content_type=None)
        logger.info(f"FazerCards ML order: {result}")
    except Exception as e:
        logger.error(f"FazerCards ML order хато: {e}")
        return False, ""

    api_order_id = ""
    if isinstance(result, dict):
        order_block = result.get("order") or {}
        api_order_id = str(order_block.get("id") or result.get("id") or "")
    if not result.get("ok") or not api_order_id:
        logger.error(f"FazerCards ML фармоиш нашуд: {result}")
        return False, api_order_id

    for _ in range(60):
        await asyncio.sleep(10)
        status_data = await _fazer_status(api_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, api_order_id
        if status in _FAZER_FAILED:
            logger.error(f"FazerCards ML {api_order_id} рад шуд: {status}")
            return False, api_order_id
        if status and status not in _FAZER_PROCESSING:
            note_unknown_status(status, api_order_id)
    logger.warning(f"FazerCards ML {api_order_id} дар 10 дақиқа тамом нашуд")
    return False, api_order_id


async def get_nickname_ffbr(player_id: str) -> str:
    """Номи аккаунти FF Brazil. Мисли FFID — агар категорияи BR санҷишро
    дастгирӣ накунад, категорияи кории СНГ-ро мекӯшад."""
    cats = [FFBR_CATEGORY]
    if config.FF_CATEGORY_VALIDATE not in cats:
        cats.append(config.FF_CATEGORY_VALIDATE)
    for cat in cats:
        name = await _ffid_validate_one(player_id, cat)
        if name:
            return name
    return ""


async def auto_donate_ffbr(player_id: str, offer_id: str,
                           existing_order_id: str = "", order_id: int | str = ""):
    """Донати худкор барои Free Fire Brazil (category: free_fire_br)."""
    retry_tag = ""
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id
        if status == "processing":
            return False, existing_order_id
        if status not in _FAZER_FAILED:
            note_unknown_status(status, existing_order_id)
            logger.warning(f"auto_donate_ffbr: ҳолати {existing_order_id} "
                           f"номаълум ({status!r}) — кӯшиши нав НАШУД")
            return False, existing_order_id
        retry_tag = existing_order_id

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        "Idempotency-Key": _idem_key("ffbr", order_id, retry_tag),
    }
    payload = {
        "category_id": FFBR_CATEGORY,
        "offer_id": offer_id,
        "fields": {"player_id": player_id},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/order",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                result = await r.json(content_type=None)
        logger.info(f"FazerCards FFBR order: {result}")
    except Exception as e:
        logger.error(f"FazerCards FFBR order хато: {e}")
        return False, ""

    api_order_id = ""
    if isinstance(result, dict):
        order_block = result.get("order") or {}
        api_order_id = str(order_block.get("id") or result.get("id") or "")
    if not result.get("ok") or not api_order_id:
        logger.error(f"FazerCards FFBR фармоиш нашуд: {result}")
        return False, api_order_id

    for _ in range(60):
        await asyncio.sleep(10)
        status_data = await _fazer_status(api_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, api_order_id
        if status in _FAZER_FAILED:
            return False, api_order_id
    return False, api_order_id


# ==================== TELEGRAM STARS / PREMIUM ====================
async def buy_telegram_stars(username: str, quantity: int, order_id: int | str = "",
                              existing_order_id: str = ""):
    """
    Харидани Telegram Stars.
    Бармегардонад: (success: bool, order_id: str, uncertain: bool, cost_usd: float | None)
    uncertain=True маънояш: дархост ба FazerCards таймаут задааст ва мо
    ҳатто НАФАҲМИДЕМ фармоиш дар тарафи онҳо сохта шуд ё не — пеш аз
    "Дубора кӯшиш" дар FazerCards санҷед!
    """
    if not config.FAZER_KEY:
        return False, "", False, None
    retry_tag = ""
    # Агар кӯшиши қаблӣ ID дошта бошад — аввал ҳолати ВОҚЕИИ онро месанҷем,
    # то фармоиши муваффақро дубора нахарем (зидди дучандон харҷ)
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id, False, _extract_cost_usd(status_data)
        if status == "processing":
            return False, existing_order_id, False, None
        if status not in _FAZER_FAILED:
            # Ҳолат номаълум — хариди НАВ намекунем (зидди дучандон харҷ)
            logger.warning(
                f"ҳолати {existing_order_id} номаълум ({status!r}) — кӯшиши нав НАШУД"
            )
            return False, existing_order_id, True, None
        retry_tag = existing_order_id
    username = username.lstrip("@")
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит — ниг. изоҳи _idem_key
        "Idempotency-Key": _idem_key("stars", order_id, retry_tag),
    }
    payload = {"telegram_username": username, "quantity": quantity}
    result = None
    last_error = ""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{config.FAZER_BASE}/telegram/stars/buy",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    result = await r.json(content_type=None)
            logger.info(f"Telegram Stars buy: {result}")
            break
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Telegram Stars buy кӯшиши {attempt + 1}/3 ноком (хато: {last_error or 'таймаут/шабака'})")
            if attempt < 2:
                await asyncio.sleep(3)
    if result is None:
        logger.error(f"Telegram Stars buy хато (баъд аз 3 кӯшиш): {last_error}")
        return False, "", True, None

    if result.get("ok"):
        order = result.get("order") or {}
        api_id = str(order.get("id", ""))
        return True, api_id, False, _extract_cost_usd(result)
    return False, "", False, None


async def buy_telegram_premium(username: str, months: int, order_id: int | str = "",
                                existing_order_id: str = ""):
    """
    Харидани Telegram Premium.
    Бармегардонад: (success: bool, order_id: str, uncertain: bool, cost_usd: float | None)
    uncertain=True — ниг. изоҳи buy_telegram_stars.
    """
    if not config.FAZER_KEY:
        return False, "", False, None
    retry_tag = ""
    if existing_order_id:
        status_data = await _fazer_status(existing_order_id)
        status = ""
        if isinstance(status_data, dict):
            status = (status_data.get("order") or {}).get("status") \
                or status_data.get("status") or ""
        if status == "completed":
            return True, existing_order_id, False, _extract_cost_usd(status_data)
        if status == "processing":
            return False, existing_order_id, False, None
        if status not in _FAZER_FAILED:
            # Ҳолат номаълум — хариди НАВ намекунем (зидди дучандон харҷ)
            logger.warning(
                f"ҳолати {existing_order_id} номаълум ({status!r}) — кӯшиши нав НАШУД"
            )
            return False, existing_order_id, True, None
        retry_tag = existing_order_id
    username = username.lstrip("@")
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит — ниг. изоҳи _idem_key
        "Idempotency-Key": _idem_key("premium", order_id, retry_tag),
    }
    payload = {"telegram_username": username, "months": months}
    result = None
    last_error = ""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{config.FAZER_BASE}/telegram/premium/buy",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    result = await r.json(content_type=None)
            logger.info(f"Telegram Premium buy: {result}")
            break
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Telegram Premium buy кӯшиши {attempt + 1}/3 ноком (хато: {last_error or 'таймаут/шабака'})")
            if attempt < 2:
                await asyncio.sleep(3)
    if result is None:
        logger.error(f"Telegram Premium buy хато (баъд аз 3 кӯшиш): {last_error}")
        return False, "", True, None

    if result.get("ok"):
        order = result.get("order") or {}
        api_id = str(order.get("id", ""))
        return True, api_id, False, _extract_cost_usd(result)
    return False, "", False, None
