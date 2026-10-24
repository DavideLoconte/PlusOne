package io.github.gabrielesantangelo.plusone

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.SizeF
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.SurfaceView
import androidx.graphics.lowlatency.CanvasFrontBufferedRenderer
import androidx.graphics.surface.SurfaceControlCompat
import java.io.Closeable
import java.io.File
import java.util.concurrent.Executors
import kotlin.math.max
import kotlin.math.min

/** A PDF opened with Android's own renderer; pages render one at a time. */
class Pdf(file: File) : Closeable {
    private val fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val renderer = PdfRenderer(fd)

    /** Page sizes in points, as shown (the renderer applies crop box and rotation). */
    val sizes: List<SizeF> = (0 until renderer.pageCount).map { i ->
        renderer.openPage(i).use { SizeF(it.width.toFloat(), it.height.toFloat()) }
    }

    private var closed = false

    /** Draws page index into bitmap, page points mapped by matrix; nothing once closed. */
    @Synchronized
    fun render(index: Int, bitmap: Bitmap, matrix: Matrix) {
        if (closed) return
        renderer.openPage(index).use { it.render(bitmap, null, matrix, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY) }
    }

    @Synchronized
    override fun close() {
        if (closed) return
        closed = true
        renderer.close()
        fd.close()
    }
}

/**
 * One page of a PDF to sign. The pen draws: each sample goes straight into
 * the front buffer (ink under the nib), and the finished stroke is kept in
 * page points. Its side button (or an eraser end) erases whole strokes.
 * Fingers never draw: they pan, pinch to zoom, and a double tap zooms in
 * where it lands or back to the whole page. The page itself is rendered in
 * the background for the current zoom; meanwhile the last rendering is
 * scaled, so moving around never waits for it.
 */
@SuppressLint("ViewConstructor")
class SignView(context: Context, private val pdf: Pdf) : SurfaceView(context) {

    /** Called after anything the toolbar shows may have changed. */
    var onChange: () -> Unit = {}
    /** The eraser button in the toolbar, for pens without a side button. */
    var eraserMode = false
    /** The pen: its colour and width (points at full pressure). */
    var penColor = Ink.COLOR
    var penWidth = Ink.WIDTH
    /** Marks waiting for a tap to say where they go (signature, date, text). */
    var pending: ((Float, Float) -> List<Mark>)? = null
    var onPlaced: () -> Unit = {}
    /** The toolbar's lasso: the pen picks marks instead of drawing. */
    var selectMode = false
    /** A selection appeared (true) or went (false). */
    var onSelection: (Boolean) -> Unit = {}
    /** Around the page (the theme's). */
    var canvasColor = Color.rgb(48, 48, 50)

    var index = 0
        private set
    val pageCount get() = pdf.sizes.size
    val marks = HashMap<Int, MutableList<Mark>>()
    private val history = ArrayDeque<Pair<Int, List<Mark>>>()
    val canUndo get() = history.isNotEmpty()
    val isEmpty get() = selection == null && marks.values.all { it.isEmpty() }

    // Marks picked to move, turn or scale (Selection.kt): they leave their
    // page's list while picked, and go back where they ended up.
    private var selection: Selection? = null
    private var grab: Grab? = null
    private var grabbed = -1          // the handle dragged, for the look
    private var grabStepped = false   // the drag's undo step is taken
    private var fingerGrab = false
    private var lasso: FloatArrayBuilder? = null  // screen x, y, 0 while the lasso is drawn
    private val look = SelectionLook(resources.displayMetrics.density)
    val hasSelection get() = selection != null

    /** Marks from the screen this one replaces. */
    fun restore(saved: Map<Int, List<Mark>>) {
        for ((page, list) in saved) marks[page] = list.toMutableList()
        changed()
        onChange()
    }

    // screen = page * zoom + (ox, oy)
    private var zoom = 1f
    private var ox = 0f
    private var oy = 0f
    private var fitZoom = 1f
    /** Space kept free at the top (title bar) and bottom (toolbar), in pixels. */
    var topInset = 0f
    var bottomInset = 0f

