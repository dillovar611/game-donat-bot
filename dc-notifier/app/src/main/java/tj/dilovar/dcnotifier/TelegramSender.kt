package tj.dilovar.dcnotifier

import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

object TelegramSender {
    /**
     * Паёмро тавассути Bot API ба гурӯҳ мефиристад.
     * true = муваффақ, false = хато (баъдтар аз навбат такрор мешавад).
     */
    fun send(token: String, chatId: String, text: String): Boolean {
        return try {
            val url = URL("https://api.telegram.org/bot$token/sendMessage")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 15000
            conn.readTimeout = 15000
            conn.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")

            val body = "chat_id=" + URLEncoder.encode(chatId, "UTF-8") +
                    "&text=" + URLEncoder.encode(text, "UTF-8")

            OutputStreamWriter(conn.outputStream, "UTF-8").use { it.write(body) }

            val code = conn.responseCode
            conn.disconnect()
            code in 200..299
        } catch (e: Exception) {
            false
        }
    }
}
