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
import android.widget.OverScroller
import androidx.graphics.lowlatency.CanvasFrontBufferedRenderer
import androidx.graphics.surface.SurfaceControlCompat
import java.io.Closeable
import java.io.File
import kotlin.math.max
import kotlin.math.min

/** A PDF opened with Android's own renderer; pages render one at a time.
 * Opening reads every page's size: off the UI thread (a long PDF takes a
 * while). Throws if the PDF cannot be read, or has no page. */
class Pdf(file: File) : Closeable {
    private val fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val renderer: PdfRenderer

    /** Page sizes in points, as shown (the renderer applies crop box and rotation). */
    val sizes: List<SizeF>

    init {
        var opened: PdfRenderer? = null
        try {
            opened = PdfRenderer(fd)
            sizes = (0 until opened.pageCount).map { i ->
                opened.openPage(i).use { SizeF(it.width.toFloat(), it.height.toFloat()) }
            }
            require(sizes.isNotEmpty() && sizes.all { it.width > 0 && it.height > 0 }) { "no pages" }
            renderer = opened
        } catch (e: Throwable) {
            opened?.close()
            fd.close()
            throw e
        }
    }

    private var closed = false
    // The page last drawn stays open: the tiles of a page come one after the
    // other, and opening it again each time parsed it and decoded its images again.
    private var open: PdfRenderer.Page? = null

    /** Draws page index into bitmap, page points mapped by matrix; nothing once closed. */
    @Synchronized
    fun render(index: Int, bitmap: Bitmap, matrix: Matrix) {
        if (closed) return
        val page = open?.takeIf { it.index == index } ?: run {
            open?.close()
            open = null  // should the next one fail to open, this one is not used again
            renderer.openPage(index).also { open = it }
        }
        page.render(bitmap, null, matrix, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
    }

    @Synchronized
    override fun close() {
        if (closed) return
        closed = true
        open?.close()
        renderer.close()
        fd.close()
    }
}

/**
 * The pages of a PDF to sign: one at a time (the arrows turn them), or all
 * of them in a column the fingers scroll through, as a note app shows a
 * notebook (continuous). The pen draws: each sample goes straight into the
 * front buffer (ink under the nib), and the finished stroke is kept in the
 * points of the page it started on. Its side button (or an eraser end)
 * erases whole strokes. Fingers never draw: they pan (a flick scrolls on),
 * pinch to zoom, and a double tap zooms in where it lands or back to the
 * whole page. The pages are rendered in the background for the current
 * zoom; meanwhile the last rendering is scaled, so moving around never
 * waits for it.
 */
@SuppressLint("ViewConstructor")
class SignView(context: Context, private val pdf: Pdf, private val continuous: Boolean) : SurfaceView(context) {

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

    /** The page shown, or (scrolling) the one in the middle of the screen. */
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

    /** Marks from the screen this one replaces. */
    fun restore(saved: Map<Int, List<Mark>>) {
        for ((page, list) in saved) marks[page] = list.toMutableList()
        changed()
        onChange()
    }

    // Where each page lies among the others, in points: alone at 0, 0, or
    // in the column, centred, GAP apart.
    private val lefts = FloatArray(pageCount)
    private val tops = FloatArray(pageCount)
    private var columnWidth = 0f
    private var columnHeight = 0f

    init {
        if (continuous) {
            columnWidth = pdf.sizes.maxOf { it.width }
            var y = 0f
            pdf.sizes.forEachIndexed { i, size ->
                lefts[i] = (columnWidth - size.width) / 2
                tops[i] = y
                y += size.height + GAP
            }
            columnHeight = y - GAP
        }
    }

    // screen = (page's place + page point) * zoom + (ox, oy)
    private var zoom = 1f
    private var ox = 0f
    private var oy = 0f
    private var fitZoom = 1f
    /** Space kept free at the top (title bar) and bottom (toolbar), in pixels. */
    var topInset = 0f
    var bottomInset = 0f

