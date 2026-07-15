package tj.dilovar.dcnotifier

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Мехонад экрани барномаи DC City (tj.dc.next1) дар ҳамин телефон, вақте
 * ScanScheduler онро худкор мекушояд:
 *   1. Агар экрани PIN бошад — рамзи нигоҳдоштаро (SecurePrefs) худкор
 *      "пахш" мекунад (мисли ангушти соҳиби телефон).
 *   2. Агар саҳифаи асосӣ бошад — ба таби "Таърих" мегузарад.
 *   3. Дар саҳифаи амалиётҳо — хатҳои амалиётро мехонад, ҳар кадоме нав
 *      бошад (пештар надида)-ро ба канал мефиристад, бо тамғаи "вобаста
 *      ба фармоиш" / "вобастагӣ надорад". Ҳар санҷиш як хабар мефиристад
 *      (агар чизи нав набошад ҳам — то маълум шавад, ки санҷиш зинда аст).
 *   4. Баъд бармегардад ба хонаи телефон (DC-ро дар пасизамина мемонад).
 *
 * ДИҚҚАТ: тамғагузорӣ дар ин ҷо танҳо аз рӯи ШАКЛИ матн аст (эҳтимолӣ),
 * на аз рӯи мутобиқати воқеӣ бо базаи фармоишҳо дар сервер — сервер
 * метавонад минбаъд аз ин рӯйхат санҷиши дақиқтар кунад.
 */
class DcAccessibilityService : AccessibilityService() {

    companion object {
        private const val DC_PACKAGE = "tj.dc.next1"

        private val CARD_REF_RE = Regex("card_(\\d+)", RegexOption.IGNORE_CASE)
        private val AMOUNT_RE = Regex("\\b\\d{1,3}(?:[.,]\\d{2})\\b")
        private val TIME_RE = Regex("\\b\\d{2}:\\d{2}:\\d{2}\\b")

        @Volatile
        var instance: DcAccessibilityService? = null

        /** Аз ScanScheduler даъват мешавад, вақте вақти сканкунӣ расид. */
        fun triggerOpenApp(ctx: Context) {
            try {
                val launch = ctx.packageManager.getLaunchIntentForPackage(DC_PACKAGE) ?: return
                launch.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or
                        android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP)
                ctx.startActivity(launch)
            } catch (e: Exception) {}
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var lastActionAt = 0L
    private var lastUnmatchedDiagAt = 0L
    private var wentToHistoryTab = false

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val pkg = event?.packageName?.toString() ?: return
        if (pkg != DC_PACKAGE) return
        // на бештар аз як маротиба дар 1.5 сония коркард кунем (event-ҳо зуд-зуд меоянд)
        val now = System.currentTimeMillis()
        if (now - lastActionAt < 1500) return
        lastActionAt = now

        val root = rootInActiveWindow ?: return
        try {
            handleScreen(root)
        } catch (e: Exception) {
        } finally {
            root.recycle()
        }
    }

    override fun onInterrupt() {}

    private fun handleScreen(root: AccessibilityNodeInfo) {
        val allText = mutableListOf<String>()
        collectText(root, allText)
        val joined = allText.joinToString("\n")

        when {
            joined.contains("Рамзи дастрасиро ворид кунед") -> enterPin(root)
            !wentToHistoryTab && (joined.contains("Пардохти хизматҳо") || joined.contains("Барномаҳо")) -> {
                // Саҳифаи асосӣ — ба таби "Таърих" меравем
                if (clickNodeWithText(root, "Таърих")) {
                    wentToHistoryTab = true
                }
            }
            joined.contains("Амалиётҳо") || joined.contains("Выписка") -> {
                processTransactions(allText)
            }
            else -> {
                // Экрани ношинос — то 60 сония як бор хабар медиҳем (на ҳар event),
                // то бидонем дар кадом саҳифа монда истодаем, бе спам
                val now = System.currentTimeMillis()
                if (now - lastUnmatchedDiagAt > 60_000) {
                    lastUnmatchedDiagAt = now
                    val preview = joined.take(300)
                    Sender.enqueue(applicationContext, "❔ Экрани ношинос дар DC:\n$preview")
                    Sender.flushAsync(applicationContext)
                }
            }
        }
    }

