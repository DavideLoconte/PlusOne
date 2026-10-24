package io.github.gabrielesantangelo.plusone

import android.content.Context
import android.graphics.Color
import android.graphics.RectF
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Ink as the PC keeps it (plusone/pdf.py, plusone/ink.py): marks in page
 * points, the origin at the top left of the page as shown (crop box,
 * rotation applied), y downwards.
 */
object Ink {
    val COLOR = rgb(0.05, 0.1, 0.35)     // pdf.py INK
    const val WIDTH = 1.6f               // points at full pressure (pdf.py PEN_WIDTH)
    const val MIN_WIDTH = 0.3f           // ink.py MIN_WIDTH, MAX_WIDTH
    const val MAX_WIDTH = 6.0f
    const val SIGNATURE_WIDTH = 140f     // ink.py: a placed signature is about 5 cm wide
    const val TEXT_SIZE = 11f            // pdf.py Text.size
    private const val FULL_PRESSURE = 0.6f

    /** The PC's inks (plusone/sign.py), name resource and colour. */
    val INKS = listOf(
        R.string.ink_blue_ink to COLOR,
        R.string.ink_black to rgb(0.08, 0.08, 0.1),
        R.string.ink_blue to rgb(0.1, 0.3, 0.85),
        R.string.ink_red to rgb(0.8, 0.1, 0.1),
        R.string.ink_green to rgb(0.1, 0.5, 0.2),
    )

    /** pdf.py ink_width: from a hairline for a light touch to the full width. */
    fun width(width: Float, pressure: Float) =
        width * (0.1f + 0.9f * min(1f, max(0f, pressure) / FULL_PRESSURE))

    /** ink.py thickness / pen_width: the slider's thin end gets most of its travel. */
    fun thickness(width: Float) = sqrt(max(0f, min(1f, (width - MIN_WIDTH) / (MAX_WIDTH - MIN_WIDTH))))
    fun penWidth(position: Float) = MIN_WIDTH + (MAX_WIDTH - MIN_WIDTH) * position * position

    fun rgb(r: Double, g: Double, b: Double) =
        Color.rgb((r * 255).toInt(), (g * 255).toInt(), (b * 255).toInt())

    fun colorJson(color: Int) =
        JSONArray(listOf(Color.red(color) / 255.0, Color.green(color) / 255.0, Color.blue(color) / 255.0))

    fun colorOf(json: JSONArray?, default: Int = COLOR) =
        if (json == null || json.length() != 3) default
        else rgb(json.optDouble(0), json.optDouble(1), json.optDouble(2))

    fun distanceToSegment(px: Float, py: Float, ax: Float, ay: Float, bx: Float, by: Float): Float {
        val dx = bx - ax
        val dy = by - ay
        val length = dx * dx + dy * dy
        val t = if (length == 0f) 0f else ((px - ax) * dx + (py - ay) * dy) / length
        val c = min(1f, max(0f, t))
        return hypot(px - (ax + c * dx), py - (ay + c * dy))
    }
}

/** Something on a page: a stroke or a piece of text. */
sealed interface Mark {
    val bounds: RectF
    /** Whether an eraser of this radius going from a to b touches it. */
    fun touches(ax: Float, ay: Float, bx: Float, by: Float, radius: Float): Boolean
    fun toJson(): JSONObject
}

/** x, y, pressure triples in page points. */
class Stroke(val points: FloatArray, val width: Float = Ink.WIDTH, val color: Int = Ink.COLOR) : Mark {
    override val bounds = RectF(points[0], points[1], points[0], points[1]).also { box ->
        for (i in points.indices step 3) box.union(points[i], points[i + 1])
        box.inset(-width, -width)
    }

    override fun touches(ax: Float, ay: Float, bx: Float, by: Float, radius: Float): Boolean {
        val path = RectF(min(ax, bx) - radius, min(ay, by) - radius, max(ax, bx) + radius, max(ay, by) + radius)
        if (!RectF.intersects(bounds, path)) return false
        for (i in points.indices step 3) {
            if (Ink.distanceToSegment(points[i], points[i + 1], ax, ay, bx, by) <=
                radius + Ink.width(width, points[i + 2]) / 2) return true
        }
        return false
    }

    override fun toJson(): JSONObject = JSONObject()
        .put("type", "stroke")
        .put("width", width.toDouble())
        .put("color", Ink.colorJson(color))
        .put("points", JSONArray().also { a -> points.forEach { a.put(it.toDouble()) } })
}