    /** The pages laid out: the one shown, or the whole column. */
    private fun laidOut() = if (continuous) 0 until pageCount else index..index

    /** Where page p is on screen, for this zoom and offset. */
    private fun pageRect(p: Int) = pdf.sizes[p].let {
        RectF(ox + lefts[p] * zoom, oy + tops[p] * zoom, ox + (lefts[p] + it.width) * zoom, oy + (tops[p] + it.height) * zoom)
    }

    /** The pages laid out that the screen shows (part of). */
    private fun onScreen() = laidOut().filter {
        pageRect(it).let { r -> r.bottom > 0 && r.top < height && r.right > 0 && r.left < width }
    }

    /** The page at screen height y: under it, or, in the gap between two, the nearer. */
    private fun pageAt(y: Float): Int {
        if (!continuous) return index
        val at = (y - oy) / zoom
        var best = 0
        var distance = Float.MAX_VALUE
        for (p in 0 until pageCount) {
            val d = max(0f, max(tops[p] - at, at - (tops[p] + pdf.sizes[p].height)))
            if (d < distance) {
                best = p
                distance = d
            }
        }
        return best
    }

    private fun toPageX(p: Int, x: Float) = (x - ox) / zoom - lefts[p]
    private fun toPageY(p: Int, y: Float) = (y - oy) / zoom - tops[p]

    private val pages = Pages(pdf) { changed() }

    /** A page on screen as the render thread draws it: whole (scaled), the
     * sharp tiles there are over it, its marks. */
    private class Shown(val rect: RectF, val whole: Bitmap?, val tiles: List<Pair<Bitmap, RectF>>, val marks: List<Mark>)

