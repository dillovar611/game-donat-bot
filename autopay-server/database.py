"""
Кор бо базаи MySQL.
БЕ БАЛАНС — танҳо корбарон, маҳсулотҳо ва фармоишҳо.
"""
import aiomysql
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import config

TJ_TZ = ZoneInfo("Asia/Dushanbe")

logger = logging.getLogger(__name__)

pool = None  # пул дар create_pool() сохта мешавад


# ==================== ПАЙВАСТ ====================
async def create_pool():
    """Пули пайвастро месозад. Session timezone ба вақти Тоҷикистон
    (+05:00) гузошта мешавад, то NOW()/CURRENT_TIMESTAMP/CURDATE() дар
    MySQL бо вақти сервери Тоҷикистон корбар кунанд, новобаста аз
    минтақаи вақти системавии сервери боти мо."""
    global pool
    pool = await aiomysql.create_pool(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        db=config.DB_NAME,
        autocommit=True,
        charset="utf8mb4",
        minsize=3,
        maxsize=40,   # 10 барои ҳаҷми баланд кам буд — пайвастҳо навбат
                      # мекашиданд ва барои баъзе мизоҷон бот "намекард"
        init_command="SET time_zone = '+05:00'",
    )
    logger.info("✅ База пайваст шуд (timezone: +05:00 Тоҷикистон)")


async def init_db():
    """Ҷадвалҳоро месозад (агар набошанд)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # ---- Корбарон ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    full_name VARCHAR(255),
                    terms_accepted TINYINT DEFAULT 0,
                    is_blocked TINYINT DEFAULT 0,
                    is_banned TINYINT DEFAULT 0,
                    ban_reason VARCHAR(255) DEFAULT '',
                    discount_percent DECIMAL(5,2) DEFAULT 0,
                    reminder_noorder_sent TINYINT DEFAULT 0,
                    reminder_discount3_sent TINYINT DEFAULT 0,
                    reminder_discount5_sent TINYINT DEFAULT 0,
                    discount3_used TINYINT DEFAULT 0,
                    discount5_used TINYINT DEFAULT 0,
                    referrer_id BIGINT DEFAULT NULL,
                    referral_balance DECIMAL(10,2) DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Барои database-и кӯҳна (агар ҷадвали users аллакай вуҷуд дошта бошад
            # бе ин сутунҳо) — иловаи бехатар, хатогиро нодида мегирад агар
            # сутун аллакай мавҷуд бошад
            for ddl in (
                "ALTER TABLE users ADD COLUMN is_banned TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN ban_reason VARCHAR(255) DEFAULT ''",
                "ALTER TABLE users ADD COLUMN discount_percent DECIMAL(5,2) DEFAULT 0",
                "ALTER TABLE users ADD COLUMN reminder_noorder_sent TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN reminder_discount3_sent TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN reminder_discount5_sent TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN discount3_used TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN discount5_used TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN referrer_id BIGINT DEFAULT NULL",
                "ALTER TABLE users ADD COLUMN referral_balance DECIMAL(10,2) DEFAULT 0",
                "ALTER TABLE users ADD COLUMN winback_sent TINYINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN winback_active TINYINT DEFAULT 0",
                # №1 — тахфифи 1% баъди хариди муваффақ (то ин вақт фаъол)
                "ALTER TABLE users ADD COLUMN reoffer_until DATETIME DEFAULT NULL",
            ):
                try:
                    await cur.execute(ddl)
                except Exception:
                    pass  # сутун аллакай вуҷуд дорад
            # ---- Маҳсулотҳо (алмазҳои Free Fire) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    amount INT NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    label VARCHAR(255),
                    offer_id VARCHAR(255),
                    is_active TINYINT DEFAULT 1,
                    sort_order INT DEFAULT 0
                )
            """)
            try:
                await cur.execute("ALTER TABLE products ADD COLUMN is_featured TINYINT DEFAULT 0")
            except Exception:
                pass
            # ---- Танзимот (key-value, барои рақами корти ДС, leaderboard ва ғ.) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key_name VARCHAR(100) PRIMARY KEY,
                    value TEXT
                )
            """)
            # ---- Таърихи баланс (ҳар пуркунӣ/харид/мукофот/баргардонӣ) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS balance_transactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    tx_type VARCHAR(30) NOT NULL,
                    order_id INT DEFAULT NULL,
                    balance_after DECIMAL(10,2) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX (user_id, created_at)
                )
            """)
            # ---- Хариди интизорӣ (агар мизоҷ бо "норасогӣ" пур карда бошад —
            # баъди пуркунӣ ин харид худкор анҷом дода мешавад) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_purchases (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    topup_order_id INT NOT NULL,
                    user_id BIGINT NOT NULL,
                    game_id VARCHAR(255) NOT NULL,
                    nickname VARCHAR(255) DEFAULT '',
                    amount INT DEFAULT 0,
                    price DECIMAL(10,2) NOT NULL,
                    label VARCHAR(255) NOT NULL,
                    offer_id VARCHAR(255) DEFAULT '',
                    fulfilled TINYINT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX (topup_order_id)
                )
            """)
            # ---- Комбоҳо (бандли якчанд маҳсулот бо нархи ягона) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS combos (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    label VARCHAR(255) NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    is_active TINYINT DEFAULT 1,
                    sort_order INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS combo_items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    combo_id INT NOT NULL,
                    product_id INT DEFAULT NULL,
                    custom_label VARCHAR(255) DEFAULT NULL,
                    quantity INT DEFAULT 1
                )
            """)
            # ---- Фармоишҳо ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    game_id VARCHAR(100),
                    nickname VARCHAR(255),
                    amount INT,
                    price DECIMAL(10,2),
                    label VARCHAR(255),
                    offer_id VARCHAR(255),
                    payment_method VARCHAR(50),
                    status VARCHAR(50) DEFAULT 'pending',
                    api_order_id VARCHAR(255),
                    check_file_id VARCHAR(255),
                    paid_with_referral_balance TINYINT DEFAULT 0,
                    referral_credited TINYINT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for ddl in (
                "ALTER TABLE orders ADD COLUMN paid_with_referral_balance TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN referral_credited TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN order_group_id VARCHAR(64) DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN check_hash VARCHAR(64) DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN stale_reminder_sent TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN donating_at DATETIME DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN reject_reason VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN cost_tjs DECIMAL(10,2) DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN uncertain_flagged TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN expiry_warned TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN late_recovered TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN combo_id INT DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN is_balance_topup TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN confirmed_at DATETIME DEFAULT NULL",
                "ALTER TABLE orders ADD COLUMN recheck_tries INT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN nudge_sent TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN admin_alerted TINYINT DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN auto_retried TINYINT DEFAULT 0",
            ):
                try:
                    await cur.execute(ddl)
                except Exception:
                    pass

            # ---- Нархи шахсии мизоҷ (VIP pricing) — танҳо барои FF СНГ ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS custom_prices (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    product_id INT,
                    custom_price DECIMAL(10,2),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_user_product (user_id, product_id)
                )
            """)

            # ---- DC Next: Kod-ҳои дидашуда (автопардохт, зидди такрор) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS dc_kods (
                    kod VARCHAR(64) PRIMARY KEY,
                    summa DECIMAL(10,2),
                    matched_order_id INT DEFAULT NULL,
                    received_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # ---- Силкаҳои ноаён (redirect): токен → линки воқеии pay.dc.tj ----
            # Мизоҷ танҳо pay.wineclo.com/<токен>-ро мебинад; сервер онро ба
            # линки воқеӣ равона мекунад (корт ва домен пинҳон мемонанд).
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS pay_links (
                    token VARCHAR(24) PRIMARY KEY,
                    url TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ---- Хотираи доимии FSM (то рестарт ҳолати мизоҷонро гум накунад) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS fsm_states (
                    storage_key VARCHAR(255) PRIMARY KEY,
                    state VARCHAR(255) NULL,
                    data MEDIUMTEXT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                )
            """)

            # ---- Standoff 2: голд (донати ДАСТӢ — donatov.net API надорад,
            # пас offer_id нест; соҳиб худаш иҷро мекунад) ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS standoff_products (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    amount INT NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    label VARCHAR(255),
                    is_active TINYINT DEFAULT 1,
                    sort_order INT DEFAULT 0
                )
            """)

            # ---- Free Fire Brazil: донати ХУДКОР (FazerCards, category
            # free_fire_br) — пас offer_id ҲАСТ, мисли FF СНГ ----
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS ffbr_products (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    amount INT NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    label VARCHAR(255),
                    offer_id VARCHAR(255),
                    is_active TINYINT DEFAULT 1,
                    sort_order INT DEFAULT 0
                )
            """)
            # Mobile Legends — ба ҷуз Player ID боз Server (Zone) ID лозим аст,
            # вале ин ба МАҲСУЛОТ дахл надорад (мизоҷ онро менависад), пас
            # ҷадвал мисли ffbr_products аст.
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS ml_products (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    amount INT NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    label VARCHAR(255),
                    offer_id VARCHAR(255),
                    is_active TINYINT DEFAULT 1,
                    sort_order INT DEFAULT 0
                )
            """)
    # Агар маҲсулот набошад, намунаҲои пешфарзро илова мекунем
    await _seed_default_products()


async def _seed_default_products():
    """Агар ҷадвали products холӣ бошад, нархҳои пешфарзро мегузорад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM products")
            count = (await cur.fetchone())[0]
            if count and count > 0:
                return
            # (amount, price, label, offer_id) — offer_id аз FazerCards FF CIS
            defaults = [
                (100,  10.00, "💎 100 (+10 bonus)",   "110_diamonds"),
                (310,  28.00, "💎 310 (+31 bonus)",   "341_diamonds"),
                (520,  46.00, "💎 520 (+52 bonus)",   "572_diamonds"),
                (1060, 90.00, "💎 1060 (+106 bonus)", "1166_diamonds"),
                (2180, 180.00, "💎 2180 (+218 bonus)", "2398_diamonds"),
                (5600, 450.00, "💎 5600 (+560 bonus)", "6160_diamonds"),
            ]
            for i, (amount, price, label, offer_id) in enumerate(defaults):
                is_featured = 1 if amount == 520 else 0
                await cur.execute(
                    "INSERT INTO products (amount, price, label, offer_id, sort_order, is_featured) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (amount, price, label, offer_id, i, is_featured)
                )
            logger.info("✅ Нархҳои пешфарз илова шуданд")


# ==================== КОРБАРОН ====================
async def get_user(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            return await cur.fetchone()


async def add_user(user_id: int, username: str, full_name: str) -> bool:
    """
    Корбари навро сабт мекунад (агар аллакай вуҷуд дошта бошад, нодида
    мегирад). True бармегардонад агар корбар ВОҦЕАН нав сабт шуда бошад
    (барои донистани он ки бояд referrer гузошта шавад ё на).
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # ON DUPLICATE KEY UPDATE id=id ба ҷои INSERT IGNORE:
            # INSERT IGNORE ҳар бор дар лог "Warning: Duplicate entry ...
            # for key 'PRIMARY'" менависад — ва азбаски ин функсия дар ҳар
            # ҳаракати ҳар корбар даъват мешавад, лог пур аз огоҳии беҳуда
            # мешуд ва хатоҳои ВОҚЕӢ дар байнашон гум мешуданд.
            # Маънои rowcount бетағйир аст: 1 = корбари НАВ сабт шуд,
            # 0 = аллакай буд (чунки id=id ҳељ чизро иваз намекунад).
            await cur.execute(
                "INSERT INTO users (id, username, full_name) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE id=id",
                (user_id, username, full_name)
            )
            return cur.rowcount > 0


async def is_terms_accepted(user_id: int) -> bool:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT terms_accepted FROM users WHERE id=%s", (user_id,))
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def accept_terms(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE users SET terms_accepted=1 WHERE id=%s", (user_id,))


async def get_all_users():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM users ORDER BY id DESC")
            return await cur.fetchall()


# ==================== РЕФЕРРАЛ ====================
async def set_referrer(user_id: int, referrer_id: int):
    """
    Корбари НАВРО ба referrer пайваст мекунад. Танҳо агар:
      - корбар ҲАНУЗ referrer надошта бошад (бори аввал)
      - корбар худашро referrer-и худ нагузорад
    """
    if user_id == referrer_id:
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET referrer_id=%s WHERE id=%s AND referrer_id IS NULL",
                (referrer_id, user_id)
            )


async def get_referrer_id(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT referrer_id FROM users WHERE id=%s", (user_id,))
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def get_referral_balance(user_id: int) -> float:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT referral_balance FROM users WHERE id=%s", (user_id,)
            )
            row = await cur.fetchone()
            return float(row[0]) if row and row[0] else 0.0


async def get_balance_transactions(user_id: int, limit: int = 15) -> list:
    """Таърихи охирини тағйири баланси корбар (пуркунӣ/харид/мукофот/баргардонӣ)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT amount, tx_type, order_id, balance_after, created_at "
                "FROM balance_transactions WHERE user_id=%s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (user_id, limit)
            )
            return await cur.fetchall()


async def admin_adjust_balance(user_id: int, amount: float, reason: str = "") -> tuple:
    """
    Дастӣ (аз тарафи админ) балансро иваз мекунад — мусбат = илова, манфӣ = кам.
    Дар ЯК транзаксия бо сабти таърих. Агар кам кардан аз баланси мавҷуда зиёд
    бошад, рад мекунад (баланс манфӣ намешавад).
    Бармегардонад: (ok: bool, old_balance: float, new_balance: float)
    """
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT referral_balance FROM users WHERE id=%s FOR UPDATE", (user_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.rollback()
                    return False, 0.0, 0.0
                old_balance = float(row[0] or 0)
                new_balance = round(old_balance + amount, 2)
                if new_balance < 0:
                    await conn.rollback()
                    return False, old_balance, old_balance
                await cur.execute(
                    "UPDATE users SET referral_balance=%s WHERE id=%s",
                    (new_balance, user_id)
                )
                await _log_balance_tx(cur, user_id, amount, "admin_adjust")
            await conn.commit()
            return True, old_balance, new_balance
        except Exception:
            await conn.rollback()
            raise


