package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Insets
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Signing a PDF: the page full screen, a bar on top (Cancel, title, Done)
 * and a floating toolbar under the page, as on the PC. Started with the
 * PDF's path and, when the PC sent it, the job it answers and the PC's pen.
 */
class SignActivity : Activity() {
    private lateinit var pdf: Pdf
    private lateinit var view: SignView
    private lateinit var undo: View
    private lateinit var eraser: android.widget.ImageButton
    private lateinit var penButton: PenDot
    private lateinit var signatureThumb: SignatureThumb
    private lateinit var keep: Button
    private lateinit var hint: LinearLayout
    private lateinit var hintText: TextView
    private lateinit var pageLabel: TextView
    private lateinit var pages: LinearLayout
    private lateinit var root: FrameLayout
    private lateinit var top: LinearLayout
    private lateinit var tools: HorizontalScrollView
    private var bars = Insets.NONE  // system bars and display cutout
    private var job: String? = null
    private var answered = false
    private var signature: Signature? = null  // what "Signature" places: the first saved

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val path = intent.getStringExtra(EXTRA_PATH) ?: return finish()
        job = intent.getStringExtra(EXTRA_JOB)
        pdf = try {
            Pdf(File(path))
        } catch (e: Exception) {
            Toast.makeText(this, getString(R.string.cannot_open, e.message), Toast.LENGTH_LONG).show()
            return finish()
        }
        signature = Signatures.all(this).firstOrNull()
        val (color, width) = intent.getStringExtra(EXTRA_PEN)?.let { JSONObject(it) }?.let {
            Ink.colorOf(it.optJSONArray("color")) to it.optDouble("width", Ink.WIDTH.toDouble()).toFloat()
        } ?: Pen.load(this)
        view = SignView(this, pdf).apply {
            topInset = dp(56f)
            bottomInset = dp(84f)
            penColor = color
            penWidth = width.coerceIn(Ink.MIN_WIDTH, Ink.MAX_WIDTH)
            onChange = ::refresh
            onPlaced = { hint.visibility = View.GONE }
        }

