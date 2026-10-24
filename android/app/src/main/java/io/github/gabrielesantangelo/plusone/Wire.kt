package io.github.gabrielesantangelo.plusone

import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.Socket

/**
 * Messages between the PC and the app (plusone/companion.py): a 4-byte
 * big-endian length, a JSON header of that length, and as many payload
 * bytes as the header's "size" says (none without one).
 */
class Wire(socket: Socket) {
    private val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
    private val output = DataOutputStream(BufferedOutputStream(socket.getOutputStream()))

    fun send(header: JSONObject, payload: ByteArray? = null) = synchronized(output) {
        if (payload != null) header.put("size", payload.size)
        val bytes = header.toString().toByteArray()
        output.writeInt(bytes.size)
        output.write(bytes)
        if (payload != null) output.write(payload)
        output.flush()
    }

    /** The next message: its header, and its payload if it has one. */
    fun receive(): Pair<JSONObject, ByteArray?> {
        val length = input.readInt()
        require(length in 1..MAX_HEADER) { "bad header length $length" }
        val header = JSONObject(String(ByteArray(length).also { input.readFully(it) }))
        val size = header.optInt("size", -1)
        val payload = if (size >= 0) {
            require(size <= MAX_PAYLOAD) { "payload too big: $size" }
            ByteArray(size).also { input.readFully(it) }
        } else null
        return header to payload
    }

    companion object {
        const val MAX_HEADER = 16 * 1024 * 1024      // marks of a long document
        const val MAX_PAYLOAD = 512 * 1024 * 1024    // a PDF
    }
}
