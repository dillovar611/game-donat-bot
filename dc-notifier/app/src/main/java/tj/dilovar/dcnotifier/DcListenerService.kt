package tj.dilovar.dcnotifier

import android.app.Notification
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import kotlin.concurrent.thread

/**
 * Notification-ҳои ҳамаи барномаҳоро мехонад, вале ТАНҲО онҳоеро, ки ба
 * воридоти пул (Зачисление/Zachislenie) монанданд, ба гурӯҳи Telegram
 * мефиристад. Парсинги дақиқ (Summa/Kod) дар СЕРВЕР мешавад — ин ҷо танҳо
 * матни хом фиристода мешавад, то ҳангоми тағйири формати DC танҳо
 * серверро нав кунем, на APK-ро.
 *
 * Навбат (queue): агар интернет набошад, паём гум намешавад — дар
 * SharedPreferences нигоҳ дошта шуда, ҳар 30 сония аз нав кӯшиш мешавад.
 */
class DcListenerService : NotificationListenerService() {

    private val handler = Handler(Looper.getMainLooper())
    private var flushScheduled = false

    // Калимаҳое, ки нишон медиҳанд ин notification-и воридоти пул аст
    private val keywords = listOf(
        "зачисление", "zachislenie", "поступление", "postuplenie"
    )

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try {
            // Notification-и худи мо ё системаро нодида мегирем
            if (sbn.packageName == packageName) return

            val extras = sbn.notification.extras
            val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
            val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
            val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
            val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
                ?.joinToString("\n") { it.toString() } ?: ""

            // Пурратарин матнро интихоб мекунем
            val body = listOf(big, lines, text).firstOrNull { it.isNotBlank() } ?: ""
            val full = "$title\n$body".trim()
            if (full.isBlank()) return

            val lower = full.lowercase()
            if (keywords.none { lower.contains(it) }) return

            // Зидди такрор: ҳамон матн дар 60 сонияи охир
            val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
            val hash = full.hashCode().toString()
            val lastHash = prefs.getString("last_hash", "")
            val lastTime = prefs.getLong("last_time", 0)
            val now = System.currentTimeMillis()
            if (hash == lastHash && now - lastTime < 60_000) return
            prefs.edit().putString("last_hash", hash).putLong("last_time", now).apply()

            val message = "DCNOTIF [${sbn.packageName}]\n$full"
            enqueue(message)
            flushQueue()
        } catch (e: Exception) {
            // ҳеҷ гоҳ crash накунем — notification-ҳои дигар муҳиманд
        }
    }

    // ==================== НАВБАТ ====================

    @Synchronized
    private fun enqueue(message: String) {
        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val arr = JSONArray(prefs.getString("queue", "[]"))
        arr.put(message)
        // ҳадди аксар 200 паём нигоҳ медорем
        val trimmed = JSONArray()
        val start = if (arr.length() > 200) arr.length() - 200 else 0
        for (i in start until arr.length()) trimmed.put(arr.getString(i))
        prefs.edit().putString("queue", trimmed.toString()).apply()
    }

    private fun flushQueue() {
        thread {
            val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
            val token = prefs.getString("token", "") ?: ""
            val chatId = prefs.getString("chat_id", "") ?: ""
            if (token.isEmpty() || chatId.isEmpty()) return@thread

            var failed = false
            synchronized(this) {
                var arr = JSONArray(prefs.getString("queue", "[]"))
                while (arr.length() > 0) {
                    val msg = arr.getString(0)
                    if (TelegramSender.send(token, chatId, msg)) {
                        // аз навбат мебарорем
                        val rest = JSONArray()
                        for (i in 1 until arr.length()) rest.put(arr.getString(i))
                        arr = rest
                        prefs.edit()
                            .putString("queue", arr.toString())
                            .putInt("sent_count", prefs.getInt("sent_count", 0) + 1)
                            .apply()
                    } else {
                        failed = true
                        break
                    }
                }
            }

            // Агар нашуд — баъд аз 30 сония аз нав
            if (failed && !flushScheduled) {
                flushScheduled = true
                handler.postDelayed({
                    flushScheduled = false
                    flushQueue()
                }, 30_000)
            }
        }
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        // Ҳангоми пайвастшавӣ навбати мондаро мефиристем
        flushQueue()
    }
}
