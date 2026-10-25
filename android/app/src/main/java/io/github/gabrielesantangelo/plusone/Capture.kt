package io.github.gabrielesantangelo.plusone

import android.annotation.SuppressLint
import android.content.Context
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.hardware.display.DisplayManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.MediaRecorder
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Size
import android.view.Display
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/**
 * The tablet's camera or microphone for the PC (plusone/devices.py): what
 * it captures, encoded here (H.264, Opus) and sent on the connection the PC
 * asked on, until it hangs up. The PC asks only while one of its programs
 * uses the device, so nothing goes over the network otherwise.
 */
abstract class Capture(protected val wire: Wire) {
    @Volatile protected var stopped = false

    /** Starts capturing; returns what the PC is to expect. Throws if it cannot. */
    abstract fun start(): JSONObject

    /** Ends it, whichever thread asks (the PC hung up, the link broke). */
    abstract fun stop()

    /** Sends one encoded piece; a link that is gone ends the capture. */
    protected fun send(type: String, info: MediaCodec.BufferInfo, data: ByteArray) {
        try {
            wire.send(JSONObject().put("type", type).put("pts", info.presentationTimeUs)
                .put("key", info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0), data)
        } catch (e: IOException) {
            stop()
        }
    }

    /** The codec's output, sent as it comes, until stopped; then the codec goes. */
    protected fun drain(codec: MediaCodec, type: String) {
        val info = MediaCodec.BufferInfo()
        try {
            while (!stopped) {
                val index = codec.dequeueOutputBuffer(info, DRAIN_WAIT)
                if (index < 0) continue
                val buffer = codec.getOutputBuffer(index)
                // Opus's header: the PC knows it (one channel, 48 kHz).
                val skip = type == "audio" && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0
                if (buffer != null && info.size > 0 && !skip) {
                    val data = ByteArray(info.size)
                    buffer.position(info.offset)
                    buffer.get(data)
                    send(type, info, data)
                }
                codec.releaseOutputBuffer(index, false)
            }
        } catch (e: IllegalStateException) {
            // Stopped from another thread meanwhile.
        } finally {
            try { codec.stop() } catch (e: IllegalStateException) {}
            codec.release()
        }
    }

    companion object {
        const val TAG = "PlusOne"
        const val DRAIN_WAIT = 100_000L  // µs
    }
}

/** The camera, as H.264 frames the size and rate the PC asked for (about). */
class CameraCapture(private val context: Context, wire: Wire, request: JSONObject) : Capture(wire) {
    private val facing = if (request.optString("facing") == "back") CameraCharacteristics.LENS_FACING_BACK
        else CameraCharacteristics.LENS_FACING_FRONT
    private val wanted = Size(request.optInt("width", 1280).coerceIn(320, 1920),
        request.optInt("height", 720).coerceIn(240, 1080))
    private val fps = request.optInt("fps", 30).coerceIn(10, 30)
    private val bitrate = request.optInt("bitrate", 2_500_000).coerceIn(250_000, 8_000_000)
    private val thread = HandlerThread("plusone-camera").apply { start() }
    private val handler = Handler(thread.looper)
    private val executor = Executor { handler.post(it) }
    @Volatile private var camera: CameraDevice? = null
    @Volatile private var session: CameraCaptureSession? = null

