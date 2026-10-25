package io.github.gabrielesantangelo.plusone

import android.Manifest
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom
import java.util.concurrent.CompletableFuture
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

/**
 * Always ready for the PC: listens over TLS on the home network and says so
 * with mDNS (_plusone._tcp), so Plus One on the PC can hand it a PDF to sign.
 * A foreground service (Android shows it as a quiet notification); it does
 * nothing until the PC connects.
 */
class CompanionService : Service() {
    @Volatile private var server: SSLServerSocket? = null
    @Volatile private var registration: NsdManager.RegistrationListener? = null
    @Volatile private var stopped = false
    // A connection per PDF: a PC sending a batch has a few dozen at most
    // (companion.py MAX_JOBS); more connections are turned away.
    private val connections = ThreadPoolExecutor(0, MAX_CONNECTIONS, 60, TimeUnit.SECONDS, SynchronousQueue())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        Notifications.channels(this)
        try {
            startForeground(Notifications.SERVICE_ID, Notifications.ready(this),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } catch (e: Exception) {
            // Restarted from the background (START_STICKY) where Android does
            // not allow it: the next time the app opens starts it again.
            Log.w(TAG, "foreground service not allowed now: $e")
            stopSelf()
            return
        }
        // PDFs of jobs this process no longer has (it was ended meanwhile).
        File(cacheDir, "jobs").listFiles()?.forEach { if (it.nameWithoutExtension !in Jobs.ids) it.delete() }
        Jobs.listen(queueChanged)
        queueChanged()  // nor a notification for them
        Thread(::serve, "plusone-server").start()
        handler.postDelayed(::idleCheck, IDLE_CHECK)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        lastUse = System.currentTimeMillis()  // the app was opened, or a job came
        return if (Options.alwaysReady(this)) START_STICKY else START_NOT_STICKY
    }

    // Not always ready: stop once nothing has happened for a while.
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    @Volatile private var lastUse = System.currentTimeMillis()
    private val busy = AtomicInteger()  // PCs connected
    private val unpaired = AtomicInteger()  // connections not yet known to be a paired PC

    private fun idleCheck() {
        // PDFs whose PC stopped looking for the tablet.
        for (job in Jobs.expire()) if (job.ownsFile) job.file.delete()
        if (!Options.alwaysReady(this) && busy.get() == 0 && Jobs.ids.isEmpty() &&
            System.currentTimeMillis() - lastUse > IDLE_STOP) {
            stopSelf()
            return
        }
        handler.postDelayed(::idleCheck, IDLE_CHECK)
    }

    /** The notification for what waits to be signed: it alerts when the
     * screen cannot open by itself, and only for the first PDF of a batch. */
    private val queueChanged: () -> Unit = {
        Notifications.waiting(this, Jobs.waiting(), alert = !Settings.canDrawOverlays(this))
    }

    override fun onDestroy() {
        instance = null
        Jobs.unlisten(queueChanged)
        handler.removeCallbacksAndMessages(null)
        synchronized(this) {
            stopped = true
            registration?.let { runCatching { (getSystemService(NSD_SERVICE) as NsdManager).unregisterService(it) } }
            registration = null
            server?.close()
        }
        connections.shutdownNow()
        super.onDestroy()
    }

