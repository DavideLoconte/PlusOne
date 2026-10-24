package io.github.gabrielesantangelo.plusone

import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/** Signing jobs the PC sent, waiting for the signing screen's answer. */
object Jobs {
    private val waiting = ConcurrentHashMap<String, (JSONObject?) -> Unit>()

    fun expect(id: String, onResult: (JSONObject?) -> Unit) {
        waiting[id] = onResult
    }

    /** result: the marks, or null if the user cancelled. */
    fun finish(id: String, result: JSONObject?) {
        waiting.remove(id)?.invoke(result)
    }
}
