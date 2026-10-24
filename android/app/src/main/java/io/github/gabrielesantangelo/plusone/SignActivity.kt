package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Signing a PDF: the page full screen, a bar on top (Cancel, title, Done)
 * and a floating toolbar under the page. Started with the PDF's path and,
 * when the PC sent it, the job it answers.
 */
class SignActivity : Activity() {
    private lateinit var pdf: Pdf
    private lateinit var view: SignView
    private lateinit var undo: Button
    private lateinit var eraser: Button
    private lateinit var pageLabel: TextView
    private lateinit var pages: LinearLayout
    private var job: String? = null
    private var answered = false

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
        view = SignView(this, pdf).apply {
            topInset = dp(56f)
            bottomInset = dp(84f)
            onChange = ::refresh
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

        undo = button("↶") { view.undo() }
        eraser = button(getString(R.string.eraser)) {
            view.eraserMode = !view.eraserMode
            refresh()
        }
        val tools = bar().apply {
            addView(undo)
            addView(eraser)
            addView(button(getString(R.string.clear_page)) { view.clearPage() })
            addView(button(getString(R.string.fit)) { view.fit() })
        }
        pageLabel = TextView(this).apply { setTextColor(Color.WHITE); setPadding(12, 0, 12, 0) }
        pages = bar().apply {
            addView(button("‹") { view.showPage(view.index - 1) })
            addView(pageLabel)
            addView(button("›") { view.showPage(view.index + 1) })
            visibility = if (pdf.sizes.size > 1) View.VISIBLE else View.GONE
        }

        val root = FrameLayout(this)
        root.addView(view)
        root.addView(top, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        root.addView(tools, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
            bottomMargin = dp(18f).toInt()
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
        eraser.alpha = if (view.eraserMode) 1f else 0.6f
        eraser.setTextColor(if (view.eraserMode) Color.rgb(120, 174, 237) else Color.WHITE)
        pageLabel.text = getString(R.string.page_of, view.index + 1, view.pageCount)
    }

    private fun done() {
        answer(marksJson())
    }

    private fun cancel() {
        answer(null)
    }

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

    /** The marks as the PC's plusone/pdf.py takes them, page by page. */
    private fun marksJson(): JSONObject {
        val pagesJson = JSONArray()
        for ((page, strokes) in view.marks.toSortedMap()) {
            if (strokes.isEmpty()) continue
            val list = JSONArray()
            for (s in strokes) {
                list.put(JSONObject().apply {
                    put("type", "stroke")
                    put("width", s.width.toDouble())
                    put("color", JSONArray(listOf(Color.red(s.color) / 255.0, Color.green(s.color) / 255.0,
                        Color.blue(s.color) / 255.0)))
                    put("points", JSONArray().also { a -> s.points.forEach { a.put(it.toDouble()) } })
                })
            }
            pagesJson.put(JSONObject().apply {
                put("page", page)
                put("marks", list)
            })
        }
        return JSONObject().put("pages", pagesJson)
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

    private fun button(label: String, accent: Boolean = false, onClick: () -> Unit) = Button(this).apply {
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
        setOnClickListener { onClick() }
    }

    companion object {
        const val EXTRA_PATH = "path"
        const val EXTRA_JOB = "job"
        const val EXTRA_TITLE = "title"
    }
}