/** Text from its baseline's left end, as pdf.py's Text. */
class TextMark(val x: Float, val y: Float, val text: String, val size: Float = Ink.TEXT_SIZE,
               val color: Int = Ink.COLOR) : Mark {
    // pdf.py draws it in "Sans"; glyphs average about half an em (ink.py touches).
    override val bounds = RectF(x, y - size, x + 0.55f * size * text.length, y + 0.25f * size)

    override fun touches(ax: Float, ay: Float, bx: Float, by: Float, radius: Float): Boolean {
        val box = RectF(bounds).apply { inset(-radius, -radius) }
        return box.contains(ax, ay) || box.contains(bx, by)
    }

    override fun toJson(): JSONObject = JSONObject()
        .put("type", "text").put("x", x.toDouble()).put("y", y.toDouble())
        .put("text", text).put("size", size.toDouble()).put("color", Ink.colorJson(color))
}

/** A saved signature as ink.py normalize() keeps it: top-left at 0,0, width 1,
 * and the width in points it was drawn at. */
class Signature(val width: Float, val strokes: List<FloatArray>) {

    /** ink.py place(): its left end and vertical centre at x, y, with the pen's colour. */
    fun place(x: Float, y: Float, pen: Float, color: Int, size: Float = Ink.SIGNATURE_WIDTH): List<Stroke> {
        val height = strokes.maxOf { s -> (1 until s.size step 3).maxOfOrNull { s[it] } ?: 0f } * size
        // A small signature keeps a thin line: the pen scales with it.
        val scaled = max(Ink.MIN_WIDTH, min(pen * size / width, pen * 1.5f))
        return strokes.map { s ->
            FloatArray(s.size) { i ->
                when (i % 3) {
                    0 -> x + s[i] * size
                    1 -> y - height / 2 + s[i] * size
                    else -> s[i]
                }
            }.let { Stroke(it, scaled, color) }
        }
    }

    fun toJson(): JSONObject = JSONObject().put("width", width.toDouble()).put("strokes", JSONArray().also { list ->
        for (s in strokes) list.put(JSONArray().also { pts ->
            for (i in s.indices step 3) pts.put(JSONArray(listOf(s[i].toDouble(), s[i + 1].toDouble(), s[i + 2].toDouble())))
        })
    })

    companion object {
        /** ink.py normalize(). */
        fun of(marks: List<Stroke>): Signature? {
            if (marks.isEmpty()) return null
            val x0 = marks.minOf { it.bounds.left + it.width }
            val y0 = marks.minOf { it.bounds.top + it.width }
            val width = (marks.maxOf { it.bounds.right - it.width } - x0).takeIf { it > 0f } ?: 1f
            return Signature(width, marks.map { stroke ->
                FloatArray(stroke.points.size) { i ->
                    when (i % 3) {
                        0 -> (stroke.points[i] - x0) / width
                        1 -> (stroke.points[i] - y0) / width
                        else -> stroke.points[i]
                    }
                }
            })
        }

        fun fromJson(json: JSONObject?): Signature? {
            val list = json?.optJSONArray("strokes") ?: return null
            val strokes = (0 until list.length()).mapNotNull { i ->
                val pts = list.optJSONArray(i) ?: return@mapNotNull null
                FloatArray(pts.length() * 3) { k -> pts.getJSONArray(k / 3).getDouble(k % 3).toFloat() }
                    .takeIf { it.isNotEmpty() }
            }
            return if (strokes.isEmpty()) null else Signature(json.optDouble("width", 1.0).toFloat(), strokes)
        }
    }
}

/** The pen last chosen, kept on the tablet (the PC's arrives with each PDF). */
object Pen {
    private const val PREFS = "pen"

    fun load(context: Context): Pair<Int, Float> = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).let {
        it.getInt("color", Ink.COLOR) to it.getFloat("width", Ink.WIDTH)
    }

    fun save(context: Context, color: Int, width: Float) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putInt("color", color).putFloat("width", width).apply()
}

/** A growable FloatArray: strokes take thousands of samples. */
class FloatArrayBuilder {
    private var data = FloatArray(3 * 256)
    var size = 0
        private set

    fun add(x: Float, y: Float, p: Float) {
        if (size + 3 > data.size) data = data.copyOf(data.size * 2)
        data[size] = x
        data[size + 1] = y
        data[size + 2] = p
        size += 3
    }

    fun toArray(): FloatArray = data.copyOf(size)
}
