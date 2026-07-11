package tj.dilovar.dcnotifier

import android.content.Context
import org.json.JSONArray
import kotlin.concurrent.thread

/**
 * Навбати умумии фиристодан — ҳам listener ва ҳам хизмати доимӣ
 * ҳамин объектро истифода мебаранд. Паём ҳеҷ гоҳ гум намешавад:
 * агар интернет/Telegram ҷавоб надиҳад, дар навбат мемонад ва
 * баъдтар аз нав кӯшиш мешавад.
 */
object Sender {

    @Synchronized
    fun enqueue(ctx: Context, message: String) {
        val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val arr = JSONArray(prefs.getString("queue", "[]"))
        arr.put(message)
        val trimmed = JSONArray()
        val start = if (arr.length() > 200) arr.length() - 200 else 0
        for (i in start until arr.length()) trimmed.put(arr.getString(i))
        prefs.edit().putString("queue", trimmed.toString()).apply()
    }

    fun flushAsync(ctx: Context) {
        thread { flush(ctx) }
    }

    @Synchronized
    fun flush(ctx: Context) {
        val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val token = prefs.getString("token", "") ?: ""
        val chatId = prefs.getString("chat_id", "") ?: ""
        if (token.isEmpty() || chatId.isEmpty()) return

        var arr = JSONArray(prefs.getString("queue", "[]"))
        while (arr.length() > 0) {
            val msg = arr.getString(0)
            if (TelegramSender.send(token, chatId, msg)) {
                val rest = JSONArray()
                for (i in 1 until arr.length()) rest.put(arr.getString(i))
                arr = rest
                prefs.edit()
                    .putString("queue", arr.toString())
                    .putInt("sent_count", prefs.getInt("sent_count", 0) + 1)
                    .putLong("last_sent_at", System.currentTimeMillis())
                    .apply()
            } else {
                break  // интернет нест — баъдтар аз нав
            }
        }
    }
}
