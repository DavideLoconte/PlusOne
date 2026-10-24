package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.graphics.Typeface
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Asking the user whether to pair with a PC, showing the code both screens show. */
object Pairing {
    private class Request(val pc: String, val code: String) {
        val answered = CountDownLatch(1)
        @Volatile var accepted = false
    }

    private val requests = ConcurrentHashMap<String, Request>()

    /** Blocks the calling (connection) thread until the user answers, two minutes at most. */
    fun ask(context: Context, pc: String, code: String): Boolean {
        val request = Request(pc, code)
        requests[code] = request
        val open = Intent(context, PairActivity::class.java)
            .putExtra(PairActivity.EXTRA_CODE, code)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Settings.canDrawOverlays(context)) context.startActivity(open)
        Notifications.pairing(context, pc, code, open)
        val answered = request.answered.await(2, TimeUnit.MINUTES)
        requests.remove(code)
        Notifications.cancelPairing(context)
        return answered && request.accepted
    }

    fun pending(code: String): Pair<String, String>? = requests[code]?.let { it.pc to it.code }

    fun answer(code: String, accepted: Boolean) {
        requests[code]?.let {
            it.accepted = accepted
            it.answered.countDown()
        }
    }
}

/** The pairing question, over whatever is on screen. Its own layout: an
 * AlertDialog inside a dialog-themed activity was dismissed as it opened. */
class PairActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setFinishOnTouchOutside(false)
        val code = intent.getStringExtra(EXTRA_CODE) ?: return finish()
        val (pc, _) = Pairing.pending(code) ?: return finish()
        fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
        fun button(label: Int, accept: Boolean) = Button(this).apply {
            setText(label)
            isAllCaps = false
            setOnClickListener {
                Pairing.answer(code, accept)
                finish()
            }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(28), dp(24), dp(28), dp(16))
            addView(TextView(context).apply {
                text = getString(R.string.pair_title, pc)
                textSize = 20f
            })
            addView(TextView(context).apply {
                text = getString(R.string.pair_compare)
                textSize = 15f
                setPadding(0, dp(12), 0, dp(4))
            })
            addView(TextView(context).apply {
                text = code.chunked(3).joinToString(" ")
                textSize = 40f
                typeface = Typeface.MONOSPACE
                gravity = Gravity.CENTER
                setPadding(0, dp(8), 0, dp(16))
            })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.END
                addView(button(R.string.cancel, false))
                addView(button(R.string.pair, true))
            })
        })
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
