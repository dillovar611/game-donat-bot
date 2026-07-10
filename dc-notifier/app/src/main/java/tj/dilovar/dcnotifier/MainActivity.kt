package tj.dilovar.dcnotifier

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {

    private lateinit var etToken: EditText
    private lateinit var etChatId: EditText
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        etToken = findViewById(R.id.etToken)
        etChatId = findViewById(R.id.etChatId)
        tvStatus = findViewById(R.id.tvStatus)

        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        etToken.setText(prefs.getString("token", ""))
        etChatId.setText(prefs.getString("chat_id", ""))

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            prefs.edit()
                .putString("token", etToken.text.toString().trim())
                .putString("chat_id", etChatId.text.toString().trim())
                .apply()
            Toast.makeText(this, "Сабт шуд ✅", Toast.LENGTH_SHORT).show()
            updateStatus()
        }

        findViewById<Button>(R.id.btnTest).setOnClickListener {
            val token = prefs.getString("token", "") ?: ""
            val chatId = prefs.getString("chat_id", "") ?: ""
            if (token.isEmpty() || chatId.isEmpty()) {
                Toast.makeText(this, "Аввал токен ва chat_id-ро сабт кунед!", Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            thread {
                val ok = TelegramSender.send(token, chatId, "TEST az DC Notifier — agar in payom rasid, hama durust ast! ✅")
                runOnUiThread {
                    Toast.makeText(
                        this,
                        if (ok) "Фиристода шуд ✅ Гурӯҳро тафтиш кунед!" else "ХАТО ❌ Токен/chat_id ё интернетро тафтиш кунед",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }

        findViewById<Button>(R.id.btnNotifAccess).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }

        findViewById<Button>(R.id.btnBattery).setOnClickListener {
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:$packageName")
            }
            try {
                startActivity(intent)
            } catch (e: Exception) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }

        updateStatus()
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }

    private fun updateStatus() {
        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        val hasCfg = !(prefs.getString("token", "") ?: "").isEmpty() &&
                !(prefs.getString("chat_id", "") ?: "").isEmpty()

        val listeners = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: ""
        val hasAccess = listeners.contains(packageName)

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        val batteryOk = pm.isIgnoringBatteryOptimizations(packageName)

        tvStatus.text = buildString {
            append("Ҳолат:\n")
            append(if (hasCfg) "✅ Токен/chat_id сабт шудааст\n" else "❌ Токен/chat_id холӣ\n")
            append(if (hasAccess) "✅ Иҷозати notification дода шудааст\n" else "❌ Иҷозати notification ЛОЗИМ аст!\n")
            append(if (batteryOk) "✅ Сарфаи батарея хомӯш аст\n" else "⚠️ Сарфаи батарея фаъол (тавсия: хомӯш кунед)\n")
            append("\nПаёмҳои фиристодашуда: ${prefs.getInt("sent_count", 0)}")
        }
    }
}
