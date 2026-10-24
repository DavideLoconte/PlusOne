package io.github.gabrielesantangelo.plusone

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max

/**
 * Marks picked to be moved, turned or scaled, the way note apps do it
 * (docs/FINDINGS.md): a box that turns with them, a handle at each corner
 * that scales from the opposite one, a turn handle outside the bottom-right
 * corner, and the inside to drag. In page points; matrix is what the marks
 * went through since they were picked, so repeated moves never blur them.
 */
class Selection(val page: Int, val originals: List<Mark>) {
    val matrix = Matrix()
    /** The marks' box when picked; the box shown is this through matrix. */
    val frame = RectF(originals.first().bounds).also { box -> originals.forEach { box.union(it.bounds) } }

    private var shown: List<Mark>? = null
    private val shownFor = Matrix()

    /** The marks as they are now. */
    val marks: List<Mark>
        get() {
            val cached = shown
            if (cached != null && shownFor == matrix) return cached
            shownFor.set(matrix)
            return originals.map { it.transformed(matrix) }.also { shown = it }
        }

    /** The box's corners in page points: top left, top right, bottom right, bottom left. */
    fun corners(): FloatArray = floatArrayOf(frame.left, frame.top, frame.right, frame.top,
        frame.right, frame.bottom, frame.left, frame.bottom).also { matrix.mapPoints(it) }

    /** How far it is turned, in degrees (-180..180]. */
    val angle: Float
        get() {
            val v = FloatArray(9).also { matrix.getValues(it) }
            return Math.toDegrees(atan2(v[Matrix.MSKEW_Y], v[Matrix.MSCALE_X]).toDouble()).toFloat()
        }
}

/** What a touch on the selection grabbed. */
sealed interface Grab {
    class Move(var x: Float, var y: Float) : Grab
    /** From a corner: the opposite corner stays, the marks keep their proportions. */
    class Scale(val anchorX: Float, val anchorY: Float, val fromX: Float, val fromY: Float, val start: Matrix) : Grab
    class Turn(val cx: Float, val cy: Float, val from: Float, val start: Matrix, val startAngle: Float) : Grab
}

/** Which marks a lasso (a closed polygon, page points x, y, ...) picks:
 * those its area touches, as note apps pick by default. */
object Lasso {
    fun picks(mark: Mark, polygon: FloatArray): Boolean {
        val box = RectF(polygon[0], polygon[1], polygon[0], polygon[1])
        for (i in polygon.indices step 2) box.union(polygon[i], polygon[i + 1])
        if (!RectF.intersects(box, mark.bounds)) return false
        return when (mark) {
            is Stroke -> {
                val p = mark.points
                (p.indices step 3).any { inside(polygon, p[it], p[it + 1]) } ||
                    (0 until p.size - 3 step 3).any { crosses(polygon, p[it], p[it + 1], p[it + 3], p[it + 4]) }
            }
            is TextMark -> {
                val b = mark.bounds
                inside(polygon, b.centerX(), b.centerY()) ||
                    (polygon.indices step 2).any { b.contains(polygon[it], polygon[it + 1]) } ||
                    crosses(polygon, b.left, b.top, b.right, b.bottom) || crosses(polygon, b.left, b.bottom, b.right, b.top)
            }
        }
    }