    private fun serve() {
        // Not listening is not ready: tried again (the keystore's first key
        // may fail once) instead of a "Ready" nobody can reach.
        var socket: SSLServerSocket? = null
        while (socket == null) {
            if (stopped) return
            socket = try {
                val factory = Identity.sslContext().serverSocketFactory
                // A port the PC knows: over Wi-Fi Direct it checks who the
                // tablet is before mDNS has said where the app listens.
                (try { factory.createServerSocket(PORT) } catch (e: java.net.BindException) {
                    factory.createServerSocket(0)
                }) as SSLServerSocket
            } catch (e: Exception) {
                Log.e(TAG, "cannot listen, again in a while", e)
                Thread.sleep(LISTEN_RETRY)
                null
            }
        }
        socket.needClientAuth = true
        socket.enabledProtocols = arrayOf("TLSv1.3")
        synchronized(this) {
            // Stopped while the first key was being made: nothing to leave behind.
            if (stopped) return socket.close()
            server = socket
            try {
                advertise(socket.localPort)
            } catch (e: Exception) {
                Log.e(TAG, "not on the network by mDNS (the PC still finds it at its port): $e")
            }
        }
        while (!socket.isClosed) {
            val client = try {
                socket.accept() as SSLSocket
            } catch (e: Exception) {
                if (socket.isClosed) break
                Log.w(TAG, "accept: $e")  // out of fds a moment, say: not the end of listening
                Thread.sleep(200)
                continue
            }
            // Small messages go at once: the microphone's are 100 bytes every 20 ms,
            // which Nagle's algorithm would hold back for the PC's delayed ACKs.
            try { client.tcpNoDelay = true } catch (e: Exception) {}
            // Connections not through TLS yet, few at a time: strangers that
            // never finish their handshake cannot take every thread from a paired PC.
            if (handshaking.incrementAndGet() > MAX_HANDSHAKING) {
                handshaking.decrementAndGet()
                client.close()
                continue
            }
            try {
                connections.execute { handle(client) }
            } catch (e: RejectedExecutionException) {
                handshaking.decrementAndGet()
                client.close()
            }
        }
    }

