package io.github.gabrielesantangelo.plusone

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * What the app is doing and what it may do: the service that waits for the
 * PC, the permissions that let a PDF open by itself, the paired PCs.
 */
class MainActivity : Activity() {
    private lateinit var content: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // adb shell am start -n …/.MainActivity --es path /data/user/0/…/files/x.pdf
        // opens the signing screen on a PDF (for trying it out).
        intent.getStringExtra(SignActivity.EXTRA_PATH)?.let { path ->
            startActivity(Intent(this, SignActivity::class.java).putExtra(SignActivity.EXTRA_PATH, path))
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(32), dp(32), dp(32), dp(32))
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.rgb(30, 30, 32))
            addView(content)
        })
    }

    override fun onResume() {
        super.onResume()
        CompanionService.start(this)  // ready now (for a while, if not always)
        refresh()
    }

    private fun refresh() {
        content.removeAllViews()
        content.addView(text(getString(R.string.app_name), 28f, bold = true))
        content.addView(text(getString(R.string.status_ready, CompanionService.deviceName(this)), 16f))

        content.addView(heading(getString(R.string.heading_ready)))
        content.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(text(getString(if (Options.alwaysReady(this@MainActivity)) R.string.always_on else R.string.always_off), 15f),
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(android.widget.Switch(this@MainActivity).apply {
                val states = arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf())
                thumbTintList = android.content.res.ColorStateList(states, intArrayOf(Color.WHITE, Color.LTGRAY))
                trackTintList = android.content.res.ColorStateList(states,
                    intArrayOf(Color.rgb(53, 132, 228), Color.rgb(90, 90, 96)))
                isChecked = Options.alwaysReady(this@MainActivity)
                setOnCheckedChangeListener { _, on ->
                    Options.setAlwaysReady(this@MainActivity, on)
                    refresh()
                }
            })
        })

        content.addView(heading(getString(R.string.heading_open)))
        if (Settings.canDrawOverlays(this)) {
            content.addView(text(getString(R.string.overlay_on), 15f))
        } else {
            content.addView(text(getString(R.string.overlay_off), 15f))
            content.addView(button(getString(R.string.overlay_allow)) {
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            })
        }

        content.addView(heading(getString(R.string.heading_paired)))
        val paired = Paired.all(this)
        if (paired.isEmpty()) content.addView(text(getString(R.string.paired_none), 15f))
        for ((fingerprint, name) in paired) {
            content.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(text(name, 16f), LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
                addView(button(getString(R.string.forget)) {
                    Paired.forget(this@MainActivity, fingerprint)
                    refresh()
                })
            })
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun text(s: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        textSize = size
        setTextColor(Color.rgb(230, 230, 232))
        if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, dp(4), 0, dp(4))
    }

    private fun heading(s: String) = text(s, 13f, bold = true).apply {
        setTextColor(Color.rgb(120, 174, 237))
        setPadding(0, dp(28), 0, dp(6))
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setTextColor(Color.WHITE)
        background = GradientDrawable().apply {
            setColor(Color.rgb(53, 132, 228))
            cornerRadius = dp(20).toFloat()
        }
        setPadding(dp(18), 0, dp(18), 0)
        setOnClickListener { onClick() }
    }
}