async def get_balance_summary() -> dict:
    """Ҳисоботи умумии балансҳо — қарзи умумӣ, шумораи дорандагон, ҳаракати имрӯз."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(referral_balance),0) AS total, "
                "COUNT(*) AS holders FROM users WHERE referral_balance > 0"
            )
            row = await cur.fetchone()
            total = float(row["total"] or 0)
            holders = row["holders"] or 0

            await cur.execute(
                "SELECT COALESCE(SUM(amount),0) AS s FROM balance_transactions "
                "WHERE tx_type='topup' AND DATE(created_at)=CURDATE()"
            )
            topup_today = float((await cur.fetchone())["s"] or 0)

            await cur.execute(
                "SELECT COALESCE(SUM(-amount),0) AS s FROM balance_transactions "
                "WHERE tx_type='purchase' AND DATE(created_at)=CURDATE()"
            )
            spent_today = float((await cur.fetchone())["s"] or 0)

            await cur.execute(
                "SELECT COALESCE(SUM(amount),0) AS s FROM balance_transactions "
                "WHERE tx_type='topup'"
            )
            topup_all = float((await cur.fetchone())["s"] or 0)

            return {
                "total": total,
                "holders": holders,
                "topup_today": topup_today,
                "spent_today": spent_today,
                "topup_all": topup_all,
            }


async def count_users_with_balance() -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM users WHERE referral_balance > 0")
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_users_with_balance(offset: int = 0, limit: int = 15) -> list:
    """Рӯйхати ҲАМАИ корбароне, ки баланс доранд — аз зиёдтарин ба камтарин."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, full_name, referral_balance "
                "FROM users WHERE referral_balance > 0 "
                "ORDER BY referral_balance DESC, id LIMIT %s OFFSET %s",
                (limit, offset)
            )
            return await cur.fetchall()


async def create_pending_purchase(topup_order_id: int, user_id: int, game_id: str,
                                   nickname: str, amount, price: float, label: str,
                                   offer_id: str) -> int:
    """Ниятҳои хариди мизоҷро сабт мекунад — то баъди пуркунии баланс худкор анҷом дода шавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO pending_purchases "
                "(topup_order_id, user_id, game_id, nickname, amount, price, label, offer_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (topup_order_id, user_id, game_id, nickname, amount, price, label, offer_id)
            )
            return cur.lastrowid


async def get_pending_purchase_for_topup(topup_order_id: int):
    """Хариди интизорие, ки ба ин фармоиши пуркунӣ вобаста аст (агар ҳанӯз иҷро нашуда бошад)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM pending_purchases WHERE topup_order_id=%s AND fulfilled=0 LIMIT 1",
                (topup_order_id,)
            )
            return await cur.fetchone()


async def mark_pending_purchase_fulfilled(pending_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE pending_purchases SET fulfilled=1 WHERE id=%s", (pending_id,)
            )


async def get_referral_count(user_id: int) -> int:
    """Шумораи корбароне, ки тариқи ин референдат ба бот пайваст шудаанд."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM users WHERE referrer_id=%s", (user_id,)
            )
            return (await cur.fetchone())[0]


async def get_referral_subusers(referrer_id: int):
    """
    Рӯйхати ҲАМАИ зердастони ин корбар, бо ҷамъи маблаге, ки ҳар як аз
    онҷо ба референдер овардааст (аз orders, ки referral_credited=1 доранд).
    Натиҷа: [{"id":..., "username":..., "full_name":..., "earned": 12.5}, ...]
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, full_name FROM users "
                "WHERE referrer_id=%s ORDER BY id DESC",
                (referrer_id,)
            )
            subusers = await cur.fetchall()
            if not subusers:
                return []
            percent = config.REFERRAL_PERCENT
            for u in subusers:
                # DictCursor натиҷаро ҳамчун луғат бармегардонад — барои ҳамин
                # алиас (AS s) лозим аст, вагарна [0] хатои KeyError медиҳад
                await cur.execute(
                    "SELECT COALESCE(SUM(price), 0) AS s FROM orders "
                    "WHERE user_id=%s AND referral_credited=1",
                    (u["id"],)
                )
                row = await cur.fetchone()
                order_sum = (row["s"] if row else 0) or 0
                u["earned"] = round(float(order_sum) * percent / 100, 2)
            return subusers


async def _log_balance_tx(cur, user_id: int, amount: float, tx_type: str, order_id: int = None):
    """
    Як сатр ба таърихи баланс сабт мекунад — ҳамеша дар ҲАМОН cursor/пайвасте,
    ки худи тағйири баланс аллакай дар он иҷро шудааст, то balance_after
    ҳамеша дуруст бошад (бе равзанаи race бо навсозии дигар).
    """
    await cur.execute("SELECT referral_balance FROM users WHERE id=%s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        balance_after = 0.0
    elif isinstance(row, dict):
        balance_after = float(row["referral_balance"])
    else:
        balance_after = float(row[0])
    await cur.execute(
        "INSERT INTO balance_transactions (user_id, amount, tx_type, order_id, balance_after) "
        "VALUES (%s,%s,%s,%s,%s)",
        (user_id, amount, tx_type, order_id, balance_after)
    )


async def add_referral_earning(referrer_id: int, amount: float, order_id: int = None):
    """Ба балансаи реферралии корбар маблаг илова мекунад (баргардонии фармоиши радшуда).
    Дар як транзаксия — то навсозии баланс ва сабти таърих якҷоя commit шаванд."""
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE users SET referral_balance = referral_balance + %s WHERE id=%s",
                    (amount, referrer_id)
                )
                await _log_balance_tx(cur, referrer_id, amount, "refund", order_id)
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


async def credit_balance_topup(order_id: int, user_id: int, amount: float) -> bool:
    """
    Атомикӣ (ЯК транзаксия): фармоиши пуркунии баланс (is_balance_topup=1)-ро
    ба 'confirmed' мегузаронад ва маблағро ба баланси корбар илова мекунад —
    ФАҚАТ агар ҳанӯз коркард нашуда бошад (зидди дукаратшавӣ). Ҳарду навсозӣ
    ва сабти таърих дар ЯК commit — то агар байнашон сервер қатъ шавад, ё
    ҳарду шаванд ё ҳељкадом (баланс нопурра намемонад).
    """
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE orders SET status='confirmed', confirmed_at=NOW() "
                    "WHERE id=%s AND status != 'confirmed'",
                    (order_id,)
                )
                if cur.rowcount == 0:
                    await conn.rollback()
                    return False
                await cur.execute(
                    "UPDATE users SET referral_balance = referral_balance + %s WHERE id=%s",
                    (amount, user_id)
                )
                await _log_balance_tx(cur, user_id, amount, "topup", order_id)
            await conn.commit()
            return True
        except Exception:
            await conn.rollback()
            raise


async def deduct_referral_balance(user_id: int, amount: float):
    """
    Аз баланси корбар маблаг кам мекунад, ФАҦАТ агар баланс кофӣ бошад.
    Дар ЯК транзаксия. Бармегардонад:
      • балансро БАЪДИ кам кардан (float) агар муваффақ шуд;
      • None агар баланс кам бошад.
    Ин баланси дақиқ дар ДОХИЛИ ҳамон транзаксия хонда мешавад — то паёми
    админ рақами дурустро нишон диҳад, на балансе, ки фармоиши ҳамзамони
    дигар дертар тағйир додааст (race дар НАМОИШ).
    """
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE users SET referral_balance = referral_balance - %s "
                    "WHERE id=%s AND referral_balance >= %s",
                    (amount, user_id, amount)
                )
                if cur.rowcount == 0:
                    await conn.rollback()
                    return None
                await _log_balance_tx(cur, user_id, -amount, "purchase")
                await cur.execute(
                    "SELECT referral_balance FROM users WHERE id=%s", (user_id,))
                row = await cur.fetchone()
                bal_after = float(row[0]) if row else 0.0
            await conn.commit()
            return bal_after
        except Exception:
            await conn.rollback()
            raise


async def credit_referral_for_order(order_id: int, percent: float = None):
    """
    Агар ин фармоиш аллакай мукофоти referral надода бошад, ва корбараш
    referrer дошта бошад, 5%-и нархи фармоишро ба балансаи referrer
    илова мекунад ва фламми referral_credited-ро мегузорад (то такрор
    нашавад). Маблаги мукофотро бармегардонад (0 агар ҳеч чиз дода
    нашуд), бо ID ва username-и referrer (барои хабардор кардан).
    """
    if percent is None:
        percent = config.REFERRAL_PERCENT
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Атомикӣ ҳуқуқи кредитро мегирем — фақат ЯК даъват метавонад
                # referral_credited-ро аз 0 ба 1 гузаронад (зидди дукаратшавӣ
                # ҳангоми ду даъвати ҳамзамон)
                await cur.execute(
                    "UPDATE orders SET referral_credited=1 WHERE id=%s AND referral_credited=0",
                    (order_id,)
                )
                if cur.rowcount == 0:
                    await conn.rollback()
                    return 0.0, None
                await cur.execute(
                    "SELECT user_id, price FROM orders WHERE id=%s", (order_id,)
                )
                order = await cur.fetchone()
                await cur.execute(
                    "SELECT referrer_id FROM users WHERE id=%s", (order["user_id"],)
                )
                urow = await cur.fetchone()
                referrer_id = urow["referrer_id"] if urow else None
                if not referrer_id:
                    # Referrer нест — кредит лозим нест, флагро бармегардонем
                    await conn.rollback()
                    return 0.0, None
                # ГИРД мекунем: сутуни баланс DECIMAL(10,2) аст ва MySQL
                # худаш гирд мекунад, вале Python ба мизоҷ рақами гирднашуда
                # менависад — 1.345 дар база 1.35 мешуд, дар паём 1.34.
                # Фарқи як тин дар ҳар мукофот ҷамъ мешавад.
                reward = round(float(order["price"]) * percent / 100, 2)
                await cur.execute(
                    "UPDATE users SET referral_balance = referral_balance + %s WHERE id=%s",
                    (reward, referrer_id)
                )
                await _log_balance_tx(cur, referrer_id, reward, "referral_reward", order_id)
            await conn.commit()
            return reward, referrer_id
        except Exception:
            await conn.rollback()
            raise


# ==================== МАҲСУЛОТҲО ====================
async def get_products():
    """Ҳамаи маҳсулотҳои фаъол."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM products WHERE is_active=1 ORDER BY sort_order, amount"
            )
            return await cur.fetchall()


# ==================== НАРХИ ШАХСИИ МИЗОҶ (VIP PRICING, ФАҦАТ FF СНГ) ====================
async def set_custom_price(user_id: int, product_id: int, price: float):
    """Нархи шахсии як маҳсулотро барои корбар мегузорад (илова/тавсиа)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO custom_prices (user_id, product_id, custom_price) "
                "VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE custom_price=%s",
                (user_id, product_id, price, price)
            )


async def get_custom_price(user_id: int, product_id: int):
    """Нархи шахсии як маҳсулот барои корбарро мебиёрад (ё None агар набошад)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT custom_price FROM custom_prices WHERE user_id=%s AND product_id=%s",
                (user_id, product_id)
            )
            row = await cur.fetchone()
            return float(row[0]) if row else None


async def get_custom_prices_for_user(user_id: int):
    """Ҳамаи нархҳои шахсии як корбар (бо маводи маҳсулот якҷоя)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT cp.product_id, cp.custom_price, p.label, p.amount, p.price AS default_price "
                "FROM custom_prices cp "
                "JOIN products p ON p.id = cp.product_id "
                "WHERE cp.user_id=%s "
                "ORDER BY p.sort_order, p.amount",
                (user_id,)
            )
            return await cur.fetchall()


async def delete_custom_price(user_id: int, product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM custom_prices WHERE user_id=%s AND product_id=%s",
                (user_id, product_id)
            )


async def get_all_products():
    """Ҳамаи маҳсулотҳо (барои админ)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM products ORDER BY sort_order, amount")
            return await cur.fetchall()


async def get_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def add_product(amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO products (amount, price, label, offer_id) VALUES (%s,%s,%s,%s)",
                (amount, price, label, offer_id)
            )


# ==================== ТӮҲФАИ ТАСОДУФӢ (ҳар N фармоиши тасдиқшуда) ====================
async def count_confirmed_orders() -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM orders WHERE status='confirmed' AND is_balance_topup=0")
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_confirmed_batch_user_ids(offset: int, limit: int) -> list:
    """user_id-и як 'порсия'-и фармоишҳои тасдиқшуда (аз рӯи тартиби id) — барои интихоби тасодуфии барандаи тӯҳфа."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM orders WHERE status='confirmed' AND is_balance_topup=0 "
                "ORDER BY id ASC LIMIT %s OFFSET %s",
                (limit, offset)
            )
            return [r[0] for r in await cur.fetchall()]


async def get_confirmed_batch_user_ids_recent(offset: int, limit: int, hours: int = 24) -> list:
    """
    Мисли get_confirmed_batch_user_ids, вале танҳо онҳое, ки фармоишашон дар
    N соати охир будааст — барои огоҳии "наздикӣ", то ба мизоҷони кайҳо
    харидакарда (шояд фаромӯш карда) нафиристем, балки танҳо ба онҳое, ки
    ҳанӯз фаъоланд.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM ("
                "    SELECT user_id, created_at FROM orders WHERE status='confirmed' AND is_balance_topup=0 "
                "    ORDER BY id ASC LIMIT %s OFFSET %s"
                ") t WHERE created_at >= NOW() - INTERVAL %s HOUR",
                (limit, offset, hours)
            )
            return [r[0] for r in await cur.fetchall()]


async def get_recent_confirmed_user_ids(limit: int) -> list:
    """user_id-и N фармоиши ОХИРИНИ тасдиқшуда (аз кӯҳна ба нав) —
    барои санҷиши чархи тӯҳфа дар админ-панел."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM ("
                "  SELECT id, user_id FROM orders WHERE status='confirmed' "
                "  AND is_balance_topup=0 ORDER BY id DESC LIMIT %s"
                ") t ORDER BY t.id ASC", (limit,))
            return [r[0] for r in await cur.fetchall()]