    private fun collectText(node: AccessibilityNodeInfo?, out: MutableList<String>) {
        if (node == null) return
        val t = node.text?.toString()
        if (!t.isNullOrBlank()) out.add(t.trim())
        val d = node.contentDescription?.toString()
        if (!d.isNullOrBlank()) out.add(d.trim())
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                collectText(child, out)
            } finally {
                child.recycle()
            }
        }
    }

    private fun findNodesWithText(root: AccessibilityNodeInfo, text: String): List<AccessibilityNodeInfo> {
        val result = mutableListOf<AccessibilityNodeInfo>()
        fun walk(node: AccessibilityNodeInfo?) {
            if (node == null) return
            val t = node.text?.toString()
            if (t != null && t.trim() == text) result.add(node)
            for (i in 0 until node.childCount) {
                walk(node.getChild(i))
            }
        }
        walk(root)
        return result
    }

    private fun clickNodeWithText(root: AccessibilityNodeInfo, text: String): Boolean {
        val nodes = findNodesWithText(root, text)
        for (n in nodes) {
            var target: AccessibilityNodeInfo? = n
            // агар худи нод "clickable" набошад, аз волидайн меҷӯем
            var depth = 0
            while (target != null && !target.isClickable && depth < 5) {
                target = target.parent
                depth++
            }
            if (target != null && target.isClickable) {
                val ok = target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                if (ok) return true
            }
        }
        return false
    }

    /** PIN-и нигоҳдоштаро рақам ба рақам "пахш" мекунад. */
    private fun enterPin(root: AccessibilityNodeInfo) {
        val pin = SecurePrefs.getPin(applicationContext)
        if (pin.isBlank() || pin.length !in 4..6) return
        handler.post {
            for ((idx, ch) in pin.withIndex()) {
                handler.postDelayed({
                    val r = rootInActiveWindow ?: return@postDelayed
                    try {
                        clickNodeWithText(r, ch.toString())
                    } finally {
                        r.recycle()
                    }
                }, idx * 350L)
            }
        }
    }

    /** Хатҳои амалиётро ҷудо мекунад, наваҳояшро мешуморад ва ҲАР САНҶИШ як хабар мефиристад. */
    private fun processTransactions(rawLines: List<String>) {
        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val seen = try {
            JSONObject(prefs.getString("scan_seen", "{}") ?: "{}")
        } catch (e: Exception) {
            JSONObject()
        }
        val foundArr = org.json.JSONArray()

        // Хатҳоеро, ки ба амалиёт монанданд (маблағ ва вақт доранд) ҷудо мекунем
        var i = 0
        while (i < rawLines.size) {
            val line = rawLines[i]
            if (AMOUNT_RE.containsMatchIn(line) && (TIME_RE.containsMatchIn(line) ||
                        (i + 2 < rawLines.size && TIME_RE.containsMatchIn(rawLines[i + 2])))) {
                val context5 = rawLines.subList(i, minOf(i + 5, rawLines.size)).joinToString(" | ")
                val hash = context5.hashCode().toString()
                if (!seen.has(hash)) {
                    seen.put(hash, System.currentTimeMillis())
                    val tag = classify(context5)
                    foundArr.put("$tag :: $context5")
                }
            }
            i++
        }

        // тозакунии hash-ҳои кӯҳна (>7 рӯз)
        val freshSeen = JSONObject()
        val nowTs = System.currentTimeMillis()
        val keys = seen.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            val t = seen.optLong(k, 0)
            if (nowTs - t < 7L * 24 * 60 * 60 * 1000) freshSeen.put(k, t)
        }
        prefs.edit().putString("scan_seen", freshSeen.toString()).apply()

        // Ҳар санҷиш як хабар мефиристад — то маълум шавад, ки санҷиш зинда аст
        sendBatch(foundArr)

        // Кор тамом — ба хонаи телефон бармегардем, DC-ро дар пасизамина мемонем
        handler.postDelayed({
            try {
                performGlobalAction(GLOBAL_ACTION_HOME)
            } catch (e: Exception) {}
            wentToHistoryTab = false
        }, 800)
    }

    private fun classify(line: String): String {
        val cardMatch = CARD_REF_RE.find(line)
        return when {
            cardMatch != null -> "✅ Вобаста ба фармоиш #${cardMatch.groupValues[1]}"
            line.contains("DC WALLET", ignoreCase = true) -> "❓ Аз Алиф (бе рамз — санҷиши маблағ лозим)"
            else -> "⚠️ Вобастагӣ ба бот надорад"
        }
    }

    private fun sendBatch(arr: org.json.JSONArray) {
        val time = SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(java.util.Date())
        val sb = StringBuilder()
        if (arr.length() == 0) {
            sb.append("DCSCAN [$time] — санҷиш иҷро шуд, амалиёти нав ёфт нашуд.")
        } else {
            sb.append("DCSCAN [$time] — ${arr.length()} амалиёти нав ёфт шуд:\n\n")
            for (i in 0 until arr.length()) {
                sb.append("${i + 1}. ${arr.getString(i)}\n")
            }
        }
        Sender.enqueue(applicationContext, sb.toString())
        Sender.flushAsync(applicationContext)
    }
}
