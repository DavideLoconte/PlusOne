package io.github.gabrielesantangelo.plusone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.graphics.Typeface
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.color.DynamicColors
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import com.google.android.material.R as M

/** Asking the user whether to pair with a PC, showing the code both screens
 * show: one question at a time, and none for a little while after one was
 * not accepted, so nobody on the network can keep the screen covered. */
object Pairing {
    private class Request(val code: String, val pc: String) {
        val answered = CountDownLatch(1)
        @Volatile var accepted = false
        @Volatile var close: (() -> Unit)? = null  // the question's window, when shown
    }

    @Volatile private var current: Request? = null
    private var quietUntil = 0L  // under this
    private val main = Handler(Looper.getMainLooper())

    /** Blocks the calling (connection) thread until the user answers, two
     * minutes at most: whether they accepted; null when no question may be
     * asked now (another is up, or one was just turned down). */
    fun ask(context: Context, pc: String, code: String): Boolean? {
        val request = Request(code, pc)
        synchronized(this) {
            if (current != null || SystemClock.elapsedRealtime() < quietUntil) return null
            current = request
        }
        val open = Intent(context, PairActivity::class.java)
            .putExtra(PairActivity.EXTRA_CODE, code)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Settings.canDrawOverlays(context)) context.startActivity(open)
        Notifications.pairing(context, pc, code, open)
        val accepted = request.answered.await(2, TimeUnit.MINUTES) && request.accepted
        synchronized(this) {
            current = null
            if (!accepted) quietUntil = SystemClock.elapsedRealtime() + QUIET
        }
        Notifications.cancelPairing(context, code)
        main.post { request.close?.invoke() }
        return accepted
    }

    /** The PC's name, while its pairing waits for an answer. */
    fun pending(code: String): String? = current?.takeIf { it.code == code }?.pc

    /** The user's answer, or the PC's refusal (which ends the question here too). */
    fun answer(code: String, accepted: Boolean) {
        current?.takeIf { it.code == code }?.let {
            it.accepted = accepted
            it.answered.countDown()
        }
    }

    /** close runs (on the main thread) when the question is over, however it ended. */
    fun onClose(code: String, close: () -> Unit) {
        current?.takeIf { it.code == code }?.close = close
    }

    private const val QUIET = 10_000L  // ms without questions after one was not accepted
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
        Pairing.onClose(code) { if (!isFinishing) finish() }  // answered elsewhere, or refused on the PC
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