async def get_display_names(user_ids: list) -> dict:
    """
    Барои ҳар user_id номи намоишӣ бармегардонад — барои чархи тӯҳфа.
    Тартиб: username → калимаи аввали ном → «Мизоҷ».
    """
    out = {}
    if not user_ids:
        return out
    uniq = list({int(u) for u in user_ids})
    ph = ",".join(["%s"] * len(uniq))
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"SELECT id, username, full_name FROM users WHERE id IN ({ph})", uniq)
            for row in await cur.fetchall() or []:
                nm = (row.get("username") or "").strip()
                if not nm:
                    full = (row.get("full_name") or "").strip()
                    nm = full.split()[0] if full else ""
                out[int(row["id"])] = nm or "Мизоҷ"
    for u in uniq:
        out.setdefault(u, "Мизоҷ")
    return out


async def get_last_giveaway_winner() -> dict | None:
    """Мизоҷи охирине, ки тӯҳфаи ройгон бурдааст (барои намоиши иҷтимоӣ)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT o.user_id, u.username, u.full_name FROM orders o "
                "LEFT JOIN users u ON u.id = o.user_id "
                "WHERE o.payment_method='giveaway' AND o.status='confirmed' "
                "ORDER BY o.id DESC LIMIT 1"
            )
            return await cur.fetchone()


async def count_giveaway_wins() -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM orders WHERE payment_method='giveaway' AND status='confirmed'"
            )
            row = await cur.fetchone()
            return row[0] if row else 0


async def update_product(product_id: int, amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE products SET amount=%s, price=%s, label=%s, offer_id=%s WHERE id=%s",
                (amount, price, label, offer_id, product_id)
            )


async def toggle_product_featured(product_id: int):
    """
    Маҳсулотро ба ҳолати "🔥 Маъмултарин" мегузорад — танҳо ЯК маҳсулот
    метавонад дар як вақт featured бошад, пас аввал ҳамаро хомӯш мекунад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT is_featured FROM products WHERE id=%s", (product_id,))
            row = await cur.fetchone()
            currently_featured = bool(row and row[0])
            await cur.execute("UPDATE products SET is_featured=0")
            if not currently_featured:
                await cur.execute("UPDATE products SET is_featured=1 WHERE id=%s", (product_id,))


async def delete_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM products WHERE id=%s", (product_id,))


# ==================== КОМБОҲО ====================
async def create_combo(label: str, price: float) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO combos (label, price) VALUES (%s,%s)", (label, price)
            )
            return cur.lastrowid


async def get_combos() -> list:
    """Ҳамаи комбоҳо (фаъол ва ғайрифаъол) — барои админ."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM combos ORDER BY sort_order, id")
            return await cur.fetchall()


async def get_active_combos() -> list:
    """Танҳо комбоҳои фаъол — барои мизоҷ."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM combos WHERE is_active=1 ORDER BY sort_order, id"
            )
            return await cur.fetchall()


async def get_combo(combo_id: int) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM combos WHERE id=%s", (combo_id,))
            return await cur.fetchone()


async def toggle_combo_active(combo_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE combos SET is_active = 1 - is_active WHERE id=%s", (combo_id,)
            )


async def delete_combo(combo_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM combo_items WHERE combo_id=%s", (combo_id,))
            await cur.execute("DELETE FROM combos WHERE id=%s", (combo_id,))


async def clear_combo_items(combo_id: int):
    """Ҳамаи қисмҳои комборо нест мекунад — барои таҳрир (нест + аз нав сабт)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM combo_items WHERE combo_id=%s", (combo_id,))


async def update_combo(combo_id: int, label: str, price: float):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE combos SET label=%s, price=%s WHERE id=%s", (label, price, combo_id)
            )


async def add_combo_item(combo_id: int, product_id: int | None, custom_label: str | None, quantity: int = 1):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO combo_items (combo_id, product_id, custom_label, quantity) "
                "VALUES (%s,%s,%s,%s)",
                (combo_id, product_id, custom_label, quantity)
            )


async def get_combo_items(combo_id: int) -> list:
    """Ҳар қисми комбо — агар аз маҳсулоти мавҷуда бошад, ном/миқдорашро
    аз ҷадвали products мегирад; агар дастӣ (custom_label) бошад, ҳамонро."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT ci.*, p.label AS product_label, p.amount AS product_amount, "
                "p.offer_id AS product_offer_id FROM combo_items ci "
                "LEFT JOIN products p ON p.id = ci.product_id "
                "WHERE ci.combo_id=%s ORDER BY ci.id",
                (combo_id,)
            )
            return await cur.fetchall()


# ==================== ФАРМОИШҲО ====================
async def create_order(user_id, game_id, nickname, amount, price, label, offer_id,
                        payment_method, order_group_id=None, combo_id=None):
    """Фармоиши нав месозад ва ID-ро бармегардонад. order_group_id — барои
    сабад (якчанд маҳсулот дар як пардохт), фармоишҳои як гурӯҳро мепайвандад.
    combo_id — агар ин фармоиш харидани комбо бошад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO orders
                    (user_id, game_id, nickname, amount, price, label, offer_id,
                     payment_method, order_group_id, combo_id, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            """, (user_id, game_id, nickname, amount, price, label, offer_id,
                  payment_method, order_group_id, combo_id))
            return cur.lastrowid


async def get_orders_by_group(order_group_id: str):
    """Ҳамаи фармоишҳои як гурӯҳи сабад (order_group_id)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE order_group_id=%s ORDER BY id",
                (order_group_id,)
            )
            return await cur.fetchall()


async def mark_order_paid_with_balance(order_id: int):
    """Фламми paid_with_referral_balance-ро мегузорад (барои омор/назорат)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET paid_with_referral_balance=1 WHERE id=%s",
                (order_id,)
            )


async def get_last_player_id(user_id: int, prefix: str = "") -> dict | None:
    """
    ID-и player-и охирин истифодашудаи ҳамин корбар барои ҳамин намуди хизмат
    мегардонад — то ID-ро дубора пешниҳод кунем (бе маҷбур кардан ба навиштани
    дубора). prefix холӣ = FF (бе префикс), "FFID:" = FF Indonesia, "PUBG:" = PUBG.
    Натиҷа: {"player_id": "...", "nickname": "..."} ё None агар order-и пешина набошад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            if prefix:
                await cur.execute(
                    "SELECT game_id, nickname FROM orders "
                    "WHERE user_id=%s AND game_id LIKE %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (user_id, f"{prefix}%"),
                )
            else:
                # FF "оддӣ" — game_id бе ҳеч префикс ва танҳо рақам
                await cur.execute(
                    "SELECT game_id, nickname FROM orders "
                    "WHERE user_id=%s AND game_id REGEXP '^[0-9]+$' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (user_id,),
                )
            row = await cur.fetchone()
            if not row:
                return None
            raw_id = row["game_id"]
            player_id = raw_id[len(prefix):] if prefix else raw_id
            return {"player_id": player_id, "nickname": row.get("nickname") or ""}


async def get_order(order_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM orders WHERE id=%s", (order_id,))
            return await cur.fetchone()


async def update_order_status(order_id: int, status: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET status=%s WHERE id=%s", (status, order_id))


async def set_order_reject_reason(order_id: int, reason: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET reject_reason=%s WHERE id=%s", (reason, order_id))


async def set_order_cost(order_id: int, cost_tjs: float):
    """Арзиши воқеии фармоиш (сомонӣ, аз рӯи USD-и FazerCards/MooGold ва курби
    USD_TO_TJS_RATE)-ро сабт мекунад — барои ҳисоби фоидаи холис."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET cost_tjs=%s WHERE id=%s", (cost_tjs, order_id))


async def get_stuck_donate_orders(hours: int = 24, limit: int = 30):
    """
    Фармоишҳое, ки 'ноком' эълон шудаанд, ВАЛЕ дар FazerCards/MooGold
    ID-и воқеӣ доранд — яъне шояд дар асл иҷро шуда бошанд ё ҳанӯз дар
    ҷараён бошанд. Тафтишгари худкор (recheck_loop) онҳоро мехонад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders "
                "WHERE status='failed' AND api_order_id IS NOT NULL "
                "AND api_order_id <> '' "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
                "ORDER BY id DESC LIMIT %s",
                (hours, limit)
            )
            return await cur.fetchall()


async def claim_stuck_order_confirmed(order_id: int) -> bool:
    """
    Фармоиши 'ноком'-ро атомикӣ ба 'confirmed' мегузаронад — танҳо агар
    он ҳанӯз 'failed' бошад. Барои он ки агар админ дар ҳамин лаҳза дастӣ
    тасдиқ кунад, ду бор паём/мукофот нашавад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='confirmed' WHERE id=%s AND status='failed'",
                (order_id,)
            )
            return cur.rowcount > 0


async def claim_failed_order_for_autoretry(order_id: int) -> bool:
    """
    Атомикӣ фармоиши ВОҚЕАН нокомро барои ЯК кӯшиши худкори такрорӣ
    мегирад. Танҳо ЯК бор кор мекунад (auto_retried=1) — то ҳалқа
    беохир такрор накунад ва харҷ дучандон нашавад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='donating', donating_at=NOW(), auto_retried=1 "
                "WHERE id=%s AND status='failed' AND COALESCE(auto_retried, 0)=0",
                (order_id,)
            )
            return cur.rowcount > 0


async def count_stale_paid_orders(days: int) -> dict:
    """Чандто фармоиши 'пардохтшуда'-и аз N рӯз кӯҳнатар ҳаст ва ҷамъи пулаш."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(price), 0) AS s FROM orders "
                "WHERE status='paid' AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (days,))
            row = await cur.fetchone()
            return {"count": int((row["c"] if row else 0) or 0),
                    "sum": float((row["s"] if row else 0) or 0)}


async def archive_stale_paid_orders(days: int, limit: int = 1000) -> dict:
    """
    Фармоишҳои 'пардохтшуда'-и фаромӯшшударо ба ҳолати 'archived'
    мегузаронад.

    'archived' статуси НАВ аст — ҳељ як дархости мавҷуда онро намегирад
    (ҳамаашон рӯйхати аниқи статусҳоро мепурсанд), пас ҳисоботҳо ва
    ҳисоби фоида бетағйир мемонанд. Пул ва таърих гум НАМЕШАВАД — танҳо
    фармоиш аз рӯйхати "кор" бароварда мешавад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, price FROM orders WHERE status='paid' "
                "AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY) "
                "ORDER BY id ASC LIMIT %s", (days, limit))
            rows = await cur.fetchall() or []
            if not rows:
                return {"count": 0, "sum": 0.0, "ids": []}
            ids = [r["id"] for r in rows]
            total = sum(float(r["price"] or 0) for r in rows)
            placeholders = ",".join(["%s"] * len(ids))
            await cur.execute(
                f"UPDATE orders SET status='archived' WHERE id IN ({placeholders}) "
                f"AND status='paid'", ids)
            return {"count": cur.rowcount, "sum": total, "ids": ids}


async def get_orders_needing_admin(days: int = 3, limit: int = 30):
    """
    Фармоишҳое, ки ВОҚЕАН кӯмаки админро мехоҳанд:
      • 'paid'   — чек омада, интизори тасдиқи дастӣ
      • 'failed' — донат нашуд ва бот дигар худаш ҳал карда наметавонад
    Фармоишҳои 'donating' (дар ҷараён) ва 'autopay_search' (бот ҳанӯз
    худаш меҷӯяд) дохил намешаванд — онҳо кори админ нестанд.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders "
                "WHERE status IN ('paid', 'failed') "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "ORDER BY id DESC LIMIT %s",
                (days, limit)
            )
            return await cur.fetchall()


async def get_unalerted_admin_orders(hours: int = 12, limit: int = 50):
    """Фармоишҳои кӯмакталаб, ки ҳанӯз ба админ хабар дода нашудаанд
    (масалан дар реҷаи хомӯшии шабона таъхир шудаанд)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders "
                "WHERE status IN ('paid', 'failed') "
                "AND COALESCE(admin_alerted, 0)=0 "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
                "ORDER BY id DESC LIMIT %s",
                (hours, limit)
            )
            return await cur.fetchall()


async def get_active_order_for_user(user_id: int):
    """Фармоиши охирини «зинда»-и мизоҷ (ҳанӯз ниҳоӣ нашуда) — барои
    ҷавоби худкор ба саволи «фармоишам чӣ шуд?»."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE user_id=%s "
                "AND status IN ('awaiting_autopay','autopay_search','paid','donating','failed') "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY) "
                "ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            return await cur.fetchone()


