package io.github.gabrielesantangelo.plusone

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.MotionEvent
import android.view.SurfaceView
import androidx.graphics.lowlatency.CanvasFrontBufferedRenderer
import kotlin.math.max
import kotlin.math.min

/**
 * Ink under the nib: each pen sample is drawn straight into the front
 * buffer (no wait for the next frame), and when the pen lifts the stroke
 * moves to the normal, multi-buffered layer. Fingers never draw.
 */
class InkSurface(context: Context) : SurfaceView(context) {

    /** A piece of a stroke from one pen sample to the next, or a clear. */
    sealed interface Op
    data class Segment(
        val x0: Float, val y0: Float, val p0: Float,
        val x1: Float, val y1: Float, val p1: Float,
    ) : Op
    data object Clear : Op

    private val ink = Paint().apply {
        color = INK
        isAntiAlias = true
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }

    // What has been committed, drawn by the render thread only.
    private var page: Bitmap? = null
    private var lastX = 0f
    private var lastY = 0f
    private var lastP = 0f

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
                    Clear -> bitmap.eraseColor(Color.WHITE)
                }
            }
            canvas.drawBitmap(bitmap, 0f, 0f, null)
        }
    })

    private fun draw(canvas: Canvas, s: Segment) {
        ink.strokeWidth = inkWidth(PEN_WIDTH, (s.p0 + s.p1) / 2)
        canvas.drawLine(s.x0, s.y0, s.x1, s.y1, ink)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        val tool = event.getToolType(0)
        if (tool != MotionEvent.TOOL_TYPE_STYLUS && tool != MotionEvent.TOOL_TYPE_ERASER) {
            // A finger: a tap clears the page (palm rejection: fingers never draw).
            if (event.actionMasked == MotionEvent.ACTION_UP && event.eventTime - event.downTime < 250) {
                renderer.renderMultiBufferedLayer(listOf(Clear))
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
                renderer.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, lastX, lastY, lastP))
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.historySize) {
                    extend(event.getHistoricalX(i), event.getHistoricalY(i), event.getHistoricalPressure(i))
                }
                extend(event.x, event.y, event.pressure)
            }
            MotionEvent.ACTION_UP -> {
                extend(event.x, event.y, event.pressure)
                renderer.commit()
            }
            MotionEvent.ACTION_CANCEL -> renderer.commit()
        }
        return true
    }

    private fun extend(x: Float, y: Float, p: Float) {
        renderer.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, x, y, p))
        lastX = x
        lastY = y
        lastP = p
    }

    fun release() = renderer.release(true)

    companion object {
        val INK = Color.rgb(13, 26, 89)  // dark blue, as on the PC (plusone/pdf.py INK)
        const val PEN_WIDTH = 6f          // pixels at full pressure
        private const val FULL_PRESSURE = 0.6f

        /** The PC's plusone/pdf.py ink_width: a hairline for a light touch up to the full width. */
        fun inkWidth(width: Float, pressure: Float) =
            width * (0.1f + 0.9f * min(1f, max(0f, pressure) / FULL_PRESSURE))
    }
}
