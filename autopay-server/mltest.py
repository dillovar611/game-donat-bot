"""
Скрипти ТАШХИСИ Mobile Legends (дар шелли сервер иҷро мешавад).

Чӣ кор мекунад:
  1. Месанҷад кадом category_id воқеан оффер дорад
  2. Агар Player ID + Server ID диҳед — месанҷад кадом ҷуфти номи
     майдон FazerCards қабул мекунад

Ҳељ пул сарф намекунад ва ҳељ донат намекунад — танҳо мепурсад.

Тарзи истифода (дар /var/www/ws2562/data):
    python3 mltest.py                       # танҳо категорияҳоро месанҷад
    python3 mltest.py 123456789 2001        # инчунин номи майдонҳоро месанҷад
"""
import json
import sys
import urllib.error
import urllib.request

import env  # noqa: F401  — калидҳоро ба муҳит бор мекунад
import config

# Худи ҳамон номҳое, ки бот месанҷад
CATEGORIES = [
    "mobile_legends_global", "mobile_legends", "mobile_legends_auto",
    "mobile_legends_cis", "mobile_legends_bang_bang", "mobilelegends",
    "mlbb", "mlbb_global", "mlbb_auto", "ml_global", "ml",
    "mobile_legends_gl", "mobile_legend", "moba_mobile_legends",
]

FIELD_PAIRS = [
    ("player_id", "server_id"),
    ("user_id", "zone_id"),
    ("player_id", "zone_id"),
    ("user_id", "server_id"),
    ("uid", "zone"),
    ("userid", "zoneid"),
    ("account_id", "server_id"),
    ("id", "server"),
]


def _call(url: str, payload=None):
    """Дархост мефиристад ва (код, ҷавоб)-ро бармегардонад."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"X-API-Key": config.FAZER_KEY,
                 "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return "ХАТО", {"error": str(e)}


def _offers_of(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("offers") or data.get("data") or data.get("result") or []
    return []


def _name_in(data):
    """Номи аккаунтро аз ҷавоб мебарорад (агар бошад)."""
    if not isinstance(data, dict):
        return ""
    for src in (data, data.get("data") or {}, data.get("result") or {}):
        if not isinstance(src, dict):
            continue
        for key in ("player_name", "username", "nickname", "name"):
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def main():
    if not config.FAZER_KEY:
        print("❌ FAZER_KEY холӣ аст — скрипт аз папкаи бот иҷро шавад.")
        return
    base = config.FAZER_BASE
    print(f"🌐 {base}\n")

    print("=" * 52)
    print("1️⃣  ҶУСТУҶӮИ category_id")
    print("=" * 52)
    working = []
    for cid in CATEGORIES:
        status, data = _call(f"{base}/topups/offers?category_id={cid}")
        offers = _offers_of(data)
        if offers:
            working.append(cid)
            first = offers[0]
            name = first.get("name") or first.get("title") or "—"
            print(f"✅ {cid:28} {len(offers):>3} оффер · {name}")
        else:
            print(f"❌ {cid:28} ({status})")

    if not working:
        print("\n⚠️ Ягон category_id кор накард.")
        return
    print(f"\n🔑 Кор мекунанд: {', '.join(working)}")

    if len(sys.argv) < 3:
        print("\nℹ️ Барои санҷиши номи майдонҳо ID илова кунед:")
        print("   python3 mltest.py <PlayerID> <ServerID>")
        return

    player_id, server_id = sys.argv[1], sys.argv[2]
    print()
    print("=" * 52)
    print(f"2️⃣  НОМИ МАЙДОНҲО ({player_id} / {server_id})")
    print("=" * 52)
    cid = working[0]
    print(f"   category_id: {cid}\n")
    found = []
    for f_player, f_server in FIELD_PAIRS:
        status, data = _call(
            f"{base}/topups/validate-id",
            {"category_id": cid,
             "fields": {f_player: player_id, f_server: server_id}},
        )
        name = _name_in(data)
        pair = f"{f_player} | {f_server}"
        if name:
            found.append(pair)
            print(f"✅ {pair:28} → 👤 {name}")
        else:
            err = ""
            if isinstance(data, dict):
                err = str(data.get("error") or data.get("message") or "")[:45]
            print(f"❌ {pair:28} ({status}) {err}")

    print()
    if found:
        print("🎯 ИН САТРРО ба «⚙️ Танзимоти API» гузоред:")
        print(f"\n   {cid} | {found[0]}\n")
    else:
        print("⚠️ Ягон ҷуфти ном номи аккаунтро надод.")
        print("   Эҳтимол ин категория санҷиши номро дастгирӣ намекунад —")
        print("   ин ҲАНӮЗ маънои онро надорад, ки донат кор намекунад.")
        print(f"   Бо ҳамин кӯшиш кунед: {cid} | player_id | server_id")
        print("   ва бо ЯК хариди хурди воқеӣ санҷед.")


if __name__ == "__main__":
    main()
