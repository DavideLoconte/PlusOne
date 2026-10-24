package io.github.gabrielesantangelo.plusone

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Insets
import android.graphics.Paint
import android.os.Bundle
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import android.window.OnBackInvokedDispatcher
import androidx.core.view.children
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.slider.LabelFormatter
import com.google.android.material.slider.Slider
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import com.google.android.material.R as M

/**
 * Signing the PDFs the PC sent (Jobs): one at a time, the page full
 * screen, a bar on top (close, title, pages, the queue, Done) and a
 * floating toolbar under the page, as on the PC. Done or cancelled, a PDF
 * goes back to the PC and the next one in the queue opens; the queue
 * button switches between them, keeping what was drawn on each.
 */
class SignActivity : AppCompatActivity() {
    private lateinit var root: FrameLayout
    private lateinit var view: SignView     // a new one for each document
    private var pdf: Pdf? = null
    private var current: Jobs.Job? = null
    private var seen = 0L                   // the last job the queue told about
    private var penColor = Ink.COLOR
    private var penWidth = Ink.WIDTH
    private var chromeTop = 0f              // the page's room: under the bar,
    private var chromeBottom = 0f           // over the toolbar
    private var bars = Insets.NONE          // system bars and display cutout
    private var signature: Signature? = null  // what "Signature" places: the first saved

    private lateinit var top: LinearLayout
    private lateinit var titleText: TextView
    private lateinit var subtitle: TextView
    private lateinit var pages: LinearLayout
    private lateinit var pageLabel: TextView
    private lateinit var queueButton: MaterialButton
    private lateinit var doneButton: MaterialButton
    private lateinit var tools: HorizontalScrollView
    private lateinit var undo: MaterialButton
    private lateinit var eraser: MaterialButton
    private lateinit var lassoButton: MaterialButton
    private lateinit var penButton: PenDot
    private lateinit var signatureThumb: SignatureThumb
    private lateinit var keep: MaterialButton
    private lateinit var hint: LinearLayout
    private lateinit var hintText: TextView
    private lateinit var selectionBar: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val first = Jobs.get(savedInstanceState?.getString(STATE_JOB))
            ?: Jobs.get(intent.getStringExtra(EXTRA_JOB))
            ?: Jobs.waiting().firstOrNull()
            ?: return finish()
        signature = Signatures.all(this).firstOrNull()
        // The PC's pen, so both sides sign alike; once changed here, this one.
        val (color, width) = if (savedInstanceState != null) Pen.load(this) else first.pen?.let {
            Ink.colorOf(it.optJSONArray("color")) to it.optDouble("width", Ink.WIDTH.toDouble()).toFloat()
        } ?: Pen.load(this)
        penColor = color
        penWidth = width.coerceIn(Ink.MIN_WIDTH, Ink.MAX_WIDTH)
        seen = Jobs.waiting().maxOfOrNull { it.order } ?: 0