async def get_system_health(hours: int = 24, watch_since=None, recent_days: int = 3) -> dict:
    """
    Саломатии система дар N соати охир — барои панели админ.

    watch_since — лаҳзаи оғози бот. Тафтишгари худкор танҳо фармоишҳои
    баъд аз ин лаҳзаро пайгирӣ мекунад, пас 'stuck_now' низ бояд ҳаминро
    ҳисоб кунад, вагарна рақам гумроҳкунанда мешавад.
    """
    out = {
        "confirmed": 0, "failed": 0, "uncertain": 0, "rejected": 0,
        "avg_minutes": None, "stuck_now": 0, "waiting_admin": 0,
        "waiting_admin_old": 0, "donating_now": 0, "success_rate": None,
        "paid_window": 0, "service_rate": None,
    }
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT status, COUNT(*) AS c FROM orders "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
                "GROUP BY status", (hours,))
            for row in await cur.fetchall():
                st = row["status"]
                if st == "confirmed":
                    out["confirmed"] = int(row["c"])
                elif st == "failed":
                    out["failed"] = int(row["c"])
                elif st == "rejected":
                    out["rejected"] = int(row["c"])
                elif st == "paid":
                    # 'пардохтшуда'-и ҳамин равзанаи вақт — барои фоизи
                    # хизматрасонӣ (на рӯйхати умумии 3-рӯза)
                    out["paid_window"] = int(row["c"])

            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders "
                "WHERE uncertain_flagged=1 "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)", (hours,))
            row = await cur.fetchone()
            out["uncertain"] = int((row["c"] if row else 0) or 0)

            await cur.execute(
                "SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, confirmed_at)) AS s "
                "FROM orders WHERE confirmed_at IS NOT NULL "
                "AND confirmed_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)", (hours,))
            row = await cur.fetchone()
            secs = (row["s"] if row else None)
            if secs is not None:
                out["avg_minutes"] = round(float(secs) / 60, 1)

            # Фармоишҳое, ки тафтишгар ВОҚЕАН пайгирӣ мекунад — танҳо
            # баъд аз оғози бот сохташуда (ниг. autopay._BOT_START_TS)
            if watch_since is not None:
                await cur.execute(
                    "SELECT COUNT(*) AS c FROM orders WHERE status='failed' "
                    "AND api_order_id IS NOT NULL AND api_order_id <> '' "
                    "AND created_at >= %s", (watch_since,))
            else:
                await cur.execute(
                    "SELECT COUNT(*) AS c FROM orders WHERE status='failed' "
                    "AND api_order_id IS NOT NULL AND api_order_id <> '' "
                    "AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)")
            row = await cur.fetchone()
            out["stuck_now"] = int((row["c"] if row else 0) or 0)

            # Интизори тасдиқ: НАВҲО (ҳамонҳое ки дар «Кор барои ман» ҳастанд)
            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE status='paid' "
                "AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)", (recent_days,))
            row = await cur.fetchone()
            out["waiting_admin"] = int((row["c"] if row else 0) or 0)

            # Фармоишҳои КӮҲНАИ 'paid' — солҳо боз овезон мондаанд ва
            # аслан кори имрӯза нестанд, вале донистанашон фоидаовар аст
            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE status='paid' "
                "AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (recent_days,))
            row = await cur.fetchone()
            out["waiting_admin_old"] = int((row["c"] if row else 0) or 0)

            await cur.execute("SELECT COUNT(*) AS c FROM orders WHERE status='donating'")
            row = await cur.fetchone()
            out["donating_now"] = int((row["c"] if row else 0) or 0)

    # Фоизи ТЕХНИКӢ — мошини донат чӣ хел кор мекунад
    total = out["confirmed"] + out["failed"]
    if total > 0:
        out["success_rate"] = round(out["confirmed"] * 100.0 / total, 1)

    # Фоизи ХИЗМАТРАСОНӢ — аз ҳар 100 мизоҷе, ки ПУЛ ДОД, чандто хизмат
    # гирифт. Фармоишҳои ҳанӯз ҳалнашуда (интизори тасдиқ) низ ҳисоб
    # мешаванд, чунки барои мизоҷ онҳо "нагирифтам" маъно доранд.
    # Радшудаҳо (чеки қалбакӣ ва ғ.) ба ҳисоб намераванд — онҳо
    # хатогии хизматрасонӣ нестанд.
    served_total = out["confirmed"] + out["failed"] + out["paid_window"]
    if served_total > 0:
        out["service_rate"] = round(out["confirmed"] * 100.0 / served_total, 1)
    else:
        out["service_rate"] = None
    return out


async def reset_recheck_state(order_id: int):
    """
    Пеш аз кӯшиши НАВИ донат (масалан админ «Дубора донат» пахш кард)
    ҳисоби санҷишҳо ва аломати «ба админ хабар дода шуд» тоза мешавад —
    то агар кӯшиши нав ҳам ноком шавад, тафтишгар аз нав кор кунад ва
    админ бори дигар хабар гирад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET recheck_tries=0, admin_alerted=0 WHERE id=%s",
                (order_id,))


async def bump_recheck_tries(order_id: int) -> int:
    """Шумораи санҷишҳои худкорро як зина боло мебарад ва рақами навро медиҳад."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE orders SET recheck_tries = COALESCE(recheck_tries, 0) + 1 "
                "WHERE id=%s", (order_id,))
            await cur.execute("SELECT recheck_tries AS t FROM orders WHERE id=%s", (order_id,))
            row = await cur.fetchone()
            return int((row["t"] if row else 0) or 0)


async def claim_admin_alert(order_id: int) -> bool:
    """
    Атомикӣ ҳуқуқи 'ба админ хабар додан'-ро мегирад. True танҳо як бор
    бармегардад — то як фармоиш чанд бор ба админ хабар нашавад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET admin_alerted=1 WHERE id=%s "
                "AND COALESCE(admin_alerted, 0)=0", (order_id,))
            return cur.rowcount > 0


async def flag_order_uncertain(order_id: int):
    """Аломат мегузорад, ки ин фармоиш бо сабаби таймаути шабака (на радди
    воқеӣ) 'нашуд' гуфта шудааст — барои гузориши шабона."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET uncertain_flagged=1 WHERE id=%s", (order_id,))


async def set_order_check(order_id: int, file_id: str, check_hash: str = None):
    """ID-и расми чекро сабт мекунад ва статусро 'paid' мегузорад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Муҳофизат: фармоиши аллакай тамомшуда (confirmed/donating/rejected)-ро
            # ба 'paid' барнагардон — вагарна дубора донат мешавад.
            await cur.execute(
                "UPDATE orders SET check_file_id=%s, check_hash=%s, status='paid' "
                "WHERE id=%s AND status NOT IN ('confirmed','donating','rejected')",
                (file_id, check_hash, order_id)
            )


async def get_stale_paid_orders(minutes: int = 20, created_after=None):
    """
    Фармоишҳои дастӣ (Алиф/Эсхата), ки чек фиристодаанд (status='paid')
    вале зиёда аз `minutes` дақиқа то ҳол тасдиқ/рад нашудаанд ва то ҳол
    ёдоварӣ нагирифтаанд. created_after (агар дода шавад) — танҳо
    фармоишҳои баъди ин вақт (то фармоишҳои кӯҳнаи дастӣ-ҳалшуда халал
    нарасонанд).
    """
    q = ("SELECT * FROM orders WHERE status='paid' AND stale_reminder_sent=0 "
         "AND created_at <= NOW() - INTERVAL %s MINUTE")
    params = [minutes]
    if created_after is not None:
        q += " AND created_at >= %s"
        params.append(created_after)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(q, tuple(params))
            return await cur.fetchall()


async def get_paid_cart_pending_no_check(minutes: int = 5, created_after=None):
    """Фармоишҳои САБАД, ки пул ОМАД (kod резерв шудааст), вале мизоҷ то ҳол
    ЧЕК нафиристодааст (status='pending', check_file_id IS NULL) ва аз
    `minutes` дақиқа зиёд гузашт. Барои огоҳии ТАЪХИРИИ админ — то дар
    ҳолати оддӣ (мизоҷ чекро баъди чанд сония мефиристад) огоҳии бармаҳал
    наравад ва админ ду паём нагирад. Танҳо фармоише бармегардад, ки kod ба
    он резерв шудааст (яъне пул воқеан омад — на сабади партофташуда)."""
    q = ("SELECT DISTINCT o.* FROM orders o "
         "JOIN dc_kods k ON k.matched_order_id = o.id "
         "WHERE o.order_group_id IS NOT NULL AND o.status='pending' "
         "AND o.check_file_id IS NULL AND o.stale_reminder_sent=0 "
         "AND o.created_at <= NOW() - INTERVAL %s MINUTE")
    params = [minutes]
    if created_after is not None:
        q += " AND o.created_at >= %s"
        params.append(created_after)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(q, tuple(params))
            return await cur.fetchall()


async def mark_stale_reminder_sent(order_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET stale_reminder_sent=1 WHERE id=%s", (order_id,)
            )


async def get_long_waiting_paid(min_minutes: int = 60, max_hours: int = 48,
                                created_after=None) -> list:
    """Фармоишҳои 'paid', ки аз min_minutes зиёд интизори тасдиқанд (вале аз
    max_hours кӯҳнатар не). created_after (агар дода шавад) — танҳо
    фармоишҳои баъди ин вақт (масалан баъди рестарт), то фармоишҳои кӯҳнаи
    дастӣ-ҳалшуда шуморо наноланд."""
    q = ("SELECT id, price, created_at FROM orders WHERE status='paid' "
         "AND created_at <= NOW() - INTERVAL %s MINUTE "
         "AND created_at >= NOW() - INTERVAL %s HOUR")
    params = [min_minutes, max_hours]
    if created_after is not None:
        q += " AND created_at >= %s"
        params.append(created_after)
    q += " ORDER BY created_at ASC"
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(q, tuple(params))
            return await cur.fetchall()


async def get_pending_orders(limit: int = 20):
    """Ҳамаи фармоишҳои 'paid' (чек фиристодашуда, ҳанӯз тасдиқ/рад нашуда),
    кӯҳнатаринашон аввал (аз ҳама бештар интизормонда)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='paid' ORDER BY created_at ASC LIMIT %s",
                (limit,)
            )
            return await cur.fetchall()


async def find_orders_by_check_hash(check_hash: str, limit: int = 10):
    """Ҳамаи фармоишҳо (новобаста аз статус) бо ҳамин hash-и чек — барои
    ҷустуҷӯи бозгашти (reverse lookup) админ бо фиристодани расм."""
    if not check_hash:
        return []
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE check_hash=%s ORDER BY created_at DESC LIMIT %s",
                (check_hash, limit)
            )
            return await cur.fetchall()


async def find_check_reuse(check_hash: str, exclude_order_ids=()) -> list:
    """
    Ҳамаи фармоишҳое, ки маҳз ҲАМИН расми чек ба онҳо пайваст шудааст —
    БЕ маҳдудияти корбар, БЕ маҳдудияти вақт.

    Се маҳдудияти кӯҳна қасдан бардошта шуданд:
      • корбар — чеки як нафар метавонад ба ҳисоби ДИГАР истифода шавад
        (ду аккаунт, ё расмро ба дӯсташ додан). Ин ҳолати аз ҳама хатарнок
        буд ва тамоман дида намешуд.
      • статус — чеки такрорӣ метавонад ҳанӯз дар кор бошад (paid,
        autopay_search, donating), яъне ҳанӯз 'confirmed' нашуда. Пештар
        маҳз ҳамин роҳи тезтарин кушода буд.
      • вақт — 72 соат сабаби техникӣ надошт.

    Фармоишҳои радшуда/мӯҳлатгузашта ҳисоб намешаванд: чеки онҳо кор
    накард, пас истифодаи дубораи ҳамон расм қонунӣ аст.
    """
    if not check_hash:
        return []
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE check_hash=%s "
                "AND status NOT IN ('rejected','expired') "
                "ORDER BY created_at ASC LIMIT 10",
                (check_hash,)
            )
            rows = await cur.fetchall()
    ex = set(exclude_order_ids or ())
    return [r for r in rows if r["id"] not in ex]


async def get_abandoned_orders_for_nudge(min_hours: int = 3, max_hours: int = 24,
                                         limit: int = 30) -> list:
    """
    Фармоишҳое, ки мизоҷ сар кард, вале чек нафиристод ва мӯҳлаташон
    гузашт — барои ЯК ёдоварии нарм.

    Танҳо касоне гирифта мешаванд, ки БАЪДИ он фармоиш ягон хариди
    тасдиқшуда НАКАРДААНД: агар мизоҷ баъдтар харид карда бошад, ёдоварӣ
    маънои худро гум мекунад ва танҳо озор медиҳад.

    Ҳар мизоҷ ЯК бор дар ин рӯйхат меафтад (аз ҳар мизоҷ навтарин
    фармоиши партофташуда), то касе ду-се паём нагирад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT o.* FROM orders o "
                "WHERE o.status='expired' AND o.nudge_sent=0 "
                "AND o.is_balance_topup=0 "
                "AND o.created_at <  NOW() - INTERVAL %s HOUR "
                "AND o.created_at >= NOW() - INTERVAL %s HOUR "
                "AND NOT EXISTS (SELECT 1 FROM orders c WHERE c.user_id=o.user_id "
                "                AND c.status='confirmed' AND c.created_at > o.created_at) "
                "AND o.id = (SELECT MAX(x.id) FROM orders x WHERE x.user_id=o.user_id "
                "            AND x.status='expired' AND x.nudge_sent=0) "
                "ORDER BY o.created_at ASC LIMIT %s",
                (min_hours, max_hours, limit)
            )
            return await cur.fetchall()


async def mark_nudge_sent(user_id: int):
    """Ҳамаи фармоишҳои партофташудаи ин мизоҷро аломат мезанад — то
    ёдоварӣ барои ҳар кадоми онҳо алоҳида такрор нашавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET nudge_sent=1 WHERE user_id=%s AND status='expired'",
                (user_id,)
            )


