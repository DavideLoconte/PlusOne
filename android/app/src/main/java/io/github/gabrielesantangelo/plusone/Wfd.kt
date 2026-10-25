package io.github.gabrielesantangelo.plusone

import android.content.ContentValues
import android.content.Context
import android.provider.MediaStore
import android.util.Log

/**
 * Second Screen's low-latency mode: it reads Documents/wfd/wfdconfig.txt
 * (README, "tablet low-latency mode"), which the user had to make by hand.
 * Made once here, through MediaStore (no permission needed for a file the
 * app creates). Should one exist already (the user's), Android names ours
 * "wfdconfig (1).txt", which Second Screen ignores: no harm either way.
 */
object Wfd {
    private const val DONE = "wfdconfig"

    fun ensure(context: Context) {
        val prefs = context.getSharedPreferences("once", Context.MODE_PRIVATE)
        if (prefs.getBoolean(DONE, false)) return
        try {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, "wfdconfig.txt")
                put(MediaStore.MediaColumns.MIME_TYPE, "text/plain")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "Documents/wfd/")
            }
            val resolver = context.contentResolver
            val uri = resolver.insert(MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values)
                ?: return
            resolver.openOutputStream(uri)?.use { it.write("sink_latency_mode low\n".toByteArray()) }
            prefs.edit().putBoolean(DONE, true).apply()
            Log.i(CompanionService.TAG, "Second Screen's low-latency mode set (Documents/wfd/wfdconfig.txt)")
        } catch (e: Exception) {
            Log.w(CompanionService.TAG, "Second Screen's low-latency file not made: $e")
        }
    }
}