    /** Even-odd ray casting. */
    fun inside(polygon: FloatArray, x: Float, y: Float): Boolean {
        var result = false
        val n = polygon.size / 2
        var j = n - 1
        for (i in 0 until n) {
            val xi = polygon[2 * i]
            val yi = polygon[2 * i + 1]
            val xj = polygon[2 * j]
            val yj = polygon[2 * j + 1]
            if ((yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) result = !result
            j = i
        }
        return result
    }

    private fun crosses(polygon: FloatArray, ax: Float, ay: Float, bx: Float, by: Float): Boolean {
        val n = polygon.size / 2
        for (i in 0 until n) {
            val j = (i + 1) % n
            if (segmentsCross(ax, ay, bx, by, polygon[2 * i], polygon[2 * i + 1], polygon[2 * j], polygon[2 * j + 1])) {
                return true
            }
        }
        return false
    }

    private fun side(ax: Float, ay: Float, bx: Float, by: Float, cx: Float, cy: Float) =
        (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    private fun segmentsCross(ax: Float, ay: Float, bx: Float, by: Float,
                              cx: Float, cy: Float, dx: Float, dy: Float) =
        side(ax, ay, bx, by, cx, cy) * side(ax, ay, bx, by, dx, dy) < 0 &&
            side(cx, cy, dx, dy, ax, ay) * side(cx, cy, dx, dy, bx, by) < 0
}

/**
 * The selection on screen: its box (no smaller than MIN_SIDE, so the
 * handles never crowd), the corner handles and the turn handle, and the
 * lasso while it is drawn: #0B99FF lines, white handles.
 */
class SelectionLook(private val density: Float) {
    /** The box shown, screen pixels: 4 corners, then the turn handle. */
    class Shape(val quad: FloatArray, val turnX: Float, val turnY: Float)

    /** visible: where on screen a handle can be touched (not under a bar). */
    fun shape(corners: FloatArray, visible: RectF): Shape {
        val q = corners.copyOf()
        val minSide = MIN_SIDE * density
        // Widen a side shorter than minSide on both ends, along its own direction.
        grow(q, 0, 1, 3, 2, minSide, 1f, 0f)
        grow(q, 0, 3, 1, 2, minSide, 0f, 1f)
        // The turn handle: out of the bottom-right corner along both edges,
        // or of another corner when that is off screen or under a bar.
        val out = TURN_OFFSET * density
        val room = RectF(visible).apply { inset(TURN_RADIUS * density, TURN_RADIUS * density) }
        var first: Pair<Float, Float>? = null
        for (k in intArrayOf(2, 3, 1, 0)) {
            val prev = (k + 3) % 4
            val next = (k + 1) % 4
            val (ux, uy) = unit(q[2 * k] - q[2 * prev], q[2 * k + 1] - q[2 * prev + 1])
            val (vx, vy) = unit(q[2 * k] - q[2 * next], q[2 * k + 1] - q[2 * next + 1])
            val x = q[2 * k] + (ux + vx) * out
            val y = q[2 * k + 1] + (uy + vy) * out
            if (first == null) first = x to y
            if (room.contains(x, y)) return Shape(q, x, y)
        }
        return Shape(q, first!!.first, first.second)
    }

    /** Widens the opposite sides a-b and d-c (corner indices) to at least
     * minSide; a side of no length grows along the default direction. */
    private fun grow(q: FloatArray, a: Int, b: Int, d: Int, c: Int, minSide: Float, defaultX: Float, defaultY: Float) {
        val dx = q[2 * b] - q[2 * a]
        val dy = q[2 * b + 1] - q[2 * a + 1]
        val length = hypot(dx, dy)
        if (length >= minSide) return
        val ux = if (length > 0.01f) dx / length else defaultX
        val uy = if (length > 0.01f) dy / length else defaultY
        val half = (minSide - length) / 2
        q[2 * a] -= ux * half; q[2 * a + 1] -= uy * half
        q[2 * d] -= ux * half; q[2 * d + 1] -= uy * half
        q[2 * b] += ux * half; q[2 * b + 1] += uy * half
        q[2 * c] += ux * half; q[2 * c + 1] += uy * half
    }

    private fun unit(x: Float, y: Float): Pair<Float, Float> {
        val l = hypot(x, y)
        return if (l < 0.0001f) 0f to 0f else x / l to y / l
    }

    /** What screen point x, y grabs: a corner (0-3), the turn handle (4), inside (5), or nothing (-1). */
    fun hit(shape: Shape, x: Float, y: Float): Int {
        if (hypot(x - shape.turnX, y - shape.turnY) <= TURN_HIT * density) return 4
        val corner = (0 until 4).minByOrNull { hypot(x - shape.quad[2 * it], y - shape.quad[2 * it + 1]) }!!
        if (hypot(x - shape.quad[2 * corner], y - shape.quad[2 * corner + 1]) <= CORNER_HIT * density) return corner
        return if (Lasso.inside(shape.quad, x, y)) 5 else -1
    }

    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2 * density
        strokeCap = Paint.Cap.ROUND
        color = BLUE
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val icon = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.8f * density
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(255, 70, 49)
        textSize = 13 * density
    }
    private val dots = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3 * density
        strokeCap = Paint.Cap.ROUND
        color = BLUE
        pathEffect = DashPathEffect(floatArrayOf(2 * density, 10 * density), 0f)
    }
    private val dotsUnder = Paint(dots).apply {
        color = Color.argb(50, 255, 255, 255)
        pathEffect = DashPathEffect(floatArrayOf(2 * density, 10 * density), 6 * density)
    }
    private val path = Path()
    private val arc = RectF()

