package tj.dilovar.dcnotifier

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * PIN-и ҳисоби DC танҳо дар ҳамин ҷо, бо рамзгузории Android Keystore
 * нигоҳ дошта мешавад — ҳеҷ гоҳ ба сервер фиристода намешавад.
 */
object SecurePrefs {
    private var cached: SharedPreferences? = null

    private fun prefs(ctx: Context): SharedPreferences {
        cached?.let { return it }
        val masterKey = MasterKey.Builder(ctx)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        val p = EncryptedSharedPreferences.create(
            ctx,
            "secure_cfg",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
        cached = p
        return p
    }

    fun getPin(ctx: Context): String = prefs(ctx).getString("dc_pin", "") ?: ""

    fun setPin(ctx: Context, pin: String) {
        prefs(ctx).edit().putString("dc_pin", pin).apply()
    }
}