        root = FrameLayout(this)
        buildTop()
        buildTools()
        root.addView(top, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP))
        root.addView(hint, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        root.addView(selectionBar, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        root.addView(tools, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        root.addView(keep, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL))
        root.setOnApplyWindowInsetsListener { _, insets ->
            bars = systemBars(insets)
            placeChrome()
            insets
        }
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> placeChrome() }
        setContentView(root)
        onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { askCancel() }
        Jobs.listen(queueChanged)
        show(first)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Chosen in the app's list; a PDF that just came is told about by queueChanged.
        if (intent.getBooleanExtra(EXTRA_CHOSEN, false)) Jobs.get(intent.getStringExtra(EXTRA_JOB))?.let(::switchTo)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        current?.let { outState.putString(STATE_JOB, it.id) }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        compact()
    }

    // -- the queue ---------------------------------------------------------------

    /** Opens job's PDF, with what was drawn on it before. */
    private fun show(job: Jobs.Job) {
        val opened = try {
            Pdf(job.file)
        } catch (e: Exception) {
            Toast.makeText(this, getString(R.string.cannot_open, e.message), Toast.LENGTH_LONG).show()
            Jobs.finish(job.id, null)  // the PC hears it was not signed
            if (current == null) next()  // else the one shown stays
            return
        }
        leave()
        current = job
        pdf = opened
        view = SignView(this, opened).apply {
            topInset = chromeTop
            bottomInset = chromeBottom
            canvasColor = themeColor(M.attr.colorSurfaceContainerHigh)
            penColor = this@SignActivity.penColor
            penWidth = this@SignActivity.penWidth
            onChange = ::refresh
            onPlaced = { hint.visibility = View.GONE }
            onSelection = { on -> selectionBar.visibility = if (on) View.VISIBLE else View.GONE }
        }
        root.addView(view, 0)
        job.draft?.let { view.restore(it) }
        view.showPage(job.draftPage)
        titleText.text = job.title
        subtitle.text = job.pc
        subtitle.visibility = if (job.pc.isEmpty()) View.GONE else View.VISIBLE
        pages.visibility = if (opened.sizes.size > 1) View.VISIBLE else View.GONE
        hint.visibility = View.GONE
        selectionBar.visibility = View.GONE
        setTaskDescription(ActivityManager.TaskDescription.Builder().setLabel(job.title).build())
        refresh()
    }

    /** Puts the document away: what was drawn waits on its job. */
    private fun leave() {
        if (!::view.isInitialized) return
        view.deselect()  // the picked marks go back on their page
        current?.takeIf { Jobs.get(it.id) != null }?.let { job ->
            job.draft = view.marks.filterValues { it.isNotEmpty() }.mapValues { it.value.toList() }
            job.draftPage = view.index
        }
        view.release()
        root.removeView(view)
        pdf?.close()
        pdf = null
    }

    private fun switchTo(job: Jobs.Job) {
        if (job !== current) show(job)
    }

    /** The next PDF in the queue, or closing when there is none. */
    private fun next() {
        val following = Jobs.waiting().firstOrNull { it !== current }
        if (following == null) {
            current = null
            finish()
        } else {
            show(following)
        }
    }

    /** Sends the shown PDF's answer (null: not signed) and goes on to the next. */
    private fun answer(result: JSONObject?) {
        val job = current ?: return
        current = null  // nothing to keep for it
        Jobs.finish(job.id, result)
        next()
        if (current != null) snackbar(getString(if (result != null) R.string.sent else R.string.not_signed, job.title))
    }

    /** PDFs that came, or went (the PC stopped waiting for one). */
    private val queueChanged: () -> Unit = queue@{
        if (isFinishing || isDestroyed) return@queue
        val job = current
        if (job != null && Jobs.get(job.id) == null) {
            current = null
            next()
            if (current != null) snackbar(getString(R.string.gone, job.title))
            return@queue
        }
        val waiting = Jobs.waiting()
        val came = waiting.filter { it.order > seen }
        if (came.isNotEmpty()) {
            seen = came.maxOf { it.order }
            val text = if (came.size == 1) getString(R.string.came, came[0].title)
            else resources.getQuantityString(R.plurals.came_many, came.size, came.size)
            Snackbar.make(root, text, Snackbar.LENGTH_LONG).setAnchorView(tools)
                .setAction(R.string.show) { if (came.size == 1) switchTo(came[0]) else showQueue(queueButton) }
                .show()
        }
        refreshQueue()
    }

    private fun snackbar(text: String) = Snackbar.make(root, text, Snackbar.LENGTH_SHORT).setAnchorView(tools).show()

    /** The queue: tap a PDF to sign it now; or cancel them all. */
    private fun showQueue(anchor: View) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(12), dp(8), dp(8))
        }
        val popup = popup(box)
        box.addView(label(getString(R.string.queue_title), M.attr.textAppearanceTitleSmall, androidx.appcompat.R.attr.colorPrimary).apply {
            setPadding(dp(12), 0, dp(12), dp(8))
        })
        for (job in Jobs.waiting()) {
            val shown = job === current
            val started = if (shown) !view.isEmpty else !job.draft.isNullOrEmpty()
            val line = listOfNotNull(job.pc.takeIf { it.isNotEmpty() }, getString(R.string.started).takeIf { started })
            box.addView(row(R.drawable.ic_document, job.title, line.joinToString(" · ").ifEmpty { null },
                            oneLine = true).apply {
                setPadding(dp(12), dp(8), dp(16), dp(8))
                minimumWidth = dp(280)
                background = if (shown) surface(M.attr.colorSecondaryContainer, dp(16f)) else null
                foreground = ripple()
                setOnClickListener {
                    popup.dismiss()
                    switchTo(job)
                }
            })
        }
        box.addView(button(getString(R.string.cancel_all), Look.TEXT, R.drawable.ic_close) {
            popup.dismiss()
            askCancelAll()
        }.apply { layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = dp(4) } })
        showBelow(popup, box, anchor)
    }

    private fun askCancelAll() {
        val count = Jobs.waiting().size
        MaterialAlertDialogBuilder(this)
            .setTitle(resources.getQuantityString(R.plurals.cancel_all_title, count, count))
            .setMessage(R.string.cancel_all_detail)
            .setPositiveButton(R.string.cancel_all) { _, _ ->
                current = null
                Jobs.waiting().forEach { Jobs.finish(it.id, null) }
                finish()
            }
            .setNegativeButton(R.string.keep_signing, null)
            .show()
    }

    // -- the bars ----------------------------------------------------------------

    private fun buildTop() {
        titleText = label(null, M.attr.textAppearanceTitleMedium).apply {
            isSingleLine = true
            ellipsize = TextUtils.TruncateAt.MIDDLE
        }
        subtitle = label(null, M.attr.textAppearanceBodySmall, M.attr.colorOnSurfaceVariant).apply {
            isSingleLine = true
            ellipsize = TextUtils.TruncateAt.END
        }
        pageLabel = label(null, M.attr.textAppearanceLabelLarge, M.attr.colorOnSurfaceVariant).apply {
            setPadding(dp(2), 0, dp(2), 0)
        }
        pages = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = surface(M.attr.colorSurfaceContainerHigh)
            addView(iconButton(R.drawable.ic_chevron_left, R.string.previous_page) { view.showPage(view.index - 1) })
            addView(pageLabel)
            addView(iconButton(R.drawable.ic_chevron_right, R.string.next_page) { view.showPage(view.index + 1) })
        }
        queueButton = button("", Look.TONAL, R.drawable.ic_documents) { showQueue(it) }.apply {
            contentDescription = getString(R.string.queue_title)
            tooltipText = contentDescription
        }
        doneButton = button(getString(R.string.done), Look.FILLED, R.drawable.ic_check) { done() }
        top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(themeColor(M.attr.colorSurface))
            addView(iconButton(R.drawable.ic_close, R.string.cancel) { askCancel() })
            // The title gives way on a narrow screen, never Done.
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(8), 0, dp(8), 0)
                addView(titleText)
                addView(subtitle)
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(pages, gap())
            addView(queueButton, gap())
            addView(doneButton, gap())
        }
        compact()
    }

    private fun gap() = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
        ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) }

    /** On a phone, Done is its tick alone. */
    private fun compact() {
        val narrow = resources.configuration.screenWidthDp < 600
        doneButton.text = if (narrow) null else getString(R.string.done)
        doneButton.contentDescription = getString(R.string.done)
        doneButton.iconPadding = if (narrow) 0 else dp(8)
    }

    private fun buildTools() {
        penButton = PenDot().apply {
            contentDescription = getString(R.string.pen)
            tooltipText = contentDescription
            setOnClickListener { showPen(it) }
        }
        eraser = toolButton(R.drawable.ic_eraser, R.string.eraser) {
            view.eraserMode = !view.eraserMode
            view.selectMode = false
            refresh()
        }.apply { isCheckable = true }
        lassoButton = toolButton(R.drawable.ic_lasso, R.string.select) {
            view.selectMode = !view.selectMode
            view.eraserMode = false
            refresh()
        }.apply { isCheckable = true }
        undo = toolButton(R.drawable.ic_undo, R.string.undo) { view.undo() }
        // The first saved signature, one tap to place it; the chevron lists them all.
        signatureThumb = SignatureThumb(this, signature, penColor).apply {
            contentDescription = getString(R.string.my_signature)
            tooltipText = contentDescription
            setOnClickListener { placeSignature() }
        }
        val signatureButton = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = surface(M.attr.colorSurfaceContainerHighest)
            setPadding(dp(6), dp(4), 0, dp(4))
            addView(signatureThumb, LinearLayout.LayoutParams(dp(72), dp(36)))
            addView(toolButton(R.drawable.ic_more, R.string.my_signatures) { showSignatures(it) })
        }
        val toolRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(penButton, LinearLayout.LayoutParams(dp(48), dp(48)))
            addView(eraser)
            addView(lassoButton)
            addView(undo)
            addView(toolButton(R.drawable.ic_clear, R.string.clear_page) { view.clearPage() })
            addView(separator())
            addView(signatureButton)
            addView(toolButton(R.drawable.ic_date, R.string.date) { placeDate() })
            addView(toolButton(R.drawable.ic_text, R.string.text) { askText() })
            addView(separator())
            addView(toolButton(R.drawable.ic_fit, R.string.fit) { view.fit() })
        }
        // Material's floating toolbar; wider than some phones, it scrolls there.
        tools = HorizontalScrollView(this).apply {
            background = surface(M.attr.colorSurfaceContainer)
            elevation = dp(3f)
            clipToOutline = true
            setPadding(dp(8), dp(8), dp(8), dp(8))
            isHorizontalScrollBarEnabled = false
            isHorizontalFadingEdgeEnabled = true
            setFadingEdgeLength(dp(24))
            addView(toolRow)
        }
        keep = button(getString(R.string.keep_signature), Look.FILLED, R.drawable.ic_signature) { keepSignature() }.apply {
            elevation = dp(3f)
        }
        hintText = label(null, M.attr.textAppearanceBodyLarge, M.attr.colorOnPrimaryContainer)
        hint = pill().apply {
            addView(hintText)
            addView(iconButton(R.drawable.ic_close, R.string.cancel) {
                view.pending = null
                visibility = View.GONE
            }.tinted(M.attr.colorOnPrimaryContainer))
            visibility = View.GONE
        }
        selectionBar = pill().apply {
            addView(label(getString(R.string.selection_hint), M.attr.textAppearanceBodyLarge, M.attr.colorOnPrimaryContainer))
            addView(iconButton(R.drawable.ic_clear, R.string.delete) { view.deleteSelection() }
                .tinted(M.attr.colorOnPrimaryContainer))
            addView(iconButton(R.drawable.ic_check, R.string.deselect) { view.deselect() }
                .tinted(M.attr.colorOnPrimaryContainer))
            visibility = View.GONE
        }
    }

    /** A message over the page, in the theme's accent. */
    private fun pill() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        background = surface(M.attr.colorPrimaryContainer)
        elevation = dp(3f)
        setPadding(dp(20), dp(4), dp(4), dp(4))
    }

    private fun MaterialButton.tinted(color: Int) = apply {
        iconTint = ColorStateList.valueOf(themeColor(color))
    }

    private fun toolButton(icon: Int, description: Int, onClick: (View) -> Unit) =
        iconButton(icon, description, R.attr.plusToolbarButtonStyle, onClick)

    private fun separator() = View(this).apply {
        setBackgroundColor(themeColor(M.attr.colorOutlineVariant))
        layoutParams = LinearLayout.LayoutParams(dp(1), dp(24)).apply { setMargins(dp(6), 0, dp(6), 0) }
    }

    /** The bars clear of the system's, and the page fitted between them. */
    private fun placeChrome() {
        val m = dp(16)
        val gap = dp(12)
        top.setPadding(dp(4) + bars.left, dp(8) + bars.top, dp(12) + bars.right, dp(8))
        place(tools, bottom = m + bars.bottom, left = m + bars.left, right = m + bars.right)
        val chrome = m + bars.bottom + tools.height
        place(keep, bottom = chrome + gap)
        place(hint, top = top.height + gap)
        place(selectionBar, top = top.height + gap)
        chromeTop = top.height.toFloat()
        chromeBottom = (chrome + gap).toFloat()
        if (top.height > 0 && ::view.isInitialized && (view.topInset != chromeTop || view.bottomInset != chromeBottom)) {
            view.topInset = chromeTop
            view.bottomInset = chromeBottom
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
        if (!::view.isInitialized) return
        undo.isEnabled = view.canUndo
        eraser.isChecked = view.eraserMode
        lassoButton.isChecked = view.selectMode
        penButton.invalidate()
        signatureThumb.signature = signature
        signatureThumb.color = view.penColor
        signatureThumb.invalidate()
        // Offered once something is drawn and no signature is saved yet.
        keep.visibility = if (signature == null && view.pageStrokes.isNotEmpty()) View.VISIBLE else View.GONE
        pageLabel.text = getString(R.string.page_of, view.index + 1, view.pageCount)
        refreshQueue()
    }

    /** The queue button: how many PDFs wait, shown while there is more than this one. */
    private fun refreshQueue() {
        val count = Jobs.waiting().size
        queueButton.visibility = if (count > 1) View.VISIBLE else View.GONE
        queueButton.text = NumberFormat.getIntegerInstance().format(count)
    }

    // -- popups ------------------------------------------------------------------

    private fun popup(content: View): PopupWindow {
        val scroll = ScrollView(this).apply { addView(content) }
        return PopupWindow(scroll, ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, true).apply {
            setBackgroundDrawable(surface(M.attr.colorSurfaceContainer, dp(16f)))
            elevation = dp(3f)
        }
    }

    /** A popup over the toolbar, from the anchor's left end, as tall as there is room for. */
    private fun showAbove(popup: PopupWindow, content: ViewGroup, anchor: View) {
        measure(popup, content, IntArray(2).also { anchor.getLocationInWindow(it) }[1] - bars.top - dp(16))
        popup.showAsDropDown(anchor, 0, -(popup.height + anchor.height + dp(12)))
    }

    /** A popup under the top bar, from the anchor's right end. */
    private fun showBelow(popup: PopupWindow, content: ViewGroup, anchor: View) {
        val bottom = IntArray(2).also { anchor.getLocationInWindow(it) }[1] + anchor.height
        measure(popup, content, root.height - bottom - bars.bottom - dp(16))
        popup.showAsDropDown(anchor, 0, dp(8), Gravity.END)
    }

    /** The popup as big as its content, within the screen and room high
     * (it scrolls beyond); set before showing, so it lines up with the anchor. */
    private fun measure(popup: PopupWindow, content: ViewGroup, room: Int) {
        // Its rows fill it (MATCH_PARENT), which a LinearLayout leaves out when
        // it wraps its content: it is as wide as the widest row on its own.
        val free = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        val widest = content.children.maxOfOrNull { it.measure(free, free); it.measuredWidth } ?: 0
        val wide = minOf(root.width - bars.left - bars.right - dp(32), dp(MAX_POPUP))
        content.minimumWidth = minOf(widest + content.paddingLeft + content.paddingRight, wide)
        content.measure(View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.AT_MOST), View.MeasureSpec.UNSPECIFIED)
        popup.width = content.measuredWidth
        popup.height = minOf(content.measuredHeight, room)
    }

    private fun showPen(anchor: View) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(8))
        }
        val popup = popup(box)
        box.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            for ((name, color) in Ink.INKS) {
                addView(Swatch(color, color == view.penColor).apply {
                    contentDescription = getString(name)
                    tooltipText = contentDescription
                    setOnClickListener {
                        setPen(color = color)
                        popup.dismiss()
                    }
                }, LinearLayout.LayoutParams(dp(48), dp(48)))
            }
        })
        box.addView(label(getString(R.string.thickness), M.attr.textAppearanceLabelLarge, M.attr.colorOnSurfaceVariant).apply {
            setPadding(dp(4), dp(12), 0, 0)
        })
        box.addView(Slider(this).apply {
            valueFrom = 0f
            valueTo = 1f
            value = Ink.thickness(view.penWidth).coerceIn(0f, 1f)
            labelBehavior = LabelFormatter.LABEL_GONE
            addOnChangeListener { _, position, fromUser -> if (fromUser) setPen(width = Ink.penWidth(position)) }
        }, LinearLayout.LayoutParams(dp(264), ViewGroup.LayoutParams.WRAP_CONTENT))
        showAbove(popup, box, anchor)
    }

    private fun setPen(color: Int = view.penColor, width: Float = view.penWidth) {
        penColor = color
        penWidth = width
        view.penColor = color
        view.penWidth = width
        view.eraserMode = false
        view.selectMode = false
        Pen.save(this, color, width)
        refresh()
    }

    private fun arm(message: Int, place: (Float, Float) -> List<Mark>) {
        view.eraserMode = false
        view.selectMode = false
        view.deselect()
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

    /** The saved signatures: tap one to place it, the bin to remove it, or draw a new one. */
    private fun showSignatures(anchor: View) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(12), dp(8), dp(8))
        }
        val popup = popup(box)
        for (saved in Signatures.all(this)) {
            box.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(4), 0, dp(4))
                addView(SignatureThumb(this@SignActivity, saved, view.penColor).apply {
                    contentDescription = getString(R.string.place_signature)
                    setOnClickListener {
                        popup.dismiss()
                        Signatures.use(this@SignActivity, saved.id)
                        signatureChanged()
                        placeSignature()
                    }
                }, LinearLayout.LayoutParams(dp(200), dp(64)))
                addView(iconButton(R.drawable.ic_clear, R.string.remove) {
                    popup.dismiss()
                    askRemove(saved)
                })
            })
        }
        box.addView(button(getString(R.string.new_signature), Look.TEXT, R.drawable.ic_add) {
            popup.dismiss()
            newSignature()
        })
        showAbove(popup, box, anchor)
    }

    private fun askRemove(saved: Signature) {
        MaterialAlertDialogBuilder(this)
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
        val entry = TextInputEditText(this).apply {
            isSingleLine = true
            imeOptions = EditorInfo.IME_ACTION_DONE
        }
        val field = FrameLayout(this).apply {
            setPadding(dp(24), dp(8), dp(24), 0)
            addView(TextInputLayout(context).apply {
                hint = getString(R.string.text_to_add)
                addView(entry)
            })
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.text)
            .setView(field)
            .setPositiveButton(R.string.place) { _, _ ->
                val text = entry.text.toString().trim()
                if (text.isNotEmpty()) arm(R.string.tap_text) { x, y -> listOf(TextMark(x, y, text, color = view.penColor)) }
            }
            .setNegativeButton(R.string.cancel, null)
            .create()
        entry.setOnEditorActionListener { _, _, _ ->
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).performClick()
            true
        }
        dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE)
        dialog.show()
        entry.requestFocus()
    }

    private fun keepSignature() {
        view.deselect()
        Signatures.add(this, Signature.of(view.pageStrokes) ?: return)
        signatureChanged()
        snackbar(getString(R.string.signature_kept))
    }

    // -- closing -----------------------------------------------------------------

    private fun done() {
        view.deselect()  // the selection goes back on its page, as it is
        answer(resultJson())
    }

    /** Close, Back: this PDF is not signed; asks first when something was drawn. */
    private fun askCancel() {
        if (view.isEmpty) return answer(null)
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.discard_title)
            .setMessage(R.string.discard_detail)
            .setPositiveButton(R.string.discard) { _, _ -> answer(null) }
            .setNegativeButton(R.string.keep_signing, null)
            .show()
    }

    /** The marks as the PC's plusone/pdf.py takes them, page by page, and the pen. */
    private fun resultJson(): JSONObject {
        val pagesJson = JSONArray()
        for ((page, list) in view.marks.toSortedMap()) {
            if (list.isEmpty()) continue
            pagesJson.put(JSONObject().put("page", page).put("marks", JSONArray(list.map { it.toJson() })))
        }
        return JSONObject().put("pages", pagesJson)
            .put("pen", JSONObject().put("color", Ink.colorJson(view.penColor)).put("width", view.penWidth.toDouble()))
    }

    // Gone from the screen (recreated, or swiped away): the PDFs stay in
    // the queue, the PC waits, and what was drawn is there when it opens again.
    override fun onDestroy() {
        Jobs.unlisten(queueChanged)
        leave()
        super.onDestroy()
    }

    // -- widgets -------------------------------------------------------------

    /** The pen's colour and thickness, on a light disc so dark inks show on a dark bar. */
    private inner class PenDot : View(this@SignActivity) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = dp(1f)
            color = themeColor(M.attr.colorOutlineVariant)
        }

        init {
            background = ripple(borderless = true)
        }

        override fun onDraw(canvas: Canvas) {
            val r = minOf(width, height) / 2f
            paint.color = android.graphics.Color.rgb(246, 246, 248)
            canvas.drawCircle(width / 2f, height / 2f, r * 0.66f, paint)
            canvas.drawCircle(width / 2f, height / 2f, r * 0.66f, ring)
            paint.color = view.penColor
            canvas.drawCircle(width / 2f, height / 2f, r * (0.14f + 0.36f * Ink.thickness(view.penWidth)), paint)
        }
    }

    /** A colour to pick, ringed when it is the pen's. */
    private inner class Swatch(private val color: Int, private val chosen: Boolean) : View(this@SignActivity) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)

        init {
            background = ripple(borderless = true)
        }

        override fun onDraw(canvas: Canvas) {
            val r = minOf(width, height) / 2f
            paint.style = Paint.Style.FILL
            paint.color = color
            canvas.drawCircle(width / 2f, height / 2f, r * 0.58f, paint)
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = dp(1f)
            paint.color = themeColor(M.attr.colorOutlineVariant)
            canvas.drawCircle(width / 2f, height / 2f, r * 0.58f, paint)
            if (chosen) {
                paint.strokeWidth = dp(2.5f)
                paint.color = themeColor(androidx.appcompat.R.attr.colorPrimary)
                canvas.drawCircle(width / 2f, height / 2f, r * 0.84f, paint)
            }
        }
    }

    companion object {
        const val MAX_POPUP = 480  // dp
        const val EXTRA_JOB = "job"
        const val EXTRA_CHOSEN = "chosen"
        private const val STATE_JOB = "job"

        /** The intent that opens the signing screen: on job (or the first waiting);
         * chosen: switch to it even if another is shown. */
        fun open(context: Context, job: String?, chosen: Boolean = false): Intent =
            Intent(context, SignActivity::class.java)
                .putExtra(EXTRA_JOB, job)
                .putExtra(EXTRA_CHOSEN, chosen)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}
