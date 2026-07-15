package tj.dilovar.dcnotifier

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/**
 * Аз AlarmManager фаъол мешавад (на мустақим Activity). Фиристодани
 * broadcast ҳељ гоҳ аз маҳдудияти "background activity start" манъ
 * намешавад — бинобар ин бо итминони пурра як full-screen-intent
 * notification месозем, ки Android онро ҳамчун занги воридотӣ мешиносад
 * ва ба он иҷозат медиҳад ScanTrampolineActivity-ро ҳатто аз рӯи экрани
 * қулф кушояд — ин боэътимодтарин роҳест, ки Android дорад.
 */
class ScanAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        try {
            Sender.enqueue(context, "🔔 Alarm фаъол шуд (broadcast) — full-screen intent фиристода истодааст...")
            Sender.flushAsync(context)
        } catch (e: Exception) {}

        try {
            val chId = "dcnotifier_scan_alert"
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (Build.VERSION.SDK_INT >= 26) {
                val ch = NotificationChannel(chId, "DC Notifier — Санҷиш", NotificationManager.IMPORTANCE_HIGH)
                ch.setShowBadge(false)
                nm.createNotificationChannel(ch)
            }

            val activityIntent = Intent(context, ScanTrampolineActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            }
            val flags = if (Build.VERSION.SDK_INT >= 23) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val fullScreenPi = PendingIntent.getActivity(context, 1002, activityIntent, flags)

            val notif = NotificationCompat.Builder(context, chId)
                .setContentTitle("DC Notifier")
                .setContentText("Санҷиши даврӣ...")
                .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setFullScreenIntent(fullScreenPi, true)
                .setContentIntent(fullScreenPi)
                .setAutoCancel(true)
                .setOngoing(false)
                .build()

            nm.notify(9001, notif)
        } catch (e: Exception) {
            try {
                Sender.enqueue(context, "❌ Хатои сохтани full-screen notification: ${e.message}")
                Sender.flushAsync(context)
            } catch (e2: Exception) {}
        }
    }
}
