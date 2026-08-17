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

    private const val MAX_MSG_LEN = 3500  // Telegram то 4096 иҷозат медиҳад — бо эҳтиёт кӯтоҳтар мегирем
    private const val MAX_FAIL_BEFORE_DROP = 5

    @Synchronized
    fun enqueue(ctx: Context, message: String) {
        val safe = if (message.length > MAX_MSG_LEN) message.take(MAX_MSG_LEN) + "\n…(қатъ шуд)" else message
        val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val arr = JSONArray(prefs.getString("queue", "[]"))
        arr.put(safe)
        val trimmed = JSONArray()
        val start = if (arr.length() > 200) arr.length() - 200 else 0
        for (i in start until arr.length()) trimmed.put(arr.getString(i))
        prefs.edit().putString("queue", trimmed.toString()).apply()
    }

    fun flushAsync(ctx: Context) {
        thread { flush(ctx) }
    }

    /** Агар паёми пеши навбат борҳо (то MAX_FAIL_BEFORE_DROP) ноком шавад,
     * онро партофта, навбатро идома медиҳем — то ЯК паёми вайрон тамоми
     * навбатро абадӣ банд накунад. */
    @Synchronized
    fun flush(ctx: Context) {
        val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val token = prefs.getString("token", "") ?: ""
        val chatId = prefs.getString("chat_id", "") ?: ""
        if (token.isEmpty() || chatId.isEmpty()) return

        var arr = JSONArray(prefs.getString("queue", "[]"))
        var failCount = prefs.getInt("front_fail_count", 0)
        while (arr.length() > 0) {
            val msg = arr.getString(0)
            if (TelegramSender.send(token, chatId, msg)) {
                val rest = JSONArray()
                for (i in 1 until arr.length()) rest.put(arr.getString(i))
                arr = rest
                failCount = 0
                prefs.edit()
                    .putString("queue", arr.toString())
                    .putInt("front_fail_count", 0)
                    .putInt("sent_count", prefs.getInt("sent_count", 0) + 1)
                    .putLong("last_sent_at", System.currentTimeMillis())
                    .apply()
            } else {
                failCount++
                if (failCount >= MAX_FAIL_BEFORE_DROP) {
                    // Ин паём вайрон аст (масалан аз ҳад дароз/нодуруст) — партофта, идома медиҳем
                    val rest = JSONArray()
                    for (i in 1 until arr.length()) rest.put(arr.getString(i))
                    arr = rest
                    failCount = 0
                    prefs.edit()
                        .putString("queue", arr.toString())
                        .putInt("front_fail_count", 0)
                        .apply()
                } else {
                    prefs.edit().putInt("front_fail_count", failCount).apply()
                    break  // шояд интернет нест — баъдтар аз нав кӯшиш мешавад
                }
            }
        }
    }
}