    @SuppressLint("MissingPermission")  // CompanionService checks it first
    override fun start(): JSONObject {
        val manager = context.getSystemService(CameraManager::class.java)
        val id = manager.cameraIdList.firstOrNull {
            manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == facing
        } ?: manager.cameraIdList.first()
        val traits = manager.getCameraCharacteristics(id)
        val map = traits.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)!!
        val size = pick(map.getOutputSizes(MediaCodec::class.java))
        val range = traits.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            ?.filter { it.upper == fps }?.maxByOrNull { it.lower }  // steady if it can be

        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, size.width, size.height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
            setInteger(MediaFormat.KEY_PRIORITY, 0)  // real time
            setInteger(MediaFormat.KEY_LATENCY, 1)
            // A program that starts reading midway gets going at the next key frame.
            setInteger(MediaFormat.KEY_PREPEND_HEADER_TO_SYNC_FRAMES, 1)
        }
        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val surface = codec.createInputSurface()
        codec.start()
        Thread({ drain(codec, "video") }, "plusone-camera-out").start()

        try {
            val opened = CompletableFuture<CameraDevice>()
            manager.openCamera(id, executor, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    camera = device
                    opened.complete(device)
                }
                override fun onDisconnected(device: CameraDevice) {
                    opened.completeExceptionally(IOException("camera taken by another app"))
                    stop()
                }
                override fun onError(device: CameraDevice, error: Int) {
                    opened.completeExceptionally(IOException("camera error $error"))
                    stop()
                }
            })
            val device = opened.get(OPEN_WAIT, TimeUnit.MILLISECONDS)
            val configured = CompletableFuture<CameraCaptureSession>()
            device.createCaptureSession(SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
                listOf(OutputConfiguration(surface)), executor, object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(s: CameraCaptureSession) {
                        session = s
                        configured.complete(s)
                    }
                    override fun onConfigureFailed(s: CameraCaptureSession) {
                        configured.completeExceptionally(IOException("camera session refused"))
                    }
                }))
            val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                addTarget(surface)
                range?.let { set(android.hardware.camera2.CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it) }
            }
            configured.get(OPEN_WAIT, TimeUnit.MILLISECONDS).setRepeatingRequest(request.build(), null, handler)
        } catch (e: Exception) {
            stop()
            throw e
        }
        Log.i(TAG, "camera $id: ${size.width}x${size.height} at $fps fps for the PC")
        return JSONObject().put("type", "camera").put("width", size.width).put("height", size.height)
            .put("fps", fps).put("rotation", rotation(traits))
    }

    /** The size asked for, else the biggest no larger of its shape, else the nearest. */
    private fun pick(sizes: Array<Size>): Size {
        sizes.firstOrNull { it == wanted }?.let { return it }
        val shape = wanted.width.toFloat() / wanted.height
        return sizes.filter { it.width <= wanted.width && it.height <= wanted.height &&
                abs(it.width.toFloat() / it.height - shape) < 0.02f }.maxByOrNull { it.width * it.height }
            ?: sizes.minBy { abs(it.width * it.height - wanted.width * wanted.height) }
    }

    /** Degrees clockwise the PC turns the picture by, for it to stand upright as
     * the tablet is held now (the sensor sits turned in the tablet). */
    private fun rotation(traits: CameraCharacteristics): Int {
        val sensor = traits.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        val display = context.getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
        val degrees = (display?.rotation ?: 0) * 90
        return if (facing == CameraCharacteristics.LENS_FACING_FRONT) (sensor + degrees) % 360
            else (sensor - degrees + 360) % 360
    }

    override fun stop() {
        if (stopped) return
        stopped = true
        try { session?.close() } catch (e: Exception) {}
        try { camera?.close() } catch (e: Exception) {}
        thread.quitSafely()
    }

    companion object {
        const val OPEN_WAIT = 5_000L  // ms
    }
}

/** The microphone, as Opus packets (or plain samples where there is no Opus encoder). */
class MicCapture(wire: Wire, request: JSONObject) : Capture(wire) {
    private val bitrate = request.optInt("bitrate", 32_000).coerceIn(12_000, 128_000)
    private var record: AudioRecord? = null
    private var gain: AutomaticGainControl? = null
    private var quiet: NoiseSuppressor? = null

    @SuppressLint("MissingPermission")  // CompanionService checks it first
    override fun start(): JSONObject {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        // The plain microphone, brought to a microphone's level by Android's own
        // gain control (the voice call source is far too quiet on a Galaxy Tab),
        // and its noise suppression where there is one.
        val record = AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, maxOf(min, FRAME_BYTES * 4))
        check(record.state == AudioRecord.STATE_INITIALIZED) { "the microphone is not available" }
        this.record = record
        if (AutomaticGainControl.isAvailable()) gain = AutomaticGainControl.create(record.audioSessionId)?.apply { enabled = true }
        if (NoiseSuppressor.isAvailable()) quiet = NoiseSuppressor.create(record.audioSessionId)?.apply { enabled = true }
        val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, RATE, 1).apply {
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
        }
        val codec = MediaCodecList(MediaCodecList.REGULAR_CODECS).findEncoderForFormat(format)?.let {
            MediaCodec.createByCodecName(it).apply {
                configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                start()
            }
        }
        record.startRecording()
        Thread({ pump(record, codec) }, "plusone-mic").start()
        if (codec != null) Thread({ drain(codec, "audio") }, "plusone-mic-out").start()
        Log.i(TAG, "microphone for the PC: ${if (codec != null) "Opus" else "PCM"}, gain control ${gain != null}")
        return JSONObject().put("type", "microphone").put("codec", if (codec != null) "opus" else "pcm")
            .put("rate", RATE).put("channels", 1)
    }

    /** 20 ms at a time: to the encoder, or as they are. */
    private fun pump(record: AudioRecord, codec: MediaCodec?) {
        val frame = ByteArray(FRAME_BYTES)
        val info = MediaCodec.BufferInfo()
        var samples = 0L
        try {
            while (!stopped) {
                val n = record.read(frame, 0, frame.size)
                if (n <= 0) continue
                val pts = samples * 1_000_000 / RATE
                samples += n / 2
                if (codec == null) {
                    info.set(0, n, pts, 0)
                    send("audio", info, frame.copyOf(n))
                    continue
                }
                val index = codec.dequeueInputBuffer(DRAIN_WAIT)
                if (index < 0) continue  // the encoder is behind: this piece goes
                codec.getInputBuffer(index)?.apply { clear(); put(frame, 0, n) }
                codec.queueInputBuffer(index, 0, n, pts, 0)
            }
        } catch (e: IllegalStateException) {
            // The encoder went meanwhile.
        } finally {
            gain?.release()
            quiet?.release()
            record.stop()
            record.release()
        }
    }

    override fun stop() {
        stopped = true
    }

    companion object {
        const val RATE = 48_000
        const val FRAME_BYTES = RATE / 50 * 2  // 20 ms of 16-bit mono
    }
}
