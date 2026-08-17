package tj.dilovar.dcnotifier

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * Ҳар ~2 дақиқа "бедор" мекунад — DcListenerService-ро kick мекунад (агар
 * қатъ шуда бошад, аз система rebind мехоҳад; агар зинда бошад, панели
 * notification-ро аз нав месканад, то push-ҳое, ки гум шуда буданд ёфта
 * шаванд).
 *
 * САБАБ: KeepAliveService (foreground) дохилаш танҳо як Handler.postDelayed
 * ҳар 40 сония дошт — вале баъзе телефонҳо (масалан Samsung бо "Sleeping
 * apps"/"Deep sleeping apps") метавонанд ҳатто хизмати foreground-ро
 * "мунҷамид" (freeze) кунанд, ки дар натиҷа Handler дигар "тик" намезанад
 * ва DCNOTIF-ҳо даҳҳо дақиқа дер мемонанд (то даме ки ягон чизи дигар,
 * масалан AlarmManager-и санҷиши DC, тасодуфан протсесро бедор кунад).
 * AlarmManager.setExactAndAllowWhileIdle ин мушкилро ҳал мекунад, зеро
 * системаро дар ҳар ҳолат бедор мекунад — новобаста аз он ки хизмат
 * "мунҷамид" шуда бошад ё не.
 */
class KeepAliveAlarmReceiver : BroadcastReceiver() {
    companion object {
        private const val INTERVAL_MS = 2 * 60 * 1000L
        private const val REQUEST_CODE = 9911

        fun schedule(ctx: Context) {
            try {
                val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
                val intent = Intent(ctx, KeepAliveAlarmReceiver::class.java)
                val pi = PendingIntent.getBroadcast(
                    ctx, REQUEST_CODE, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
                val triggerAt = System.currentTimeMillis() + INTERVAL_MS
                if (Build.VERSION.SDK_INT >= 23) {
                    am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pi)
                } else {
                    @Suppress("DEPRECATION")
                    am.setExact(AlarmManager.RTC_WAKEUP, triggerAt, pi)
                }
            } catch (e: Exception) {}
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        try {
            Sender.flushAsync(context)
            DcListenerService.kick(context)
        } catch (e: Exception) {}
        // Навбатии оянда — то занҷир бе канда идома ёбад
        schedule(context)
    }
}
