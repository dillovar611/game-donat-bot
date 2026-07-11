package tj.dilovar.dcnotifier

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.IBinder

/**
 * Хизмати доимии foreground — Android барномаро дигар "хоб" карда
 * наметавонад. Ҳар 40 сония:
 *   - navбати нафиристодаро аз нав мефиристад,
 *   - хизмати шунавандаро аз нав пайваст мекунад (агар қатъ шуда бошад),
 *   - панели notification-ро аз нав месканад (агар notification-е гум
 *     шуда бошад, мегирад).
 */
class KeepAliveService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            try {
                Sender.flushAsync(applicationContext)
                DcListenerService.kick(applicationContext)
            } catch (e: Exception) {}
            handler.postDelayed(this, 40_000)
        }
    }

    override fun onCreate() {
        super.onCreate()
        val chId = "dcnotifier_alive"
        if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(
                chId, "DC Notifier", NotificationManager.IMPORTANCE_MIN
            )
            ch.setShowBadge(false)
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(ch)
        }
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, chId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val notif = builder
            .setContentTitle("DC Notifier фаъол ✅")
            .setContentText("Пардохтҳо назорат мешаванд")
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setOngoing(true)
            .build()
        startForeground(1, notif)
        handler.post(tick)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