# ==================== САНҶИШИ ХУДКОРИ ҲИСОБИ САБАД ====================
async def get_cart_groups_after(after_id: int, limit: int = 20) -> list:
    """
    Гурӯҳҳои сабад (як харид = якчанд фармоиш бо як order_group_id), ки
    баъд аз фармоиши `after_id` сохта шудаанд — бо ҷамъи нархашон.

    `users` ва `pms` барои санҷиш лозиманд: дар як гурӯҳ бояд ЯК мизоҷ ва
    ЯК тариқи пардохт бошад. Агар зиёд бошад, дар сохтани фармоиш хатост.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT order_group_id AS gid, COUNT(*) AS n, "
                "COALESCE(SUM(price),0) AS total, MIN(id) AS first_id, "
                "MAX(id) AS last_id, COUNT(DISTINCT user_id) AS users, "
                "MIN(user_id) AS user_id, COUNT(DISTINCT payment_method) AS pms, "
                "MIN(payment_method) AS pm, MIN(created_at) AS created "
                "FROM orders WHERE order_group_id IS NOT NULL AND id > %s "
                "GROUP BY order_group_id ORDER BY first_id LIMIT %s",
                (after_id, limit)
            )
            return await cur.fetchall()


async def find_purchase_tx(user_id: int, created_at, window_min: int = 10):
    """
    Камкунии баланси ба ин харид наздиктарин. Камкунӣ бе `order_id` сабт
    мешавад (дар лаҳзаи камкунӣ фармоиш ҳанӯз вуҷуд надорад), пас аз рӯи
    мизоҷ ва ВАҚТ меёбем.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT amount, created_at FROM balance_transactions "
                "WHERE user_id=%s AND tx_type='purchase' "
                "AND created_at BETWEEN %s - INTERVAL %s MINUTE "
                "                   AND %s + INTERVAL %s MINUTE "
                "ORDER BY ABS(TIMESTAMPDIFF(SECOND, created_at, %s)) LIMIT 1",
                (user_id, created_at, window_min, created_at, window_min, created_at)
            )
            return await cur.fetchone()


# ==================== МАЪЛУМОТ БАРОИ ОГОҲИҲОИ ХУДКОР ====================
async def get_loss_orders(hours: int = 24, limit: int = 20) -> list:
    """
    Фармоишҳои тасдиқшуда, ки арзиши хариди онҳо аз нархи фурӯш БАЛАНД
    ё БАРОБАР аст — яъне мо ба зарар ё бе фоида фурӯхтем.

    Пештар инро танҳо дар ҳисоботи шаб мешуд дид — то он вақт як рӯзи
    пурра ба зарар фурӯхта мешуд.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, user_id, label, price, cost_tjs FROM orders "
                "WHERE status='confirmed' AND cost_tjs IS NOT NULL "
                "AND price > 0 AND cost_tjs >= price "
                "AND confirmed_at >= NOW() - INTERVAL %s HOUR "
                "ORDER BY id DESC LIMIT %s",
                (hours, limit)
            )
            return await cur.fetchall()


async def get_repeat_rejected_users(days: int = 7, min_count: int = 3) -> list:
    """Мизоҷоне, ки дар N рӯзи охир чанд фармоиши РАДШУДА доранд."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT user_id, COUNT(*) AS n, MAX(id) AS last_id FROM orders "
                "WHERE status='rejected' AND created_at >= NOW() - INTERVAL %s DAY "
                "GROUP BY user_id HAVING n >= %s ORDER BY n DESC LIMIT 10",
                (days, min_count)
            )
            return await cur.fetchall()


async def get_multi_id_users(days: int = 30, min_ids: int = 5) -> list:
    """
    Мизоҷоне, ки ба ID-ҳои ЗИЁДИ ГУНОГУНИ бозӣ донат кардаанд.
    Эҳтимол онҳо худашон фурӯш мекунанд — шояд нархи чакана лозим бошад.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT user_id, COUNT(DISTINCT game_id) AS ids, COUNT(*) AS n, "
                "COALESCE(SUM(price),0) AS total FROM orders "
                "WHERE status='confirmed' AND game_id<>'' "
                "AND created_at >= NOW() - INTERVAL %s DAY "
                "GROUP BY user_id HAVING ids >= %s ORDER BY ids DESC LIMIT 10",
                (days, min_ids)
            )
            return await cur.fetchall()


async def payment_feed_health(quiet_minutes: int = 30) -> dict:
    """
    Оё пардохтҳо умуман меоянд? Бармегардонад:
      waiting  — чанд фармоиш пардохти худкорро интизор аст
      last_min — чанд дақиқа пеш охирин пардохт омад (None = ҳељ гоҳ)
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COUNT(*) AS n FROM orders "
                "WHERE status IN ('autopay_search','awaiting_autopay') "
                "AND created_at >= NOW() - INTERVAL %s MINUTE",
                (quiet_minutes,)
            )
            waiting = (await cur.fetchone())["n"]
            await cur.execute(
                "SELECT TIMESTAMPDIFF(MINUTE, MAX(received_at), NOW()) AS m "
                "FROM dc_kods"
            )
            row = await cur.fetchone()
    return {"waiting": waiting, "last_min": row["m"] if row else None}


async def set_order_api_id(order_id: int, api_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET api_order_id=%s WHERE id=%s",
                (api_id, order_id)
            )


async def get_user_orders(user_id: int, limit: int = 10):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                (user_id, limit)
            )
            return await cur.fetchall()


# ==================== БАРГАРДОНИДАНИ МИЗОЧ (ЁДОВАРӠ) ====================
async def get_users_no_order_1h() -> list:
    """
    Корбароне, ки /start заданд, вале баъд аз 1 соат ҲЕЧ order надоранд,
    ва ёдоварии ин гурӯҳ ҲАНУЗ фиристода нашудааст.
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, full_name FROM users "
                "WHERE reminder_noorder_sent=0 "
                "AND created_at <= NOW() - INTERVAL 1 HOUR "
                "AND NOT EXISTS (SELECT 1 FROM orders WHERE orders.user_id = users.id)"
            )
            return await cur.fetchall()


async def mark_reminder_noorder_sent(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET reminder_noorder_sent=1 WHERE id=%s", (user_id,)
            )


# ==================== ТАХФИФИ БАРГАРДОНИИ МИЗОҶОНИ ХОМӮШШУДА ====================
WINBACK_PERCENT = 3.0
WINBACK_DORMANT_DAYS = 14


async def get_dormant_customers_for_winback() -> list:
    """
    Мизоҷоне, ки ҳадди ақал як хариди тасдиқшуда доранд, аммо аз хариди
    ОХИРИНИ онҳо WINBACK_DORMANT_DAYS+ рӯз гузаштааст, ва ҳанӯз ЯГОН БОР
    паёми баргардонӣ нагирифтаанд (winback як маротиба дар тамоми умри
    мизоҷ дода мешавад).
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT u.id, u.username, u.full_name FROM users u "
                "WHERE u.winback_sent=0 "
                "AND (SELECT COUNT(*) FROM orders WHERE orders.user_id=u.id AND status='confirmed' AND is_balance_topup=0) >= 1 "
                "AND (SELECT MAX(created_at) FROM orders WHERE orders.user_id=u.id AND status='confirmed' AND is_balance_topup=0) "
                "    <= NOW() - INTERVAL %s DAY",
                (WINBACK_DORMANT_DAYS,)
            )
            return await cur.fetchall()


async def mark_winback_sent(user_id: int):
    """Паёми баргардониро сабт мекунад ва тахфифи -3%-ро барои хариди навбатӣ фаъол мекунад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET winback_sent=1, winback_active=1 WHERE id=%s",
                (user_id,)
            )


async def get_winback_discount(user_id: int) -> float:
    """Тахфифи фаъоли баргардонӣ (фоиз), 0 агар набошад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT winback_active FROM users WHERE id=%s", (user_id,)
            )
            row = await cur.fetchone()
            return WINBACK_PERCENT if row and row[0] else 0.0


async def clear_winback(user_id: int):
    """Тахфифи баргардониро бекор мекунад — баъд аз он ки як бор нишон дода шуд."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET winback_active=0 WHERE id=%s", (user_id,)
            )


# ==================== №1 — ТАХФИФИ 1% БАЪДИ ХАРИД (10 дақиқа) ====================
REOFFER_PERCENT = 1.0        # чанд фоиз тахфиф
REOFFER_MINUTES = 10         # чанд дақиқа фаъол


async def set_repurchase_offer(user_id: int, minutes: int = REOFFER_MINUTES):
    """Баъди хариди муваффақ — тахфифи 1%-ро барои `minutes` дақиқа фаъол
    мекунад (мизоҷ агар ҳозир боз харад, 1% арзон мегирад)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET reoffer_until = NOW() + INTERVAL %s MINUTE WHERE id=%s",
                (minutes, user_id),
            )


async def has_repurchase_offer(user_id: int) -> bool:
    """Оё тахфифи 1%-и баъди харид ҳоло фаъол аст (мӯҳлаташ нагузашта)?"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT reoffer_until FROM users "
                "WHERE id=%s AND reoffer_until IS NOT NULL AND reoffer_until > NOW()",
                (user_id,),
            )
            return (await cur.fetchone()) is not None


async def clear_repurchase_offer(user_id: int):
    """Тахфифи 1%-ро бекор мекунад (баъди истифода — як маротиба)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET reoffer_until = NULL WHERE id=%s", (user_id,)
            )


