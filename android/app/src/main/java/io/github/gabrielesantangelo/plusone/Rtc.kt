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
        val factory = Rtc.factory(context, noiseReduction)
        val gathered = CompletableFuture<Unit>()
        val connection = factory.createPeerConnection(
            PeerConnection.RTCConfiguration(emptyList()).apply {
                sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
                bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE
                rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE
                continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE
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
        if (camera) {
            // Up to the bitrate the PC asked for; congestion control goes lower.
            sender.parameters = sender.parameters.apply {
                encodings.forEach { it.maxBitrateBps = request.optInt("bitrate", 2_500_000) }
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
        // libwebrtc's own processing: its gain control (the Galaxy Tab's
        // microphone is far too quiet as it comes) and, if asked, its noise
        // suppression. No echo cancelling: the tablet plays nothing back.
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
        const val GATHER = 5_000L  // ms for ICE's candidates, at most
    }
}

/** libwebrtc, set up once for the process. */
object Rtc {
    val egl: EglBase by lazy { EglBase.create() }
    private var factory: PeerConnectionFactory? = null
    private var noise: Boolean? = null

    /** The factory, made again when the noise reduction asked for changes
     * (the audio device's own, Android's, goes on or off with it). */
    @Synchronized
    fun factory(context: Context, noiseReduction: Boolean): PeerConnectionFactory {
        factory?.let { if (noise == noiseReduction) return it }
        if (factory == null && noise == null) {
            PeerConnectionFactory.initialize(PeerConnectionFactory.InitializationOptions.builder(context.applicationContext)
                .createInitializationOptions())
        }
        val audio = JavaAudioDeviceModule.builder(context.applicationContext)
            .setUseHardwareNoiseSuppressor(noiseReduction)
            .setUseHardwareAcousticEchoCanceler(false)
            .createAudioDeviceModule()
        val made = PeerConnectionFactory.builder()
            .setAudioDeviceModule(audio)
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(egl.eglBaseContext, true, true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(egl.eglBaseContext))
            .createPeerConnectionFactory()
        audio.release()  // the factory holds it now
        factory = made
        noise = noiseReduction
        return made
    }
}
