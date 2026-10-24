package io.github.gabrielesantangelo.plusone

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.MotionEvent
import android.view.SurfaceView
import androidx.graphics.lowlatency.CanvasFrontBufferedRenderer
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Ink under the nib: each pen sample is drawn straight into the front
 * buffer (no wait for the next frame), and when the pen lifts the stroke
 * moves to the normal, multi-buffered layer. With the S Pen's side button
 * held (or the eraser end of a pen), the pen erases whole strokes, as on
 * the PC. Fingers never draw.
 */
class InkSurface(context: Context) : SurfaceView(context) {

    /** x, y, pressure triples of a finished stroke, and the box its ink stays in. */
    class Stroke(val points: FloatArray) {
        val bounds = RectF().also { box ->
            box.set(points[0], points[1], points[0], points[1])
            for (i in points.indices step 3) box.union(points[i], points[i + 1])
            box.inset(-PEN_WIDTH, -PEN_WIDTH)
        }
    }

    /** What the render thread is asked to draw. */
    sealed interface Op
    data class Segment(
        val x0: Float, val y0: Float, val p0: Float,
        val x1: Float, val y1: Float, val p1: Float,
    ) : Op
    /** The page again, with only these strokes (after erasing or clearing). */
    class Redraw(val strokes: List<Stroke>) : Op

    private val ink = Paint().apply {
        color = INK
        isAntiAlias = true
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }

    // Finished strokes, on the UI thread; the render thread gets copies.
    private val strokes = mutableListOf<Stroke>()
    private var current: FloatArrayBuilder? = null
    private var erasing = false
    private var lastX = 0f
    private var lastY = 0f
    private var lastP = 0f

    // What has been committed, drawn by the render thread only.
    private var page: Bitmap? = null

    private val renderer = CanvasFrontBufferedRenderer(this, object : CanvasFrontBufferedRenderer.Callback<Op> {
        override fun onDrawFrontBufferedLayer(canvas: Canvas, bufferWidth: Int, bufferHeight: Int, param: Op) {
            if (param is Segment) draw(canvas, param)
        }

        override fun onDrawMultiBufferedLayer(
            canvas: Canvas, bufferWidth: Int, bufferHeight: Int, params: Collection<Op>,
        ) {
            val bitmap = page?.takeIf { it.width == bufferWidth && it.height == bufferHeight }
                ?: Bitmap.createBitmap(bufferWidth, bufferHeight, Bitmap.Config.ARGB_8888)
                    .also { it.eraseColor(Color.WHITE); page = it }
            val onPage = Canvas(bitmap)
            for (op in params) {
                when (op) {
                    is Segment -> draw(onPage, op)
                    is Redraw -> {
                        bitmap.eraseColor(Color.WHITE)
                        op.strokes.forEach { drawStroke(onPage, it) }
                    }
                }
            }
            canvas.drawBitmap(bitmap, 0f, 0f, null)
        }
    })

    private fun draw(canvas: Canvas, s: Segment) {
        ink.strokeWidth = inkWidth(PEN_WIDTH, (s.p0 + s.p1) / 2)
        canvas.drawLine(s.x0, s.y0, s.x1, s.y1, ink)
    }

    private fun drawStroke(canvas: Canvas, stroke: Stroke) {
        val p = stroke.points
        if (p.size == 3) {
            draw(canvas, Segment(p[0], p[1], p[2], p[0], p[1], p[2]))
            return
        }
        for (i in 0 until p.size - 3 step 3) {
            draw(canvas, Segment(p[i], p[i + 1], p[i + 2], p[i + 3], p[i + 4], p[i + 5]))
        }
    }

    private fun isEraser(event: MotionEvent) =
        event.getToolType(0) == MotionEvent.TOOL_TYPE_ERASER ||
            event.isButtonPressed(MotionEvent.BUTTON_STYLUS_PRIMARY) ||
            event.isButtonPressed(MotionEvent.BUTTON_STYLUS_SECONDARY)

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        val tool = event.getToolType(0)
        if (tool != MotionEvent.TOOL_TYPE_STYLUS && tool != MotionEvent.TOOL_TYPE_ERASER) {
            // A finger: a tap clears the page (palm rejection: fingers never draw).
            if (event.actionMasked == MotionEvent.ACTION_UP && event.eventTime - event.downTime < 250) {
                strokes.clear()
                renderer.renderMultiBufferedLayer(listOf(Redraw(emptyList())))
            }
            return true
        }
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                // Every sample as it comes, not batched up to the next frame.
                requestUnbufferedDispatch(event)
                lastX = event.x
                lastY = event.y
                lastP = event.pressure
                erasing = isEraser(event)
                if (erasing) {
                    eraseAlong(lastX, lastY, lastX, lastY)
                } else {
                    current = FloatArrayBuilder().apply { add(lastX, lastY, lastP) }
                    renderer.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, lastX, lastY, lastP))
                }
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.historySize) {
                    extend(event.getHistoricalX(i), event.getHistoricalY(i), event.getHistoricalPressure(i))
                }
                extend(event.x, event.y, event.pressure)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (event.actionMasked == MotionEvent.ACTION_UP) extend(event.x, event.y, event.pressure)
                current?.let { strokes.add(Stroke(it.toArray())) }
                current = null
                if (!erasing) renderer.commit()
                erasing = false
            }
        }
        return true
    }

    private fun extend(x: Float, y: Float, p: Float) {
        if (erasing) {
            eraseAlong(lastX, lastY, x, y)
        } else {
            current?.add(x, y, p)
            renderer.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, x, y, p))
        }
        lastX = x
        lastY = y
        lastP = p
    }

    /** Removes the strokes the eraser touches going from (x0, y0) to (x1, y1). */
    private fun eraseAlong(x0: Float, y0: Float, x1: Float, y1: Float) {
        val reach = ERASER_RADIUS
        val path = RectF(min(x0, x1) - reach, min(y0, y1) - reach, max(x0, x1) + reach, max(y0, y1) + reach)
        val hit = strokes.filter { RectF.intersects(it.bounds, path) && touches(it, x0, y0, x1, y1, reach) }
        if (hit.isNotEmpty()) {
            strokes.removeAll(hit)
            renderer.renderMultiBufferedLayer(listOf(Redraw(strokes.toList())))
        }
    }

    private fun touches(stroke: Stroke, x0: Float, y0: Float, x1: Float, y1: Float, reach: Float): Boolean {
        val p = stroke.points
        for (i in p.indices step 3) {
            if (distanceToSegment(p[i], p[i + 1], x0, y0, x1, y1) <= reach + inkWidth(PEN_WIDTH, p[i + 2]) / 2) {
                return true
            }
        }
        return false
    }

    fun release() = renderer.release(true)

    /** A growable FloatArray: strokes take thousands of samples. */
    private class FloatArrayBuilder {
        private var data = FloatArray(3 * 256)
        private var size = 0

        fun add(x: Float, y: Float, p: Float) {
            if (size + 3 > data.size) data = data.copyOf(data.size * 2)
            data[size] = x
            data[size + 1] = y
            data[size + 2] = p
            size += 3
        }

        fun toArray(): FloatArray = data.copyOf(size)
    }

    companion object {
        val INK = Color.rgb(13, 26, 89)  // dark blue, as on the PC (plusone/pdf.py INK)
        const val PEN_WIDTH = 6f          // pixels at full pressure
        const val ERASER_RADIUS = 24f     // pixels around the nib
        private const val FULL_PRESSURE = 0.6f

        /** The PC's plusone/pdf.py ink_width: a hairline for a light touch up to the full width. */
        fun inkWidth(width: Float, pressure: Float) =
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
}
