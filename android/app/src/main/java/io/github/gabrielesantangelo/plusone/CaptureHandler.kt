package io.github.gabrielesantangelo.plusone

import android.Manifest
import android.content.pm.PackageManager
import android.util.Log
import io.github.gabrielesantangelo.plusone.link.Handler
import io.github.gabrielesantangelo.plusone.link.Link
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/** A program on the PC uses the camera or the microphone: sent over WebRTC
 * (RtcCapture) to the PC that offered on the link, until it hangs up. Only
 * if the user lets the PC have it, here. */
class CaptureHandler(private val service: CompanionService) : Handler {
    override val types = setOf("camera", "microphone")

    private val asked = ConcurrentHashMap<String, Long>()  // kind -> when the user was

    override fun handle(link: Link, request: JSONObject, size: Long): Boolean {
        val kind = request.optString("type")
        val camera = kind == "camera"
        // Not let, or Android not asked yet: the user is asked here and now
        // (a notification, once a minute at most: the PC asks again every 10 s).
        fun ask(reason: String): Boolean {
            val now = System.currentTimeMillis()
            if (now - (asked[kind] ?: 0L) > ASK_AGAIN) {
                asked[kind] = now
                Notifications.askDevice(service, kind, link.pc)
            }
            link.refuse(reason)
            return false
        }
        if (!(if (camera) Options.camera(service) else Options.microphone(service))) return ask("off")
        val permission = if (camera) Manifest.permission.CAMERA else Manifest.permission.RECORD_AUDIO
        if (service.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) return ask("permission")
        Notifications.cancelDevice(service, kind)  // allowed now
        val offer = request.optString("sdp")
        if (offer.isEmpty()) {
            link.refuse("unavailable")  // a PC of before WebRTC
            return false
        }
        // The PC says whether libwebrtc takes the noise out: not when it does
        // itself (a neural network there), nor when its user wants none. A
        // camera or microphone that fails ends the session (the PC tries
        // again), not an empty stream.
        val capture = RtcCapture(service, request, request.optBoolean("noise_reduction", true)) { link.close() }
        service.began(capture, CompanionService.Session(kind, link))
        try {
            if (!service.allowedTypes(kind) && !service.foreground(kind)) {
                link.refuse("background")
                return false
            }
            val answer = try {
                capture.answer(offer)
            } catch (e: Exception) {
                Log.w(CompanionService.TAG, "$kind not available: $e")
                link.refuse("unavailable")
                return false
            }
            link.send(JSONObject().put("type", "answer").put("sdp", answer))
            service.showSessions()
            service.lowLatency(true)
            // The PC holds it as long as its program uses the device, and pings.
            link.hold()
        } finally {
            capture.stop()
            service.ended(capture)
        }
        return false
    }

    companion object {
        const val ASK_AGAIN = 60_000L  // ms between notifications asking for the camera or microphone
    }
}