    /** active: the corner (0-3) or turn handle (4) being dragged, or -1; turned: the angle to show while turning. */
    fun draw(canvas: Canvas, shape: Shape, active: Int, turned: Float?) {
        val q = shape.quad
        for (i in 0 until 4) {
            val j = (i + 1) % 4
            canvas.drawLine(q[2 * i], q[2 * i + 1], q[2 * j], q[2 * j + 1], line)
        }
        val r = CORNER_RADIUS * density
        for (i in 0 until 4) {
            fill.color = if (i == active) BLUE else Color.WHITE
            canvas.drawCircle(q[2 * i], q[2 * i + 1], r, fill)
            canvas.drawCircle(q[2 * i], q[2 * i + 1], r, line)
        }
        // The turn handle: a round button with a turning arrow.
        val tr = TURN_RADIUS * density
        val turning = active == 4
        fill.color = if (turning) BLUE else Color.WHITE
        canvas.drawCircle(shape.turnX, shape.turnY, tr, fill)
        canvas.drawCircle(shape.turnX, shape.turnY, tr, line)
        icon.color = if (turning) Color.WHITE else BLUE
        val ir = tr * 0.48f
        arc.set(shape.turnX - ir, shape.turnY - ir, shape.turnX + ir, shape.turnY + ir)
        canvas.drawArc(arc, -60f, 280f, false, icon)
        val tipX = shape.turnX + ir * 0.5f
        val tipY = shape.turnY - ir * 0.866f
        path.reset()
        path.moveTo(tipX - ir * 0.55f, tipY - ir * 0.25f)
        path.lineTo(tipX, tipY)
        path.lineTo(tipX - ir * 0.15f, tipY + ir * 0.55f)
        canvas.drawPath(path, icon)
        if (turned != null) {
            canvas.drawText("%.1f°".format(((turned % 360) + 360) % 360), shape.turnX + tr + 6 * density,
                shape.turnY + label.textSize / 3, label)
        }
    }

    /** The lasso so far, screen points x, y, ..., closed as it will be. */
    fun drawLasso(canvas: Canvas, points: FloatArray) {
        if (points.size < 4) return
        path.reset()
        path.moveTo(points[0], points[1])
        for (i in 2 until points.size step 2) path.lineTo(points[i], points[i + 1])
        path.close()
        canvas.drawPath(path, dots)
        canvas.drawPath(path, dotsUnder)
    }

    companion object {
        val BLUE = Color.rgb(11, 153, 255)
        const val CORNER_RADIUS = 6f   // dp (a note app's: 10 px at 2x; a little bigger for fingers)
        const val CORNER_HIT = 22f     // dp
        const val TURN_RADIUS = 13f    // dp
        const val TURN_HIT = 24f       // dp
        const val TURN_OFFSET = 25f    // dp along each edge out of the corner
        const val MIN_SIDE = 60f       // dp: a smaller box is shown this big
        const val SNAP = 4f            // degrees from a right angle that snap to it
    }
}

/** The snapped angle: within SNAP of a multiple of 90 degrees, that multiple. */
fun snapAngle(degrees: Float): Float {
    val nearest = Math.round(degrees / 90f) * 90f
    return if (abs(degrees - nearest) <= SelectionLook.SNAP) nearest else degrees
}

/** The uniform scale that takes from (relative to anchor) towards to, along from's direction. */
fun scaleAlong(anchorX: Float, anchorY: Float, fromX: Float, fromY: Float, toX: Float, toY: Float): Float {
    val dx = fromX - anchorX
    val dy = fromY - anchorY
    val length2 = dx * dx + dy * dy
    if (length2 < 1e-6f) return 1f
    return max(0.05f, ((toX - anchorX) * dx + (toY - anchorY) * dy) / length2)
}
