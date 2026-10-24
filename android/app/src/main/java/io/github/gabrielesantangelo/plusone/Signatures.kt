package io.github.gabrielesantangelo.plusone

import android.app.Dialog
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.GradientDrawable
import android.util.AtomicFile
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

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
        return (0 until list.length()).mapNotNull { list.optJSONObject(it)?.let(Signature::fromJson) } to
            (0 until removed.length()).mapNotNull { removed.opt(it) as? String }
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
 */
class SignaturePad(context: Context, private val color: Int, private val pen: Float,
                   private val onSaved: (Signature) -> Unit) : Dialog(context) {
    private val pad = PadView(context)
    private val density = context.resources.displayMetrics.density

    init {
        val save = button(context.getString(R.string.save), accent = true) {
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
        val again = android.widget.ImageButton(context).apply {
            setImageResource(R.drawable.ic_clear)
            contentDescription = context.getString(R.string.start_over)
            tooltipText = contentDescription
            background = null
            minimumWidth = (48 * density).toInt()
            minimumHeight = (48 * density).toInt()
            setOnClickListener { pad.clear() }
        }
        save.isEnabled = false
        save.alpha = 0.4f
        pad.onChange = {
            val any = pad.strokes.isNotEmpty()
            save.isEnabled = any
            save.alpha = if (any) 1f else 0.4f
        }
        pad.color = color
        pad.pen = pen
        val box = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val p = (20 * density).toInt()
            setPadding(p, p, p, p)
            background = GradientDrawable().apply { setColor(Color.rgb(44, 44, 48)); cornerRadius = 24 * density }
            addView(TextView(context).apply {
                setText(R.string.new_signature)
                setTextColor(Color.WHITE)
                textSize = 20f
                setPadding(0, 0, 0, (12 * density).toInt())
            })
            addView(pad, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0))
            addView(TextView(context).apply {
                setText(R.string.sign_here)
                setTextColor(Color.LTGRAY)
                gravity = Gravity.CENTER
                setPadding(0, (8 * density).toInt(), 0, (8 * density).toInt())
            })
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(again)
                addView(View(context), LinearLayout.LayoutParams(0, 1, 1f))
                addView(button(context.getString(R.string.cancel)) { dismiss() })
                addView(save)
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

    private fun button(label: String, accent: Boolean = false, onClick: () -> Unit) = Button(context).apply {
        text = label
        isAllCaps = false
        isSingleLine = true
        setTextColor(Color.WHITE)
        background = GradientDrawable().apply {
            setColor(if (accent) Color.rgb(53, 132, 228) else Color.TRANSPARENT)
            cornerRadius = 20 * density
        }
        setPadding((18 * density).toInt(), 0, (18 * density).toInt(), 0)
        setOnClickListener { onClick() }
    }

    /** White card with a line to sign on. */
    private class PadView(context: Context) : View(context) {
        val strokes = mutableListOf<FloatArrayBuilder>()
        var color = Ink.COLOR
        var pen = Ink.WIDTH
        var onChange: () -> Unit = {}
        private var current: FloatArrayBuilder? = null
        private var penSeen = false
        private val density = resources.displayMetrics.density
        private val card = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
        private val line = Paint().apply { color = Color.argb(46, 0, 0, 0); strokeWidth = density }
        private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
        private val box = RectF()

        override fun performClick(): Boolean = super.performClick()

        fun clear() {
            strokes.clear()
            invalidate()
            onChange()
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            val stylus = event.getToolType(0).let {
                it == MotionEvent.TOOL_TYPE_STYLUS || it == MotionEvent.TOOL_TYPE_ERASER
            }
            if (stylus) penSeen = true else if (penSeen) return true  // a palm, once the pen wrote here
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    requestUnbufferedDispatch(event)
                    current = FloatArrayBuilder().also { strokes.add(it) }
                    add(event, stylus)
                }
                MotionEvent.ACTION_MOVE -> add(event, stylus)
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    add(event, stylus)
                    current = null
                    onChange()
                    if (event.actionMasked == MotionEvent.ACTION_UP) performClick()
                }
            }
            invalidate()
            return true
        }

        private fun add(event: MotionEvent, stylus: Boolean) {
            val stroke = current ?: return
            for (h in 0 until event.historySize) {
                stroke.add(event.getHistoricalX(h), event.getHistoricalY(h),
                    if (stylus) event.getHistoricalPressure(h) else NO_PRESSURE)
            }
            stroke.add(event.x, event.y, if (stylus) event.pressure else NO_PRESSURE)
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
        }
    }

    companion object {
        const val PAD_POINTS = 280f  // the pad's width in page points: twice a placed signature
        const val NO_PRESSURE = 0.35f  // a finger draws a medium line (as a mouse on the PC)
    }
}