    private class Picture(val index: Int, val bitmap: Bitmap, val zoom: Float, val ox: Float, val oy: Float)
    @Volatile private var picture: Picture? = null
    private val pageRenderer = Executors.newSingleThreadExecutor()
    private var renderSerial = 0

    /** What the render thread draws: a snapshot, as the UI thread moves on. */
    private class Scene(
        val index: Int, val size: SizeF, val zoom: Float, val ox: Float, val oy: Float,
        val picture: Picture?, val marks: List<Mark>, val eraser: FloatArray?,
        val shape: SelectionLook.Shape? = null, val grabbed: Int = -1, val turned: Float? = null,
        val lasso: FloatArray? = null,
    )
    @Volatile private var scene: Scene? = null

    /** A piece of the stroke being drawn, in screen pixels. */
    class Segment(
        val x0: Float, val y0: Float, val p0: Float,
        val x1: Float, val y1: Float, val p1: Float, val width: Float, val color: Int,
    )

    private val ink = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        color = Ink.COLOR
    }
    // The page, a sheet lifted off the canvas.
    private val white = Paint().apply {
        color = Color.WHITE
        val d = resources.displayMetrics.density
        setShadowLayer(6 * d, 0f, 2 * d, Color.argb(56, 0, 0, 0))
    }
    // pdf.py draws text in "Sans".
    private val text = Paint().apply { isAntiAlias = true; typeface = android.graphics.Typeface.SANS_SERIF }
    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG)
    private val eraserPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        strokeWidth = 3f
        color = Color.argb(200, 80, 80, 80)
    }

    private val front = CanvasFrontBufferedRenderer(this, object : CanvasFrontBufferedRenderer.Callback<Segment> {
        override fun onDrawFrontBufferedLayer(canvas: Canvas, bufferWidth: Int, bufferHeight: Int, param: Segment) {
            ink.color = param.color
            ink.strokeWidth = Ink.width(param.width, (param.p0 + param.p1) / 2)
            canvas.drawLine(param.x0, param.y0, param.x1, param.y1, ink)
        }

        override fun onDrawMultiBufferedLayer(
            canvas: Canvas, bufferWidth: Int, bufferHeight: Int, params: Collection<Segment>,
        ) = drawScene(canvas)

        override fun onMultiBufferedLayerRenderComplete(
            frontBufferedLayerSurfaceControl: SurfaceControlCompat, multiBufferedLayerSurfaceControl: SurfaceControlCompat,
            transaction: SurfaceControlCompat.Transaction,
        ) {
            post {
                sceneInFlight = false
                if (sceneWanted) requestScene()
            }
        }
    })

    init {
        // Back from another app the surface is new: draw the scene into it.
        holder.addCallback(object : android.view.SurfaceHolder.Callback {
            override fun surfaceCreated(holder: android.view.SurfaceHolder) {}
            override fun surfaceChanged(holder: android.view.SurfaceHolder, format: Int, width: Int, height: Int) {
                post {
                    sceneInFlight = false
                    changed()
                }
            }
            override fun surfaceDestroyed(holder: android.view.SurfaceHolder) {}
        })
    }

    // -- view ----------------------------------------------------------------

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        fit()
    }

    fun fit() {
        val size = pdf.sizes[index]
        if (width == 0 || height == 0) return
        val margin = 24f
        val room = height - topInset - bottomInset
        fitZoom = min((width - 2 * margin) / size.width, (room - 2 * margin) / size.height)
        zoom = fitZoom
        ox = (width - size.width * zoom) / 2
        oy = topInset + (room - size.height * zoom) / 2
        changed(render = true)
    }

    fun showPage(i: Int) {
        if (i !in 0 until pageCount) return
        deselect()
        index = i
        picture = null
        fit()
        onChange()
    }

    private fun zoomAt(newZoom: Float, fx: Float, fy: Float) {
        val z = max(fitZoom * 0.5f, min(fitZoom * 10f, newZoom))
        ox = fx - (fx - ox) * z / zoom
        oy = fy - (fy - oy) * z / zoom
        zoom = z
    }

    /** The view or the marks changed: draw again, and render the page anew if asked. */
    private fun changed(render: Boolean = false, eraser: FloatArray? = null) {
        scene = snapshot(eraser)
        requestScene()
        if (render) renderPage()
    }

    /** What the render thread is to draw, as things are now. */
    private fun snapshot(eraser: FloatArray? = null): Scene {
        val s = selection?.takeIf { it.page == index }
        val shown = (marks[index]?.toList() ?: emptyList()) + (s?.marks ?: emptyList())
        val shape = s?.let { look.shape(screenCorners(it), visibleArea()) }
        val turned = if (grab is Grab.Turn) s?.angle else null
        val lassoPoints = lasso?.toArray()?.let { p -> FloatArray(p.size / 3 * 2) { p[it / 2 * 3 + it % 2] } }
        return Scene(index, pdf.sizes[index], zoom, ox, oy, picture, shown, eraser, shape, grabbed, turned, lassoPoints)
    }

    /** The view less the bars over it. */
    private fun visibleArea() = RectF(0f, topInset, width.toFloat(), height - bottomInset)

    private fun screenCorners(s: Selection) = s.corners().also { c ->
        for (i in c.indices step 2) {
            c[i] = c[i] * zoom + ox
            c[i + 1] = c[i + 1] * zoom + oy
        }
    }

    // The multi-buffered layer has three buffers: asked for a new scene
    // faster than they come back (every frame while erasing or zooming),
    // its renderer waited for one forever and the screen froze. One scene
    // at a time then, at most once a frame; the latest wins.
    private var sceneInFlight = false
    private var sceneWanted = false
    private var frameWaiting = false

    private var sceneSent = 0L

    private var retryPosted = false

    private fun requestScene() {
        sceneWanted = true
        // Should a completion never come, do not wait for it forever.
        if (sceneInFlight && SystemClock.uptimeMillis() - sceneSent > 500) sceneInFlight = false
        if (sceneInFlight && !retryPosted) {
            retryPosted = true
            postDelayed({
                retryPosted = false
                if (sceneWanted) requestScene()
            }, 550)
        }
        if (sceneInFlight || frameWaiting) return
        frameWaiting = true
        postOnAnimation {
            frameWaiting = false
            if (sceneInFlight || !sceneWanted) return@postOnAnimation
            sceneWanted = false
            sceneInFlight = true
            sceneSent = SystemClock.uptimeMillis()
            front.renderMultiBufferedLayer(emptyList())
        }
    }

    private fun renderPage() {
        val serial = ++renderSerial
        val i = index
        val z = zoom
        val x = ox
        val y = oy
        val w = width
        val h = height
        if (w == 0 || h == 0) return
        pageRenderer.execute {
            if (serial != renderSerial) return@execute
            val bitmap = try {
                Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).also {
                    it.eraseColor(Color.WHITE)
                    pdf.render(i, it, Matrix().apply { setScale(z, z); postTranslate(x, y) })
                }
            } catch (e: Throwable) {
                // A page the renderer cannot draw, or no memory for it: the
                // scaled last rendering stays, and the app with it.
                android.util.Log.w("PlusOne", "page $i not rendered: $e")
                return@execute
            }
            post {
                if (serial == renderSerial) {
                    picture = Picture(i, bitmap, z, x, y)
                    changed()
                }
            }
        }
    }

    private fun drawScene(canvas: Canvas) {
        canvas.drawColor(canvasColor)
        val s = scene ?: return
        val left = s.ox
        val top = s.oy
        val right = s.ox + s.size.width * s.zoom
        val bottom = s.oy + s.size.height * s.zoom
        canvas.drawRect(left, top, right, bottom, white)
        canvas.save()
        canvas.clipRect(left, top, right, bottom)
        s.picture?.takeIf { it.index == s.index }?.let { p ->
            // Rendered for another zoom or position: scaled meanwhile.
            val k = s.zoom / p.zoom
            val m = Matrix().apply { setScale(k, k); postTranslate(s.ox - p.ox * k, s.oy - p.oy * k) }
            canvas.drawBitmap(p.bitmap, m, bitmapPaint)
        }
        canvas.translate(s.ox, s.oy)
        canvas.scale(s.zoom, s.zoom)
        for (mark in s.marks) {
            when (mark) {
                is Stroke -> drawStroke(canvas, mark)
                is TextMark -> {
                    text.color = mark.color
                    text.textSize = mark.size
                    canvas.save()
                    canvas.rotate(Math.toDegrees(mark.angle.toDouble()).toFloat(), mark.x, mark.y)
                    canvas.drawText(mark.text, mark.x, mark.y, text)
                    canvas.restore()
                }
            }
        }
        canvas.restore()
        s.eraser?.let { canvas.drawCircle(it[0], it[1], ERASER_RADIUS, eraserPaint) }
        s.shape?.let { look.draw(canvas, it, s.grabbed, s.turned) }
        s.lasso?.let { look.drawLasso(canvas, it) }
    }

    private fun drawStroke(canvas: Canvas, stroke: Stroke) {
        val p = stroke.points
        ink.color = stroke.color
        if (p.size == 3) {
            ink.strokeWidth = Ink.width(stroke.width, p[2])
            canvas.drawPoint(p[0], p[1], ink)
        }
        for (i in 0 until p.size - 3 step 3) {
            ink.strokeWidth = Ink.width(stroke.width, (p[i + 2] + p[i + 5]) / 2)
            canvas.drawLine(p[i], p[i + 1], p[i + 3], p[i + 4], ink)
        }
    }

    // -- the pen -------------------------------------------------------------

    private var stroke: FloatArrayBuilder? = null
    private var erasing = false
    private var erasedThisStroke = false
    private var lastX = 0f
    private var lastY = 0f
    private var lastP = 0f
    private var penSeen = 0L  // when the pen was last near the screen

    private var penId = -1  // the pen's pointer while it touches the screen

    private fun isEraser(e: MotionEvent, i: Int) = eraserMode ||
        e.getToolType(i) == MotionEvent.TOOL_TYPE_ERASER ||
        e.isButtonPressed(MotionEvent.BUTTON_STYLUS_PRIMARY) ||
        e.isButtonPressed(MotionEvent.BUTTON_STYLUS_SECONDARY)

    private fun isPen(e: MotionEvent, i: Int) = e.getToolType(i).let {
        it == MotionEvent.TOOL_TYPE_STYLUS || it == MotionEvent.TOOL_TYPE_ERASER
    }

    override fun onHoverEvent(event: MotionEvent): Boolean {
        if (isPen(event, 0)) penSeen = SystemClock.uptimeMillis()
        return super.onHoverEvent(event)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        // The pen is whichever pointer it is: a palm may have landed first.
        if (penId >= 0 || (0 until event.pointerCount).any { isPen(event, it) }) {
            penSeen = SystemClock.uptimeMillis()
            fingersDown = 0  // a pan the palm started ends here
            pen(event)
            return true
        }
        // A palm resting while the pen writes or hovers is no gesture.
        if (SystemClock.uptimeMillis() - penSeen < PALM_WINDOW) return true
        fingers(event)
        return true
    }

    private fun pen(e: MotionEvent) {
        val action = e.actionMasked
        val down = (action == MotionEvent.ACTION_DOWN || action == MotionEvent.ACTION_POINTER_DOWN) &&
            penId < 0 && isPen(e, e.actionIndex)
        val i = if (down) e.actionIndex else e.findPointerIndex(penId)
        if (i < 0) return  // the palm's own comings and goings
        val up = action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL ||
            (action == MotionEvent.ACTION_POINTER_UP && e.actionIndex == i)
        when {
            down -> {
                penId = e.getPointerId(i)
                requestUnbufferedDispatch(e)  // every sample as it comes, not once a frame
                lastX = e.getX(i)
                lastY = e.getY(i)
                lastP = e.getPressure(i)
                erasing = isEraser(e, i)
                erasedThisStroke = false
                val place = pending
                if (place != null && !erasing) {
                    // A tap says where the signature, date or text goes.
                    placeAt(place, toPageX(lastX), toPageY(lastY))
                    stroke = null
                    placing = true
                    return
                }
                placing = false
                // The selection takes the pen on its handles and inside;
                // elsewhere it ends, and the lasso, if chosen, starts.
                if (!erasing && grabSelection(lastX, lastY)) return
                deselect()
                if (selectMode && !erasing) {
                    lasso = FloatArrayBuilder().apply { add(lastX, lastY, 0f) }
                    changed()
                    return
                }
                if (erasing) {
                    eraseAlong(lastX, lastY, lastX, lastY)
                } else {
                    stroke = FloatArrayBuilder().apply { add(toPageX(lastX), toPageY(lastY), lastP) }
                    front.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, lastX, lastY, lastP,
                        penWidth * zoom, penColor))
                }
            }
            up -> {
                penId = -1
                if (placing) {
                    placing = false
                    return
                }
                if (grab != null) return endGrab()
                if (lasso != null) return endLasso(action == MotionEvent.ACTION_CANCEL)
                if (action != MotionEvent.ACTION_CANCEL) extend(e.getX(i), e.getY(i), e.getPressure(i))
                val done = stroke
                stroke = null
                if (erasing) {
                    erasing = false
                    changed()  // the eraser's ring goes
                } else if (done != null) {
                    addMarks(listOf(Stroke(done.toArray(), penWidth, penColor)))
                    sceneInFlight = true  // commit draws the multi-buffered layer too
                    sceneSent = SystemClock.uptimeMillis()
                    front.commit()
                }
            }
            action == MotionEvent.ACTION_MOVE -> {
                if (placing) return
                if (grab != null) return drag(e.getX(i), e.getY(i))
                lasso?.let {
                    it.add(e.getX(i), e.getY(i), 0f)
                    return changed()
                }
                for (h in 0 until e.historySize) {
                    extend(e.getHistoricalX(i, h), e.getHistoricalY(i, h), e.getHistoricalPressure(i, h))
                }
                extend(e.getX(i), e.getY(i), e.getPressure(i))
            }
        }
    }

    private fun extend(x: Float, y: Float, p: Float) {
        if (erasing) {
            eraseAlong(lastX, lastY, x, y)
        } else {
            stroke?.add(toPageX(x), toPageY(y), p)
            front.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, x, y, p, penWidth * zoom, penColor))
        }
        lastX = x
        lastY = y
        lastP = p
    }

    private fun toPageX(x: Float) = (x - ox) / zoom
    private fun toPageY(y: Float) = (y - oy) / zoom

    private fun addMarks(new: List<Mark>) {
        val list = marks.getOrPut(index) { mutableListOf() }
        history.addLast(index to list.toList())
        list.addAll(new)
        scene = snapshot()
        onChange()
    }

    private var placing = false

    private fun placeAt(place: (Float, Float) -> List<Mark>, x: Float, y: Float) {
        pending = null
        val placed = place(x, y)
        addMarks(placed)
        // Picked at once: placed a little off, it moves, turns or scales from here.
        select(placed)
        onPlaced()
    }

    // -- selection -----------------------------------------------------------

    /** Picks marks of the page shown. */
    private fun select(picked: List<Mark>) {
        deselect()
        val list = marks[index] ?: return
        val mine = picked.filter { m -> list.any { it === m } }
        if (mine.isEmpty()) return changed()
        list.removeAll { m -> mine.any { it === m } }
        selection = Selection(index, mine)
        onSelection(true)
        changed()
    }

    /** Puts the selection back on its page, as it is now. */
    fun deselect() {
        val s = selection ?: return
        selection = null
        grab = null
        grabbed = -1
        marks.getOrPut(s.page) { mutableListOf() }.addAll(s.marks)
        onSelection(false)
        changed()
    }

    fun deleteSelection() {
        val s = selection ?: return
        history.addLast(s.page to committed(s))
        selection = null
        grab = null
        onSelection(false)
        changed()
        onChange()
    }

    /** The page's marks with the selection where it is now. */
    private fun committed(s: Selection) = (marks[s.page]?.toList() ?: emptyList()) + s.marks

    /** A touch on the selection's handles or inside it: true if it took it. */
    private fun grabSelection(x: Float, y: Float): Boolean {
        val s = selection?.takeIf { it.page == index } ?: return false
        val hit = look.hit(look.shape(screenCorners(s), visibleArea()), x, y)
        if (hit < 0) return false
        val px = toPageX(x)
        val py = toPageY(y)
        val c = s.corners()
        grab = when (hit) {
            5 -> Grab.Move(px, py)
            4 -> {
                val cx = (c[0] + c[4]) / 2
                val cy = (c[1] + c[5]) / 2
                Grab.Turn(cx, cy, degrees(px - cx, py - cy), Matrix(s.matrix), s.angle)
            }
            else -> {
                val o = (hit + 2) % 4
                Grab.Scale(c[2 * o], c[2 * o + 1], c[2 * hit], c[2 * hit + 1], Matrix(s.matrix))
            }
        }
        grabbed = if (hit == 5) -1 else hit
        grabStepped = false
        changed()
        return true
    }

    private fun degrees(dx: Float, dy: Float) = Math.toDegrees(kotlin.math.atan2(dy, dx).toDouble()).toFloat()

    private fun drag(x: Float, y: Float) {
        val s = selection ?: return
        val g = grab ?: return
        val px = toPageX(x)
        val py = toPageY(y)
        if (!grabStepped) {
            // One undo step per drag, taken when it actually moves something.
            history.addLast(s.page to committed(s))
            grabStepped = true
            onChange()
        }
        when (g) {
            is Grab.Move -> {
                s.matrix.postTranslate(px - g.x, py - g.y)
                g.x = px
                g.y = py
            }
            is Grab.Scale -> {
                val k = scaleAlong(g.anchorX, g.anchorY, g.fromX, g.fromY, px, py)
                s.matrix.set(g.start)
                s.matrix.postScale(k, k, g.anchorX, g.anchorY)
            }
            is Grab.Turn -> {
                val target = snapAngle(g.startAngle + degrees(px - g.cx, py - g.cy) - g.from)
                s.matrix.set(g.start)
                s.matrix.postRotate(target - g.startAngle, g.cx, g.cy)
            }
        }
        changed()
    }

    private fun endGrab() {
        grab = null
        grabbed = -1
        changed()
    }

    /** The lasso closes: the marks its area touches are picked; a tap picks the mark under it. */
    private fun endLasso(cancelled: Boolean) {
        val points = lasso?.toArray() ?: return
        lasso = null
        val list = marks[index]
        if (cancelled || list.isNullOrEmpty()) return changed()
        var left = points[0]; var right = points[0]; var top = points[1]; var bottom = points[1]
        for (i in points.indices step 3) {
            left = min(left, points[i]); right = max(right, points[i])
            top = min(top, points[i + 1]); bottom = max(bottom, points[i + 1])
        }
        val tap = TAP_SIZE * resources.displayMetrics.density
        val picked = if (right - left < tap && bottom - top < tap) {
            val x = toPageX(points[0])
            val y = toPageY(points[1])
            val r = tap / zoom
            listOfNotNull(list.lastOrNull { it.touches(x, y, x, y, r) })
        } else {
            val polygon = FloatArray(points.size / 3 * 2) { i ->
                if (i % 2 == 0) toPageX(points[i / 2 * 3]) else toPageY(points[i / 2 * 3 + 1])
            }
            list.filter { Lasso.picks(it, polygon) }
        }
        if (picked.isEmpty()) changed() else select(picked)
    }

    /** The strokes on the page shown (for keeping them as the signature). */
    val pageStrokes get() = ((marks[index] ?: emptyList()) + (selection?.takeIf { it.page == index }?.marks
        ?: emptyList())).filterIsInstance<Stroke>()

    /** Removes the strokes the eraser touches going from a to b (screen pixels). */
    private fun eraseAlong(ax: Float, ay: Float, bx: Float, by: Float) {
        val list = marks[index] ?: mutableListOf()
        val radius = ERASER_RADIUS / zoom
        val hit = list.filter { it.touches(toPageX(ax), toPageY(ay), toPageX(bx), toPageY(by), radius) }
        if (hit.isNotEmpty()) {
            // The first removal of a pass opens an undo step; the rest join it.
            if (!erasedThisStroke) history.addLast(index to list.toList())
            erasedThisStroke = true
            list.removeAll(hit)
            onChange()
        }
        changed(eraser = floatArrayOf(bx, by))
    }

    fun undo() {
        val (page, before) = history.removeLastOrNull() ?: return
        // The step restores the page as a whole: the selection was in it.
        if (selection != null) {
            selection = null
            grab = null
            onSelection(false)
        }
        marks[page] = before.toMutableList()
        if (page != index) showPage(page) else changed()
        onChange()
    }

    fun clearPage() {
        deselect()
        val list = marks[index] ?: return
        if (list.isEmpty()) return
        history.addLast(index to list.toList())
        list.clear()
        changed()
        onChange()
    }

    // -- fingers -------------------------------------------------------------

    private var lastFocusX = 0f
    private var lastFocusY = 0f
    private var fingersDown = 0

    private val scaler = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            zoomAt(zoom * detector.scaleFactor, detector.focusX, detector.focusY)
            changed()
            return true
        }
    })

    private val taps = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
            val place = pending
            if (place == null) {
                deselect()  // a tap away from the selection ends it
                return true
            }
            placeAt(place, toPageX(e.x), toPageY(e.y))
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            // In where the finger lands, or back to the whole page.
            if (zoom < fitZoom * 1.5f) {
                zoomAt(fitZoom * DOUBLE_TAP_ZOOM, e.x, e.y)
                changed(render = true)
            } else {
                fit()
            }
            return true
        }
    })

    private fun fingers(e: MotionEvent) {
        // One finger on the selection drags it, as the pen does; more fingers
        // join in for nothing until they all lift.
        if (e.actionMasked == MotionEvent.ACTION_DOWN && grabSelection(e.x, e.y)) {
            fingerGrab = true
            return
        }
        if (fingerGrab) {
            when (e.actionMasked) {
                MotionEvent.ACTION_MOVE -> drag(e.getX(0), e.getY(0))
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    fingerGrab = false
                    endGrab()
                }
            }
            return
        }
        taps.onTouchEvent(e)
        scaler.onTouchEvent(e)
        // Pan with the fingers' centre.
        var fx = 0f
        var fy = 0f
        val up = e.actionMasked == MotionEvent.ACTION_POINTER_UP
        val n = e.pointerCount - if (up) 1 else 0
        for (i in 0 until e.pointerCount) {
            if (up && i == e.actionIndex) continue
            fx += e.getX(i)
            fy += e.getY(i)
        }
        fx /= max(1, n)
        fy /= max(1, n)
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN, MotionEvent.ACTION_POINTER_UP -> {
                fingersDown = n
            }
            MotionEvent.ACTION_MOVE -> if (fingersDown > 0) {
                ox += fx - lastFocusX
                oy += fy - lastFocusY
                changed()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                fingersDown = 0
                changed(render = true)  // sharp again at the new zoom
            }
        }
        lastFocusX = fx
        lastFocusY = fy
    }

    // -- result --------------------------------------------------------------

    fun release() {
        front.release(true)
        pageRenderer.shutdownNow()
    }

    companion object {
        const val ERASER_RADIUS = 24f      // pixels around the nib
        const val PALM_WINDOW = 400L        // ms after the pen was seen during which touches are a palm
        const val DOUBLE_TAP_ZOOM = 2.5f
        const val TAP_SIZE = 10f            // dp: a lasso smaller than this is a tap on a mark
    }
}
