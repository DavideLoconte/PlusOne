package io.github.gabrielesantangelo.plusone

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.ByteBuffer

/**
 * The PC's sound on the speakers as it is (plusone/pcm.py): 16-bit stereo PCM
 * at 48 kHz, no codec, in sealed packets of 5 ms, played with a plain media
 * AudioTrack, as the tablet's own players do. (libwebrtc played it before:
 * made for calls, its jitter buffer stretched and squeezed the music.)
 *
 * PREFILL packets wait before it starts, against the Wi-Fi's unevenness. The
 * two clocks drift apart (the PC's and the tablet's sound chip, tens of parts
 * per million): the playback rate follows by at most RATE_TRIM, which no ear
 * hears, so the wait neither grows nor runs dry. A packet that does not come
 * is the last one again, fading out.
 */
class PcmPlayback(key: ByteArray, private val pc: InetAddress, private val pcPort: Int,
                  private val rate: Int, private val frames: Int) {
    val socket = DatagramSocket(0).apply { receiveBufferSize = 1 shl 20 }
    private val sealed = Sealed(key)
    private val bytes = frames * CHANNELS * 2
    private val packets = HashMap<Int, ByteArray>()  // by number, under the lock
    private val lock = Object()
    @Volatile private var running = true
    private var next = 0          // the next to play
    private var started = false
    private var missed = 0
    private var late = 0
    private var held = PREFILL.toDouble()  // packets waiting, smoothed
    private val track = AudioTrack.Builder()
        .setAudioAttributes(AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            .build())
        .setAudioFormat(AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(rate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
            .build())
        .setTransferMode(AudioTrack.MODE_STREAM)
        .setBufferSizeInBytes(maxOf(AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_STEREO,
            AudioFormat.ENCODING_PCM_16BIT), bytes * 4))
        .build()
    private val receiver = Thread(::receive, "plusone-speakers-in")
    private val player = Thread(::play, "plusone-speakers-out")

    fun start() {
        receiver.start()
        player.start()
    }

    fun stop() {
        running = false
        socket.close()
        synchronized(lock) { lock.notifyAll() }
        player.join(1000)
        try {
            track.stop()
        } catch (e: IllegalStateException) {
        }
        track.release()
    }

    private fun receive() {
        val buffer = ByteArray(2048)
        val packet = DatagramPacket(buffer, buffer.size)
        while (running) {
            try {
                socket.receive(packet)
            } catch (e: Exception) {
                break
            }
            if (packet.address != pc) continue
            val body = sealed.open(buffer, packet.length) ?: continue
            if (body.size != 5 + bytes || body[0] != PCM) continue
            val number = ByteBuffer.wrap(body, 1, 4).int
            synchronized(lock) {
                if (started && number - next < 0) {
                    late++
                } else if (packets.size < MAX_HELD) {
                    packets[number] = body.copyOfRange(5, body.size)
                    lock.notifyAll()
                }
            }
        }
    }

    private fun play() {
        var last = ByteArray(bytes)
        var played = 0
        while (running) {
            val data: ByteArray = synchronized(lock) {
                if (!started) {
                    // The first packets: wait for PREFILL of them, from the oldest.
                    while (running && packets.size < PREFILL) lock.wait()  // stop() wakes it too
                    if (!running) return
                    next = packets.keys.minWithOrNull { a, b -> a - b }!!
                    started = true
                    track.play()
                }
                var got = packets.remove(next)
                if (got == null && packets.isEmpty()) {
                    lock.wait(WAIT_MS)  // a moment for it: the Wi-Fi was slow
                    got = packets.remove(next)
                }
                if (got == null && packets.isEmpty()) {
                    // Nothing for a while: the PC stopped, or the network. Start
                    // again from a full wait when it comes back.
                    started = false
                    track.pause()
                    track.flush()
                    return@synchronized null
                }
                next++
                held = held * 0.99 + packets.size * 0.01
                got ?: fade(last).also { missed++ }
            } ?: continue
            last = data
            track.write(data, 0, data.size)
            if (++played % TRIM_EVERY == 0) trim()
        }
    }

    /** The playback rate, a hair faster while more than PREFILL waits, slower while less. */
    private fun trim() {
        val error = (held - PREFILL) / PREFILL
        val factor = 1.0 + (error * RATE_TRIM).coerceIn(-RATE_TRIM, RATE_TRIM)
        track.playbackRate = (rate * factor).toInt()
        val report = ByteBuffer.allocate(13).put(STATS).putInt((held * frames).toInt()).putInt(missed).putInt(late).array()
        missed = 0
        late = 0
        try {
            val packet = sealed.seal(report)
            socket.send(DatagramPacket(packet, packet.size, pc, pcPort))
        } catch (e: Exception) {
            if (running) Log.w(CompanionService.TAG, "speakers: cannot report: $e")
        }
    }

    /** The last packet again, fading out: a lost one is barely heard. */
    private fun fade(last: ByteArray): ByteArray {
        val out = ByteArray(bytes)
        val samples = ByteBuffer.wrap(last).order(java.nio.ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val faded = ByteBuffer.wrap(out).order(java.nio.ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val n = samples.limit()
        for (i in 0 until n) faded.put(i, (samples.get(i) * (n - i) / n).toShort())
        return out
    }

    companion object {
        const val PCM: Byte = 7
        const val STATS: Byte = 5
        const val CHANNELS = 2
        const val PREFILL = 8         // packets of 5 ms: 40 ms
        const val MAX_HELD = 200      // a second: more is a stuck clock, not jitter
        const val WAIT_MS = 30L
        const val TRIM_EVERY = 200    // packets: once a second
        const val RATE_TRIM = 0.002   // at most 0.2 %
    }
}
