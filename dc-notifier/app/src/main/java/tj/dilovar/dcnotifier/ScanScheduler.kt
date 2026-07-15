package tj.dilovar.dcnotifier

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import java.util.Calendar
import kotlin.random.Random

/**
 * Ҷадвалбандии санҷиши даврӣ тавассути AlarmManager.setAlarmClock() —
 * ин ягона роҳест, ки Android ба он иҷозат медиҳад аз паси замина
 * (аз Service/BroadcastReceiver) барномаи дигареро худкор кушояд
 * (мисли барномаҳои "садои бедоркунӣ"). Оддии startActivity() аз
 * Service, бе ин, аз Android 10 боз бесадо манъ карда мешавад.
 *
 *   - рӯзона (08:00-23:00): ҳар 20-40 дақ, то сония тасодуфӣ
 *   - шабона (23:00-08:00): ҳар 2-3 соат, то сония тасодуфӣ
 */
object ScanScheduler {

    fun scheduleNextAlarm(ctx: Context) {
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        val isNight = hour >= 23 || hour < 8

        val delayMs = if (isNight) {
            randomBetween(2 * 60 * 60, 3 * 60 * 60) * 1000L
        } else {
            randomBetween(20 * 60, 40 * 60) * 1000L
        }
        val triggerAt = System.currentTimeMillis() + delayMs

        val intent = Intent(ctx, ScanTrampolineActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        }
        val flags = if (Build.VERSION.SDK_INT >= 23) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }
        val pi = PendingIntent.getActivity(ctx, 1001, intent, flags)

        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        try {
            am.setAlarmClock(AlarmManager.AlarmClockInfo(triggerAt, pi), pi)
        } catch (e: Exception) {
            // баъзе OEM-ҳо маҳдудият доранд — ҳамчун эҳтиёт setAndAllowWhileIdle,
            // вале ин хабар медиҳем, чунки ин намуди alarm аз маҳдудияти
            // кушодани барнома аз паси замина истисно НАДОРАД
            try {
                Sender.enqueue(ctx, "⚠️ setAlarmClock хато дод (${e.message}), setAndAllowWhileIdle истифода шуд — эҳтимол DC накушояд")
                Sender.flushAsync(ctx)
            } catch (e2: Exception) {}
            try {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pi)
            } catch (e2: Exception) {}
        }

        ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
            .edit().putLong("next_scan_at", triggerAt).apply()
    }

    private fun randomBetween(minSeconds: Int, maxSeconds: Int): Int =
        Random.nextInt(minSeconds, maxSeconds + 1)
}
