package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupWindow
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
 * PDF's path and, when the PC sent it, the job it answers, the PC's pen and
 * the saved signature.
 */
class SignActivity : Activity() {
    private lateinit var pdf: Pdf
    private lateinit var view: SignView
    private lateinit var undo: Button
    private lateinit var eraser: Button
    private lateinit var penButton: Button
    private lateinit var signatureButton: Button
    private lateinit var keep: Button
    private lateinit var hint: LinearLayout
    private lateinit var hintText: TextView
    private lateinit var pageLabel: TextView
    private lateinit var pages: LinearLayout
    private var job: String? = null
    private var answered = false
    private var signature: Signature? = null
    private var signatureKept = false  // a new one to hand to the PC

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
        signature = Signature.fromJson(intent.getStringExtra(EXTRA_SIGNATURE)?.let { JSONObject(it) })
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

        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.rgb(36, 36, 38))
            setPadding(dp(8f).toInt(), dp(6f).toInt(), dp(8f).toInt(), dp(6f).toInt())
            addView(button(getString(R.string.cancel)) { cancel() })
            addView(TextView(context).apply {
                text = intent.getStringExtra(EXTRA_TITLE) ?: File(path).name
                setTextColor(Color.WHITE)
                textSize = 15f
                gravity = Gravity.CENTER
                isSingleLine = true
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(button(getString(R.string.done), accent = true) { done() })
        }

        penButton = button("●") { showPen(it) }
        undo = button("↶") { view.undo() }
        eraser = button(getString(R.string.eraser)) {
            view.eraserMode = !view.eraserMode
            refresh()
        }
        signatureButton = button(getString(R.string.my_signature)) { placeSignature() }
        val tools = bar().apply {
            addView(penButton)
            addView(eraser)
            addView(undo)
            addView(button(getString(R.string.clear_page)) { view.clearPage() })
            addView(separator())
            addView(signatureButton)
            addView(button(getString(R.string.date)) { placeDate() })
            addView(button(getString(R.string.text)) { askText() })
            addView(separator())
            addView(button(getString(R.string.fit)) { view.fit() })
        }
        keep = button(getString(R.string.keep_signature), accent = true) { keepSignature() }
        pageLabel = TextView(this).apply { setTextColor(Color.WHITE); setPadding(12, 0, 12, 0) }
        pages = bar().apply {
            addView(button("‹") { view.showPage(view.index - 1) })
            addView(pageLabel)
            addView(button("›") { view.showPage(view.index + 1) })
            visibility = if (pdf.sizes.size > 1) View.VISIBLE else View.GONE
        }
        hintText = TextView(this).apply { setTextColor(Color.WHITE); textSize = 15f }
        hint = bar().apply {
            background = GradientDrawable().apply { setColor(Color.rgb(53, 132, 228)); cornerRadius = dp(24f) }
            setPadding(dp(18f).toInt(), dp(2f).toInt(), dp(4f).toInt(), dp(2f).toInt())
            addView(hintText)
            addView(button("✕") {
                view.pending = null
                visibility = View.GONE
            })
            visibility = View.GONE
        }

        val root = FrameLayout(this)
        root.addView(view)
        root.addView(top, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        root.addView(hint, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply {
            topMargin = dp(68f).toInt()
        })
        root.addView(tools, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
            bottomMargin = dp(18f).toInt()
        })
        root.addView(keep, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
            bottomMargin = dp(84f).toInt()
        })
        root.addView(pages, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.START).apply {
            bottomMargin = dp(18f).toInt()
            leftMargin = dp(18f).toInt()
        })
        setContentView(root)
        refresh()
    }

    private fun refresh() {
        undo.isEnabled = view.canUndo
        undo.alpha = if (view.canUndo) 1f else 0.4f
        eraser.setTextColor(if (view.eraserMode) Color.rgb(120, 174, 237) else Color.WHITE)
        penButton.setTextColor(view.penColor)
        signatureButton.isEnabled = signature != null
        signatureButton.alpha = if (signature != null) 1f else 0.4f
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
        popup.elevation = dp(8f)
        popup.showAsDropDown(anchor, 0, -dp(200f).toInt())
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

    private fun placeSignature() {
        val saved = signature ?: return
        arm(R.string.tap_signature) { x, y -> saved.place(x, y, view.penWidth, view.penColor) }
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
        signature = Signature.of(view.pageStrokes) ?: return
        signatureKept = true
        Toast.makeText(this, R.string.signature_kept, Toast.LENGTH_LONG).show()
        refresh()
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

    /** The marks as the PC's plusone/pdf.py takes them, page by page; the
     * pen; a signature kept here. */
    private fun resultJson(): JSONObject {
        val pagesJson = JSONArray()
        for ((page, list) in view.marks.toSortedMap()) {
            if (list.isEmpty()) continue
            pagesJson.put(JSONObject().put("page", page).put("marks", JSONArray(list.map { it.toJson() })))
        }
        val result = JSONObject().put("pages", pagesJson)
            .put("pen", JSONObject().put("color", Ink.colorJson(view.penColor)).put("width", view.penWidth.toDouble()))
        if (signatureKept) signature?.let { result.put("signature", it.toJson()) }
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
        background = GradientDrawable().apply {
            setColor(Color.rgb(54, 54, 58))
            cornerRadius = dp(28f)
        }
        setPadding(dp(6f).toInt(), dp(4f).toInt(), dp(6f).toInt(), dp(4f).toInt())
        elevation = dp(6f)
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
        const val EXTRA_SIGNATURE = "signature"
    }
}