    private fun advertise(port: Int) {
        val info = NsdServiceInfo().apply {
            serviceName = deviceName(this@CompanionService).take(63)  // a DNS label's most
            serviceType = SERVICE_TYPE
            setPort(port)
            setAttribute("id", Identity.fingerprint(Identity.certificate.encoded).take(16))
            // A TXT entry, key and value, is 255 bytes at most.
            setAttribute("name", deviceName(this@CompanionService).take(80))
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.i(TAG, "on the network as ${info.serviceName}, port $port")
            }
            override fun onRegistrationFailed(info: NsdServiceInfo, error: Int) {
                Log.e(TAG, "mDNS registration failed: $error")
                synchronized(this@CompanionService) { if (registration === this) registration = null }
            }
            override fun onServiceUnregistered(info: NsdServiceInfo) {}
            override fun onUnregistrationFailed(info: NsdServiceInfo, error: Int) {}
        }
        registration = listener
        (getSystemService(NSD_SERVICE) as NsdManager).registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
    }

    /** One PC: hello, pairing if either side does not know the other and the
     * PC asks to pair (its user chose this tablet), then jobs. */
    private fun handle(socket: SSLSocket) {
        busy.incrementAndGet()
        lastUse = System.currentTimeMillis()
        var link: Wire? = null
        var stranger = false  // counted in unpaired: not (yet) a paired PC
        var shaking = true  // counted in handshaking
        var fingerprint: String? = null
        try {
            socket.use {
                // Nothing waits long for a peer that is not even paired: the
                // whole exchange (TLS, hello, pairing's messages) in
                // HANDSHAKE_DEADLINE, not only each read, and a few such
                // peers at a time (a paired PC is never turned away for them).
                val guard = Wire.WATCHDOG.schedule({ socket.close() }, HANDSHAKE_DEADLINE, TimeUnit.MILLISECONDS)
                socket.soTimeout = HANDSHAKE_TIMEOUT
                socket.startHandshake()
                handshaking.decrementAndGet()
                shaking = false
                val pc = socket.session.peerCertificates[0].encoded
                fingerprint = Identity.fingerprint(pc)
                val known = Paired.contains(this, fingerprint)
                if (!known) {
                    stranger = true
                    if (unpaired.incrementAndGet() > MAX_UNPAIRED) return
                }
                val wire = Wire(socket)
                val hello = wire.receive(Wire.UNPAIRED_HEADER)
                require(hello.optString("type") == "hello") { "expected hello" }
                val pcName = hello.optString("name", "PC").take(64)
                // "at": the address the PC reached, so it can tell this
                // tablet answered itself (over Wi-Fi Direct, say), not
                // through someone passing the connection on.
                wire.send(JSONObject().put("type", "hello").put("name", deviceName(this))
                    .put("version", PROTOCOL).put("paired", known).put("at", address(socket.localAddress)))
                // Another version: the PC sees ours and says so to its user.
                if (hello.optInt("version") != PROTOCOL) return
                if (!known || !hello.optBoolean("paired")) {
                    // Only when the user pairs, in Plus One's window on the PC.
                    if (!hello.optBoolean("pair")) return
                    if (!pair(socket, wire, pc, pcName, guard)) return
                    Paired.add(this, fingerprint, pcName)
                }
                guard.cancel(false)
                // Its links, closed the moment the user forgets it (disconnect()).
                links.computeIfAbsent(fingerprint) { ConcurrentHashMap.newKeySet() }.add(socket)
                if (!Paired.contains(this, fingerprint)) return  // forgotten just now
                if (stranger) {
                    unpaired.decrementAndGet()
                    stranger = false
                }
                // The PC asks "ping" every 15 s while the user signs.
                socket.soTimeout = PAIRED_TIMEOUT
                link = wire
                while (true) {
                    val message = wire.receive()
                    val type = message.optString("type")
                    val size = message.optLong("size", 0)
                    require(size == 0L || type == "sign") { "unexpected payload" }
                    when (type) {
                        "sign" -> sign(wire, message, size, pcName, fingerprint)
                        "resume" -> {
                            val job = message.optString("job")
                            if (Jobs.resume(job, fingerprint, wire) { answer(wire, job, it.result) }) {
                                wire.send(JSONObject().put("type", "resumed").put("job", job))
                            } else {
                                wire.send(JSONObject().put("type", "unknown-job").put("job", job))
                            }
                        }
                        "got" -> {
                            val job = message.optString("job")
                            if (Jobs.acknowledge(job, fingerprint)) jobFile(job)?.delete()
                        }
                        "ping" -> wire.send(JSONObject().put("type", "pong"))
                        "camera", "microphone" -> return capture(socket, wire, message, type, pcName)
                        "display" -> return display(socket, wire, message, pcName)
                        "speakers" -> return playback(socket, wire, message, pcName)
                        "bye" -> return
                    }
                }
            }
        } catch (e: Throwable) {
            // Errors too: a peer's deeply nested JSON overflows the parser's
            // stack, and an uncaught Error on a pool thread ends the process.
            Log.i(TAG, "connection ended: $e")
        } finally {
            if (shaking) handshaking.decrementAndGet()
            if (stranger) unpaired.decrementAndGet()
            fingerprint?.let { links[it]?.remove(socket) }
            link?.let { Jobs.detach(it) }  // its jobs wait for the PC to come back
            busy.decrementAndGet()
            lastUse = System.currentTimeMillis()
        }
    }

    // Connections still in their TLS handshake, and each paired PC's open links.
    private val handshaking = AtomicInteger()
    private val links = ConcurrentHashMap<String, MutableSet<SSLSocket>>()

    /** The user forgot this PC: what it has open ends now (a capture in
     * progress, a PDF being signed), not when it happens to hang up. */
    fun disconnect(fingerprint: String) {
        links.remove(fingerprint)?.forEach { runCatching { it.close() } }
    }

    // The camera's and the microphone's captures: what, its connection, for which PC.
    private class Capture(val kind: String, val socket: SSLSocket, val pc: String)
    private val captures = mutableMapOf<RtcCapture, Capture>()
    private var elevating: CompletableFuture<Boolean>? = null  // under captures

    /** A program on the PC uses the camera or the microphone: sent over
     * WebRTC (RtcCapture) to the PC that offered on this connection, until it
     * hangs up. Only if the user lets the PC have it, here. */
    private fun capture(socket: SSLSocket, wire: Wire, request: JSONObject, kind: String, pc: String) {
        val camera = kind == "camera"
        fun refuse(reason: String) = wire.send(JSONObject().put("type", "refused").put("reason", reason))
        // Not let, or Android not asked yet: the user is asked here and now
        // (a notification, once a minute at most: the PC asks again every 10 s).
        fun ask(reason: String) {
            val now = System.currentTimeMillis()
            if (now - (asked[kind] ?: 0L) > ASK_AGAIN) {
                asked[kind] = now
                Notifications.askDevice(this, kind, pc)
            }
            refuse(reason)
        }
        if (!(if (camera) Options.camera(this) else Options.microphone(this))) return ask("off")
        val permission = if (camera) Manifest.permission.CAMERA else Manifest.permission.RECORD_AUDIO
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) return ask("permission")
        Notifications.cancelDevice(this, kind)  // allowed now
        val offer = request.optString("sdp")
        if (offer.isEmpty()) return refuse("unavailable")  // a PC of before WebRTC
        // The PC says whether libwebrtc takes the noise out: not when it does
        // itself (a neural network there), nor when its user wants none. A
        // camera or microphone that fails ends the session (the PC tries
        // again), not an empty stream.
        val capture = RtcCapture(this, request, request.optBoolean("noise_reduction", true)) {
            runCatching { socket.close() }
        }
        synchronized(captures) { captures[capture] = Capture(kind, socket, pc) }
        try {
            if (!allowedTypes(kind) && !foreground(kind)) return refuse("background")
            val answer = try {
                capture.answer(offer)
            } catch (e: Exception) {
                Log.w(TAG, "$kind not available: $e")
                return refuse("unavailable")
            }
            wire.send(JSONObject().put("type", "answer").put("sdp", answer))
            showCaptures()
            lowLatency(true)
            // The PC holds it as long as its program uses the device, and pings.
            socket.soTimeout = RtcCapture.SILENT_PC
            while (wire.receive().optString("type") != "stop") continue
        } finally {
            capture.stop()
            synchronized(captures) { captures.remove(capture) }
            showCaptures()
            lowLatency(false)
        }
    }

    // The foreground service's types now: camera and microphone stay once
    // they were taken in front, so the PC's next use needs no CaptureActivity.
    @Volatile private var types = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE

    private fun allowedTypes(kind: String) = types and typeOf(kind) != 0

    private fun typeOf(kind: String) = if (kind == "camera") ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        else ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE

    /** Camera and microphone are only for a foreground service started, or
     * started again, while the app is in front: for a moment a see-through
     * activity is (CaptureActivity, which may open over other apps). Once:
     * the types stay while the service runs and the user lets the PC have them. */
    private fun foreground(kind: String): Boolean {
        // CaptureActivity can open only over other apps (or the app's own
        // screen), and not over the lock screen: else no use trying.
        val locked = getSystemService(android.app.KeyguardManager::class.java).isKeyguardLocked
        if (locked || !(Settings.canDrawOverlays(this) || Front.shown)) return false
        // Camera and microphone asked at once: one activity, one wait.
        val done = synchronized(captures) {
            elevating?.takeIf { !it.isDone } ?: CompletableFuture<Boolean>().also {
                elevating = it
                startActivity(Intent(this, CaptureActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION))
            }
        }
        try {
            done.get(ELEVATE_WAIT, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            Log.w(TAG, "could not come to the front for the camera or microphone: $e")
        }
        return allowedTypes(kind)
    }

    /** While the app is in front (CaptureActivity, MainActivity): the service
     * takes the camera and the microphone the user lets the PC have. */
    fun elevate() {
        var wanted = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
        for ((kind, on) in listOf("camera" to Options.camera(this), "microphone" to Options.microphone(this))) {
            val permission = if (kind == "camera") Manifest.permission.CAMERA else Manifest.permission.RECORD_AUDIO
            if (on && checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) wanted = wanted or typeOf(kind)
        }
        if (wanted != types) {
            val before = types
            types = wanted
            if (!showForeground()) types = before  // what the service really has
        }
        synchronized(captures) { elevating?.complete(true) }
    }

    /** The foreground service with its types, and its notification as the
     * captures are now: whether Android took it. */
    private fun showForeground(): Boolean = try {
        startForeground(Notifications.SERVICE_ID, notification(), types)
        true
    } catch (e: Exception) {
        Log.w(TAG, "foreground service: $e")
        false
    }

    private fun notification(): android.app.Notification {
        val now = synchronized(captures) { captures.values.toList() + speakerSessions.keys }
        val kinds = now.map { it.kind }.toSet()
        val pcs = now.map { it.pc }.distinct().joinToString(", ")
        displaying?.let { return Notifications.displaying(this, it) }
        return if (kinds.isEmpty()) Notifications.ready(this) else Notifications.capturing(this, kinds, pcs)
    }

    // Wi-Fi without power saving while the PC uses the camera or the microphone:
    // saving, it holds packets back and sends them in bunches every 100-300 ms.
    private val wifiLock by lazy {
        getSystemService(android.net.wifi.WifiManager::class.java)
            .createWifiLock(android.net.wifi.WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "plusone:capture")
            .apply { setReferenceCounted(false) }
    }

    private fun lowLatency(capturing: Boolean) {
        val any = synchronized(captures) { captures.isNotEmpty() || speakerSessions.isNotEmpty() }
        try {
            val showing = displaying != null && DisplaySession.current?.visible == true
            if (capturing || any || showing) wifiLock.acquire() else wifiLock.release()
        } catch (e: Exception) {
            Log.w(TAG, "Wi-Fi lock: $e")
        }
    }

    /** The PC's desktop on this screen (DisplaySession, DisplayActivity),
     * until the PC hangs up or says stop. The key for its packets comes
     * over this paired link, fresh for each session. */
    private fun display(socket: SSLSocket, wire: Wire, request: JSONObject, pc: String) {
        val key = request.optString("key").chunked(2).mapNotNull { it.toIntOrNull(16)?.toByte() }.toByteArray()
        val port = request.optInt("port")
        require(key.size == 32 && port in 1..65535) { "bad display request" }
        if (!(Settings.canDrawOverlays(this) || Front.shown)) {
            wire.send(JSONObject().put("type", "refused").put("reason", "background"))
            return
        }
        val session = DisplaySession(key, socket.inetAddress, port)
        // Ended here (Back, Stop): the link closes, and the PC with it.
        session.onEnd = { Thread { runCatching { socket.close() } }.start() }
        session.onVisible = { lowLatency(false) }  // no low-latency Wi-Fi while nothing is shown
        DisplaySession.current?.stop()
        DisplaySession.current = session
        session.start()
        try {
            val mode = getSystemService(android.hardware.display.DisplayManager::class.java)
                .getDisplay(android.view.Display.DEFAULT_DISPLAY).mode
            wire.send(JSONObject().put("type", "display-ready").put("port", session.socket.localPort)
                .put("width", maxOf(mode.physicalWidth, mode.physicalHeight))
                .put("height", minOf(mode.physicalWidth, mode.physicalHeight))
                .put("refresh", mode.refreshRate.toDouble()))
            startActivity(Intent(this, DisplayActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            displaying = pc
            showCaptures()
            lowLatency(true)
            socket.soTimeout = RtcCapture.SILENT_PC
            while (wire.receive().optString("type") != "stop") continue
        } finally {
            session.stop()
            if (DisplaySession.current === session) DisplaySession.current = null
            android.os.Handler(mainLooper).post { DisplayActivity.ended(session) }
            if (DisplaySession.current == null) displaying = null
            showCaptures()
            lowLatency(false)
        }
    }

    @Volatile private var displaying: String? = null  // the PC whose desktop is shown

    /** The PC's sound on the speakers (RtcPlayback), while one of its
     * programs plays and the user lets it (Plus One → For the PC). */
    private fun playback(socket: SSLSocket, wire: Wire, request: JSONObject, pc: String) {
        if (!Options.speakers(this)) {
            wire.send(JSONObject().put("type", "refused").put("reason", "off"))
            return
        }
        val playback = RtcPlayback(this) { runCatching { socket.close() } }
        val entry = Capture("speakers", socket, pc)
        val answer = try {
            playback.answer(request.optString("sdp"))
        } catch (e: Exception) {
            Log.w(TAG, "speakers not available: $e")
            playback.stop()
            wire.send(JSONObject().put("type", "refused").put("reason", "unavailable"))
            return
        }
        synchronized(captures) { speakerSessions[entry] = playback }
        try {
            wire.send(JSONObject().put("type", "answer").put("sdp", answer))
            showCaptures()
            lowLatency(true)
            socket.soTimeout = RtcCapture.SILENT_PC
            while (wire.receive().optString("type") != "stop") continue
        } finally {
            playback.stop()
            synchronized(captures) { speakerSessions.remove(entry) }
            showCaptures()
            lowLatency(false)
        }
    }

    private val speakerSessions = mutableMapOf<Capture, RtcPlayback>()  // under captures

    /** What the notification says, when only that changed. */
    private fun showCaptures() =
        getSystemService(android.app.NotificationManager::class.java).notify(Notifications.SERVICE_ID, notification())

    /** The user took the device back from the PC (MainActivity): its captures
     * end, and the service lets go of it. */
    fun stopCaptures(kind: String) {
        val sockets = synchronized(captures) {
            (captures.values + speakerSessions.keys).filter { it.kind == kind }.map { it.socket }
        }
        sockets.forEach { try { it.close() } catch (e: Exception) {} }
        if (kind != "speakers" && allowedTypes(kind)) {
            types = types and typeOf(kind).inv()
            showForeground()
        }
    }

    /** Numeric comparison: the app commits to its random number, learns the
     * PC's, shows its own; both screens then show a code made of the two
     * certificates and the two numbers, and the user confirms it on both. */
    private fun pair(socket: SSLSocket, wire: Wire, pc: ByteArray, pcName: String,
                     guard: java.util.concurrent.ScheduledFuture<*>): Boolean {
        val mine = Identity.certificate.encoded
        val nonce = ByteArray(16).also { SecureRandom().nextBytes(it) }
        wire.send(JSONObject().put("type", "pair-commit").put("commit", Identity.commitment(nonce, mine, pc)))
        val number = wire.receive(Wire.UNPAIRED_HEADER)
        val pcNonce = hex(number.optString("nonce"))
        require(number.optString("type") == "pair-nonce" && pcNonce?.size == 16) { "expected the PC's number" }
        wire.send(JSONObject().put("type", "pair-nonce").put("nonce", nonce.joinToString("") { "%02x".format(it) }))
        val code = Identity.pairingCode(pc, mine, pcNonce!!, nonce)
        guard.cancel(false)  // the user takes their time
        socket.soTimeout = PAIRING_TIMEOUT
        // The PC's answer may come first (refused there): it closes the
        // question here too, instead of leaving it up for two minutes.
        val answer = java.util.concurrent.atomic.AtomicReference<String>()
        val listening = Thread {
            val type = try { wire.receive(Wire.UNPAIRED_HEADER).optString("type") } catch (e: Throwable) { "" }
            answer.set(type)
            if (type != "pair-ok") Pairing.answer(code, false)
        }.apply { start() }
        val from = address(socket.inetAddress)
        val accepted = Pairing.ask(this, pcName, code, from) ?: return false  // another question is up, or too soon
        wire.send(JSONObject().put("type", if (accepted) "pair-ok" else "pair-no"))
        listening.join(PAIRING_TIMEOUT.toLong())
        return accepted && answer.get() == "pair-ok"
    }

    /** An address as the PC writes it: IPv4 without the "::ffff:" prefix, no scope. */
    private fun address(address: java.net.InetAddress): String =
        address.hostAddress.orEmpty().substringBefore('%').removePrefix("::ffff:")

    private fun hex(s: String): ByteArray? =
        if (s.length % 2 != 0 || !s.all { it in "0123456789abcdef" }) null
        else ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }

    /** Where a job's PDF goes; null for an id that is not the PC's 32 hex digits. */
    private fun jobFile(job: String): File? =
        if (JOB_ID.matches(job)) File(File(cacheDir, "jobs").apply { mkdirs() }, "$job.pdf") else null

    /** A PDF to sign: it joins the queue, and the signing screen opens on
     * it (or, already open on another, says it came); its answer goes back. */
    private fun sign(wire: Wire, message: JSONObject, size: Long, pc: String, peer: String) {
        val job = message.optString("job")
        val file = jobFile(job)
        require(file != null && size in 1..Wire.MAX_PAYLOAD && job !in Jobs.ids) { "bad job" }
        // Room for it: a PC, even a paired one, does not get to fill the tablet.
        // Taken before the upload, not checked then written: uploads at once
        // count each other too.
        val reserved = incoming.addAndGet(size)
        val slots = arriving.incrementAndGet()
        try {
            require(Jobs.waiting().size + slots <= MAX_JOBS && Jobs.bytes() + reserved <= MAX_JOB_BYTES &&
                file.parentFile!!.usableSpace > reserved + FREE_SPACE) { "no room for another PDF" }
            file.outputStream().use { wire.readPayload(size, it) }
        } catch (e: Throwable) {
            file.delete()
            throw e
        } finally {
            incoming.addAndGet(-size)
            arriving.decrementAndGet()
        }
        try {
            // Signatures added or removed on the PC since the last job.
            message.optJSONObject("signatures")?.let { Signatures.merge(this, it) }
            val title = message.optString("title", file.name).take(200)
            // With the PC's pen, so both sides sign alike.
            val added = Jobs.start(Jobs.Job(job, file, title, pc, message.optJSONObject("pen"), peer = peer), wire) {
                answer(wire, job, it.result)
            }
            require(added) { "a job of that id already" }
        } catch (e: Throwable) {
            file.delete()  // half a PDF is of no use
            throw e
        }
        // Opening over whatever is on screen needs "Display over other apps";
        // without it, the notification (queueChanged) is there to tap.
        if (Settings.canDrawOverlays(this)) startActivity(SignActivity.open(this, job))
        wire.send(JSONObject().put("type", "opened").put("job", job))
    }

    /** Sends a job's answer, and the saved signatures for the PC to merge;
     * false if the link is gone (the PC asks again). The job stays until
     * the PC says it got it. */
    private fun answer(wire: Wire, job: String, result: JSONObject?): Boolean = try {
        val message = JSONObject().put("job", job)
        if (result != null) message.put("type", "signed").put("marks", result) else message.put("type", "cancelled")
        // The saved signatures too, for the PC to merge, unless that would
        // make the answer more than the PC takes (companion.py: 16 MB).
        val withSignatures = JSONObject(message.toString()).put("signatures", Signatures.toJson(this))
        wire.send(if (withSignatures.toString().length < MAX_ANSWER) withSignatures else message)
        true
    } catch (e: Exception) {
        Log.w(TAG, "could not answer job $job yet: $e")
        false
    }

    private val asked = java.util.concurrent.ConcurrentHashMap<String, Long>()  // kind -> when the user was

    // An upload's bytes and slot, from before it starts until it is a job.
    private val incoming = AtomicLong()
    private val arriving = AtomicInteger()

    companion object {
        const val TAG = "PlusOne"
        const val SERVICE_TYPE = "_plusone._tcp"
        const val PROTOCOL = 3
        const val PORT = 47236                 // companion.py APP_PORT; another if it is taken
        const val ELEVATE_WAIT = 5_000L        // ms for CaptureActivity to bring the app in front
        const val MAX_CONNECTIONS = 32
        const val MAX_UNPAIRED = 4             // connections of peers not yet known, at a time
        const val MAX_HANDSHAKING = 8          // connections still in their TLS handshake, at a time
        const val ASK_AGAIN = 60_000L          // ms between notifications asking for the camera or microphone
        const val LISTEN_RETRY = 30_000L       // ms before trying to listen again
        const val MAX_ANSWER = 15 * 1024 * 1024  // characters of an answer, under the PC's 16 MB
        const val HANDSHAKE_DEADLINE = 20_000L // ms for an unknown peer's whole handshake
        const val MAX_JOBS = 50                // PDFs waiting at once
        const val MAX_JOB_BYTES = 1024L * 1024 * 1024
        const val FREE_SPACE = 200L * 1024 * 1024  // left free on the tablet, at least
        const val HANDSHAKE_TIMEOUT = 20_000   // ms for TLS, hello and the pairing messages
        const val PAIRING_TIMEOUT = 150_000    // ms: the user compares the codes
        const val PAIRED_TIMEOUT = 90_000      // ms without a word from a paired PC
        private val JOB_ID = Regex("[0-9a-f]{32}")
        const val IDLE_CHECK = 60_000L       // ms
        const val IDLE_STOP = 10 * 60_000L   // ms without a PC when not always ready

        fun deviceName(context: Context): String =
            Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME) ?: Build.MODEL

        /** The running service, for the activities that talk to it. */
        @Volatile var instance: CompanionService? = null

        fun start(context: Context) {
            context.startForegroundService(Intent(context, CompanionService::class.java))
        }
    }
}
