package io.github.gabrielesantangelo.plusone

import android.app.Dialog
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PointF
import android.graphics.RectF
import android.util.AtomicFile
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import com.google.android.material.button.MaterialButton
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import com.google.android.material.R as M

/**
 * The saved signatures, as plusone/signatures.py keeps the PC's: the order
 * is this tablet's, the most recently used first ("Signature" places the
 * first), and removed ids are remembered. Each job brings the PC's list and
 * takes this one back, and both sides merge: every signature either side
 * has, except those either side removed.
 */
object Signatures {
    private const val FILE = "signatures.json"
    private const val MAX = 50
    private const val KEEP_REMOVED = 500

    @Synchronized
    fun all(context: Context): List<Signature> = load(context).first

    /** Keeps a new signature, first in the list. */
    @Synchronized
    fun add(context: Context, signature: Signature) {
        val (list, removed) = load(context)
        save(context, listOf(signature) + list, removed)
    }

    @Synchronized
    fun remove(context: Context, id: String) {
        val (list, removed) = load(context)
        save(context, list.filter { it.id != id }, removed + id)
    }

    /** Moves it to the front: "Signature" places it next time. */
    @Synchronized
    fun use(context: Context, id: String) {
        val (list, removed) = load(context)
        val chosen = list.firstOrNull { it.id == id } ?: return
        save(context, listOf(chosen) + list.filter { it.id != id }, removed)
    }

    /** The PC's list ({"signatures", "removed"}) merged into this one. */
    @Synchronized
    fun merge(context: Context, theirs: JSONObject) {
        val (list, removed) = load(context)
        val (theirList, theirRemoved) = parse(theirs)
        val gone = (removed + theirRemoved).distinct()
        val known = list.map { it.id }.toSet()
        save(context, list.filter { it.id !in gone } + theirList.filter { it.id !in gone && it.id !in known }, gone)
    }

    /** This list as a job's answer takes it to the PC. */
    @Synchronized
    fun toJson(context: Context): JSONObject {
        val (list, removed) = load(context)
        return JSONObject().put("signatures", JSONArray(list.map { it.toJson() })).put("removed", JSONArray(removed))
    }

    private fun file(context: Context) = AtomicFile(File(context.filesDir, FILE))

    private fun load(context: Context): Pair<List<Signature>, List<String>> = try {
        parse(JSONObject(String(file(context).readFully())))
    } catch (e: Exception) {
        emptyList<Signature>() to emptyList()
    }

    private fun parse(json: JSONObject): Pair<List<Signature>, List<String>> {
        val list = json.optJSONArray("signatures") ?: JSONArray()
        val removed = json.optJSONArray("removed") ?: JSONArray()
        // One of each, short ids, no more than are kept anyway.
        return (0 until list.length()).mapNotNull { list.optJSONObject(it)?.let(Signature::fromJson) }
            .distinctBy { it.id }.take(MAX) to
            (0 until removed.length()).mapNotNull { (removed.opt(it) as? String)?.takeIf { s -> s.length in 1..64 } }
                .distinct().takeLast(KEEP_REMOVED)
    }

    private fun save(context: Context, list: List<Signature>, removed: List<String>) {
        val json = JSONObject()
            .put("signatures", JSONArray(list.take(MAX).map { it.toJson() }))
            .put("removed", JSONArray(removed.takeLast(KEEP_REMOVED)))
        val file = file(context)
        val out = file.startWrite()
        try {
            out.write(json.toString().toByteArray())
            file.finishWrite(out)
        } catch (e: Exception) {
            file.failWrite(out)
            throw e
        }
    }
}

/** A signature fitted into the view on a white card, in the pen's colour. */
class SignatureThumb(context: Context, var signature: Signature?, var color: Int) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeCap = Paint.Cap.ROUND
        style = Paint.Style.STROKE
    }
    private val card = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
    private val density = resources.displayMetrics.density
    private val box = RectF()

    override fun onDraw(canvas: Canvas) {
        val radius = 8 * density
        box.set(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawRoundRect(box, radius, radius, card)
        val shown = signature ?: return
        val margin = 6 * density
        val tall = shown.strokes.maxOf { s -> (1 until s.size step 3).maxOfOrNull { s[it] } ?: 0f }.coerceAtLeast(0.01f)
        val scale = minOf(width - 2 * margin, (height - 2 * margin) / tall)
        val ox = (width - scale) / 2
        val oy = (height - tall * scale) / 2
        paint.color = color
        for (s in shown.strokes) {
            for (i in 0 until s.size - 3 step 3) {
                paint.strokeWidth = Ink.width(1.8f * density, (s[i + 2] + s[i + 5]) / 2)
                canvas.drawLine(ox + s[i] * scale, oy + s[i + 1] * scale, ox + s[i + 3] * scale, oy + s[i + 4] * scale, paint)
            }
        }
    }
}

