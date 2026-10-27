package io.github.gabrielesantangelo.plusone

import android.os.ParcelFileDescriptor
import android.util.Log
import java.io.DataInputStream
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLSocket

/**
 * Plus One over the USB cable (Android Open Accessory): the PC is the USB
 * host (plusone/usb.py) and this tablet its accessory. One bulk stream
 * carries what the network would: the PC's TLS connections, each handed to
 * CompanionService as if it had come over the network, and the display's
 * sealed datagrams. Pairing, TLS and AES-GCM stay end to end: the cable is
 * trusted with nothing, and no socket here listens for other apps.
 *
 * A frame: kind u8 | connection u8 | length u32 (big-endian) | data.
 * HELLO from the PC starts afresh (what was open before is closed) and is
 * answered with HELLO, so the PC knows the app reads.
 */
class UsbTunnel(
    private val service: CompanionService,
    private val fd: ParcelFileDescriptor,
    private val onBroken: () -> Unit,  // the stream failed, not stop(): the PC reset it, or the cable went
) {
    private val input = DataInputStream(Transfers(FileInputStream(fd.fileDescriptor)))
    private val output = FileOutputStream(fd.fileDescriptor)
    private val pipes = ConcurrentHashMap<Int, Socket>()  // connection -> our end of its pipe
    private val loopback = InetAddress.getLoopbackAddress()
    @Volatile private var running = true

    fun start() {
        Thread(::receive, "plusone-usb-in").start()
    }

    fun stop() {
        if (!running) return
        running = false
        Log.i(CompanionService.TAG, "usb: the cable's link ended")
        closeAll()
        runCatching { fd.close() }
    }

    private fun receive() {
        try {
            while (running) {
                val kind = input.readUnsignedByte()
                val id = input.readUnsignedByte()
                val length = input.readInt()
                require(length in 0..MAX_FRAME) { "a frame of $length bytes" }
                val data = ByteArray(length).also { input.readFully(it) }
                when (kind) {
                    HELLO -> {
                        closeAll()
                        send(HELLO, 0, ByteArray(0))
                        Log.i(CompanionService.TAG, "usb: a PC at the other end of the cable")
                    }
                    OPEN -> open(id)
                    // ponytail: a pipe slow to read holds the cable's other frames
                    // (loopback buffers make it rare); a queue per pipe if it shows.
                    DATA -> pipes[id]?.let { pipe ->
                        try { pipe.getOutputStream().write(data) } catch (e: Exception) { close(id, tell = true) }
                    }
                    CLOSE -> close(id, tell = false)
                    DATAGRAM -> DisplaySession.current?.fromTunnel(data) { send(DATAGRAM, 0, it) }
                }
            }
        } catch (e: Exception) {
            if (running) Log.i(CompanionService.TAG, "usb: the cable's stream: $e")
        }
        val broken = running
        stop()
        if (broken) onBroken()
    }

    /** A connection from the PC: a loopback pipe, its far end through TLS
     * as the server, to the service as any other connection. */
    private fun open(id: Int) {
        close(id, tell = false)
        val (ours, theirs) = try { pipe() } catch (e: Exception) {
            Log.w(CompanionService.TAG, "usb: no pipe: $e")
            return send(CLOSE, id, ByteArray(0))
        }
        pipes[id] = ours
        val tls = (Identity.sslContext().socketFactory.createSocket(theirs, "usb", theirs.port, true) as SSLSocket)
            .apply {
                useClientMode = false
                needClientAuth = true
                enabledProtocols = arrayOf("TLSv1.3")
            }
        service.accept(tls)
        Thread({
            val buffer = ByteArray(CHUNK)
            try {
                val stream = ours.getInputStream()
                while (true) {
                    val n = stream.read(buffer)
                    if (n < 0) break
                    send(DATA, id, buffer.copyOf(n))
                }
            } catch (e: Exception) {
            }
            if (pipes.remove(id, ours)) send(CLOSE, id, ByteArray(0))
            runCatching { ours.close() }
        }, "plusone-usb-$id").start()
    }

    private fun close(id: Int, tell: Boolean) {
        pipes.remove(id)?.let { runCatching { it.close() } }
        if (tell) send(CLOSE, id, ByteArray(0))
    }

    private fun closeAll() {
        for (id in pipes.keys.toList()) close(id, tell = false)
    }

    /** Two connected loopback sockets. Another app may connect to the
     * port for that instant too: only the one from our own end is taken. */
    private fun pipe(): Pair<Socket, Socket> = ServerSocket(0, 4, loopback).use { server ->
        server.soTimeout = PIPE_TIMEOUT
        val ours = Socket(loopback, server.localPort).apply { tcpNoDelay = true }
        while (true) {
            val theirs = server.accept()
            if (theirs.port == ours.localPort) return ours to theirs.apply { tcpNoDelay = true }
            theirs.close()
        }
        @Suppress("UNREACHABLE_CODE") error("unreachable")
    }

    /** One frame, whole, from any thread. */
    private fun send(kind: Int, id: Int, data: ByteArray) {
        val frame = ByteBuffer.allocate(HEADER + data.size)
            .put(kind.toByte()).put(id.toByte()).putInt(data.size).put(data).array()
        try {
            synchronized(output) { output.write(frame) }
        } catch (e: Exception) {
            if (running) Log.i(CompanionService.TAG, "usb: cannot send: $e")
            stop()
        }
    }

    /** The accessory's stream as the driver allows it: read in whole
     * transfers (a smaller read loses the rest of one), and never asked
     * what is available (BufferedInputStream does: the driver says EINVAL). */
    private class Transfers(private val input: FileInputStream) : InputStream() {
        private val buffer = ByteArray(BUFFER)
        private var at = 0
        private var end = 0

        private fun fill(): Boolean {
            while (at == end) {
                val n = input.read(buffer, 0, buffer.size)
                if (n < 0) return false
                at = 0
                end = n
            }
            return true
        }

        override fun read(): Int = if (fill()) buffer[at++].toInt() and 0xFF else -1

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (len == 0) return 0
            if (!fill()) return -1
            val n = minOf(len, end - at)
            System.arraycopy(buffer, at, b, off, n)
            at += n
            return n
        }

        override fun available() = end - at
    }

    companion object {
        const val HELLO = 0
        const val OPEN = 1
        const val DATA = 2
        const val CLOSE = 3
        const val DATAGRAM = 4
        const val HEADER = 6
        const val CHUNK = 16 * 1024          // bytes of a pipe read, one frame
        const val BUFFER = 16 * 1024         // the accessory's reads: its driver's transfer size (more: EINVAL)
        const val MAX_FRAME = 1024 * 1024
        const val PIPE_TIMEOUT = 2_000       // ms for our own end to connect
    }
}
