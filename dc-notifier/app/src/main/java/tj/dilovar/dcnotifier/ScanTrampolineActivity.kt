package tj.dilovar.dcnotifier

import android.app.Activity
import android.os.Bundle
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Activity-и КОМИЛАН ноаён — танҳо аз AlarmManager.setAlarmClock() фаъол
 * мешавад. Азбаски ин ҳамчун "садои бедоркунӣ" фаъол мешавад, Android
 * ба он иҷозат медиҳад, ки берун аз навбати муқаррарӣ барномаи дигареро
 * (DC City) кушояд — чизе ки аз Service мустақим имконнопазир аст.
 *
 * Кор: DC-ро мекушояд → навбати навбатии санҷишро ҷадвал мекунад →
 * худашро фавран мебандад (дар таърих намемонад).
 *
 * Хабари "🔔 Alarm фаъол шуд" фавран мефиристад — то маълум шавад, ки
 * ХУДИ alarm кор мекунад ё не (новобаста аз он ки DC мекушояд ё не).
 */
class ScanTrampolineActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val time = SimpleDateFormat("dd.MM HH:mm:ss", Locale.getDefault()).format(java.util.Date())
        try {
            Sender.enqueue(applicationContext, "🔔 [$time] Alarm фаъол шуд — DC City кушода истодааст...")
            Sender.flushAsync(applicationContext)
        } catch (e: Exception) {}
        try {
            DcAccessibilityService.triggerOpenApp(applicationContext)
        } catch (e: Exception) {
            try {
                Sender.enqueue(applicationContext, "❌ [$time] Хатои кушодани DC: ${e.message}")
                Sender.flushAsync(applicationContext)
            } catch (e2: Exception) {}
        } finally {
            ScanScheduler.scheduleNextAlarm(applicationContext)
            finish()
        }
    }
}
