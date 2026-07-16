package tj.dilovar.dcnotifier

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/** Баъд аз бозоғозии телефон хизмати доимиро худкор сар мекунад. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            try {
                val svc = Intent(context, KeepAliveService::class.java)
                if (Build.VERSION.SDK_INT >= 26) {
                    context.startForegroundService(svc)
                } else {
                    context.startService(svc)
                }
            } catch (e: Exception) {
                // баъзе версияҳои Android аз boot иҷозат намедиҳанд —
                // дар ин ҳолат хизмат ҳангоми кушодани барнома сар мешавад
            }
            try {
                ScanScheduler.scheduleNextAlarm(context)
            } catch (e: Exception) {}
            try {
                KeepAliveAlarmReceiver.schedule(context)
            } catch (e: Exception) {}
        }
    }
}