/**
 * Draws a new signature, with the pen (pressure shows) or a finger, and
 * keeps it; onSaved then. Once the pen touched it, fingers are ignored.
 * The pen's side button, its eraser end or the eraser button erase whole
 * strokes, as on the page.
 */
class SignaturePad(context: Context, private val color: Int, private val pen: Float,
                   private val onSaved: (Signature) -> Unit) : Dialog(context) {
    private val pad = PadView(context)
    private val density = context.resources.displayMetrics.density

    init {
        val save = context.button(context.getString(R.string.save), Look.FILLED) {
            val scale = pad.width / PAD_POINTS
            val strokes = pad.strokes.map { s ->
                val pts = s.toArray()
                for (i in pts.indices) if (i % 3 != 2) pts[i] /= scale
                Stroke(pts, pen, color)
            }
            Signature.of(strokes)?.let {
                Signatures.add(context, it)
                onSaved(it)
            }
            dismiss()
        }
        val again = context.iconButton(R.drawable.ic_clear, R.string.start_over) { pad.clear() }
        val eraser = context.iconButton(R.drawable.ic_eraser, R.string.eraser,
            M.attr.materialIconButtonFilledTonalStyle) { pad.eraserMode = (it as MaterialButton).isChecked }
        eraser.isCheckable = true
        save.isEnabled = false
        pad.onChange = { save.isEnabled = pad.strokes.isNotEmpty() }
        pad.color = color
        pad.pen = pen
        val box = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(context.dp(24), context.dp(24), context.dp(24), context.dp(16))
            background = context.surface(M.attr.colorSurfaceContainerHigh)
            addView(context.label(context.getString(R.string.new_signature), M.attr.textAppearanceHeadlineSmall).apply {
                setPadding(0, 0, 0, context.dp(16))
            })
            addView(pad, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0))
            addView(context.label(context.getString(R.string.sign_here), M.attr.textAppearanceBodyMedium,
                M.attr.colorOnSurfaceVariant).apply {
                gravity = Gravity.CENTER
                setPadding(0, context.dp(8), 0, context.dp(8))
            })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(again)
                addView(eraser)
                addView(View(context), LinearLayout.LayoutParams(0, 1, 1f))
                addView(context.button(context.getString(R.string.cancel), Look.TEXT) { dismiss() })
                addView(save, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = context.dp(8) })
            })
        }
        setContentView(box)
        setCanceledOnTouchOutside(false)
        window?.setBackgroundDrawableResource(android.R.color.transparent)
    }

    override fun onStart() {
        super.onStart()
        // As wide as fits, up to about a sheet of paper's width; the pad 5:2,
        // or less where the screen is short.
        val metrics = context.resources.displayMetrics
        val width = minOf(metrics.widthPixels - (24 * density).toInt(), (760 * density).toInt())
        pad.layoutParams = pad.layoutParams.apply { height = minOf(width * 2 / 5, metrics.heightPixels / 2) }
        window?.setLayout(width, ViewGroup.LayoutParams.WRAP_CONTENT)
        window?.setGravity(Gravity.CENTER)
    }

    /** White card with a line to sign on. */
    private class PadView(context: Context) : View(context) {
        val strokes = mutableListOf<FloatArrayBuilder>()
        var color = Ink.COLOR
        var pen = Ink.WIDTH
        var onChange: () -> Unit = {}
        var eraserMode = false
        private var current: FloatArrayBuilder? = null
        private var penSeen = false
        private var eraser: PointF? = null  // where the eraser is while it is down
        private val density = resources.displayMetrics.density
        private val card = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
        private val line = Paint().apply { color = Color.argb(46, 0, 0, 0); strokeWidth = density }
        private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
        private val box = RectF()
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 1.5f * density
            color = Color.argb(200, 80, 80, 80)
        }

        override fun performClick(): Boolean = super.performClick()

        fun clear() {
            strokes.clear()
            invalidate()
            onChange()
        }

        private var active = -1  // the pointer drawing (or erasing): the pen's, once there is one

        private fun isPen(e: MotionEvent, i: Int) = e.getToolType(i).let {
            it == MotionEvent.TOOL_TYPE_STYLUS || it == MotionEvent.TOOL_TYPE_ERASER
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            val action = event.actionMasked
            if (action == MotionEvent.ACTION_DOWN || action == MotionEvent.ACTION_POINTER_DOWN) {
                val i = event.actionIndex
                val pen = isPen(event, i)
                if (pen) penSeen = true
                // The pen takes over from a palm that landed first; once the
                // pen wrote here, fingers are palms.
                val palmDrawing = active >= 0 && event.findPointerIndex(active).let { it >= 0 && !isPen(event, it) }
                if ((active < 0 && (pen || !penSeen)) || (pen && palmDrawing)) {
                    if (active >= 0) finish()
                    begin(event, i)
                }
                invalidate()
                return true
            }
            val i = event.findPointerIndex(active)
            if (i < 0) return true
            when (action) {
                MotionEvent.ACTION_MOVE -> if (eraser != null) erase(event.getX(i), event.getY(i)) else add(event, i)
                MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP, MotionEvent.ACTION_CANCEL -> {
                    if (action == MotionEvent.ACTION_POINTER_UP && event.actionIndex != i) return true
                    if (eraser == null && action != MotionEvent.ACTION_CANCEL) add(event, i)
                    finish()
                    if (action == MotionEvent.ACTION_UP) performClick()
                }
            }
            invalidate()
            return true
        }

        private fun begin(event: MotionEvent, i: Int) {
            active = event.getPointerId(i)
            requestUnbufferedDispatch(event)
            if (eraserMode || event.getToolType(i) == MotionEvent.TOOL_TYPE_ERASER ||
                event.isButtonPressed(MotionEvent.BUTTON_STYLUS_PRIMARY) ||
                event.isButtonPressed(MotionEvent.BUTTON_STYLUS_SECONDARY)) {
                eraser = PointF(event.getX(i), event.getY(i))
                erase(event.getX(i), event.getY(i))
            } else {
                current = FloatArrayBuilder().also { strokes.add(it) }
                add(event, i)
            }
        }

        private fun finish() {
            active = -1
            current = null
            eraser = null
            onChange()
        }

        /** Removes the strokes the eraser touches on its way to x, y. */
        private fun erase(x: Float, y: Float) {
            val from = eraser ?: return
            val radius = ERASER_RADIUS * density
            val reach = radius + pen * width / PAD_POINTS / 2
            strokes.removeAll { builder ->
                val s = builder.toArray()
                (s.indices step 3).any { k ->
                    Ink.distanceToSegment(s[k], s[k + 1], from.x, from.y, x, y) <= reach
                }
            }
            from.set(x, y)
        }

        private fun add(event: MotionEvent, i: Int) {
            val stroke = current ?: return
            val stylus = isPen(event, i)
            for (h in 0 until event.historySize) {
                stroke.add(event.getHistoricalX(i, h), event.getHistoricalY(i, h),
                    if (stylus) event.getHistoricalPressure(i, h) else NO_PRESSURE)
            }
            stroke.add(event.getX(i), event.getY(i), if (stylus) event.getPressure(i) else NO_PRESSURE)
        }

        override fun onDraw(canvas: Canvas) {
            val radius = 12 * density
            box.set(0f, 0f, width.toFloat(), height.toFloat())
            canvas.drawRoundRect(box, radius, radius, card)
            val y = height * 0.72f
            canvas.drawLine(24 * density, y, width - 24 * density, y, line)
            val scale = width / PAD_POINTS
            ink.color = color
            for (builder in strokes) {
                val s = builder.toArray()
                if (s.size == 3) {
                    ink.style = Paint.Style.FILL
                    canvas.drawCircle(s[0], s[1], Ink.width(pen * scale, s[2]) / 2, ink)
                    continue
                }
                ink.style = Paint.Style.STROKE
                for (i in 0 until s.size - 3 step 3) {
                    ink.strokeWidth = Ink.width(pen * scale, (s[i + 2] + s[i + 5]) / 2)
                    canvas.drawLine(s[i], s[i + 1], s[i + 3], s[i + 4], ink)
                }
            }
            eraser?.let { canvas.drawCircle(it.x, it.y, ERASER_RADIUS * density, ring) }
        }
    }

    companion object {
        const val PAD_POINTS = 280f  // the pad's width in page points: twice a placed signature
        const val NO_PRESSURE = 0.35f  // a finger draws a medium line (as a mouse on the PC)
        const val ERASER_RADIUS = 12f  // dp around the nib
    }
}
