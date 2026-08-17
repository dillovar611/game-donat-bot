<?php
// Редиректи силкаи ноаён: pay.wineclo.com/<token> → линки воқеии pay.dc.tj
// Токенро аз базаи ҳамон бот (ҷадвали pay_links) мехонад ва равона мекунад.
// db.ini дар ҳамин папка бо командаи серверӣ сохта мешавад (креденшлҳо
// ба чат нишон дода намешаванд).
$t = isset($_GET['t']) ? trim($_GET['t'], "/ \t\n") : '';
if ($t === '') { header('Location: https://t.me/'); exit; }

$cfg = @parse_ini_file(__DIR__ . '/db.ini');
if (!$cfg) { header('Location: https://t.me/'); exit; }

$m = @new mysqli($cfg['host'], $cfg['user'], $cfg['pass'], $cfg['name']);
if ($m->connect_errno) { header('Location: https://t.me/'); exit; }

$s = $m->prepare('SELECT url FROM pay_links WHERE token=?');
$s->bind_param('s', $t);
$s->execute();
$s->bind_result($url);
if ($s->fetch() && $url && strpos($url, 'pay.dc.tj') !== false) {
    header('Location: ' . $url, true, 302);
} else {
    header('Location: https://t.me/');
}
exit;
