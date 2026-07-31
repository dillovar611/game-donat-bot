"""
API-и Free Fire:
  1. get_nickname()  — номи аккаунтро аз ID мегирад (RapidAPI + FazerCards)
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


async def _nickname_rapidapi(player_id: str) -> str:
    """Аз RapidAPI id-game-checker номро мегирад."""
    if not config.RAPIDAPI_KEYS:
        return ""
    url = f"https://id-game-checker.p.rapidapi.com/ff-global/{player_id}"
    for key in config.RAPIDAPI_KEYS:
        try:
            headers = {
                "x-rapidapi-key": key,
                "x-rapidapi-host": "id-game-checker.p.rapidapi.com",
            }
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=12)
                ) as r:
                    data = await r.json(content_type=None)
            if data.get("error"):
                continue
            block = data.get("data") or {}
            nick = (
                block.get("username")
                or block.get("nickname")
                or block.get("name")
                or ""
            )
            if nick:
                return str(nick).strip()
        except Exception as e:
            logger.warning(f"RapidAPI хато ({key[:6]}…): {e}")
            continue
    return ""


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
async def _fazer_order(offer_id: str, player_id: str, order_id: int | str = "") -> dict:
    """Фармоиш ба FazerCards мефиристад."""
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит (аз рӯи order_id-и худамон), на тасодуфӣ — то агар
        # "Дубора донат" зада шавад, FazerCards дархостро такрорӣ шинохта,
        # фармоиши ДУЮМ насозад (зидди дучандон харҷ)
        "Idempotency-Key": f"donate-{order_id}" if order_id else str(uuid.uuid4()),
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
_MOOGOLD_FAILED = {"failed", "cancelled", "canceled", "error", "refunded", "rejected"}


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
    # Агар фармоиши пешина ба MooGold тааллуқ дошта бошад
    if existing_order_id.startswith("moo:"):
        ok, tagged = await _moogold_check(existing_order_id[4:])
        if ok is True:
            return True, tagged, False, None
        if ok is None:
            return False, tagged, False, None  # ҳанӯз дар ҷараён — мунтазир мемонем
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
        # failed/cancelled/error — поён фармоиши нав месозем

    if not offer_id:
        logger.error("auto_donate: offer_id холист")
        return False, "", False, None

    # ---- Кӯшиши 1: FazerCards ----
    if config.FAZER_KEY:
        result = await _fazer_order(offer_id, player_id, order_id)
        api_order_id = ""
        if isinstance(result, dict):
            order_block = result.get("order") or {}
            api_order_id = str(order_block.get("id") or result.get("id") or "")
        cost_usd = _extract_cost_usd(result)
        logger.info(f"[COST-DEBUG] auto_donate: cost_usd={cost_usd!r} extracted from order-creation result for offer={offer_id}")

        if result.get("ok") and api_order_id:
            ever_confirmed = False  # оё ягон бор ҷавоби воқеии FazerCards гирифтем
            for _ in range(60):
                await asyncio.sleep(10)
                status_data = await _fazer_status(api_order_id)
                status = ""
                if isinstance(status_data, dict):
                    if status_data.get("ok") is True:
                        ever_confirmed = True
                    status = (status_data.get("order") or {}).get("status") \
                        or status_data.get("status") or ""
                if status == "completed":
                    final_cost = cost_usd or _extract_cost_usd(status_data)
                    logger.info(f"[COST-DEBUG] auto_donate: completed, cost_usd={cost_usd!r} status_data_cost={_extract_cost_usd(status_data)!r} final_cost={final_cost!r}")
                    return True, api_order_id, False, final_cost
                if status in ("failed", "cancelled", "error", "refunded"):
                    break  # ба MooGold мегузарем
            else:
                # 10 дақиқа гузашт, ҳанӯз "processing" (ё ҳамеша таймаут) —
                # мунтазир мемонем, ба MooGold нагузарем (то дучандон
                # фармоиш нашавад)
                return False, api_order_id, not ever_confirmed, cost_usd
        else:
            logger.warning(f"FazerCards фармоиш нашуд, MooGold-ро санҷем: {result}")
    else:
        logger.warning("FAZER_KEY нест — рост ба MooGold мегузарем")

    # ---- Кӯшиши 2: MooGold (fallback) — арзиши воқеӣ маълум нест ----
    success, tagged = await _moogold_fallback(offer_id, player_id)
    return success, tagged, False, None


# ==================== FREE FIRE INDONESIA ====================
async def get_nickname_ffid(player_id: str) -> str:
    """
    Номи аккаунти Free Fire Indonesia.
    Тавассути FazerCards validate-id (RapidAPI ин серверро дастгирӣ намекунад).
    """
    if not config.FAZER_KEY:
        return ""
    headers = {"X-API-Key": config.FAZER_KEY, "Content-Type": "application/json"}
    payload = {"category_id": config.FFID_CATEGORY_VALIDATE, "fields": {"player_id": player_id}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{config.FAZER_BASE}/topups/validate-id",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json(content_type=None)
        logger.info(f"FazerCards FFID validate ({config.FFID_CATEGORY_VALIDATE}, id={player_id}): {data}")
        if not data.get("ok"):
            logger.warning(f"FazerCards FFID validate натиҷаи ok=false: {data}")
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
        logger.warning(f"FazerCards FFID validate хато: {e}")
    return ""


async def auto_donate_ffid(player_id: str, offer_id: str, existing_order_id: str = "", order_id: int | str = ""):
    """Донати худкор барои Free Fire Indonesia."""
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

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит (аз order_id-и худамон), на тасодуфӣ — то агар
        # даъвати такрорӣ шавад (масалан такроран пас аз таймаути шабака),
        # FazerCards онро ҳамон дархост шиносад, на фармоиши дуюм насозад
        "Idempotency-Key": f"ffid-{order_id}" if order_id else str(uuid.uuid4()),
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

    if not offer_id or not config.FAZER_KEY:
        return False, ""

    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит — ниг. изоҳи auto_donate_ffid
        "Idempotency-Key": f"pubg-{order_id}" if order_id else str(uuid.uuid4()),
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


# ==================== TELEGRAM STARS / PREMIUM ====================
async def buy_telegram_stars(username: str, quantity: int, order_id: int | str = ""):
    """
    Харидани Telegram Stars.
    Бармегардонад: (success: bool, order_id: str, uncertain: bool, cost_usd: float | None)
    uncertain=True маънояш: дархост ба FazerCards таймаут задааст ва мо
    ҳатто НАФАҲМИДЕМ фармоиш дар тарафи онҳо сохта шуд ё не — пеш аз
    "Дубора кӯшиш" дар FazerCards санҷед!
    """
    if not config.FAZER_KEY:
        return False, "", False, None
    username = username.lstrip("@")
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        # Калиди собит (аз рӯи order_id-и худамон), на тасодуфӣ — то агар
        # "Дубора кӯшиш" зада шавад, FazerCards ҳамон дархостро такрорӣ
        # шинохта, ФАРМОИШИ ДУЮМ насозад (зидди дучандон харҷ)
        "Idempotency-Key": f"stars-{order_id}" if order_id else str(uuid.uuid4()),
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


async def buy_telegram_premium(username: str, months: int, order_id: int | str = ""):
    """
    Харидани Telegram Premium.
    Бармегардонад: (success: bool, order_id: str, uncertain: bool, cost_usd: float | None)
    uncertain=True — ниг. изоҳи buy_telegram_stars.
    """
    if not config.FAZER_KEY:
        return False, "", False, None
    username = username.lstrip("@")
    headers = {
        "X-API-Key": config.FAZER_KEY,
        "Content-Type": "application/json",
        "Idempotency-Key": f"premium-{order_id}" if order_id else str(uuid.uuid4()),
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
