package io.github.gabrielesantangelo.plusone

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Matrix
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.util.LruCache
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.ceil
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * The pages, rendered off the UI thread the way a note app does it: each
 * page whole at a modest resolution, there almost at once (scaled while
 * moving), and sharp tiles of TILE pixels at the zoom, in steps of √2, for
 * what is on screen and just around it. Both kept in memory, the least
 * recently used dropped first. What is wanted next is said again at each
 * move (want): what is no longer wanted is never rendered.
 */
class Pages(private val pdf: Pdf, private val onReady: () -> Unit) {
    sealed interface Job
    data class Whole(val page: Int) : Job
    data class Tile(val page: Int, val level: Int, val x: Int, val y: Int) : Job

    private val wholes = object : LruCache<Int, Bitmap>(WHOLE_BYTES) {
        override fun sizeOf(key: Int, value: Bitmap) = value.byteCount
    }
    private val tiles = object : LruCache<Tile, Bitmap>(TILE_BYTES) {
        override fun sizeOf(key: Tile, value: Bitmap) = value.byteCount
    }

    private val lock = Object()
    private var queue: List<Job> = emptyList()  // under lock
    // Being rendered, or rendered and not yet in the cache: not asked for again.
    private val inFlight = ConcurrentHashMap.newKeySet<Job>()
    @Volatile private var stopped = false
    private val main = Handler(Looper.getMainLooper())

    init {
        // One thread: Android's PDF renderer draws one page at a time anyway.
        Thread(::work, "plusone-pages").start()
    }

    fun whole(page: Int): Bitmap? = wholes.get(page)
    fun tile(key: Tile): Bitmap? = tiles.get(key)

    /** Pixels per point of a page rendered whole: as many as WHOLE_PIXELS allow. */
    fun wholeScale(page: Int) = pdf.sizes[page].let { sqrt(WHOLE_PIXELS / (it.width * it.height)) }

    /** How many pages rendered whole the cache holds at least. */
    fun wholesThatFit() = (WHOLE_BYTES / (WHOLE_PIXELS * 4)).toInt() - 1

    /** What to render next, the most wanted first, instead of what was wanted before. */
    fun want(jobs: Collection<Job>) {
        synchronized(lock) {
            queue = jobs.filter { it !in inFlight }
            lock.notifyAll()
        }
    }

    /** Memory is short: the tiles go, the whole pages mostly. */
    fun trim() {
        tiles.evictAll()
        wholes.trimToSize(WHOLE_BYTES / 4)
    }

    fun release() {
        stopped = true
        synchronized(lock) { lock.notifyAll() }
        // Up to a few hundred megabytes: not left for the garbage collector to find.
        tiles.evictAll()
        wholes.evictAll()
    }

    private fun work() {
        while (true) {
            val job = synchronized(lock) {
                while (queue.isEmpty() && !stopped) lock.wait()
                if (stopped) return
                queue.first().also {
                    queue = queue.drop(1)
                    inFlight += it
                }
            }
            val bitmap = try {
                render(job)
            } catch (e: Throwable) {
                // A page the renderer cannot draw, or no memory for it: the
                // rest goes on, and the app with it.
                Log.w("PlusOne", "$job not rendered: $e")
                null
            }
            if (bitmap == null) {
                inFlight -= job
                continue
            }
            main.post {
                inFlight -= job
                if (stopped) return@post
                when (job) {
                    is Whole -> wholes.put(job.page, bitmap)
                    is Tile -> tiles.put(job, bitmap)
                }
                onReady()
            }
        }
    }

    private fun render(job: Job): Bitmap = when (job) {
        is Whole -> {
            val s = wholeScale(job.page)
            val size = pdf.sizes[job.page]
            Bitmap.createBitmap(ceil(size.width * s).toInt(), ceil(size.height * s).toInt(), Bitmap.Config.ARGB_8888)
                .also {
                    it.eraseColor(Color.WHITE)
                    pdf.render(job.page, it, Matrix().apply { setScale(s, s) })
                }
        }
        is Tile -> {
            val s = scale(job.level)
            // Opaque, past the page's edge too: it covers the scaled whole page under it.
            Bitmap.createBitmap(TILE, TILE, Bitmap.Config.ARGB_8888).also {
                it.eraseColor(Color.WHITE)
                pdf.render(job.page, it, Matrix().apply {
                    setScale(s, s)
                    postTranslate(-job.x * TILE.toFloat(), -job.y * TILE.toFloat())
                })
            }
        }
    }

    companion object {
        const val TILE = 400                          // pixels
        const val WHOLE_PIXELS = 2_000_000f           // a page rendered whole: A4 at about 170 dpi
        const val TILE_BYTES = 128 * 1024 * 1024
        const val WHOLE_BYTES = 96 * 1024 * 1024
        private val SQRT2 = sqrt(2f)

        /** The level a zoom (pixels per point) renders at: the next step of √2 up,
         * so tiles are 1 to 1.41 times as sharp as the screen. */
        fun level(zoom: Float) = ceil(ln(zoom) / ln(SQRT2) - 1e-4f).toInt()

        fun scale(level: Int) = SQRT2.pow(level)
    }
}
