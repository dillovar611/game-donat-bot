package tj.dilovar.dcnotifier

import android.content.Context
import java.util.Calendar
import kotlin.random.Random

/**
 * Вақти навбатии кушодани барномаи DC-ро ҳисоб мекунад — комилан
 * тасодуфӣ (то сония), бе такрори намуна:
 *   - рӯзона (08:00-23:00): ҳар 20-40 дақ
 *   - шабона (23:00-08:00): ҳар 2-3 соат
 * KeepAliveService дар ҳар tick (40 сония) чек мекунад, ки вақт расидааст ё не.
 */
object ScanScheduler {

    fun isDue(ctx: Context): Boolean {
        val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val nextAt = prefs.getLong("next_scan_at", 0)
        return System.currentTimeMillis() >= nextAt
    }

    fun scheduleNext(ctx: Context) {
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        val isNight = hour >= 23 || hour < 8

        val delayMs = if (isNight) {
            // 2-3 соат, то сония тасодуфӣ
            randomBetween(2 * 60 * 60, 3 * 60 * 60) * 1000L
        } else {
            // 20-40 дақ, то сония тасодуфӣ
            randomBetween(20 * 60, 40 * 60) * 1000L
        }

        val nextAt = System.currentTimeMillis() + delayMs
        ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
            .edit().putLong("next_scan_at", nextAt).apply()
    }

    private fun randomBetween(minSeconds: Int, maxSeconds: Int): Int =
        Random.nextInt(minSeconds, maxSeconds + 1)
}
