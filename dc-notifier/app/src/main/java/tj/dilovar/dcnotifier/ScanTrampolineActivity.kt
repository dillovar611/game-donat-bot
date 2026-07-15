package tj.dilovar.dcnotifier

import android.app.Activity
import android.os.Bundle

/**
 * Activity-и КОМИЛАН ноаён — танҳо аз AlarmManager.setAlarmClock() фаъол
 * мешавад. Азбаски ин ҳамчун "садои бедоркунӣ" фаъол мешавад, Android
 * ба он иҷозат медиҳад, ки берун аз навбати муқаррарӣ барномаи дигареро
 * (DC City) кушояд — чизе ки аз Service мустақим имконнопазир аст.
 *
 * Кор: DC-ро мекушояд → навбати навбатии санҷишро ҷадвал мекунад →
 * худашро фавран мебандад (дар таърих намемонад).
 */
class ScanTrampolineActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            DcAccessibilityService.triggerOpenApp(applicationContext)
        } catch (e: Exception) {
        } finally {
            ScanScheduler.scheduleNextAlarm(applicationContext)
            finish()
        }
    }
}
