package io.github.gabrielesantangelo.plusone

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.MotionEvent
import android.view.View
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.atan
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.tan

/**
 * Fingers and the pen on the PC's desktop. Each packet says what is on the
 * screen now (the fingers down, the pen's samples since the last one), sent
 * at every change and again every REPEAT ms while anything touches it: a
 * packet lost on the way costs a moment, never a finger stuck down on the PC.
 *
 * INPUT: kind | fingers u8 | per finger: id u8, x u16, y u16 (0..65535 of
 * the screen) | pen samples u8 | per sample the S Pen's report after its ID:
 * bits u8 (tip 1, barrel 2, eraser 8, in range 16), x u16, y u16
 * (0..32767), pressure u16 (0..4096), tilt x i8, tilt y i8 (degrees).
 * The pen's samples are all of them (Android's historical ones too), and
 * events come as they happen, not once a frame (unbuffered dispatch).
 */
class DisplayInput(private val view: View, private val session: () -> DisplaySession?) {
    private val fingers = LinkedHashMap<Int, Pair<Float, Float>>()  // id -> where, in the view
    private var penDown = false
    private var penSeenAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val repeat = object : Runnable {
        override fun run() {
            if (fingers.isEmpty() && !penDown) return
            send(emptyList())
            handler.postDelayed(this, REPEAT)
        }
    }

    init {
        view.requestUnbufferedDispatch(InputDevice.SOURCE_CLASS_POINTER)
        view.setOnTouchListener { _, e -> touch(e); true }
        view.setOnGenericMotionListener { _, e -> hover(e) }
    }

    private fun touch(e: MotionEvent) {
        val action = e.actionMasked
        val ending = action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL
        val samples = ArrayList<ByteArray>()
        fingers.clear()
        for (i in 0 until e.pointerCount) {
            val lifted = ending || (action == MotionEvent.ACTION_POINTER_UP && i == e.actionIndex)
            if (isPen(e, i)) {
                penSeenAt = SystemClock.uptimeMillis()
                for (h in 0 until e.historySize) samples += penSample(e, i, h, tip = true)
                samples += penSample(e, i, -1, tip = !lifted)
                penDown = !lifted
            } else if (!lifted && !palm()) {
                fingers[e.getPointerId(i)] = e.getX(i) to e.getY(i)
            }
        }
        send(samples)
        if (ending || action == MotionEvent.ACTION_POINTER_UP) send(emptyList())  // the last word, twice
        handler.removeCallbacks(repeat)
        if (fingers.isNotEmpty() || penDown) handler.postDelayed(repeat, REPEAT)
    }

    private fun hover(e: MotionEvent): Boolean {
        if (!isPen(e, 0)) return false
        penSeenAt = SystemClock.uptimeMillis()
        val leaving = e.actionMasked == MotionEvent.ACTION_HOVER_EXIT
        val samples = ArrayList<ByteArray>()
        for (h in 0 until e.historySize) samples += penSample(e, 0, h, tip = false)
        samples += penSample(e, 0, -1, tip = false, inRange = !leaving)
        fingers.clear()  // a pen near: the hand on the screen is a palm
        send(samples)
        if (leaving) send(listOf(penSample(e, 0, -1, tip = false, inRange = false)))
        return true
    }

    /** Fingers while the pen is near, or was a moment ago, are the hand holding it. */
    private fun palm() = SystemClock.uptimeMillis() - penSeenAt < PALM_WINDOW

    private fun isPen(e: MotionEvent, i: Int) = e.getToolType(i).let {
        it == MotionEvent.TOOL_TYPE_STYLUS || it == MotionEvent.TOOL_TYPE_ERASER
    }

    /** The S Pen's report (after its ID) for pointer i at history h (-1: now). */
    private fun penSample(e: MotionEvent, i: Int, h: Int, tip: Boolean, inRange: Boolean = true): ByteArray {
        fun axis(a: Int) = if (h < 0) e.getAxisValue(a, i) else e.getHistoricalAxisValue(a, i, h)
        val x = if (h < 0) e.getX(i) else e.getHistoricalX(i, h)
        val y = if (h < 0) e.getY(i) else e.getHistoricalY(i, h)
        val pressure = if (h < 0) e.getPressure(i) else e.getHistoricalPressure(i, h)
        // Android: how far from upright (0..pi/2) and towards where (0 up, pi/2 right).
        val tilt = axis(MotionEvent.AXIS_TILT)
        val towards = axis(MotionEvent.AXIS_ORIENTATION)
        val tiltX = Math.toDegrees(atan(tan(tilt) * sin(towards)).toDouble())
        val tiltY = Math.toDegrees(atan(-tan(tilt) * cos(towards)).toDouble())
        var bits = 0
        if (inRange) {
            bits = bits or IN_RANGE
            if (tip) bits = bits or TIP
            if (e.buttonState and MotionEvent.BUTTON_STYLUS_PRIMARY != 0) bits = bits or BARREL
            if (e.getToolType(i) == MotionEvent.TOOL_TYPE_ERASER) bits = bits or ERASER
        }
        return ByteBuffer.allocate(PEN_SAMPLE).order(ByteOrder.LITTLE_ENDIAN)
            .put(bits.toByte())
            .putShort(scale(x, view.width, PEN_MAX))
            .putShort(scale(y, view.height, PEN_MAX))
            .putShort((pressure.coerceIn(0f, 1f) * PRESSURE_MAX).toInt().toShort())
            .put(tiltX.coerceIn(-90.0, 90.0).toInt().toByte())
            .put(tiltY.coerceIn(-90.0, 90.0).toInt().toByte())
            .array()
    }

    private fun scale(v: Float, size: Int, max: Int) =
        (v / size.coerceAtLeast(1) * max).toInt().coerceIn(0, max).toShort()

    private fun send(pen: List<ByteArray>) {
        val session = session() ?: return
        // A few at most per packet: the samples of one event, kept whole.
        for (chunk in pen.chunked(MAX_SAMPLES).ifEmpty { listOf(emptyList()) }) {
            val b = ByteBuffer.allocate(3 + fingers.size * 5 + chunk.size * PEN_SAMPLE).order(ByteOrder.BIG_ENDIAN)
            b.put(DisplaySession.INPUT).put(fingers.size.toByte())
            for ((id, at) in fingers) {
                b.put(id.toByte())
                    .putShort(scale(at.first, view.width, TOUCH_MAX))
                    .putShort(scale(at.second, view.height, TOUCH_MAX))
            }
            b.put(chunk.size.toByte())
            for (sample in chunk) b.put(sample)
            session.send(b.array())
        }
    }

    companion object {
        const val REPEAT = 100L        // ms, while a finger or the pen touches
        const val PALM_WINDOW = 400L   // ms after the pen was near
        const val PEN_SAMPLE = 9
        const val MAX_SAMPLES = 64
        const val TOUCH_MAX = 65535
        const val PEN_MAX = 32767
        const val PRESSURE_MAX = 4096
        const val TIP = 0x01
        const val BARREL = 0x02
        const val ERASER = 0x08
        const val IN_RANGE = 0x10
    }
}
