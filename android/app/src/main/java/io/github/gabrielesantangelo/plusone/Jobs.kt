package io.github.gabrielesantangelo.plusone

import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

/**
 * Signing jobs the PC sent. A job outlives the connection it came on: if
 * the link drops while the user signs, the PC connects again and asks for
 * it ("resume"). Its answer is kept until the PC says it got it ("got"):
 * bytes handed to a link that died on the way are not an answer delivered.
 */
object Jobs {
    class Job {
        var result: JSONObject? = null
        var finished = false
        /** Sends the job's answer to the PC; true if it went out. */
        var deliver: ((Job) -> Boolean)? = null
    }

    private val jobs = ConcurrentHashMap<String, Job>()
    private val network = Executors.newSingleThreadExecutor()  // not on the UI thread

    val ids: Set<String> get() = jobs.keys.toSet()

    fun start(id: String, deliver: (Job) -> Boolean) {
        jobs[id] = Job().also { it.deliver = deliver }
    }

    /** The signing screen's answer: the marks, or null if the user cancelled. */
    fun finish(id: String, result: JSONObject?) {
        val job = jobs[id] ?: return
        val deliver = synchronized(job) {
            job.result = result
            job.finished = true
            job.deliver
        }
        if (deliver != null) network.execute { deliver(job) }
    }

    /** The PC asks again on a new connection; false if the job is unknown. */
    fun resume(id: String, deliver: (Job) -> Boolean): Boolean {
        val job = jobs[id] ?: return false
        val finished = synchronized(job) {
            job.deliver = deliver
            job.finished
        }
        if (finished) deliver(job)
        return true
    }

    /** The PC has the answer: the job is over. */
    fun acknowledge(id: String): Boolean = jobs[id]?.takeIf { it.finished }?.let { jobs.remove(id) } != null
}
