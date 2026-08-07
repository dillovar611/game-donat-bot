"""
Сервери РЕДИРЕКТ барои силкаҳои ноаён (pay.wineclo.com/<token>).

Мизоҷ силкаи кӯтоҳро мебинад (масалан pay.wineclo.com/aX7k2), сервер онро
ба линки воқеии pay.dc.tj равона мекунад — корт ва домени пардохт пинҳон
мемонанд.

Базаи ҲАМОН ботро истифода мебарад (config + database), пас ягон танзими
иловагӣ лозим нест.

Иҷро дар сервер (як бор):
    pm2 start dcredirect.py --name dcredirect --interpreter python3
Порт: 8899 (ё аз тағйирёбандаи муҳити DCREDIRECT_PORT).
Баъд дар ISPmanager зердомени pay.wineclo.com-ро ба 127.0.0.1:8899 proxy кунед.
"""
import logging
import os

from aiohttp import web

import config  # noqa: F401  — env-ро бор мекунад
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dcredirect")

PORT = int(os.getenv("DCREDIRECT_PORT", "8899"))

# Агар токен ёфт нашавад, ба ин ҷо равона мешавад (сафҳаи хайрия)
FALLBACK_URL = "https://t.me/"


async def handle(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip("/")
    if not token:
        return web.Response(text="OK", status=200)  # санҷиши саломатӣ
    try:
        url = await db.get_pay_url(token)
    except Exception as e:
        logger.error(f"get_pay_url({token}) хато: {e}")
        url = None
    if not url:
        logger.warning(f"Токени ёфтнашуда: {token}")
        raise web.HTTPFound(FALLBACK_URL)
    # 302 → линки воқеии pay.dc.tj
    raise web.HTTPFound(url)


async def _init(app):
    await db.create_pool()
    logger.info(f"dcredirect тайёр — порт {PORT}")


def main():
    app = web.Application()
    app.router.add_get("/", handle)          # санҷиши саломатӣ / решагӣ
    app.router.add_get("/{token}", handle)
    app.on_startup.append(_init)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
