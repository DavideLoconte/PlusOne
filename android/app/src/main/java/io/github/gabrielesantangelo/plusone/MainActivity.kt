package io.github.gabrielesantangelo.plusone

import android.Manifest
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.materialswitch.MaterialSwitch
import java.io.File
import com.google.android.material.R as M

/**
 * What the app is doing and what it may do: the PDFs waiting to be signed,
 * the service that waits for the PC, the saved signatures, the permission
 * that lets a PDF open by itself, the paired PCs.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var content: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) tryOut(intent)  // not again when recreated
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(themeColor(M.attr.colorSurfaceContainer))
            clipToPadding = false
            addView(content)
            // Clear of the status and navigation bars (the app is drawn behind
            // them), and no wider than reads well on a big screen.
            var bars = android.graphics.Insets.NONE
            fun pad(v: View) {
                val side = maxOf(dp(16), (v.width - bars.left - bars.right - dp(MAX_WIDTH)) / 2)
                v.setPadding(bars.left + side, bars.top + dp(8), bars.right + side, bars.bottom + dp(24))
            }
            setOnApplyWindowInsetsListener { v, insets ->
                bars = systemBars(insets)
                pad(v)
                insets
            }
            addOnLayoutChangeListener { v, l, _, r, _, ol, _, or_, _ -> if (r - l != or_ - ol) v.post { pad(v) } }
        })
    }

    /** adb shell am start -S -n …/.MainActivity --es path /data/user/0/…/files/x.pdf
     * opens the signing screen on a PDF (for trying it out; debug builds only). */
    private fun tryOut(intent: Intent) {
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        val path = intent.getStringExtra(EXTRA_PATH) ?: return
        val job = Jobs.local(File(path), File(getExternalFilesDir(null), "last-signature.json"))
        startActivity(SignActivity.open(this, job.id, chosen = true))
    }

    override fun onResume() {
        super.onResume()
        CompanionService.start(this)  // ready now (for a while, if not always)
        Jobs.listen(queueChanged)
        refresh()
    }

    override fun onPause() {
        Jobs.unlisten(queueChanged)
        super.onPause()
    }

    private val queueChanged: () -> Unit = { refresh() }

    private fun refresh() {
        content.removeAllViews()
        content.addView(label(getString(R.string.app_name), M.attr.textAppearanceDisplaySmall).apply {
            setPadding(dp(8), dp(32), dp(8), dp(20))
        })
        content.addView(hero())

        val waiting = Jobs.waiting()
        if (waiting.isNotEmpty()) {
            section(R.string.heading_waiting, waiting.map { job ->
                val line = listOfNotNull(job.pc.takeIf { it.isNotEmpty() },
                    getString(R.string.started).takeIf { !job.draft.isNullOrEmpty() })
                Row(row(R.drawable.ic_document, job.title, line.joinToString(" · ").ifEmpty { null },
                    icon(R.drawable.ic_chevron_right), oneLine = true)) {
                    startActivity(SignActivity.open(this, job.id, chosen = true))
                }
            })
        }

        val always = MaterialSwitch(this).apply {
            isChecked = Options.alwaysReady(this@MainActivity)
            setOnCheckedChangeListener { _, on ->
                Options.setAlwaysReady(this@MainActivity, on)
                refresh()
            }
        }
        section(R.string.heading_ready, listOf(
            Row(row(R.drawable.ic_ready, getString(R.string.always_ready),
                getString(if (always.isChecked) R.string.always_on else R.string.always_off), always)) { always.toggle() },
        ))

        val scroll = MaterialSwitch(this).apply {
            isChecked = Options.scrollPages(this@MainActivity)
            setOnCheckedChangeListener { _, on ->
                Options.setScrollPages(this@MainActivity, on)
                refresh()
            }
        }
        section(R.string.heading_pages, listOf(
            Row(row(R.drawable.ic_scroll, getString(R.string.scroll_pages),
                getString(if (scroll.isChecked) R.string.scroll_on else R.string.scroll_off), scroll)) { scroll.toggle() },
        ))

        val saved = Signatures.all(this)
        section(R.string.heading_signatures, saved.map { signature ->
            Row(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(SignatureThumb(this@MainActivity, signature, Ink.COLOR), LinearLayout.LayoutParams(dp(200), dp(64)))
                addView(View(this@MainActivity), LinearLayout.LayoutParams(0, 1, 1f))
                addView(iconButton(R.drawable.ic_clear, R.string.remove) { askRemove(signature) })
            })
        } + Row(row(R.drawable.ic_add, getString(R.string.new_signature),
            if (saved.isEmpty()) getString(R.string.signatures_none) else null)) {
            val (color, width) = Pen.load(this)
            SignaturePad(this, color, width) { refresh() }.show()
        })

        val overlay = Settings.canDrawOverlays(this)
        section(R.string.heading_open, listOf(
            Row(row(R.drawable.ic_open, getString(R.string.open_by_itself),
                getString(if (overlay) R.string.overlay_on else R.string.overlay_off),
                if (overlay) icon(R.drawable.ic_check, androidx.appcompat.R.attr.colorPrimary)
                else button(getString(R.string.overlay_allow), Look.TONAL) { allowOverlay() })) { allowOverlay() },
        ))

        val paired = Paired.all(this)
        section(R.string.heading_paired, if (paired.isEmpty()) {
            listOf(Row(row(R.drawable.ic_computer, getString(R.string.paired_none_title), getString(R.string.paired_none))))
        } else paired.map { (fingerprint, name) ->
            Row(row(R.drawable.ic_computer, name, null,
                iconButton(R.drawable.ic_forget, R.string.forget) { askForget(fingerprint, name) }))
        })

        val version = packageManager.getPackageInfo(packageName, 0).versionName
        content.addView(label(getString(R.string.version, version), M.attr.textAppearanceBodySmall,
            M.attr.colorOnSurfaceVariant).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(32), 0, 0)
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }

    /** Ready, and how the PC finds this tablet: the app's whole job in a card. */
    private fun hero() = MaterialCardView(this, null, M.attr.materialCardViewFilledStyle).apply {
        setCardBackgroundColor(themeColor(M.attr.colorPrimaryContainer))
        radius = dp(28f)
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(20), dp(20), dp(24), dp(20))
            addView(icon(R.drawable.ic_ready, M.attr.colorOnPrimary, size = 28).apply {
                background = surface(androidx.appcompat.R.attr.colorPrimary, dp(28f))
                setPadding(dp(14), dp(14), dp(14), dp(14))
                layoutParams = LinearLayout.LayoutParams(dp(56), dp(56)).apply { marginEnd = dp(16) }
            })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                addView(label(getString(R.string.ready), M.attr.textAppearanceTitleLarge, M.attr.colorOnPrimaryContainer))
                addView(label(getString(R.string.status_ready, CompanionService.deviceName(this@MainActivity)),
                    M.attr.textAppearanceBodyMedium, M.attr.colorOnPrimaryContainer))
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        })
    }

    private fun section(title: Int, rows: List<Row>) {
        content.addView(sectionTitle(getString(title)))
        content.addView(group(rows))
    }

    private fun allowOverlay() {
        startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
    }

    private fun askRemove(signature: Signature) {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.remove_signature)
            .setMessage(R.string.remove_signature_detail)
            .setPositiveButton(R.string.remove) { _, _ ->
                Signatures.remove(this, signature.id)
                refresh()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun askForget(fingerprint: String, name: String) {
        MaterialAlertDialogBuilder(this)
            .setTitle(getString(R.string.forget_title, name))
            .setMessage(R.string.forget_detail)
            .setPositiveButton(R.string.forget) { _, _ ->
                Paired.forget(this, fingerprint)
                refresh()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    companion object {
        const val MAX_WIDTH = 760  // dp of content
        const val EXTRA_PATH = "path"
    }
}
