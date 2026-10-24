package io.github.gabrielesantangelo.plusone

import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.OutputStream
import java.net.Socket

/**
 * Messages between the PC and the app (plusone/companion.py): a 4-byte
 * big-endian length, a JSON header of that length, and as many payload
 * bytes as the header's "size" says (none without one). Only the PC sends
 * payloads (a PDF), and the app copies them to a file, never into memory.
 */
class Wire(socket: Socket) {
    private val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
    private val output = DataOutputStream(BufferedOutputStream(socket.getOutputStream()))

    fun send(header: JSONObject) = synchronized(output) {
        val bytes = header.toString().toByteArray()
        output.writeInt(bytes.size)
        output.write(bytes)
        output.flush()
    }

    /** The next message's header, at most maxHeader bytes; its payload, if
     * any, is next on the wire (readPayload). */
    fun receive(maxHeader: Int = MAX_HEADER): JSONObject {
        val length = input.readInt()
        require(length in 1..maxHeader) { "bad header length $length" }
        return JSONObject(String(ByteArray(length).also { input.readFully(it) }))
    }

    /** Copies size payload bytes to out. */
    fun readPayload(size: Long, out: OutputStream) {
        val buffer = ByteArray(64 * 1024)
        var left = size
        while (left > 0) {
            val n = input.read(buffer, 0, minOf(buffer.size.toLong(), left).toInt())
            if (n < 0) throw EOFException("the PC closed the connection")
            out.write(buffer, 0, n)
            left -= n
        }
    }

    companion object {
        const val MAX_HEADER = 16 * 1024 * 1024       // signatures, or the marks of a long document
        const val UNPAIRED_HEADER = 4 * 1024          // hello and pairing: small
        const val MAX_PAYLOAD = 256L * 1024 * 1024    // a PDF
    }
}
