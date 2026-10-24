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
    /** The mark moved, turned and scaled by m (ink.py transform): the pen's
     * width and the text's size scale with it. */
    fun transformed(m: android.graphics.Matrix): Mark
    fun toJson(): JSONObject
}

/** How much m scales lengths, and how far it turns (radians). */
private fun scaleOf(m: android.graphics.Matrix): Float {
    val v = FloatArray(9).also { m.getValues(it) }
    return sqrt(kotlin.math.abs(v[0] * v[4] - v[1] * v[3]))
}

private fun angleOf(m: android.graphics.Matrix): Float {
    val v = FloatArray(9).also { m.getValues(it) }
    return kotlin.math.atan2(v[3], v[0])
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

    override fun transformed(m: android.graphics.Matrix): Mark {
        val moved = points.copyOf()
        val xy = FloatArray(2)
        for (i in points.indices step 3) {
            xy[0] = points[i]; xy[1] = points[i + 1]
            m.mapPoints(xy)
            moved[i] = xy[0]; moved[i + 1] = xy[1]
        }
        return Stroke(moved, width * scaleOf(m), color)
    }

    override fun toJson(): JSONObject = JSONObject()
        .put("type", "stroke")
        .put("width", width.toDouble())
        .put("color", Ink.colorJson(color))
        .put("points", JSONArray().also { a -> points.forEach { a.put(it.toDouble()) } })
}

/** Text from its baseline's left end, as pdf.py's Text; turned by angle
 * (radians, clockwise) about that point. */
class TextMark(val x: Float, val y: Float, val text: String, val size: Float = Ink.TEXT_SIZE,
               val color: Int = Ink.COLOR, val angle: Float = 0f) : Mark {
    // pdf.py draws it in "Sans"; glyphs average about half an em (ink.py touches).
    private val local = RectF(0f, -size, 0.55f * size * text.length, 0.25f * size)

    override val bounds = RectF().also { box ->
        val corners = floatArrayOf(local.left, local.top, local.right, local.top,
            local.right, local.bottom, local.left, local.bottom)
        android.graphics.Matrix().apply { setRotate(Math.toDegrees(angle.toDouble()).toFloat()); postTranslate(x, y) }
            .mapPoints(corners)
        box.set(corners[0], corners[1], corners[0], corners[1])
        for (i in corners.indices step 2) box.union(corners[i], corners[i + 1])
    }

    /** A page point in the text's own frame: its baseline from 0,0 along x. */
    private fun frame(px: Float, py: Float): Pair<Float, Float> {
        val c = kotlin.math.cos(angle)
        val s = kotlin.math.sin(angle)
        val dx = px - x
        val dy = py - y
        return (dx * c + dy * s) to (-dx * s + dy * c)
    }

    override fun touches(ax: Float, ay: Float, bx: Float, by: Float, radius: Float): Boolean {
        val box = RectF(local).apply { inset(-radius, -radius) }
        return listOf(frame(ax, ay), frame(bx, by)).any { (u, v) -> box.contains(u, v) }
    }

    override fun transformed(m: android.graphics.Matrix): Mark {
        val at = floatArrayOf(x, y).also { m.mapPoints(it) }
        return TextMark(at[0], at[1], text, size * scaleOf(m), color, angle + angleOf(m))
    }

    override fun toJson(): JSONObject = JSONObject()
        .put("type", "text").put("x", x.toDouble()).put("y", y.toDouble())
        .put("text", text).put("size", size.toDouble()).put("color", Ink.colorJson(color))
        .put("angle", angle.toDouble())
}

/** A saved signature as ink.py normalize() keeps it: top-left at 0,0, width 1,
 * and the width in points it was drawn at; with an id (plusone/signatures.py). */
class Signature(val width: Float, val strokes: List<FloatArray>,
                val id: String = java.util.UUID.randomUUID().toString().replace("-", "")) {

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

    fun toJson(): JSONObject = JSONObject().put("id", id).put("width", width.toDouble()).put("strokes", JSONArray().also { list ->
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

        /** null if it is not a signature with an id. */
        fun fromJson(json: JSONObject): Signature? = try {
            val id = json.getString("id").takeIf { it.isNotEmpty() && it.length <= 64 }
            val list = json.getJSONArray("strokes")
            val strokes = (0 until list.length()).map { i ->
                val pts = list.getJSONArray(i)
                FloatArray(pts.length() * 3) { k -> pts.getJSONArray(k / 3).getDouble(k % 3).toFloat() }
            }.filter { it.isNotEmpty() }
            val width = json.optDouble("width", 1.0).toFloat()
            if (id == null || strokes.isEmpty() || !(width > 0f)) null else Signature(width, strokes, id)
        } catch (e: org.json.JSONException) {
            null
        }
    }
}

/** What the system bars and a display cutout cover (the app is drawn behind them). */
fun systemBars(insets: android.view.WindowInsets): android.graphics.Insets =
    insets.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.displayCutout())

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
