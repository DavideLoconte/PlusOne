package io.github.gabrielesantangelo.plusone

import android.content.Context
import android.util.Log
import org.json.JSONObject
import org.webrtc.AudioSource
import org.webrtc.Camera2Enumerator
import org.webrtc.CameraVideoCapturer
import org.webrtc.DataChannel
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoSource
import org.webrtc.audio.JavaAudioDeviceModule
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/**
 * The tablet's camera or microphone for the PC (plusone/rtc.py), over WebRTC:
 * libwebrtc captures, processes the sound (gain control, noise suppression),
 * encodes, paces and adapts to the network (congestion control, NACK, key
 * frames on request). The PC's offer and this answer go over the paired TLS
 * link, which vouches for the DTLS fingerprints in them; the media go
 * straight between the two (ICE, host addresses: the same network).
 */
class RtcCapture(private val context: Context, private val request: JSONObject, private val noiseReduction: Boolean) {
    private val camera = request.optString("type") == "camera"
    private var pc: PeerConnection? = null
    private var capturer: CameraVideoCapturer? = null
    private var videoSource: VideoSource? = null
    private var audioSource: AudioSource? = null
    private var helper: SurfaceTextureHelper? = null
    private var tracks = listOf<MediaStreamTrack>()

    /** The answer to the PC's offer, ICE candidates gathered in it. Throws if it cannot. */
    fun answer(offer: String): String {
        val factory = Rtc.factory(context)
        val gathered = CompletableFuture<Unit>()
        val connection = factory.createPeerConnection(
            PeerConnection.RTCConfiguration(emptyList()).apply {
                sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
                bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE
                rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE
                continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE
                // Packets marked by priority (DSCP): Wi-Fi's voice queue for the
                // sound, which a video key frame's burst then does not hold back.
                enableDscp = true
            },
            object : PeerConnection.Observer {
                override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) {
                    Log.i(TAG, "ICE gathering: $state")
                    if (state == PeerConnection.IceGatheringState.COMPLETE) gathered.complete(Unit)
                }
                override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                    Log.i(TAG, "${request.optString("type")} for the PC: $state")
                }
                override fun onSignalingChange(state: PeerConnection.SignalingState) {}
                override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) {}
                override fun onIceConnectionReceivingChange(receiving: Boolean) {}
                override fun onIceCandidate(candidate: IceCandidate) {
                    Log.i(TAG, "ICE candidate: ${candidate.sdp}")
                }
                override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) {}
                override fun onAddStream(stream: MediaStream) {}
                override fun onRemoveStream(stream: MediaStream) {}
                override fun onDataChannel(channel: DataChannel) {}
                override fun onRenegotiationNeeded() {}
                override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) {}
            }) ?: throw IllegalStateException("no peer connection")
        pc = connection
        val track = if (camera) cameraTrack(factory) else microphoneTrack(factory)
        tracks = listOf(track)
        val sender = connection.addTrack(track, listOf("plusone"))
        sender.parameters = sender.parameters.apply {
            encodings.forEach {
                if (camera) {
                    // Up to the bitrate the PC asked for; congestion control goes lower.
                    it.maxBitrateBps = request.optInt("bitrate", 2_500_000)
                } else {
                    it.networkPriority = HIGH
                }
            }
        }
        await { connection.setRemoteDescription(it, SessionDescription(SessionDescription.Type.OFFER, offer)) }
        Log.i(TAG, "${request.optString("type")}: the PC's offer taken")
        val answer = CompletableFuture<SessionDescription>()
        connection.createAnswer(object : Done() {
            override fun onCreateSuccess(description: SessionDescription) { answer.complete(description) }
            override fun onCreateFailure(error: String) { answer.completeExceptionally(IllegalStateException(error)) }
        }, MediaConstraints())
        val local = answer.get(WAIT, TimeUnit.MILLISECONDS)
        await { connection.setLocalDescription(it, local) }
        // The candidates in the answer: all of them, or those found by then
        // (the host ones come at once; some interface may never say it is done).
        try {
            gathered.get(GATHER, TimeUnit.MILLISECONDS)
        } catch (e: java.util.concurrent.TimeoutException) {
            Log.i(TAG, "ICE still gathering: answering with what it has")
        }
        val description = connection.localDescription.description
        Log.i(TAG, "${request.optString("type")}: answer with ${description.lines().count { it.startsWith("a=candidate") }} candidates")
        return description
    }

    private fun cameraTrack(factory: PeerConnectionFactory): MediaStreamTrack {
        val enumerator = Camera2Enumerator(context)
        val front = request.optString("facing") != "back"
        val name = enumerator.deviceNames.firstOrNull { enumerator.isFrontFacing(it) == front }
            ?: enumerator.deviceNames.first()
        val capturer = enumerator.createCapturer(name, null)
        val source = factory.createVideoSource(false)
        val helper = SurfaceTextureHelper.create("plusone-camera", Rtc.egl.eglBaseContext)
        capturer.initialize(helper, context, source.capturerObserver)
        capturer.startCapture(request.optInt("width", 1280).coerceIn(320, 1920),
            request.optInt("height", 720).coerceIn(240, 1080), request.optInt("fps", 30).coerceIn(10, 30))
        this.capturer = capturer
        this.videoSource = source
        this.helper = helper
        return factory.createVideoTrack("camera", source)
    }

    private fun microphoneTrack(factory: PeerConnectionFactory): MediaStreamTrack {
        val files = context.getExternalFilesDir(null)
        if (files != null && java.io.File(files, Rtc.RECORD_FLAG).exists()) {
            Rtc.recording = Rtc.Recording(java.io.File(files, Rtc.RECORD_FILE))
        }
        // libwebrtc's own processing: its gain control and, if asked, its
        // noise suppression. No echo cancelling: the tablet plays nothing back.
        val constraints = MediaConstraints().apply {
            mandatory += MediaConstraints.KeyValuePair("googAutoGainControl", "true")
            mandatory += MediaConstraints.KeyValuePair("googNoiseSuppression", noiseReduction.toString())
            mandatory += MediaConstraints.KeyValuePair("googHighpassFilter", "true")
            mandatory += MediaConstraints.KeyValuePair("googEchoCancellation", "false")
        }
        val source = factory.createAudioSource(constraints)
        audioSource = source
        return factory.createAudioTrack("microphone", source)
    }

    fun stop() {
        if (!camera) Rtc.recording?.let { Rtc.recording = null; it.close() }
        try { capturer?.stopCapture() } catch (e: InterruptedException) {}
        capturer?.dispose()
        pc?.dispose()  // its senders and tracks with it
        videoSource?.dispose()
        audioSource?.dispose()
        helper?.dispose()
        pc = null
        capturer = null
    }

    /** Runs a set*Description and waits for it. */
    private fun await(call: (SdpObserver) -> Unit) {
        val done = CompletableFuture<Unit>()
        call(object : Done() {
            override fun onSetSuccess() { done.complete(Unit) }
            override fun onSetFailure(error: String) { done.completeExceptionally(IllegalStateException(error)) }
        })
        done.get(WAIT, TimeUnit.MILLISECONDS)
    }

    private open class Done : SdpObserver {
        override fun onCreateSuccess(description: SessionDescription) {}
        override fun onSetSuccess() {}
        override fun onCreateFailure(error: String) {}
        override fun onSetFailure(error: String) {}
    }

    companion object {
        const val TAG = "PlusOne"
        const val WAIT = 5_000L    // ms for each step of the answer
        const val HIGH = 3         // webrtc::Priority::kHigh
        const val GATHER = 5_000L  // ms for ICE's candidates, at most
    }
}

