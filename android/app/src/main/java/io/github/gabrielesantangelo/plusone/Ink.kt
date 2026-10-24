package io.github.gabrielesantangelo.plusone

import android.graphics.Color
import android.graphics.RectF
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Ink as the PC keeps it (plusone/pdf.py): strokes in page points, the
 * origin at the top left of the page as shown (crop box, rotation
 * applied), y downwards; a width at full pressure, and a colour.
 */
object Ink {
    val COLOR = Color.rgb(13, 26, 89)   // plusone/pdf.py INK (0.05, 0.1, 0.35)
    const val WIDTH = 1.6f               // points at full pressure (pdf.py PEN_WIDTH)
    private const val FULL_PRESSURE = 0.6f

    /** pdf.py ink_width: from a hairline for a light touch to the full width. */
    fun width(width: Float, pressure: Float) =
        width * (0.1f + 0.9f * min(1f, max(0f, pressure) / FULL_PRESSURE))

    fun distanceToSegment(px: Float, py: Float, ax: Float, ay: Float, bx: Float, by: Float): Float {
        val dx = bx - ax
        val dy = by - ay
        val length = dx * dx + dy * dy
        val t = if (length == 0f) 0f else ((px - ax) * dx + (py - ay) * dy) / length
        val c = min(1f, max(0f, t))
        return hypot(px - (ax + c * dx), py - (ay + c * dy))
    }
}

/** x, y, pressure triples in page points, and the box its ink stays in. */
class Stroke(val points: FloatArray, val width: Float = Ink.WIDTH, val color: Int = Ink.COLOR) {
    val bounds = RectF(points[0], points[1], points[0], points[1]).also { box ->
        for (i in points.indices step 3) box.union(points[i], points[i + 1])
        box.inset(-width, -width)
    }

    /** Whether an eraser of this radius going from a to b touches the stroke. */
    fun touches(ax: Float, ay: Float, bx: Float, by: Float, radius: Float): Boolean {
        val path = RectF(min(ax, bx) - radius, min(ay, by) - radius, max(ax, bx) + radius, max(ay, by) + radius)
        if (!RectF.intersects(bounds, path)) return false
        for (i in points.indices step 3) {
            if (Ink.distanceToSegment(points[i], points[i + 1], ax, ay, bx, by) <=
                radius + Ink.width(width, points[i + 2]) / 2) return true
        }
        return false
    }
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
