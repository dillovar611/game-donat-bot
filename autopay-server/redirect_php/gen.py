"""
Генератори PHP-и редирект барои pay.wineclo.com.
Дар сервер иҷро мешавад: креденшлҳои базаро аз конфиги бот мегирад ва
index.php + .htaccess-ро ба папкаи сайт менависад. Креденшлҳо ба ягон ҷо
чоп намешаванд ва дар PHP (файли иҷрошаванда, на намоишӣ) мемонанд.

Иҷро (дар /var/www/ws2562/data):
    python3 gen.py
Ё бо папкаи дастӣ:
    python3 gen.py /роҳи/папкаи/pay.wineclo.com
"""
import glob
import os
import sys

import env  # noqa: F401
import config


def _php_str(v: str) -> str:
    # Сатри бехатари PHP бо нохунаки якгона
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _find_docroot() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].rstrip("/")
    pats = [
        "/var/www/*/data/www/pay.wineclo.com",
        "/var/www/*/www/pay.wineclo.com",
        "/var/www/*/data/www/pay.wineclo.com/public_html",
    ]
    for p in pats:
        hits = glob.glob(p)
        if hits:
            return hits[0].rstrip("/")
    return ""


def main():
    docroot = _find_docroot()
    if not docroot or not os.path.isdir(docroot):
        print("❌ Папкаи pay.wineclo.com ёфт нашуд. Роҳро дастӣ диҳед:")
        print("   python3 gen.py /роҳи/дақиқ")
        return

    php = (
        "<?php\n"
        "$t = isset($_GET['t']) ? trim($_GET['t'], \"/ \\t\\n\") : '';\n"
        "if ($t === '') { header('Location: https://t.me/'); exit; }\n"
        "$m = @new mysqli(%s, %s, %s, %s);\n"
        "if ($m->connect_errno) { header('Location: https://t.me/'); exit; }\n"
        "$s = $m->prepare('SELECT url FROM pay_links WHERE token=?');\n"
        "$s->bind_param('s', $t); $s->execute(); $s->bind_result($u);\n"
        "if ($s->fetch() && $u && strpos($u, 'pay.dc.tj') !== false) {\n"
        "    header('Location: ' . $u, true, 302);\n"
        "} else { header('Location: https://t.me/'); }\n"
        "exit;\n"
    ) % (
        _php_str(config.DB_HOST),
        _php_str(config.DB_USER),
        _php_str(config.DB_PASSWORD),
        _php_str(config.DB_NAME),
    )

    htaccess = (
        "RewriteEngine On\n"
        "RewriteCond %{REQUEST_FILENAME} !-f\n"
        "RewriteRule ^(.+)$ index.php?t=$1 [L,QSA]\n"
    )

    with open(os.path.join(docroot, "index.php"), "w") as f:
        f.write(php)
    with open(os.path.join(docroot, ".htaccess"), "w") as f:
        f.write(htaccess)

    print(f"✅ Файлҳо навишта шуданд ба:\n   {docroot}")
    print("   • index.php (бо креденшлҳо, файли иҷрошаванда)")
    print("   • .htaccess (бо rewrite)")
    print("\nАкнун санҷед: https://pay.wineclo.com/test  (бояд ба t.me равона кунад)")


if __name__ == "__main__":
    main()
