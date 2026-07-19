"""
setup_orderbot.py — ЯКБОРА иҷрошаванда: ҳисоби МАҲДУДИ MySQL (танҳо SELECT
ба orders) месозад ва orderbot_config.py-ро худкор менависад.

Истифода:
    python3 setup_orderbot.py <TOKEN> <NOTIFY_CHAT_ID>

Баъд аз иҷро, ин файлро (setup_orderbot.py)-ро НЕСТ КАРДАН МУМКИН АСТ —
дигар лозим нест ва пароли навро дар матни худ дошт (эҳтиёткорона).
"""
import asyncio
import secrets
import sys

import aiomysql
import config


async def main():
    if len(sys.argv) != 3:
        print("Истифода: python3 setup_orderbot.py <TOKEN> <NOTIFY_CHAT_ID>")
        sys.exit(1)
    token = sys.argv[1]
    notify_chat_id = sys.argv[2]

    new_password = secrets.token_urlsafe(24)
    db_user = "orderbot_ro"
    db_host = config.DB_HOST
    db_port = getattr(config, "DB_PORT", 3306)
    db_name = config.DB_NAME

    conn = await aiomysql.connect(
        host=db_host, port=db_port, user=config.DB_USER,
        password=config.DB_PASSWORD, db=db_name,
    )
    try:
        async with conn.cursor() as cur:
            try:
                await cur.execute(f"DROP USER IF EXISTS '{db_user}'@'{db_host}'")
            except Exception:
                pass
            try:
                await cur.execute(f"DROP USER IF EXISTS '{db_user}'@'localhost'")
            except Exception:
                pass
            # Ҳарду шакли host — то новобаста аз он ки orderbot аз кадом
            # host пайваст мешавад (localhost ё IP), кор кунад
            for host_pattern in ("localhost", "127.0.0.1", "%"):
                try:
                    await cur.execute(
                        f"CREATE USER '{db_user}'@'{host_pattern}' IDENTIFIED BY %s",
                        (new_password,),
                    )
                    await cur.execute(
                        f"GRANT SELECT ON {db_name}.orders TO '{db_user}'@'{host_pattern}'"
                    )
                except Exception as e:
                    print(f"  (огоҳӣ барои host={host_pattern}: {e})")
            await cur.execute("FLUSH PRIVILEGES")
        await conn.commit()
        print(f"✅ Ҳисоби MySQL сохта шуд: {db_user}")
    finally:
        conn.close()

    cfg_content = f'''"""orderbot_config.py — БЕРУН АЗ GIT, дар сервер танҳо мемонад."""
TOKEN = "{token}"
DB_HOST = "{db_host}"
DB_PORT = {db_port}
DB_USER = "{db_user}"
DB_PASSWORD = "{new_password}"
DB_NAME = "{db_name}"
NOTIFY_CHAT_ID = {notify_chat_id}
'''
    with open("orderbot_config.py", "w") as f:
        f.write(cfg_content)
    print("✅ orderbot_config.py навишта шуд")
    print("Акнун метавонед: pm2 start orderbot.py --name orderbot --interpreter python3")


if __name__ == "__main__":
    asyncio.run(main())
