package tj.dilovar.dcnotifier

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject

/**
 * Notification-ҳоро мехонад ва онҳоеро, ки ба пули воридотӣ монанданд,
 * ба канали Telegram мефиристад.
 *
 * Ду қабати муҳофизат зидди "гум шудан":
 *  1. onNotificationPosted — вақти воқеии омадани notification.
 *  2. rescanActive() — ҳар 40 сония (аз KeepAliveService) ҲАМАИ
 *     notification-ҳои дар панел мавҷударо аз нав тафтиш мекунад, то
 *     агар хизмат лаҳзае қатъ шуда бошад ҳам, ҳеҷ пардохт гум нашавад.
 *  3. Дедупликатсия бо hash — ҳамон паём ду бор фиристода намешавад.
 */
class DcListenerService : NotificationListenerService() {

    companion object {
        @Volatile
        private var instance: DcListenerService? = null

        private val keywords = listOf(
            "зачисление", "zachislenie", "поступление", "postuplenie",
            "перевод", "perevod", "summa", "сумма", "tjs"
        )

        /** Барномаҳое, ки ҳеҷ гоҳ пардохти бонкӣ намефиристанд — сарфи назар
         *  мешаванд. Бе ин, notification-и худи Telegram дар бораи паёми
         *  нав дар канал (ки матнаш DCNOTIF-ро дар бар мегирад) боз хонда
         *  мешавад ва давр (loop)-и бепоён месозад. */
        private val ignoredPackages = setOf(
            "org.telegram.messenger",
            "org.telegram.messenger.web",
            "org.telegram.plus",
            "nekox.messenger",
            "org.thunderdog.challegram",
        )

        /** Аз KeepAliveService даъват мешавад. Агар хизмат зинда бошад —
         *  панелро аз нав месканад; вагарна аз система rebind мехоҳад. */
        fun kick(ctx: Context) {
            val inst = instance
            if (inst != null) {
                try { inst.rescanActive() } catch (e: Exception) {}
            } else if (Build.VERSION.SDK_INT >= 24) {
                try {
                    requestRebind(ComponentName(ctx, DcListenerService::class.java))
                } catch (e: Exception) {}
            }
        }

        private fun extractText(sbn: StatusBarNotification): String {
            val extras = sbn.notification.extras
            val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
            val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
            val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
            val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
                ?.joinToString("\n") { it.toString() } ?: ""
            val body = listOf(big, lines, text).firstOrNull { it.isNotBlank() } ?: ""
            return "$title\n$body".trim()
        }
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
        rescanActive()
        Sender.flushAsync(this)
    }

    override fun onListenerDisconnected() {
        instance = null
        // фавран кӯшиши баргардонидан
        if (Build.VERSION.SDK_INT >= 24) {
            try { requestRebind(ComponentName(this, DcListenerService::class.java)) } catch (e: Exception) {}
        }
        super.onListenerDisconnected()
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        try {
            val svc = Intent(this, KeepAliveService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc) else startService(svc)
        } catch (e: Exception) {}
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        process(sbn)
    }

    /** Ҳамаи notification-ҳои ҳозира дар панелро аз нав тафтиш мекунад. */
    fun rescanActive() {
        try {
            val active = activeNotifications ?: return
            for (sbn in active) process(sbn)
        } catch (e: Exception) {}
        Sender.flushAsync(this)
    }

    private fun process(sbn: StatusBarNotification) {
        try {
            if (sbn.packageName == packageName) return
            if (sbn.packageName in ignoredPackages) return
            val full = extractText(sbn)
            if (full.isBlank()) return
            // Ҳифзи иловагӣ: агар матн аллакай тегҳи худи ин барномаро дошта
            // бошад (яъне ин пешнамоиши паёме, ки худамон фиристодем) — гузарем.
            if (full.contains("DCNOTIF")) return
            val lower = full.lowercase()
            if (keywords.none { lower.contains(it) }) return

            // Дедупликатсия: ҳамон матн дар 10 дақиқаи охир дубора не.
            // (Такрори воқеии пардохт дар сервер бо Kod филтр мешавад.)
            val hash = full.hashCode().toString()
            if (isDuplicate(hash)) return

            Sender.enqueue(this, "DCNOTIF [${sbn.packageName}]\n$full")
            Sender.flushAsync(this)
        } catch (e: Exception) {}
    }

    /** hash-ро дар рӯйхати кӯтоҳ (бо вақт) нигоҳ медорад, то дар сканҳои
     *  такрорӣ ҳамон паём боз фиристода нашавад. */
    @Synchronized
    private fun isDuplicate(hash: String): Boolean {
        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        val obj = try { JSONObject(prefs.getString("seen_hashes", "{}") ?: "{}") } catch (e: Exception) { JSONObject() }

        // тозакунии кӯҳнаҳо (>10 дақиқа) ва санҷиш
        val fresh = JSONObject()
        val keys = obj.keys()
        var seen = false
        while (keys.hasNext()) {
            val k = keys.next()
            val t = obj.optLong(k, 0)
            if (now - t < 10 * 60 * 1000) {
                fresh.put(k, t)
                if (k == hash) seen = true
            }
        }
        if (seen) {
            prefs.edit().putString("seen_hashes", fresh.toString()).apply()
            return true
        }
        fresh.put(hash, now)
        prefs.edit().putString("seen_hashes", fresh.toString()).apply()
        return false
    }
}
