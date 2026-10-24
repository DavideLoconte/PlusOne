package io.github.gabrielesantangelo.plusone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.graphics.Typeface
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.color.DynamicColors
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import com.google.android.material.R as M

/** Asking the user whether to pair with a PC, showing the code both screens show. */
object Pairing {
    private class Request(val pc: String) {
        val answered = CountDownLatch(1)
        @Volatile var accepted = false
    }

    private val requests = ConcurrentHashMap<String, Request>()

    /** Blocks the calling (connection) thread until the user answers, two
     * minutes at most. Asked again with the same code (the same PC, several
     * PDFs at once), it waits for the same answer. */
    fun ask(context: Context, pc: String, code: String): Boolean {
        var asking = false
        val request = requests.computeIfAbsent(code) { asking = true; Request(pc) }
        if (asking) {
            val open = Intent(context, PairActivity::class.java)
                .putExtra(PairActivity.EXTRA_CODE, code)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (Settings.canDrawOverlays(context)) context.startActivity(open)
            Notifications.pairing(context, pc, code, open)
        }
        val answered = request.answered.await(2, TimeUnit.MINUTES)
        if (asking) {
            requests.remove(code)
            Notifications.cancelPairing(context, code)
        }
        return answered && request.accepted
    }

    /** The PC's name, while its pairing waits for an answer. */
    fun pending(code: String): String? = requests[code]?.pc

    fun answer(code: String, accepted: Boolean) {
        requests[code]?.let {
            it.accepted = accepted
            it.answered.countDown()
        }
    }
}

/** The pairing question, over whatever is on screen. Its own layout: an
 * AlertDialog inside a dialog-themed activity was dismissed as it opened. */
class PairActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        DynamicColors.applyToActivityIfAvailable(this)  // the wallpaper's colours, as the app's other screens
        super.onCreate(savedInstanceState)
        setFinishOnTouchOutside(false)
        val code = intent.getStringExtra(EXTRA_CODE) ?: return finish()
        val pc = Pairing.pending(code) ?: return finish()
        fun answer(accept: Boolean) {
            Pairing.answer(code, accept)
            finish()
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(24), dp(24), dp(20))
            background = surface(M.attr.colorSurfaceContainerHigh)
            addView(icon(R.drawable.ic_computer, M.attr.colorSecondary))
            addView(label(getString(R.string.pair_title, pc), M.attr.textAppearanceHeadlineSmall).apply {
                gravity = Gravity.CENTER
                setPadding(0, dp(16), 0, dp(16))
            })
            addView(label(getString(R.string.pair_compare), M.attr.textAppearanceBodyMedium,
                M.attr.colorOnSurfaceVariant))
            // The code, big, in groups of three as the PC shows it.
            addView(label(code.chunked(3).joinToString(" "), M.attr.textAppearanceDisplayMedium,
                M.attr.colorOnSecondaryContainer).apply {
                typeface = Typeface.MONOSPACE
                gravity = Gravity.CENTER
                background = surface(M.attr.colorSecondaryContainer, dp(16f))
                setPadding(dp(16), dp(12), dp(16), dp(12))
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                setMargins(0, dp(16), 0, dp(24))
            })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.END
                addView(button(getString(R.string.cancel), Look.TEXT) { answer(false) })
                addView(button(getString(R.string.pair), Look.FILLED) { answer(true) },
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) })
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        })
        window.setLayout(minOf(resources.displayMetrics.widthPixels - dp(48), dp(400)), ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    companion object {
        const val EXTRA_CODE = "code"
    }
}

/** The pairing notification's buttons. */
class PairingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val code = intent.getStringExtra(PairActivity.EXTRA_CODE) ?: return
        Pairing.answer(code, intent.action == ACTION_ACCEPT)
    }

    companion object {
        const val ACTION_ACCEPT = "io.github.gabrielesantangelo.plusone.PAIR"
        const val ACTION_REJECT = "io.github.gabrielesantangelo.plusone.DONT_PAIR"
    }
}

/** Ready again after a restart. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && Options.alwaysReady(context)) {
            CompanionService.start(context)
        }
    }
}