        // Page arrows next to the title: the bottom keeps only the toolbar,
        // which leaves the page most of a phone's screen.
        pageLabel = TextView(this).apply { setTextColor(Color.WHITE); setPadding(dp(4f).toInt(), 0, dp(4f).toInt(), 0) }
        pages = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(button("‹") { view.showPage(view.index - 1) }.apply {
                contentDescription = getString(R.string.previous_page)
                minWidth = dp(40f).toInt()
            })
            addView(pageLabel)
            addView(button("›") { view.showPage(view.index + 1) }.apply {
                contentDescription = getString(R.string.next_page)
                minWidth = dp(40f).toInt()
            })
            visibility = if (pdf.sizes.size > 1) View.VISIBLE else View.GONE
        }
        top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.rgb(36, 36, 38))
            addView(button(getString(R.string.cancel)) { cancel() })
            // Title and page arrows share what Cancel and Done leave: on a
            // narrow screen they give way, never Done.
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(TextView(context).apply {
                    text = intent.getStringExtra(EXTRA_TITLE) ?: File(path).name
                    setTextColor(Color.WHITE)
                    textSize = 15f
                    gravity = Gravity.CENTER
                    isSingleLine = true
                    ellipsize = android.text.TextUtils.TruncateAt.MIDDLE
                }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
                addView(pages)
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(button(getString(R.string.done), accent = true) { done() })
        }

        penButton = PenDot().apply {
            contentDescription = getString(R.string.pen)
            setOnClickListener { showPen(it) }
        }
        undo = icon(R.drawable.ic_undo, R.string.undo) { view.undo() }
        eraser = icon(R.drawable.ic_eraser, R.string.eraser) {
            view.eraserMode = !view.eraserMode
            refresh()
        }
        // The first saved signature, one tap to place it; the chevron lists them all.
        signatureThumb = SignatureThumb(this, signature, color).apply {
            contentDescription = getString(R.string.my_signature)
            setOnClickListener { placeSignature() }
        }
        val signatureButton = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(6f).toInt(), 0, 0, 0)
            addView(signatureThumb, LinearLayout.LayoutParams(dp(72f).toInt(), dp(36f).toInt()))
            addView(icon(R.drawable.ic_more, R.string.my_signatures) { showSignatures(it) },
                LinearLayout.LayoutParams(dp(36f).toInt(), dp(48f).toInt()))
        }
        val toolRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(penButton, LinearLayout.LayoutParams(dp(48f).toInt(), dp(48f).toInt()))
            addView(eraser)
            addView(undo)
            addView(icon(R.drawable.ic_clear, R.string.clear_page) { view.clearPage() })
            addView(separator())
            addView(signatureButton)
            addView(icon(R.drawable.ic_date, R.string.date) { placeDate() })
            addView(icon(R.drawable.ic_text, R.string.text) { askText() })
            addView(separator())
            addView(icon(R.drawable.ic_fit, R.string.fit) { view.fit() })
        }
        // Wider than some phones: it scrolls there.
        tools = HorizontalScrollView(this).apply {
            styleBar()
            clipToOutline = true
            isHorizontalScrollBarEnabled = false
            isHorizontalFadingEdgeEnabled = true
            setFadingEdgeLength(dp(24f).toInt())
            addView(toolRow)
        }
        keep = button(getString(R.string.keep_signature), accent = true) { keepSignature() }
        hintText = TextView(this).apply { setTextColor(Color.WHITE); textSize = 15f }
        hint = bar().apply {
            background = GradientDrawable().apply { setColor(Color.rgb(53, 132, 228)); cornerRadius = dp(24f) }
            setPadding(dp(18f).toInt(), dp(2f).toInt(), dp(4f).toInt(), dp(2f).toInt())
            addView(hintText)
            addView(button("✕") {
                view.pending = null
                visibility = View.GONE
            }.apply { contentDescription = getString(R.string.cancel) })
            visibility = View.GONE
        }

        root = FrameLayout(this)
        root.addView(view)
        root.addView(top, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        root.addView(hint, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        root.addView(tools, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        root.addView(keep, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        root.setOnApplyWindowInsetsListener { _, insets ->
            bars = systemBars(insets)
            placeChrome()
            insets
        }
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> placeChrome() }
        setContentView(root)
        refresh()
    }

    /** The bars clear of the system's, and the page fitted between them. */
    private fun placeChrome() {
        val m = dp(14f).toInt()
        val gap = dp(10f).toInt()
        top.setPadding(dp(8f).toInt() + bars.left, dp(6f).toInt() + bars.top, dp(8f).toInt() + bars.right, dp(6f).toInt())
        place(tools, bottom = m + bars.bottom, left = m + bars.left, right = m + bars.right)
        val chrome = m + bars.bottom + tools.height
        place(keep, bottom = chrome + gap)
        place(hint, top = top.height + gap)
        val topInset = top.height.toFloat()
        val bottomInset = (chrome + gap).toFloat()
        if (top.height > 0 && (view.topInset != topInset || view.bottomInset != bottomInset)) {
            view.topInset = topInset
            view.bottomInset = bottomInset
            view.fit()
        }
    }

    private fun place(v: View, top: Int = 0, bottom: Int = 0, left: Int = 0, right: Int = 0) {
        val lp = v.layoutParams as FrameLayout.LayoutParams
        if (lp.topMargin == top && lp.bottomMargin == bottom && lp.leftMargin == left && lp.rightMargin == right) return
        lp.setMargins(left, top, right, bottom)
        v.layoutParams = lp
    }

    private fun refresh() {
        undo.isEnabled = view.canUndo
        undo.alpha = if (view.canUndo) 1f else 0.4f
        eraser.imageTintList = android.content.res.ColorStateList.valueOf(
            if (view.eraserMode) Color.rgb(120, 174, 237) else Color.WHITE)
        eraser.background = if (view.eraserMode) GradientDrawable().apply {
            setColor(Color.argb(50, 120, 174, 237)); cornerRadius = dp(24f)
        } else null
        penButton.invalidate()
        signatureThumb.signature = signature
        signatureThumb.color = view.penColor
        signatureThumb.invalidate()
        // Offered once something is drawn and no signature is saved yet.
        keep.visibility = if (signature == null && view.pageStrokes.isNotEmpty()) View.VISIBLE else View.GONE
        pageLabel.text = getString(R.string.page_of, view.index + 1, view.pageCount)
    }

    // -- tools -----------------------------------------------------------------

    private fun showPen(anchor: View) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16f).toInt(), dp(12f).toInt(), dp(16f).toInt(), dp(12f).toInt())
            background = GradientDrawable().apply { setColor(Color.rgb(54, 54, 58)); cornerRadius = dp(16f) }
        }
        val popup = PopupWindow(box, LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT, true)
        box.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            for ((name, color) in Ink.INKS) {
                addView(Swatch(color, color == view.penColor).apply {
                    contentDescription = getString(name)
                    setOnClickListener {
                        setPen(color = color)
                        popup.dismiss()
                    }
                }, LinearLayout.LayoutParams(dp(44f).toInt(), dp(44f).toInt()))
            }
        })
        box.addView(TextView(this).apply {
            setText(R.string.thickness)
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(10f).toInt(), 0, 0)
        })
        box.addView(SeekBar(this).apply {
            max = 100
            progress = (Ink.thickness(view.penWidth) * 100).toInt()
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: SeekBar, value: Int, fromUser: Boolean) {
                    if (fromUser) setPen(width = Ink.penWidth(value / 100f))
                }
                override fun onStartTrackingTouch(bar: SeekBar) {}
                override fun onStopTrackingTouch(bar: SeekBar) {}
            })
        }, LinearLayout.LayoutParams(dp(260f).toInt(), LinearLayout.LayoutParams.WRAP_CONTENT))
        showAbove(popup, box, anchor)
    }

    /** A popup over the toolbar, from the anchor's left end, as tall as there is room for. */
    private fun showAbove(popup: PopupWindow, content: View, anchor: View) {
        val room = IntArray(2).also { anchor.getLocationInWindow(it) }[1] - bars.top - dp(16f).toInt()
        content.measure(View.MeasureSpec.makeMeasureSpec(root.width, View.MeasureSpec.AT_MOST),
            View.MeasureSpec.UNSPECIFIED)
        popup.height = minOf(content.measuredHeight, room)
        popup.elevation = dp(8f)
        popup.showAsDropDown(anchor, 0, -(popup.height + anchor.height + dp(12f).toInt()))
    }

    private fun setPen(color: Int = view.penColor, width: Float = view.penWidth) {
        view.penColor = color
        view.penWidth = width
        view.eraserMode = false
        Pen.save(this, color, width)
        refresh()
    }

    private fun arm(message: Int, place: (Float, Float) -> List<Mark>) {
        view.eraserMode = false
        view.pending = place
        hintText.setText(message)
        hint.visibility = View.VISIBLE
        refresh()
    }

    /** The first saved signature; with none yet, the pad to draw one. */
    private fun placeSignature() {
        val saved = signature ?: return newSignature()
        arm(R.string.tap_signature) { x, y -> saved.place(x, y, view.penWidth, view.penColor) }
    }

    private fun newSignature() {
        SignaturePad(this, view.penColor, view.penWidth) {
            signatureChanged()
            placeSignature()
        }.show()
    }

    private fun signatureChanged() {
        signature = Signatures.all(this).firstOrNull()
        refresh()
    }

    /** The saved signatures: tap one to place it, ✕ to remove it, or draw a new one. */
    private fun showSignatures(anchor: View) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12f).toInt(), dp(12f).toInt(), dp(12f).toInt(), dp(8f).toInt())
        }
        val scroll = ScrollView(this).apply {
            background = GradientDrawable().apply { setColor(Color.rgb(54, 54, 58)); cornerRadius = dp(16f) }
            addView(box)
        }
        val popup = PopupWindow(scroll, LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT, true)
        for (saved in Signatures.all(this)) {
            box.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4f).toInt(), 0, dp(4f).toInt())
                addView(SignatureThumb(this@SignActivity, saved, view.penColor).apply {
                    contentDescription = getString(R.string.place_signature)
                    setOnClickListener {
                        popup.dismiss()
                        Signatures.use(this@SignActivity, saved.id)
                        signatureChanged()
                        placeSignature()
                    }
                }, LinearLayout.LayoutParams(dp(200f).toInt(), dp(64f).toInt()))
                addView(button("✕") {
                    popup.dismiss()
                    askRemove(saved)
                }.apply { contentDescription = getString(R.string.remove) })
            })
        }
        box.addView(button("+ " + getString(R.string.new_signature)) {
            popup.dismiss()
            newSignature()
        })
        showAbove(popup, box, anchor)
    }

    private fun askRemove(saved: Signature) {
        AlertDialog.Builder(this)
            .setTitle(R.string.remove_signature)
            .setMessage(R.string.remove_signature_detail)
            .setPositiveButton(R.string.remove) { _, _ ->
                Signatures.remove(this, saved.id)
                signatureChanged()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun placeDate() {
        val today = SimpleDateFormat("dd/MM/yyyy", Locale.ROOT).format(Date())
        arm(R.string.tap_date) { x, y -> listOf(TextMark(x, y, today, color = view.penColor)) }
    }

    private fun askText() {
        val entry = EditText(this).apply { isSingleLine = true }
        AlertDialog.Builder(this)
            .setTitle(R.string.text_to_add)
            .setView(entry)
            .setPositiveButton(R.string.place) { _, _ ->
                val text = entry.text.toString().trim()
                if (text.isNotEmpty()) arm(R.string.tap_text) { x, y -> listOf(TextMark(x, y, text, color = view.penColor)) }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun keepSignature() {
        Signatures.add(this, Signature.of(view.pageStrokes) ?: return)
        signatureChanged()
        Toast.makeText(this, R.string.signature_kept, Toast.LENGTH_LONG).show()
    }

    // -- closing -----------------------------------------------------------------

    private fun done() = answer(resultJson())

    private fun cancel() = answer(null)

    /** Hands the result to whoever asked (the PC through the link), then closes. */
    private fun answer(result: JSONObject?) {
        if (answered) return
        answered = true
        val id = job
        if (id != null) {
            Jobs.finish(id, result)
        } else if (result != null) {
            // Opened by hand (a test): keep what was drawn next to the app's files.
            File(getExternalFilesDir(null), "last-signature.json").writeText(result.toString())
            Toast.makeText(this, R.string.saved_test, Toast.LENGTH_SHORT).show()
        }
        finish()
    }

    /** The marks as the PC's plusone/pdf.py takes them, page by page, and the pen. */
    private fun resultJson(): JSONObject {
        val pagesJson = JSONArray()
        for ((page, list) in view.marks.toSortedMap()) {
            if (list.isEmpty()) continue
            pagesJson.put(JSONObject().put("page", page).put("marks", JSONArray(list.map { it.toJson() })))
        }
        val result = JSONObject().put("pages", pagesJson)
            .put("pen", JSONObject().put("color", Ink.colorJson(view.penColor)).put("width", view.penWidth.toDouble()))
        return result
    }

    override fun onDestroy() {
        if (!answered) {
            answered = true
            job?.let { Jobs.finish(it, null) }
        }
        if (::view.isInitialized) view.release()
        if (::pdf.isInitialized) pdf.close()
        super.onDestroy()
    }

    // -- widgets -------------------------------------------------------------

    private fun dp(v: Float) = v * resources.displayMetrics.density

    private fun bar() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        styleBar()
    }

    /** A floating bar's look: rounded, raised. */
    private fun View.styleBar() {
        background = GradientDrawable().apply {
            setColor(Color.rgb(54, 54, 58))
            cornerRadius = dp(28f)
        }
        setPadding(dp(6f).toInt(), dp(4f).toInt(), dp(6f).toInt(), dp(4f).toInt())
        elevation = dp(6f)
    }

    private fun icon(drawable: Int, description: Int, onClick: (View) -> Unit) = android.widget.ImageButton(this).apply {
        setImageResource(drawable)
        contentDescription = getString(description)
        tooltipText = contentDescription  // shown when the S Pen hovers
        background = null
        minimumWidth = dp(48f).toInt()
        minimumHeight = dp(48f).toInt()
        setOnClickListener { onClick(it) }
    }

    private fun separator() = View(this).apply {
        setBackgroundColor(Color.argb(60, 255, 255, 255))
        layoutParams = LinearLayout.LayoutParams(dp(1f).toInt(), dp(24f).toInt()).apply {
            setMargins(dp(4f).toInt(), 0, dp(4f).toInt(), 0)
        }
    }

    private fun button(label: String, accent: Boolean = false, onClick: (View) -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        isSingleLine = true
        textSize = 15f
        setTextColor(Color.WHITE)
        background = GradientDrawable().apply {
            setColor(if (accent) Color.rgb(53, 132, 228) else Color.TRANSPARENT)
            cornerRadius = dp(20f)
        }
        minWidth = dp(48f).toInt()
        setPadding(dp(14f).toInt(), 0, dp(14f).toInt(), 0)
        setOnClickListener { onClick(it) }
    }

    /** The pen's colour and thickness, on a light disc so dark inks show on the dark bar. */
    private inner class PenDot : View(this@SignActivity) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        override fun onDraw(canvas: Canvas) {
            val r = minOf(width, height) / 2f
            paint.color = Color.rgb(236, 236, 240)
            canvas.drawCircle(width / 2f, height / 2f, r * 0.62f, paint)
            paint.color = view.penColor
            canvas.drawCircle(width / 2f, height / 2f, r * (0.14f + 0.36f * Ink.thickness(view.penWidth)), paint)
        }
    }

    /** A colour to pick, ringed when it is the pen's. */
    private inner class Swatch(private val color: Int, private val chosen: Boolean) : View(this@SignActivity) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        override fun onDraw(canvas: Canvas) {
            val r = minOf(width, height) / 2f
            paint.style = Paint.Style.FILL
            paint.color = color
            canvas.drawCircle(width / 2f, height / 2f, r * 0.6f, paint)
            if (chosen) {
                paint.style = Paint.Style.STROKE
                paint.strokeWidth = dp(2.5f)
                paint.color = Color.WHITE
                canvas.drawCircle(width / 2f, height / 2f, r * 0.85f, paint)
            }
        }
    }

    companion object {
        const val EXTRA_PATH = "path"
        const val EXTRA_JOB = "job"
        const val EXTRA_TITLE = "title"
        const val EXTRA_PEN = "pen"
    }
}
