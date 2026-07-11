package tj.dilovar.dcnotifier

import android.app.Notification
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * Notification-ҳоро мехонад ва онҳоеро, ки ба пули воридотӣ монанданд,
 * ба канали Telegram мефиристад. Парсинги дақиқ дар СЕРВЕР мешавад.
 *
 * Филтр васеъ аст: калимаҳои "зачисление/поступление/перевод" Ё калимаи
 * "summa"/"TJS" — то ягон намуди паёми пулӣ гум нашавад. Паёми зиёдатӣ
 * мушкил нест — сервер танҳо ба он паёмҳо ҷавоб медиҳад, ки Summa+Kod
 * доранд.
 */
class DcListenerService : NotificationListenerService() {

    private val keywords = listOf(
        "зачисление", "zachislenie", "поступление", "postuplenie",
        "перевод", "perevod", "summa", "сумма", "tjs"
    )

    override fun onCreate() {
        super.onCreate()
        // Хизмати доимиро сар мекунем (агар ҳанӯз сар нашуда бошад)
        try {
            val svc = Intent(this, KeepAliveService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc)
            else startService(svc)
        } catch (e: Exception) {
        }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try {
            if (sbn.packageName == packageName) return

            val extras = sbn.notification.extras
            val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
            val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
            val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
            val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
                ?.joinToString("\n") { it.toString() } ?: ""

            // Пурратарин матн: big > lines > text
            val body = listOf(big, lines, text).firstOrNull { it.isNotBlank() } ?: ""
            val full = "$title\n$body".trim()
            if (full.isBlank()) return

            val lower = full.lowercase()
            if (keywords.none { lower.contains(it) }) return

            // Зидди такрори ҳамон як notification (update-ҳои паси ҳам):
            // айнан ҳамон матн дар 45 сонияи охир дубора намеравад.
            // (Такрори воқеӣ дар сервер бо Kod филтр мешавад.)
            val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
            val hash = full.hashCode().toString()
            val lastHash = prefs.getString("last_hash", "")
            val lastTime = prefs.getLong("last_time", 0)
            val now = System.currentTimeMillis()
            if (hash == lastHash && now - lastTime < 45_000) return
            prefs.edit().putString("last_hash", hash).putLong("last_time", now).apply()

            Sender.enqueue(this, "DCNOTIF [${sbn.packageName}]\n$full")
            Sender.flushAsync(this)
        } catch (e: Exception) {
            // ҳеҷ гоҳ crash накунем
        }
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        Sender.flushAsync(this)
    }
}
