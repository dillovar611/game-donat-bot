package tj.dilovar.dcnotifier

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper

/**
 * Хизмати доимии foreground — Android барномаро дигар "хоб" карда
 * наметавонад. Дар панели notification як сатри хурди доимӣ меистад
 * (ин талаботи Android аст). Ҳар 45 сония навбати нафиристодаро
 * аз нав мефиристад.
 */
class KeepAliveService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    private val flusher = object : Runnable {
        override fun run() {
            Sender.flushAsync(applicationContext)
            handler.postDelayed(this, 45_000)
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
        handler.post(flusher)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY  // агар система кушад — худкор аз нав мехезад
    }

    override fun onDestroy() {
        handler.removeCallbacks(flusher)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