async def get_reengagement_stats() -> dict:
    """
    Омори функсияи баргардонидани мизоҷ (ёдоварии бе-фармоиш):
    - шумораи корбароне, ки ёдоварӣ гирифтаанд
    - аз онҳо, чанд нафар БАЪД АЗ он воқеан харид кардаанд (муваффақият)
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM users WHERE reminder_noorder_sent=1"
            )
            noorder_sent = (await cur.fetchone())[0]
            await cur.execute(
                "SELECT COUNT(*) FROM users u WHERE u.reminder_noorder_sent=1 "
                "AND EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id)"
            )
            noorder_converted = (await cur.fetchone())[0]

            return {
                "noorder_sent": noorder_sent,
                "noorder_converted": noorder_converted,
            }


# ==================== ОМОР ====================
async def get_stats():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT COUNT(*) AS c FROM users")
            users = (await cur.fetchone())["c"]
            await cur.execute("SELECT COUNT(*) AS c FROM orders WHERE status='confirmed' AND is_balance_topup=0")
            orders = (await cur.fetchone())["c"]
            await cur.execute("SELECT COALESCE(SUM(price),0) AS s FROM orders WHERE status='confirmed' AND is_balance_topup=0")
            total = (await cur.fetchone())["s"]
            return {"users": users, "orders": orders, "total": float(total)}


async def get_daily_report() -> dict:
    """
    Гузориши рӯзонаи бот барои фиристодан ба админ дар соати 00:00
    (вақти Тоҷикистон):
    - Корбарони нав: имрӯз / 3 рӯз / 7 рӯз / 1 моҳ
    - Савдои имрӯз ва дина (бо сом)
    - Шумораи фармоишҳои ✅ тасдиқшуда ва ❌ радшуда (имрӯз)
    - Фоизи тағйир дар савдо нисбат ба дина
    """
    # Санаи "имрӯз" БО ВАҦТИ ТОЧИКИСТОН (на вақти сервери MySQL/Python),
    # то гузориш дар атрофи нисфишабӣ нодуруст набарояд.
    today_tj = datetime.now(TJ_TZ).date()

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # ---- Корбарони нав дар бозаҳои гуногуни вақт ----
            await cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= %s",
                (today_tj,)
            )
            new_today = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= %s",
                (today_tj - timedelta(days=3),)
            )
            new_3d = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= %s",
                (today_tj - timedelta(days=7),)
            )
            new_7d = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= %s",
                (today_tj - timedelta(days=30),)
            )
            new_30d = (await cur.fetchone())["c"]

            # ---- Савдо (фармоишҳои тасдиқшуда, ба ғайр аз пуркунии баланс) ----
            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s",
                (today_tj,)
            )
            sales_today = float((await cur.fetchone())["s"])

            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s AND created_at < %s",
                (today_tj - timedelta(days=1), today_tj)
            )
            sales_yesterday = float((await cur.fetchone())["s"])

            # ---- Даромади 7 рӯз ва 30 рӯз (бо муқоиса ба давраи пешина) ----
            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s",
                (today_tj - timedelta(days=7),)
            )
            sales_7d = float((await cur.fetchone())["s"])

            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s AND created_at < %s",
                (today_tj - timedelta(days=14), today_tj - timedelta(days=7))
            )
            sales_prev_7d = float((await cur.fetchone())["s"])

            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s",
                (today_tj - timedelta(days=30),)
            )
            sales_30d = float((await cur.fetchone())["s"])

            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s AND created_at < %s",
                (today_tj - timedelta(days=60), today_tj - timedelta(days=30))
            )
            sales_prev_30d = float((await cur.fetchone())["s"])

            def _pct_change(curr, prev):
                if prev > 0:
                    return (curr - prev) / prev * 100
                return 100.0 if curr > 0 else 0.0

            change_7d = _pct_change(sales_7d, sales_prev_7d)
            change_30d = _pct_change(sales_30d, sales_prev_30d)

            # ---- Шумори фармоишҳо имрӯз: тасдиқшуда / радшуда ----
            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s",
                (today_tj,)
            )
            confirmed_today = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders "
                "WHERE status IN ('rejected','failed') AND is_balance_topup=0 AND created_at >= %s",
                (today_tj,)
            )
            rejected_today = (await cur.fetchone())["c"]

            # ---- Фоизи радкунӣ ва миёнаи арзиши фармоиш (имрӯз) ----
            total_today = confirmed_today + rejected_today
            rejection_rate = (rejected_today / total_today * 100) if total_today else 0.0
            avg_order_value = (sales_today / confirmed_today) if confirmed_today else 0.0

            # ---- Фоизи тағйир нисбат ба дина ----
            if sales_yesterday > 0:
                change_percent = ((sales_today - sales_yesterday) / sales_yesterday) * 100
            elif sales_today > 0:
                change_percent = 100.0
            else:
                change_percent = 0.0

            # ---- Корбарони такрорӣ vs нав (имрӯз) ----
            # "Такрорӣ" = корбаре, ки фармоиши тасдиқшуда дошт ПЕШ аз имрӯз
            await cur.execute(
                "SELECT COUNT(DISTINCT o1.user_id) AS c FROM orders o1 "
                "WHERE o1.status='confirmed' AND o1.is_balance_topup=0 AND o1.created_at >= %s "
                "AND EXISTS ("
                "    SELECT 1 FROM orders o2 "
                "    WHERE o2.user_id = o1.user_id AND o2.status='confirmed' AND o2.is_balance_topup=0 "
                "    AND o2.created_at < %s"
                ")",
                (today_tj, today_tj)
            )
            repeat_customers_today = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(DISTINCT o1.user_id) AS c FROM orders o1 "
                "WHERE o1.status='confirmed' AND o1.is_balance_topup=0 AND o1.created_at >= %s "
                "AND NOT EXISTS ("
                "    SELECT 1 FROM orders o2 "
                "    WHERE o2.user_id = o1.user_id AND o2.status='confirmed' AND o2.is_balance_topup=0 "
                "    AND o2.created_at < %s"
                ")",
                (today_tj, today_tj)
            )
            new_customers_today = (await cur.fetchone())["c"]

            # ---- ГурӴҳбандии ҲАМА-ВАҦТ (1 харид, 2-5 харид, 5+ харид) ----
            await cur.execute("""
                SELECT order_count,
                       COUNT(*) AS users_count,
                       SUM(total_spent) AS group_spent
                FROM (
                    SELECT user_id,
                           COUNT(*) AS order_count,
                           SUM(price) AS total_spent
                    FROM orders
                    WHERE status='confirmed' AND is_balance_topup=0
                    GROUP BY user_id
                ) t
                GROUP BY order_count
            """)
            per_user_rows = await cur.fetchall()

            buyers_1 = buyers_2_5 = buyers_5plus = 0
            spent_1 = spent_2_5 = spent_5plus = 0.0
            for row in per_user_rows:
                oc = row["order_count"]
                if oc == 1:
                    buyers_1 += row["users_count"]
                    spent_1 += float(row["group_spent"])
                elif 2 <= oc <= 5:
                    buyers_2_5 += row["users_count"]
                    spent_2_5 += float(row["group_spent"])
                else:
                    buyers_5plus += row["users_count"]
                    spent_5plus += float(row["group_spent"])

            total_buyers = buyers_1 + buyers_2_5 + buyers_5plus
            total_buyers_spent = spent_1 + spent_2_5 + spent_5plus
            avg_spent_per_buyer = (total_buyers_spent / total_buyers) if total_buyers else 0.0

            # ---- Соати "пик" (бар асоси 30 рӯзи охир) ----
            await cur.execute(
                "SELECT HOUR(created_at) AS hr, COUNT(*) AS c FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s "
                "GROUP BY hr ORDER BY c DESC LIMIT 1",
                (today_tj - timedelta(days=30),)
            )
            peak_hour_row = await cur.fetchone()
            peak_hour = peak_hour_row["hr"] if peak_hour_row else None
            peak_hour_count = peak_hour_row["c"] if peak_hour_row else 0

            # ---- РӴзи ҳафтаи беҳтарин (бар асоси 30 рӯзи охир) ----
            # DAYNAME медиҳад номи рӯз бо забони англисӣ — дар поён ба тоҷикӣ иваз мекунем
            await cur.execute(
                "SELECT DAYNAME(created_at) AS dname, COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s "
                "GROUP BY dname ORDER BY s DESC LIMIT 1",
                (today_tj - timedelta(days=30),)
            )
            best_day_row = await cur.fetchone()
            best_weekday_en = best_day_row["dname"] if best_day_row else None
            best_weekday_sales = float(best_day_row["s"]) if best_day_row else 0.0

            weekday_tj = {
                "Monday": "Душанбе", "Tuesday": "Сешанбе", "Wednesday": "Чоршанбе",
                "Thursday": "Панҷшанбе", "Friday": "Ҷумъа",
                "Saturday": "Шанбе", "Sunday": "Якшанбе",
            }
            best_weekday = weekday_tj.get(best_weekday_en, best_weekday_en or "—")

            # ---- Топ-5 маҳсулот (аз рӯи даромад, 7 рӯзи охир) ----
            await cur.execute(
                "SELECT label, COUNT(*) AS cnt, COALESCE(SUM(price),0) AS rev, "
                "COALESCE(SUM(cost_tjs),0) AS cost, "
                # Даромади ТАНҲО он фармоишҳое, ки арзишашон маълум аст —
                # фоида бояд аз ҲАМОН фармоишҳо ҳисоб шавад. Пештар ин ҷо
                # даромади ҲАМА гирифта мешуд: агар аз 10 фармоиш танҳо
                # 3-тояш арзиш медошт, фоида чанд баробар калон менамуд.
                "COALESCE(SUM(CASE WHEN cost_tjs IS NOT NULL THEN price END),0) AS rev_c, "
                "SUM(CASE WHEN cost_tjs IS NOT NULL THEN 1 ELSE 0 END) AS with_cost "
                "FROM orders WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s "
                "GROUP BY label ORDER BY rev DESC LIMIT 5",
                (today_tj - timedelta(days=7),)
            )
            top_products = []
            for r in await cur.fetchall():
                rev = float(r["rev"])
                cost = float(r["cost"])
                rev_c = float(r["rev_c"])
                with_cost = r["with_cost"]
                margin_percent = round((rev_c - cost) / cost * 100, 1) if with_cost and cost > 0 else None
                top_products.append({
                    "label": r["label"] or "—",
                    "count": r["cnt"],
                    "revenue": rev,
                    "margin_percent": margin_percent,
                })

            # ---- Тақсимот аз рӯи усули пардохт (имрӯз) ----
            await cur.execute(
                "SELECT payment_method, "
                "SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) AS confirmed_c, "
                "SUM(CASE WHEN status IN ('rejected','failed') THEN 1 ELSE 0 END) AS rejected_c "
                "FROM orders WHERE is_balance_topup=0 AND created_at >= %s GROUP BY payment_method",
                (today_tj,)
            )
            payment_breakdown = [
                {
                    "method": r["payment_method"] or "—",
                    "confirmed": r["confirmed_c"],
                    "rejected": r["rejected_c"],
                }
                for r in await cur.fetchall() if (r["confirmed_c"] or r["rejected_c"])
            ]

            # ---- Фармоишҳои "номуайян" (таймаути такрории FazerCards) имрӯз ----
            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE uncertain_flagged=1 AND created_at >= %s",
                (today_tj,)
            )
            uncertain_today = (await cur.fetchone())["c"]

            # ---- Фармоишҳои бо пардохти дерина наҷотёфта (имрӯз) ----
            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE late_recovered=1 AND created_at >= %s",
                (today_tj,)
            )
            late_recovered_today = (await cur.fetchone())["c"]

            # ---- Мизоҷони "хомӯшшуда" (охирин харид 14+ рӯз пеш) ----
            dormant_cutoff = datetime.now(TJ_TZ) - timedelta(days=14)
            await cur.execute(
                "SELECT COUNT(*) AS c FROM ("
                "    SELECT user_id, MAX(created_at) AS last_order FROM orders "
                "    WHERE status='confirmed' AND is_balance_topup=0 GROUP BY user_id"
                ") t WHERE last_order < %s",
                (dormant_cutoff,)
            )
            dormant_customers = (await cur.fetchone())["c"]

            # ---- Фоидаи холис имрӯз (нархи фурӯш минус арзиши воқеӣ) ----
            await cur.execute(
                "SELECT COALESCE(SUM(price - cost_tjs),0) AS profit, "
                "COALESCE(SUM(cost_tjs),0) AS total_cost, COUNT(*) AS with_cost FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND cost_tjs IS NOT NULL AND created_at >= %s",
                (today_tj,)
            )
            profit_row = await cur.fetchone()
            profit_today = float(profit_row["profit"])
            orders_with_cost_today = profit_row["with_cost"]
            total_cost_today = float(profit_row["total_cost"])
            profit_margin_percent = (
                round(profit_today / total_cost_today * 100, 1)
                if total_cost_today > 0 else None
            )

            # ---- Фурӯши ҳар рӯзи 7 рӯзи охир (барои диаграммаи матнӣ) ----
            await cur.execute(
                "SELECT DATE(created_at) AS d, COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s GROUP BY DATE(created_at)",
                (today_tj - timedelta(days=6),)
            )
            by_day = {r["d"]: float(r["s"]) for r in await cur.fetchall()}
            last_7_days_sales = [
                {"date": (today_tj - timedelta(days=i)), "sales": by_day.get(today_tj - timedelta(days=i), 0.0)}
                for i in range(6, -1, -1)
            ]

            return {
                "new_today": new_today,
                "new_3d": new_3d,
                "new_7d": new_7d,
                "new_30d": new_30d,
                "sales_today": sales_today,
                "sales_yesterday": sales_yesterday,
                "sales_7d": sales_7d,
                "sales_prev_7d": sales_prev_7d,
                "change_7d": change_7d,
                "sales_30d": sales_30d,
                "sales_prev_30d": sales_prev_30d,
                "change_30d": change_30d,
                "confirmed_today": confirmed_today,
                "rejected_today": rejected_today,
                "rejection_rate": rejection_rate,
                "avg_order_value": avg_order_value,
                "change_percent": change_percent,
                "repeat_customers_today": repeat_customers_today,
                "new_customers_today": new_customers_today,
                "buyers_1": buyers_1,
                "buyers_2_5": buyers_2_5,
                "buyers_5plus": buyers_5plus,
                "avg_spent_per_buyer": avg_spent_per_buyer,
                "peak_hour": peak_hour,
                "peak_hour_count": peak_hour_count,
                "best_weekday": best_weekday,
                "best_weekday_sales": best_weekday_sales,
                "top_products": top_products,
                "payment_breakdown": payment_breakdown,
                "uncertain_today": uncertain_today,
                "late_recovered_today": late_recovered_today,
                "dormant_customers": dormant_customers,
                "profit_today": profit_today,
                "orders_with_cost_today": orders_with_cost_today,
                "profit_margin_percent": profit_margin_percent,
                "last_7_days_sales": last_7_days_sales,
            }


async def get_weekly_report() -> dict:
    """
    Гузориши ҳафтаина (7 рӯзи охир, шомили имрӯз) — барои дидани тамоюли
    калонтар нисбат ба гузориши рӯзона. Муқоиса бо 7 рӯзи пешина низ дорад.
    """
    today_tj = datetime.now(TJ_TZ).date()
    week_start = today_tj - timedelta(days=6)
    prev_week_start = week_start - timedelta(days=7)

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s, COUNT(*) AS c FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s",
                (week_start,)
            )
            row = await cur.fetchone()
            sales_week = float(row["s"])
            confirmed_week = row["c"]

            await cur.execute(
                "SELECT COALESCE(SUM(price),0) AS s, COUNT(*) AS c FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s AND created_at < %s",
                (prev_week_start, week_start)
            )
            row = await cur.fetchone()
            sales_prev_week = float(row["s"])
            confirmed_prev_week = row["c"]

            if sales_prev_week > 0:
                change_pct = (sales_week - sales_prev_week) / sales_prev_week * 100
            else:
                change_pct = 100.0 if sales_week > 0 else 0.0

            await cur.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE status IN ('rejected','failed') "
                "AND is_balance_topup=0 AND created_at >= %s",
                (week_start,)
            )
            rejected_week = (await cur.fetchone())["c"]

            await cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= %s",
                (week_start,)
            )
            new_customers_week = (await cur.fetchone())["c"]

            avg_order_value = sales_week / confirmed_week if confirmed_week else 0.0

            await cur.execute(
                "SELECT COALESCE(SUM(price - cost_tjs),0) AS profit, "
                "COALESCE(SUM(cost_tjs),0) AS total_cost, COUNT(*) AS with_cost FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND cost_tjs IS NOT NULL AND created_at >= %s",
                (week_start,)
            )
            row = await cur.fetchone()
            profit_week = float(row["profit"])
            orders_with_cost_week = row["with_cost"]
            total_cost_week = float(row["total_cost"])
            margin_percent = round(profit_week / total_cost_week * 100, 1) if total_cost_week > 0 else None

            await cur.execute(
                "SELECT label, COUNT(*) AS cnt, COALESCE(SUM(price),0) AS rev, "
                "COALESCE(SUM(cost_tjs),0) AS cost, "
                "SUM(CASE WHEN cost_tjs IS NOT NULL THEN 1 ELSE 0 END) AS with_cost "
                "FROM orders WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s "
                "GROUP BY label ORDER BY rev DESC LIMIT 5",
                (week_start,)
            )
            top_products = []
            for r in await cur.fetchall():
                rev = float(r["rev"])
                cost = float(r["cost"])
                with_cost = r["with_cost"]
                item_margin = round((rev - cost) / cost * 100, 1) if with_cost and cost > 0 else None
                top_products.append({
                    "label": r["label"] or "—", "count": r["cnt"], "revenue": rev, "margin_percent": item_margin,
                })

            await cur.execute(
                "SELECT DAYNAME(created_at) AS dname, COALESCE(SUM(price),0) AS s FROM orders "
                "WHERE status='confirmed' AND is_balance_topup=0 AND created_at >= %s GROUP BY dname ORDER BY s DESC LIMIT 1",
                (week_start,)
            )
            best_day_row = await cur.fetchone()
            best_weekday_en = best_day_row["dname"] if best_day_row else None
            best_weekday_sales = float(best_day_row["s"]) if best_day_row else 0.0
            weekday_tj = {
                "Monday": "Душанбе", "Tuesday": "Сешанбе", "Wednesday": "Чоршанбе",
                "Thursday": "Панҷшанбе", "Friday": "Ҷумъа",
                "Saturday": "Шанбе", "Sunday": "Якшанбе",
            }
            best_weekday = weekday_tj.get(best_weekday_en, best_weekday_en or "—")

            return {
                "week_start": week_start,
                "week_end": today_tj,
                "sales_week": sales_week,
                "confirmed_week": confirmed_week,
                "sales_prev_week": sales_prev_week,
                "confirmed_prev_week": confirmed_prev_week,
                "change_pct": change_pct,
                "rejected_week": rejected_week,
                "new_customers_week": new_customers_week,
                "avg_order_value": avg_order_value,
                "profit_week": profit_week,
                "orders_with_cost_week": orders_with_cost_week,
                "profit_margin_percent": margin_percent,
                "top_products": top_products,
                "best_weekday": best_weekday,
                "best_weekday_sales": best_weekday_sales,
            }


async def set_confirmed_at(order_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET confirmed_at=NOW() WHERE id=%s",
                (order_id,)
            )


# ==================== ТАНЗИМОТ ====================
async def get_setting(key: str) -> str:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT value FROM settings WHERE key_name=%s", (key,))
            row = await cur.fetchone()
            return row[0] if row else ""

async def set_setting(key: str, value: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO settings (key_name, value) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE value=%s",
                (key, value, value)
            )


# Кэши сарлавҳаи аниматсионии саломдиҳӣ. Дар ин ҷо (database) нигоҳ дошта
# мешавад, то ҳамаи модулҳо (bot=__main__, admin) ЯК нусхаро истифода
# баранд — вагарна navсозӣ ба нусхаи нодуруст мерасад.
welcome_title_cache = ""


async def load_welcome_title():
    """Сарлавҳаи аниматсиониро аз база ба кэши муштарак бор мекунад."""
    global welcome_title_cache
    welcome_title_cache = (await get_setting("welcome_title")) or ""


# Калимаҳои аниматсионии дилхоҳи админ (слот → HTML бо ҳарфҳои премиум).
# Агар слот холӣ бошад, матни оддии пешфарз истифода мешавад.
ANIM_SLOTS = ("autotasdiq", "success", "review", "cart")
anim_cache = {}


async def load_anim_phrases():
    """Ҳамаи калимаҳои аниматсиониро аз база ба кэши муштарак бор мекунад."""
    global anim_cache
    new = {}
    for slot in ANIM_SLOTS:
        try:
            new[slot] = (await get_setting(f"anim_{slot}")) or ""
        except Exception:
            new[slot] = ""
    anim_cache = new


def anim(slot: str, default: str = "") -> str:
    """Калимаи аниматсионии слотро (агар админ гузошта бошад) бармегардонад,
    вагарна матни пешфарзи оддиро. Синхронӣ — аз кэш мехонад."""
    return anim_cache.get(slot) or default


# ==================== ХОТИРАИ ДОИМИИ FSM ====================
async def fsm_set_state(key: str, state):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO fsm_states (storage_key, state) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE state=%s",
                (key, state, state)
            )


async def fsm_get_state(key: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT state FROM fsm_states WHERE storage_key=%s", (key,))
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def fsm_set_data(key: str, data_json: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO fsm_states (storage_key, data) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE data=%s",
                (key, data_json, data_json)
            )


async def fsm_get_data(key: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT data FROM fsm_states WHERE storage_key=%s", (key,))
            row = await cur.fetchone()
            return row[0] if row else None


async def get_db_now():
    """Вақти ҶОРИИ база (бо time_zone-и пул: +05:00). Барои он ки
    _BOT_START_TS бо created_at-и база ҳамоҳанг бошад (на бо вақти системаи
    хости бот, ки метавонад TZ-и дигар дошта бошад)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT NOW()")
            row = await cur.fetchone()
            return row[0] if row else None


