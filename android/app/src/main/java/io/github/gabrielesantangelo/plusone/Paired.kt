package io.github.gabrielesantangelo.plusone

import android.content.Context

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
