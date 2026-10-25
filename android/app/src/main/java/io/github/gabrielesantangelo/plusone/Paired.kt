package io.github.gabrielesantangelo.plusone

import android.content.Context

/** The app's own settings. */
object Options {
    private const val PREFS = "options"

    /** Ready for the PC all the time (and after a restart), or only for a
     * while after the app was opened. */
    fun alwaysReady(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("always", true)

    fun setAlwaysReady(context: Context, on: Boolean) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("always", on).apply()

    /** A PDF's pages one under the other, scrolled through (as a note app
     * shows a notebook), or one at a time, turned with the arrows. */
    fun scrollPages(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("scroll", true)

    fun setScrollPages(context: Context, on: Boolean) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("scroll", on).apply()

    /** The PC may use the camera, the microphone (while a program there does). Off at first. */
    fun camera(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("camera", false)

    fun setCamera(context: Context, on: Boolean) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("camera", on).apply()

    fun microphone(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("microphone", false)

    fun setMicrophone(context: Context, on: Boolean) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("microphone", on).apply()

    /** The microphone for the PC with the room's noise taken out (on at first). */
    fun noiseReduction(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("noise", true)

    fun setNoiseReduction(context: Context, on: Boolean) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("noise", on).apply()
}

/** PCs the user paired with: certificate fingerprint -> name. */
object Paired {
    private const val PREFS = "paired"

    fun all(context: Context): Map<String, String> =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).all.mapValues { it.value as String }

    fun contains(context: Context, fingerprint: String) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).contains(fingerprint)

    fun add(context: Context, fingerprint: String, name: String) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(fingerprint, name).apply()

    fun forget(context: Context, fingerprint: String) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(fingerprint).apply()
}