    /** What the render thread draws: a snapshot, as the UI thread moves on. */
    private class Scene(
        val pages: List<Shown>, val zoom: Float, val eraser: FloatArray?,
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

    /** The page shown (or in the middle) whole, in the middle of the room. */
    fun fit() {
        val size = pdf.sizes[index]
        if (width == 0 || height == 0) return
        stopFling()
        val margin = 24f
        val room = max(1f, height - topInset - bottomInset)
        fitZoom = max(MIN_ZOOM, min((width - 2 * margin) / size.width, (room - 2 * margin) / size.height))
        zoom = fitZoom
        moved = false
        ox = width / 2f - (lefts[index] + size.width / 2) * zoom
        oy = topInset + room / 2 - (tops[index] + size.height / 2) * zoom
        changed()
    }

    /** Turns to page i, or (scrolling) scrolls to it: whole if the zoom
     * shows pages whole, else its top under the bar. */
    fun showPage(i: Int) {
        if (i !in 0 until pageCount) return
        if (!continuous) deselect()  // its page goes
        index = i
        if (!continuous || zoom <= fitZoom * 1.05f || width == 0) {
            fit()
        } else {
            stopFling()
            oy = topInset + 24f - tops[i] * zoom
            clamp()
            changed()
        }
        onChange()
    }

    /** Page p on screen, turning or scrolling to it if it is not. */
    private fun reveal(p: Int) {
        if (p !in onScreen()) showPage(p) else changed()
    }

    private var moved = false  // panned or zoomed by the user since the last fit

    private fun zoomAt(newZoom: Float, fx: Float, fy: Float) {
        moved = true
        val z = max(fitZoom * 0.5f, min(fitZoom * 10f, newZoom))
        ox = fx - (fx - ox) * z / zoom
        oy = fy - (fy - oy) * z / zoom
        zoom = z
        clamp()
    }

    /** The page (or, scrolling, the column) stays on screen: its edges go
     * no further than the middle. */
    private fun clamp() {
        if (width == 0) return
        val (x0, x1, y0, y1) = scrollRange()
        ox = ox.coerceIn(x0, x1)
        oy = oy.coerceIn(y0, y1)
    }

    private fun scrollRange(): FloatArray {
        val cx = width / 2f
        val cy = topInset + (height - topInset - bottomInset) / 2
        val (w, h) = if (continuous) columnWidth to columnHeight else pdf.sizes[index].let { it.width to it.height }
        return floatArrayOf(cx - w * zoom, cx, cy - h * zoom, cy)
    }

    /** The bars' room changed (a taskbar came, say): the page fits it again,
     * unless the user moved it, which stays as it is. */
    fun setInsets(top: Float, bottom: Float) {
        if (top == topInset && bottom == bottomInset) return
        topInset = top
        bottomInset = bottom
        if (moved) {
            clamp()
            changed()
        } else {
            fit()
        }
    }

    /** Whether screen point x, y is on page p (marks are placed only there:
     * beside it they would be cut off, and still sent). */
    private fun onPage(p: Int, x: Float, y: Float) = pageRect(p).contains(x, y)

    /** The view or the marks changed: draw again, and say what is to render now. */
    private fun changed(eraser: FloatArray? = null) {
        if (continuous) follow()
        scene = snapshot(eraser)
        requestScene()
        want()
    }

    /** The view less nothing: pages are rendered under the bars too. */
    private fun screen() = RectF(0f, 0f, width.toFloat(), height.toFloat())

    /** Whether page p shown at this zoom needs tiles: its whole rendering is not sharp enough. */
    private fun tiled(p: Int) = pages.wholeScale(p) < zoom * 0.98f

    /** The tiles of page p (on screen at r) at level, over the screen and margin
     * pixels around it, the ones nearest the middle of the screen first. */
    private fun tilesOf(p: Int, r: RectF, level: Int, margin: Float): List<Pages.Tile> {
        val span = Pages.TILE / Pages.scale(level)  // points
        val size = pdf.sizes[p]
        val left = max(0f, (-margin - r.left) / zoom)
        val top = max(0f, (-margin - r.top) / zoom)
        val right = min(size.width, (width + margin - r.left) / zoom)
        val bottom = min(size.height, (height + margin - r.top) / zoom)
        if (right <= left || bottom <= top) return emptyList()
        val cx = (width / 2f - r.left) / zoom / span
        val cy = (height / 2f - r.top) / zoom / span
        val found = ArrayList<Pages.Tile>()
        for (y in (top / span).toInt()..((bottom - 0.001f) / span).toInt()) {
            for (x in (left / span).toInt()..((right - 0.001f) / span).toInt()) found += Pages.Tile(p, level, x, y)
        }
        return found.sortedBy { (it.x + 0.5f - cx).let { dx -> dx * dx } + (it.y + 0.5f - cy).let { dy -> dy * dy } }
    }

    /** Where tile t of page (on screen at r) goes: its edges on whole pixels, shared with
     * its neighbours', so no seam shows between them. */
    private fun tileRect(t: Pages.Tile, r: RectF): RectF {
        val step = Pages.TILE / Pages.scale(t.level) * zoom
        return RectF(Math.round(r.left + t.x * step).toFloat(), Math.round(r.top + t.y * step).toFloat(),
            Math.round(r.left + (t.x + 1) * step).toFloat(), Math.round(r.top + (t.y + 1) * step).toFloat())
    }

    /** Tells the renderer what is wanted now, the most wanted first: every page on
     * screen whole (the largest part of the screen first) and the ones next to
     * them, then the sharp tiles on screen, then those around it. Not while
     * pinching: the zoom is still changing. */
    private fun want() {
        if (width == 0 || height == 0 || scaler.isInProgress) return
        val view = screen()
        val shown = onScreen().map { it to pageRect(it) }.sortedByDescending { (_, r) ->
            val part = RectF(r)
            if (part.intersect(view)) part.width() * part.height() else 0f
        }
        val jobs = LinkedHashSet<Pages.Job>()
        val around = shown.map { it.first }.let { on ->
            if (on.isEmpty()) on else on + listOf(on.min() - 1, on.max() + 1).filter { it in 0 until pageCount }
        }
        // No more than the cache holds: asking for more would evict, and ask again, for ever.
        for (p in around.take(pages.wholesThatFit())) if (pages.whole(p) == null) jobs += Pages.Whole(p)
        val level = Pages.level(zoom)
        for (margin in floatArrayOf(0f, Pages.TILE.toFloat())) {
            for ((p, r) in shown) {
                if (!tiled(p)) continue
                for (t in tilesOf(p, r, level, margin)) if (pages.tile(t) == null) jobs += t
            }
        }
        pages.want(jobs)
    }

    /** The tiles to draw over page p: those of other levels first, where this
     * level's are not there yet (the nearer levels over the farther, the
     * sharper over the blurrier), then this level's. */
    private fun tileDraws(p: Int, r: RectF): List<Pair<Bitmap, RectF>> {
        if (!tiled(p)) return emptyList()
        val level = Pages.level(zoom)
        val draws = ArrayList<Pair<Bitmap, RectF>>()
        for (d in intArrayOf(-3, -2, 2, -1, 1, 0)) {
            if (d != 0 && draws.size >= MAX_STAND_INS) continue
            for (t in tilesOf(p, r, level + d, 0f)) pages.tile(t)?.let { draws += it to tileRect(t, r) }
        }
        return draws
    }

    /** Scrolling, the page in the middle of the screen is the one shown. */
    private fun follow() {
        if (width == 0) return
        val middle = pageAt(topInset + (height - topInset - bottomInset) / 2)
        if (middle != index) {
            index = middle
            onChange()
        }
    }

    /** What the render thread is to draw, as things are now. */
    private fun snapshot(eraser: FloatArray? = null): Scene {
        val s = selection
        val shown = onScreen().map { p ->
            val r = pageRect(p)
            Shown(r, pages.whole(p), tileDraws(p, r),
                (marks[p]?.toList() ?: emptyList()) + (s?.takeIf { it.page == p }?.marks ?: emptyList()))
        }
        val picked = s?.takeIf { it.page in laidOut() }
        val shape = picked?.let { look.shape(screenCorners(it), visibleArea()) }
        val turned = if (grab is Grab.Turn) picked?.angle else null
        val lassoPoints = lasso?.toArray()?.let { p -> FloatArray(p.size / 3 * 2) { p[it / 2 * 3 + it % 2] } }
        return Scene(shown, zoom, eraser, shape, grabbed, turned, lassoPoints)
    }

    /** The view less the bars over it. */
    private fun visibleArea() = RectF(0f, topInset, width.toFloat(), height - bottomInset)

    private fun screenCorners(s: Selection) = s.corners().also { c ->
        for (i in c.indices step 2) {
            c[i] = (lefts[s.page] + c[i]) * zoom + ox
            c[i + 1] = (tops[s.page] + c[i + 1]) * zoom + oy
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

    private fun drawScene(canvas: Canvas) {
        canvas.drawColor(canvasColor)
        val s = scene ?: return
        for (page in s.pages) canvas.drawRect(page.rect, white)
        for (page in s.pages) {
            canvas.save()
            canvas.clipRect(page.rect)
            page.whole?.let { canvas.drawBitmap(it, null, page.rect, bitmapPaint) }
            for ((tile, where) in page.tiles) canvas.drawBitmap(tile, null, where, bitmapPaint)
            canvas.translate(page.rect.left, page.rect.top)
            canvas.scale(s.zoom, s.zoom)
            for (mark in page.marks) {
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
        }
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
    private var strokePage = 0  // the page the stroke (or the eraser's pass) started on
    private var erasing = false
    private val erasedPages = HashSet<Int>()  // pages this eraser pass took an undo step for
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
        if (event.actionMasked == MotionEvent.ACTION_DOWN) stopFling()
        // The pen is whichever pointer it is: a palm may have landed first.
        if (penId >= 0 || (0 until event.pointerCount).any { isPen(event, it) }) {
            penSeen = SystemClock.uptimeMillis()
            endFingers(event)  // a pan or pinch the palm started ends here
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
                erasedPages.clear()
                strokePage = pageAt(lastY)
                val place = pending
                if (place != null && !erasing) {
                    // A tap says where the signature, date or text goes (on a page).
                    if (onPage(strokePage, lastX, lastY)) {
                        placeAt(place, strokePage, toPageX(strokePage, lastX), toPageY(strokePage, lastY))
                    }
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
                    stroke = FloatArrayBuilder().apply {
                        add(toPageX(strokePage, lastX), toPageY(strokePage, lastY), lastP)
                    }
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
                    addMarks(strokePage, listOf(Stroke(done.toArray(), penWidth, penColor)))
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
            stroke?.add(toPageX(strokePage, x), toPageY(strokePage, y), p)
            front.renderFrontBufferedLayer(Segment(lastX, lastY, lastP, x, y, p, penWidth * zoom, penColor))
        }
        lastX = x
        lastY = y
        lastP = p
    }

    private fun addMarks(page: Int, new: List<Mark>) {
        val list = marks.getOrPut(page) { mutableListOf() }
        history.addLast(page to list.toList())
        list.addAll(new)
        scene = snapshot()
        onChange()
    }

    private var placing = false

    private fun placeAt(place: (Float, Float) -> List<Mark>, page: Int, x: Float, y: Float) {
        pending = null
        val placed = place(x, y)
        addMarks(page, placed)
        // Picked at once: placed a little off, it moves, turns or scales from here.
        select(page, placed)
        onPlaced()
    }

    // -- selection -----------------------------------------------------------

    /** Picks marks of page. */
    private fun select(page: Int, picked: List<Mark>) {
        deselect()
        val list = marks[page] ?: return
        val mine = picked.filter { m -> list.any { it === m } }
        if (mine.isEmpty()) return changed()
        list.removeAll { m -> mine.any { it === m } }
        selection = Selection(page, mine)
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
        val s = selection?.takeIf { it.page in laidOut() } ?: return false
        val hit = look.hit(look.shape(screenCorners(s), visibleArea()), x, y)
        if (hit < 0) return false
        val px = toPageX(s.page, x)
        val py = toPageY(s.page, y)
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
                Grab.Scale(c[2 * o], c[2 * o + 1], px, py, Matrix(s.matrix))
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
        val px = toPageX(s.page, x)
        val py = toPageY(s.page, y)
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

    /** The lasso closes: the marks its area touches are picked, on the page
     * it started on; a tap picks the mark under it. */
    private fun endLasso(cancelled: Boolean) {
        val points = lasso?.toArray() ?: return
        lasso = null
        val page = pageAt(points[1])
        val list = marks[page]
        if (cancelled || list.isNullOrEmpty()) return changed()
        var left = points[0]; var right = points[0]; var top = points[1]; var bottom = points[1]
        for (i in points.indices step 3) {
            left = min(left, points[i]); right = max(right, points[i])
            top = min(top, points[i + 1]); bottom = max(bottom, points[i + 1])
        }
        val tap = TAP_SIZE * resources.displayMetrics.density
        val picked = if (right - left < tap && bottom - top < tap) {
            val x = toPageX(page, points[0])
            val y = toPageY(page, points[1])
            val r = tap / zoom
            listOfNotNull(list.lastOrNull { it.touches(x, y, x, y, r) })
        } else {
            val polygon = FloatArray(points.size / 3 * 2) { i ->
                if (i % 2 == 0) toPageX(page, points[i / 2 * 3]) else toPageY(page, points[i / 2 * 3 + 1])
            }
            list.filter { Lasso.picks(it, polygon) }
        }
        if (picked.isEmpty()) changed() else select(page, picked)
    }

    /** The strokes on the page shown (for keeping them as the signature). */
    val pageStrokes get() = ((marks[index] ?: emptyList()) + (selection?.takeIf { it.page == index }?.marks
        ?: emptyList())).filterIsInstance<Stroke>()

    /** Removes the strokes the eraser touches going from a to b (screen pixels), on any page on screen. */
    private fun eraseAlong(ax: Float, ay: Float, bx: Float, by: Float) {
        val radius = ERASER_RADIUS / zoom
        for (p in onScreen()) {
            val list = marks[p] ?: continue
            val hit = list.filter {
                it.touches(toPageX(p, ax), toPageY(p, ay), toPageX(p, bx), toPageY(p, by), radius)
            }
            if (hit.isEmpty()) continue
            // The first removal on a page opens an undo step; the rest of the pass joins it.
            if (erasedPages.add(p)) history.addLast(p to list.toList())
            list.removeAll(hit)
            onChange()
        }
        changed(eraser = floatArrayOf(bx, by))
    }

    fun undo() {
        val (page, before) = history.removeLastOrNull() ?: return
        val s = selection
        if (s != null && s.page == page) {
            // The step restores the page as a whole: the selection was in it.
            selection = null
            grab = null
            onSelection(false)
        } else {
            deselect()  // on another page: back where it is, untouched by this step
        }
        marks[page] = before.toMutableList()
        reveal(page)
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
    private val scroller = OverScroller(context)
    private var flinging = false

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
            val page = pageAt(e.y)
            if (onPage(page, e.x, e.y)) placeAt(place, page, toPageX(page, e.x), toPageY(page, e.y))
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            // In where the finger lands, or back to the whole page.
            if (zoom < fitZoom * 1.5f) {
                zoomAt(fitZoom * DOUBLE_TAP_ZOOM, e.x, e.y)
                changed()
            } else {
                fit()
            }
            return true
        }

        // Scrolling, a flick carries on, slowing down, as a list does.
        override fun onFling(e1: MotionEvent?, e2: MotionEvent, velocityX: Float, velocityY: Float): Boolean {
            if (!continuous || fingersDown > 1) return false
            val (x0, x1, y0, y1) = scrollRange()
            scroller.fling(ox.toInt(), oy.toInt(), velocityX.toInt(), velocityY.toInt(),
                x0.toInt(), x1.toInt(), y0.toInt(), y1.toInt())
            flinging = true
            moved = true
            postOnAnimation(::flingStep)
            return true
        }
    })

    private fun flingStep() {
        if (!flinging) return
        if (scroller.computeScrollOffset()) {
            ox = scroller.currX.toFloat()
            oy = scroller.currY.toFloat()
            changed()
            postOnAnimation(::flingStep)
        } else {
            flinging = false
            changed()
        }
    }

    private fun stopFling() {
        if (!flinging) return
        flinging = false
        scroller.forceFinished(true)
        changed()
    }

    /** Fingers' gesture interrupted (the pen came): the detectors hear it end. */
    private fun endFingers(event: MotionEvent) {
        if (fingersDown > 0 || fingerGrab || scaler.isInProgress) {
            val cancel = MotionEvent.obtain(event).apply { action = MotionEvent.ACTION_CANCEL }
            taps.onTouchEvent(cancel)
            scaler.onTouchEvent(cancel)
            cancel.recycle()
            if (fingerGrab) {
                fingerGrab = false
                endGrab()
            }
            changed()  // the pinch's zoom rendered sharp
        }
        fingersDown = 0
    }

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
                moved = true
                clamp()
                changed()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                fingersDown = 0
                if (!flinging) changed()  // sharp again at the new zoom
            }
        }
        lastFocusX = fx
        lastFocusY = fy
    }

    // -- result --------------------------------------------------------------

    fun release() {
        flinging = false
        front.release(true)
        pages.release()
    }

    /** Memory is short: rendered pages go (they come back as needed). */
    fun trim() = pages.trim()

    companion object {
        const val ERASER_RADIUS = 24f      // pixels around the nib
        const val PALM_WINDOW = 400L        // ms after the pen was seen during which touches are a palm
        const val DOUBLE_TAP_ZOOM = 2.5f
        const val TAP_SIZE = 10f            // dp: a lasso smaller than this is a tap on a mark
        const val GAP = 12f                 // points between pages, scrolling
        const val MIN_ZOOM = 0.01f          // pixels per point, on a window too small for a page
        const val MAX_STAND_INS = 60        // tiles of other levels drawn while this level's come
    }
}