async def fsm_cleanup(hours: int = 6):
    """Ҳолатҳои кӯҳнаи FSM (аз hours соат пештар)-ро нест мекунад — то ҷадвал
    варам накунад. Флоуи харид ~20 дақиқа аст, пас 6 соат бехатар аст."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM fsm_states WHERE updated_at < NOW() - INTERVAL %s HOUR",
                (hours,)
            )


DEFAULT_DC_CARD_NUMBER = "9762000226598802"


async def get_dc_card_number() -> str:
    """Рақами корти Душанбе Сити — дар ҳамаи линкҳои пардохт истифода мешавад."""
    value = await get_setting("dc_card_number")
    return value or DEFAULT_DC_CARD_NUMBER


async def set_dc_card_number(card_number: str):
    await set_setting("dc_card_number", card_number)


DEFAULT_MAX_BALANCE_TOPUP = 300.0


async def get_max_balance_topup() -> float:
    """Ҳадди максималии пуркунии баланс дар як маротиба (сомонӣ)."""
    value = await get_setting("max_balance_topup")
    try:
        return float(value) if value else DEFAULT_MAX_BALANCE_TOPUP
    except ValueError:
        return DEFAULT_MAX_BALANCE_TOPUP


async def set_max_balance_topup(amount: float):
    await set_setting("max_balance_topup", str(amount))


async def increment_review_count() -> int:
    """
    Шумораи тартибии отзивҳои ба канал фиристодашударо +1 мекунад ва
    рақами навро бармегардонад (атомикӣ — бехатар агар якбора якчанд
    отзив фиристода шаванд).
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO settings (key_name, value) VALUES ('review_count', '1') "
                "ON DUPLICATE KEY UPDATE value = value + 1"
            )
            await cur.execute(
                "SELECT value FROM settings WHERE key_name='review_count'"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 1


async def reset_leaderboard():
    """
    Рейтинги 'Топ харидорон'-ро тоза мекунад — order-ҳои ПЕШИН дигар
    дар рейтинг ҲИсоб намешаванд. Худи order-ҳо дар database БОҦӢ
    мемонанд (ягон чиз нест намешавад).
    """
    await set_setting("leaderboard_reset_at", datetime.now(TJ_TZ).strftime("%Y-%m-%d %H:%M:%S"))


async def get_leaderboard_reset_at() -> str:
    """Вакти охирини тоза кардани рейтинг. Холӣ агар ҳеч гоҷ тоза нашуда бошад."""
    return await get_setting("leaderboard_reset_at")

# ==================== БАН ====================
async def ban_user(user_id: int, reason: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET is_banned=1, ban_reason=%s WHERE id=%s",
                (reason, user_id)
            )

async def unban_user(user_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET is_banned=0, ban_reason='' WHERE id=%s",
                (user_id,)
            )

async def is_banned(user_id: int) -> tuple:
    """Бармегардонад (banned: bool, reason: str)"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT is_banned, ban_reason FROM users WHERE id=%s",
                (user_id,)
            )
            row = await cur.fetchone()
            if row:
                return bool(row[0]), row[1] or ""
            return False, ""

# ==================== МАЪЛУМОТИ КОРБАР ====================
async def get_user_stats(user_id: int) -> dict:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # Умумӣ
            await cur.execute("""
                SELECT COUNT(*) as total_orders, COALESCE(SUM(price), 0) as total_spent
                FROM orders WHERE user_id=%s AND status='confirmed' AND is_balance_topup=0
            """, (user_id,))
            stats = await cur.fetchone()
            # Ҳама фармоишҳо
            await cur.execute("""
                SELECT id, game_id, label, price, status, created_at
                FROM orders WHERE user_id=%s
                ORDER BY id DESC LIMIT 20
            """, (user_id,))
            orders = await cur.fetchall()
            return {
                "total_orders": stats["total_orders"],
                "total_spent": float(stats["total_spent"]),
                "orders": orders
            }


# ==================== FF INDONESIA ====================
async def get_ffid_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM ffid_products WHERE is_active=1 ORDER BY sort_order"
            )
            return await cur.fetchall()


async def get_ffid_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ffid_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_ffid_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ffid_products ORDER BY sort_order")
            return await cur.fetchall()


async def add_ffid_product(amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO ffid_products (amount, price, label, offer_id) VALUES (%s,%s,%s,%s)",
                (amount, price, label, offer_id)
            )


async def update_ffid_product(product_id: int, amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE ffid_products SET amount=%s, price=%s, label=%s, offer_id=%s WHERE id=%s",
                (amount, price, label, offer_id, product_id)
            )


async def delete_ffid_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM ffid_products WHERE id=%s", (product_id,))


# ==================== PUBG MOBILE ====================
async def get_pubg_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM pubg_products WHERE is_active=1 ORDER BY sort_order"
            )
            return await cur.fetchall()


async def get_pubg_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM pubg_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_pubg_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM pubg_products ORDER BY sort_order")
            return await cur.fetchall()


async def add_pubg_product(amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO pubg_products (amount, price, label, offer_id) VALUES (%s,%s,%s,%s)",
                (amount, price, label, offer_id)
            )


async def update_pubg_product(product_id: int, amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE pubg_products SET amount=%s, price=%s, label=%s, offer_id=%s WHERE id=%s",
                (amount, price, label, offer_id, product_id)
            )


async def delete_pubg_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM pubg_products WHERE id=%s", (product_id,))


# ==================== STANDOFF 2 (голд — донати ДАСТӢ) ====================
# Standoff 2 дар donatov.net аст, ки API надорад — пас донат дастист:
# бот фармоишро қабул мекунад, соҳиб худаш дар donatov.net иҷро мекунад.
# Барои ҳамин offer_id нест (мисли PUBG), танҳо ном/миқдор/нарх.
async def get_standoff_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM standoff_products WHERE is_active=1 ORDER BY sort_order, amount"
            )
            return await cur.fetchall()


async def get_standoff_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM standoff_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_standoff_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM standoff_products ORDER BY sort_order, amount")
            return await cur.fetchall()


async def add_standoff_product(amount: int, price: float, label: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO standoff_products (amount, price, label) VALUES (%s,%s,%s)",
                (amount, price, label)
            )


async def update_standoff_product(product_id: int, amount: int, price: float, label: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE standoff_products SET amount=%s, price=%s, label=%s WHERE id=%s",
                (amount, price, label, product_id)
            )


async def delete_standoff_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM standoff_products WHERE id=%s", (product_id,))


# ==================== FREE FIRE BRAZIL (донати ХУДКОР) ====================
async def get_ffbr_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM ffbr_products WHERE is_active=1 ORDER BY sort_order, amount")
            return await cur.fetchall()


async def get_ffbr_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ffbr_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_ffbr_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ffbr_products ORDER BY sort_order, amount")
            return await cur.fetchall()


async def add_ffbr_product(amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO ffbr_products (amount, price, label, offer_id) VALUES (%s,%s,%s,%s)",
                (amount, price, label, offer_id))


async def update_ffbr_product(product_id: int, amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE ffbr_products SET amount=%s, price=%s, label=%s, offer_id=%s WHERE id=%s",
                (amount, price, label, offer_id, product_id))


async def get_ml_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM ml_products WHERE is_active=1 ORDER BY sort_order, amount")
            return await cur.fetchall()


async def get_ml_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ml_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_ml_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM ml_products ORDER BY sort_order, amount")
            return await cur.fetchall()


async def add_ml_product(amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO ml_products (amount, price, label, offer_id) VALUES (%s,%s,%s,%s)",
                (amount, price, label, offer_id))


async def update_ml_product(product_id: int, amount: int, price: float, label: str, offer_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE ml_products SET amount=%s, price=%s, label=%s, offer_id=%s WHERE id=%s",
                (amount, price, label, offer_id, product_id))


async def delete_ml_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM ml_products WHERE id=%s", (product_id,))


async def delete_ffbr_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM ffbr_products WHERE id=%s", (product_id,))


# ==================== TELEGRAM STARS ====================
async def get_stars_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM stars_products WHERE is_active=1 ORDER BY sort_order"
            )
            return await cur.fetchall()


async def get_stars_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM stars_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_stars_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM stars_products ORDER BY sort_order")
            return await cur.fetchall()


async def add_stars_product(amount: int, price: float):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO stars_products (amount, price) VALUES (%s,%s)",
                (amount, price)
            )


async def update_stars_product(product_id: int, amount: int, price: float):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE stars_products SET amount=%s, price=%s WHERE id=%s",
                (amount, price, product_id)
            )


async def delete_stars_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM stars_products WHERE id=%s", (product_id,))


# ==================== TELEGRAM PREMIUM ====================
async def get_premium_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM premium_products WHERE is_active=1 ORDER BY sort_order"
            )
            return await cur.fetchall()


async def get_premium_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM premium_products WHERE id=%s", (product_id,))
            return await cur.fetchone()


async def get_all_premium_products():
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM premium_products ORDER BY sort_order")
            return await cur.fetchall()


async def add_premium_product(months: int, price: float):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO premium_products (months, price) VALUES (%s,%s)",
                (months, price)
            )


async def update_premium_product(product_id: int, months: int, price: float):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE premium_products SET months=%s, price=%s WHERE id=%s",
                (months, price, product_id)
            )


async def delete_premium_product(product_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM premium_products WHERE id=%s", (product_id,))


# ==================== АВТОПАРДОХТ (DC Next) ====================
async def get_active_awaiting_prices(payment_method: str) -> set:
    """Нархҳои фармоишҳои автопардохти фаъол — барои он ки ду мизоҷ
    дар як вақт ҳамон як маблағро нагиранд."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Ҳам DC ва ҳам Алиф — ҳарду ба ҳамон корти DC меоянд, пас
            # маблағ бояд дар байни ҲАРДУ усул нодир бошад
            await cur.execute(
                "SELECT price FROM orders "
                "WHERE status IN ('awaiting_autopay','autopay_search') "
                "AND payment_method IN ('dushanbe_city','alif') "
                "AND created_at >= NOW() - INTERVAL 30 MINUTE"
            )
            rows = await cur.fetchall()
            return {round(float(r[0]), 2) for r in rows}


async def create_awaiting_order(user_id, game_id, nickname, amount, price, label,
                                 offer_id, payment_method, is_balance_topup=0):
    """Фармоиши 'дар интизории автопардохт' месозад (пеш аз пардохти мизоҷ)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO orders
                    (user_id, game_id, nickname, amount, price, label, offer_id,
                     payment_method, is_balance_topup, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'awaiting_autopay')
            """, (user_id, game_id, nickname, amount, price, label, offer_id,
                  payment_method, is_balance_topup))
            return cur.lastrowid