/** libwebrtc, set up once for the process. */
object Rtc {
    val egl: EglBase by lazy { EglBase.create() }
    private var factory: PeerConnectionFactory? = null

    /** For measuring (adb only): with the file RECORD_FLAG in the app's files,
     * what the microphone captures, before libwebrtc's processing, goes to
     * RECORD_FILE: each piece's wall-clock time (ms), its size, its samples. */
    @Volatile var recording: Recording? = null

    class Recording(file: java.io.File) {
        private val out = java.io.DataOutputStream(java.io.BufferedOutputStream(java.io.FileOutputStream(file)))

        @Synchronized
        fun write(samples: JavaAudioDeviceModule.AudioSamples) {
            out.writeLong(System.currentTimeMillis())
            out.writeInt(samples.data.size)
            out.write(samples.data)
        }

        @Synchronized
        fun close() = out.close()
    }

    const val RECORD_FLAG = "record-microphone"
    const val RECORD_FILE = "microphone.raw"

    /** The factory, one for the process (noise reduction is each audio source's). */
    @Synchronized
    fun factory(context: Context): PeerConnectionFactory {
        factory?.let { return it }
        PeerConnectionFactory.initialize(PeerConnectionFactory.InitializationOptions.builder(context.applicationContext)
            .createInitializationOptions())
        // The plain microphone: the voice call source (libwebrtc's default) is
        // far too quiet on a Galaxy Tab. Noise suppression is libwebrtc's own.
        val audio = JavaAudioDeviceModule.builder(context.applicationContext)
            .setAudioSource(android.media.MediaRecorder.AudioSource.MIC)
            .setSamplesReadyCallback { samples -> recording?.write(samples) }
            .setUseHardwareNoiseSuppressor(false)
            .setUseHardwareAcousticEchoCanceler(false)
            .createAudioDeviceModule()
        val made = PeerConnectionFactory.builder()
            .setAudioDeviceModule(audio)
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl.eglBaseContext, true, true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
            .createPeerConnectionFactory()
        audio.release()  // the factory holds it now
        factory = made
        return made
    }
}
