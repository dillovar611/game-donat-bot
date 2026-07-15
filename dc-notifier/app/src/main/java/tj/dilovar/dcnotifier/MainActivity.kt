package tj.dilovar.dcnotifier

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {

    private lateinit var etToken: EditText
    private lateinit var etChatId: EditText
    private lateinit var etPin: EditText
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        etToken = findViewById(R.id.etToken)
        etChatId = findViewById(R.id.etChatId)
        etPin = findViewById(R.id.etPin)
        tvStatus = findViewById(R.id.tvStatus)

        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        etToken.setText(prefs.getString("token", ""))
        etChatId.setText(prefs.getString("chat_id", ""))
        etPin.setText(SecurePrefs.getPin(this))

        // Иҷозати нишон додани notification (Android 13+, барои хизмати доимӣ)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1
            )
        }

        startKeepAlive()

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            prefs.edit()
                .putString("token", etToken.text.toString().trim())
                .putString("chat_id", etChatId.text.toString().trim())
                .apply()
            Toast.makeText(this, "Сабт шуд ✅", Toast.LENGTH_SHORT).show()
            startKeepAlive()
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
                        if (ok) "Фиристода шуд ✅ Каналро тафтиш кунед!" else "ХАТО ❌ Токен/chat_id ё интернетро тафтиш кунед",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }

        findViewById<Button>(R.id.btnNotifAccess).setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }

        findViewById<Button>(R.id.btnSavePin).setOnClickListener {
            val pin = etPin.text.toString().trim()
            if (pin.length !in 4..6 || !pin.all { it.isDigit() }) {
                Toast.makeText(this, "PIN бояд 4-6 рақам бошад!", Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            SecurePrefs.setPin(this, pin)
            Toast.makeText(this, "PIN бо рамзгузорӣ сабт шуд ✅", Toast.LENGTH_SHORT).show()
        }

        findViewById<Button>(R.id.btnAccessibility).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        findViewById<Button>(R.id.btnExactAlarm).setOnClickListener {
            if (Build.VERSION.SDK_INT >= 31) {
                try {
                    startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM).apply {
                        data = Uri.parse("package:$packageName")
                    })
                } catch (e: Exception) {
                    Toast.makeText(this, "Ин версияи Android чунин танзимот надорад", Toast.LENGTH_LONG).show()
                }
            } else {
                Toast.makeText(this, "Дар ин версияи Android лозим нест", Toast.LENGTH_SHORT).show()
            }
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

    private fun startKeepAlive() {
        try {
            val svc = Intent(this, KeepAliveService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc)
            else startService(svc)
        } catch (e: Exception) {
        }
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

        val accessibilityEnabled = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: ""
        val hasAccessibility = accessibilityEnabled.contains(packageName)
        val hasPin = SecurePrefs.getPin(this).isNotBlank()

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        val batteryOk = pm.isIgnoringBatteryOptimizations(packageName)

        val am = getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
        val canExactAlarm = if (Build.VERSION.SDK_INT >= 31) am.canScheduleExactAlarms() else true

        val lastSent = prefs.getLong("last_sent_at", 0)
        val lastStr = if (lastSent > 0)
            SimpleDateFormat("dd.MM HH:mm:ss", Locale.getDefault()).format(Date(lastSent))
        else "—"

        val queueLen = try {
            org.json.JSONArray(prefs.getString("queue", "[]")).length()
        } catch (e: Exception) { 0 }

        tvStatus.text = buildString {
            append("Ҳолат:\n")
            append(if (hasCfg) "✅ Токен/chat_id сабт шудааст\n" else "❌ Токен/chat_id холӣ\n")
            append(if (hasAccess) "✅ Иҷозати notification дода шудааст\n" else "❌ Иҷозати notification ЛОЗИМ аст!\n")
            append(if (batteryOk) "✅ Сарфаи батарея хомӯш аст\n" else "⚠️ Сарфаи батарея фаъол (тавсия: хомӯш кунед)\n")
            append(if (hasAccessibility) "✅ Иҷозати Accessibility дода шудааст\n" else "❌ Иҷозати Accessibility (барои санҷиши даврӣ) ЛОЗИМ аст!\n")
            append(if (hasPin) "✅ PIN сабт шудааст\n" else "⚠️ PIN сабт нашудааст (санҷиши даврӣ бе PIN кор намекунад, агар сессия хомӯш шавад)\n")
            append(if (canExactAlarm) "✅ Иҷозати Alarm дақиқ дода шудааст\n" else "❌ Иҷозати Alarm дақиқ ЛОЗИМ аст (бе ин санҷиши даврӣ кор намекунад)!\n")
            append("\n📤 Фиристода шуд: ${prefs.getInt("sent_count", 0)}\n")
            append("🕒 Охирин: $lastStr\n")
            append("📦 Дар навбат: $queueLen")
        }
    }
}