async def find_awaiting_order_by_price(price: float, payment_method: str,
                                        max_age_minutes: int = 15):
    """Фармоишеро меёбад, ки ЧЕК фиристодааст ('autopay_search') ва
    мунтазири пардохт бо ҳамин нархи дақиқ аст."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='autopay_search' "
                "AND payment_method IN ('dushanbe_city','alif') AND price=%s "
                "AND created_at >= NOW() - INTERVAL %s MINUTE "
                "ORDER BY created_at ASC LIMIT 1",
                (price, max_age_minutes)
            )
            return await cur.fetchone()


async def has_awaiting_order_by_price(price: float, payment_method: str,
                                       max_age_minutes: int = 15) -> bool:
    """Оё фармоиши 'awaiting_autopay' (чек ҳанӯз наомада) бо ин нарх ҳаст?"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM orders WHERE status='awaiting_autopay' "
                "AND payment_method IN ('dushanbe_city','alif') AND price=%s "
                "AND created_at >= NOW() - INTERVAL %s MINUTE LIMIT 1",
                (price, max_age_minutes)
            )
            return (await cur.fetchone()) is not None


async def set_autopay_check(order_id: int, file_id: str, check_hash: str = None):
    """Чеки фармоиши автопардохтро сабт карда, статусро 'autopay_search'
    мегузорад — аз ҳамин лаҳза ҷустуҷӯи пардохт фаъол мешавад.
    Фармоиши 'expired' (чек дер расида) низ бармегардад, то мизоҷони
    дер расонида низ донати худкор гиранд.
    check_hash низ сабт мешавад — то абзори админии «Ҷустуҷӯи чек» ин
    фармоишҳоро низ ёфта тавонад (пештар танҳо чекҳои дастӣ ёфт мешуданд)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET check_file_id=%s, check_hash=%s, status='autopay_search' "
                "WHERE id=%s AND status IN ('awaiting_autopay','expired')",
                (file_id, check_hash, order_id)
            )
            return cur.rowcount > 0


async def claim_unmatched_kod(summa: float, order_id: int, max_age_minutes: int = 15):
    """Атомикӣ: Kod-и пардохти омада (ҳанӯз пайванднашуда) бо ҳамин маблағро
    ба ин фармоиш БАНД мекунад ва бармегардонад.

    Пеш ин танҳо SELECT буд — ду фармоиши ҳамзамон бо ҳамон нарх метавонистанд
    ҳамон як kod-ро гиранд ва ҳарду донат шаванд (як пардохт → ду донат).
    Акнун UPDATE...LIMIT 1 kod-ро атомикӣ мегирад: танҳо ЯКЕ мувафақ мешавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE dc_kods SET matched_order_id=%s "
                "WHERE matched_order_id IS NULL AND summa=%s "
                "AND received_at >= NOW() - INTERVAL %s MINUTE "
                "ORDER BY received_at ASC LIMIT 1",
                (order_id, summa, max_age_minutes)
            )
            if cur.rowcount == 0:
                return None
            await cur.execute(
                "SELECT kod FROM dc_kods WHERE matched_order_id=%s "
                "ORDER BY received_at ASC LIMIT 1",
                (order_id,)
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def get_stale_search_orders(max_age_minutes: int = 10) -> list:
    """Фармоишҳои 'autopay_search', ки аз онҳо зиёда аз N дақиқа гузашт
    ва пардохташон ёфт нашуд — статусро 'paid' мегузорад (барои тафтиши
    дастии админ) ва рӯйхаташонро бармегардонад."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='autopay_search' "
                "AND created_at < NOW() - INTERVAL %s MINUTE",
                (max_age_minutes,)
            )
            stale = await cur.fetchall()
            if stale:
                ids = [o["id"] for o in stale]
                fmt = ",".join(["%s"] * len(ids))
                await cur.execute(
                    f"UPDATE orders SET status='paid' WHERE id IN ({fmt})",
                    tuple(ids)
                )
            return stale


async def expire_stale_awaiting_orders(max_age_minutes: int = 15) -> list:
    """Фармоишҳои 'awaiting_autopay'-и мӯҳлаташон гузаштаро 'expired' мекунад
    ва рӯйхаташонро бармегардонад (то ба мизоҷ хабар диҳем)."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='awaiting_autopay' "
                "AND created_at < NOW() - INTERVAL %s MINUTE",
                (max_age_minutes,)
            )
            stale = await cur.fetchall()
            if stale:
                ids = [o["id"] for o in stale]
                fmt = ",".join(["%s"] * len(ids))
                await cur.execute(
                    f"UPDATE orders SET status='expired' WHERE id IN ({fmt})",
                    tuple(ids)
                )
            return stale


async def get_orders_nearing_expiry(warn_before_minutes: int, max_age_minutes: int) -> list:
    """Фармоишҳои 'awaiting_autopay', ки то ба итмом расидани мӯҳлат камтар
    аз warn_before_minutes мондааст ва ҳанӯз огоҳ карда нашудаанд —
    аломати огоҳиро мегузорад ва рӯйхаташонро бармегардонад."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='awaiting_autopay' AND expiry_warned=0 "
                "AND created_at < NOW() - INTERVAL %s MINUTE "
                "AND created_at >= NOW() - INTERVAL %s MINUTE",
                (max_age_minutes - warn_before_minutes, max_age_minutes)
            )
            rows = await cur.fetchall()
            if rows:
                ids = [o["id"] for o in rows]
                fmt = ",".join(["%s"] * len(ids))
                await cur.execute(
                    f"UPDATE orders SET expiry_warned=1 WHERE id IN ({fmt})",
                    tuple(ids)
                )
            return rows


async def mark_order_late_recovered(order_id: int):
    """Аломат мегузорад, ки ин фармоиш пас аз "мӯҳлаташ гузашт" бо пардохти
    дерина наҷот ёфтааст — барои омори гузориши шабона."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET late_recovered=1 WHERE id=%s", (order_id,))


async def is_kod_seen(kod: str) -> bool:
    """Оё ин Kod-и DC Next пештар дида шудааст (зидди такрор)?"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM dc_kods WHERE kod=%s", (kod,))
            return (await cur.fetchone()) is not None


async def create_pay_token(url: str) -> str:
    """Барои линки воқеӣ як токени кӯтоҳ месозад ва бармегардонад (барои
    силкаи ноаён). Редиректи pay.wineclo.com ин токенро ба линки воқеӣ
    иваз мекунад."""
    import secrets
    token = secrets.token_urlsafe(8)[:12]
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO pay_links (token, url) VALUES (%s,%s)",
                (token, url))
    return token


async def get_pay_url(token: str):
    """Линки воқеиро аз токен мегирад (барои сервери редирект)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT url FROM pay_links WHERE token=%s", (token,))
            row = await cur.fetchone()
            return row[0] if row else None


async def cleanup_pay_tokens(days: int = 3):
    """Токенҳои кӯҳнаро тоза мекунад (аз 3 рӯз калон) — база варам накунад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM pay_links WHERE created_at < NOW() - INTERVAL %s DAY",
                (days,))


async def record_kod(kod: str, summa: float) -> bool:
    """Kod-ро сабт мекунад. True = НАВ буд, False = аллакай буд.
    `kod` PRIMARY KEY аст, пас INSERT IGNORE атомикӣ такрорро мебандад —
    ин ягона санҷиши бехатар аст (is_kod_seen+INSERT ду қадами ҷудо буд ва
    агар барномаи телефон ҳамон огоҳиро ду бор фиристад, ҳарду мегузаштанд)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO dc_kods (kod, summa) VALUES (%s,%s)",
                (kod, summa)
            )
            return cur.rowcount > 0


async def cleanup_stale_pending_cart(hours: int = 6) -> int:
    """Фармоишҳои сабади РЕЗЕРВШУДА, ки мизоҷ мепартояд (status='pending',
    order_group_id дорад, чек нарасида) ва аз hours соат кӯҳнатаранд — нест
    мекунад, то ҷадвал варам накунад. Инҳо ҳеҷ гоҳ пардохт/чек нагирифтаанд."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM orders WHERE status='pending' "
                "AND order_group_id IS NOT NULL AND check_file_id IS NULL "
                "AND created_at < NOW() - INTERVAL %s HOUR "
                # Гурӯҳҳое, ки пардохташон ёфт шуда (kod резерв) — нест НАКУН
                "AND order_group_id NOT IN ("
                "  SELECT gid FROM ("
                "    SELECT DISTINCT o2.order_group_id AS gid FROM orders o2 "
                "    JOIN dc_kods k ON k.matched_order_id = o2.id "
                "    WHERE o2.order_group_id IS NOT NULL"
                "  ) AS t)",
                (hours,)
            )
            return cur.rowcount


async def find_reserved_order_for_user(user_id: int, max_age_minutes: int = 120):
    """Фармоиши мизоҷ, ки пардохташ дар банк ЁФТ ШУДА (dc_kods.matched_order_id
    = order.id) вале ҳанӯз чек нарасида (awaiting_autopay/expired). Барои он ки
    агар мизоҷ чекро ба фармоиши ГАЛАТ (#1112) фиристад, бот чекро ба фармоиши
    ДУРУСТ (#1111, ки пулаш омада) бандад."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT o.* FROM orders o "
                "JOIN dc_kods k ON k.matched_order_id = o.id "
                "WHERE o.user_id=%s "
                "AND o.status IN ('awaiting_autopay','expired') "
                "AND o.payment_method IN ('dushanbe_city','alif') "
                "AND o.order_group_id IS NULL "
                "AND o.created_at >= NOW() - INTERVAL %s MINUTE "
                "ORDER BY o.created_at DESC LIMIT 1",
                (user_id, max_age_minutes)
            )
            return await cur.fetchone()


async def mark_kod_matched(kod: str, order_id: int):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE dc_kods SET matched_order_id=%s WHERE kod=%s",
                (order_id, kod)
            )


async def find_kod_for_order(order_id: int):
    """Kod-и пардохте, ки аллакай ба ин фармоиш баста (резерв) шудааст."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT kod FROM dc_kods WHERE matched_order_id=%s "
                "ORDER BY received_at DESC LIMIT 1",
                (order_id,)
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def claim_order_for_donate(order_id: int) -> bool:
    """Атомикӣ: фармоишро ба 'paid' мегузаронад, ФАҚАТ агар он ҳанӯз дар
    ҳолати автопардохт бошад (ё "мӯҳлаташ гузашта", вале пардохти дерина
    воқеан омада бошад). False = касе аллакай гирифтааст (такрор!)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='paid' WHERE id=%s "
                "AND status IN ('awaiting_autopay','autopay_search','expired')",
                (order_id,)
            )
            return cur.rowcount > 0


async def claim_order_for_reject(order_id: int) -> bool:
    """Атомикӣ: фармоишро ба 'rejected' мегузаронад, ФАҚАТ агар он ҳанӯз
    ниҳоӣ ё дар ҳоли донат набошад. False = аллакай коркард шудааст (масалан
    ду админ ҳамзамон рад карданд) — то БАРГАРДОНИДАНИ БАЛАНС ду бор нашавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='rejected' WHERE id=%s "
                "AND status NOT IN ('confirmed','rejected','donating')",
                (order_id,)
            )
            return cur.rowcount > 0


async def claim_order_for_donate_attempt(order_id: int) -> bool:
    """Атомикӣ: фармоишро барои ЯК кӯшиши донат банд мекунад — ҳам аз
    'paid' (тасдиқи аввал), ҳам аз 'failed' (тугмаи «Дубора донат»).

    Пеш аз ин, тугмаи «Дубора донат» (статус='failed') ҳељ claim
    намегирифт — ду пахши паси ҳам ду донат месохт. Акнун пахши дуюм
    статусро дигар 'paid'/'failed' намебинад (аллакай 'donating' шуд) ва
    False мегирад. Фармоиши 'donating' (донати зинда) низ банд намешавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='donating', donating_at=NOW() "
                "WHERE id=%s AND status IN ('paid','failed')",
                (order_id,)
            )
            return cur.rowcount > 0


async def claim_paid_order_for_autodonate(order_id: int) -> bool:
    """Атомикӣ: фармоиши 'paid' (яъне аллакай ба админ фиристодашуда, чи
    аз тарафи DC-эскалатсия, чи чеки дастии Алиф/Эсхата)-ро ба 'donating'
    мегузаронад. Ин ягона роҳест барои бо ҳам бор гирифтани ин фармоиш —
    агар ҳам админ дастӣ "Тасдиқ" пахш кунад, ҳам DCSCAN/DCNOTIF ҳамон
    лаҳза пардохтро ёбад, танҳо ЯКЕ аз онҳо мувафаққ мешавад (rowcount>0),
    дигараш False мегирад ва донат такрор намешавад."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status='donating', donating_at=NOW() "
                "WHERE id=%s AND status='paid'",
                (order_id,)
            )
            return cur.rowcount > 0


async def recover_stuck_donating_orders(minutes: int = 3) -> list:
    """Агар сервер маҳз дар вақти донат (байни 'donating' ва натиҷаи ниҳоӣ)
    рестарт/қатъ шуда бошад, фармоиш метавонад доимӣ дар 'donating' монад —
    на автопардохт, на админ дигар ба он даст расонда наметавонанд. Ин
    функсия чунин фармоишҳоро баъд аз N дақиқа ба 'paid' бармегардонад, то
    админ/DCSCAN боз кӯшиш карда тавонанд."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM orders WHERE status='donating' "
                "AND donating_at < NOW() - INTERVAL %s MINUTE",
                (minutes,)
            )
            stuck = await cur.fetchall()
            if stuck:
                ids = [o["id"] for o in stuck]
                fmt = ",".join(["%s"] * len(ids))
                await cur.execute(
                    f"UPDATE orders SET status='paid' WHERE id IN ({fmt})",
                    tuple(ids)
                )
            return stuck


