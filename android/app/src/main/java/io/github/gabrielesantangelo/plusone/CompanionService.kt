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
import java.util.concurrent.atomic.AtomicInteger
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
        startForeground(Notifications.SERVICE_ID, Notifications.ready(this),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
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
            registration?.let { (getSystemService(NSD_SERVICE) as NsdManager).unregisterService(it) }
            registration = null
            server?.close()
        }
        connections.shutdownNow()
        super.onDestroy()
    }

    private fun serve() {
        val socket = try {
            val factory = Identity.sslContext().serverSocketFactory
            // A port the PC knows: over Wi-Fi Direct it checks who the
            // tablet is before mDNS has said where the app listens.
            (try { factory.createServerSocket(PORT) } catch (e: java.net.BindException) {
                factory.createServerSocket(0)
            }) as SSLServerSocket
        } catch (e: Exception) {
            Log.e(TAG, "cannot listen", e)
            return
        }
        socket.needClientAuth = true
        socket.enabledProtocols = arrayOf("TLSv1.3")
        synchronized(this) {
            // Stopped while the first key was being made: nothing to leave behind.
            if (stopped) return socket.close()
            server = socket
            advertise(socket.localPort)
        }
        while (!socket.isClosed) {
            val client = try { socket.accept() as SSLSocket } catch (e: Exception) { break }
            // Small messages go at once: the microphone's are 100 bytes every 20 ms,
            // which Nagle's algorithm would hold back for the PC's delayed ACKs.
            try { client.tcpNoDelay = true } catch (e: Exception) {}
            try {
                connections.execute { handle(client) }
            } catch (e: RejectedExecutionException) {
                client.close()
            }
        }
    }

    private fun advertise(port: Int) {
        val info = NsdServiceInfo().apply {
            serviceName = deviceName(this@CompanionService)
            serviceType = SERVICE_TYPE
            setPort(port)
            setAttribute("id", Identity.fingerprint(Identity.certificate.encoded).take(16))
            setAttribute("name", deviceName(this@CompanionService))
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.i(TAG, "on the network as ${info.serviceName}, port $port")
            }
            override fun onRegistrationFailed(info: NsdServiceInfo, error: Int) {
                Log.e(TAG, "mDNS registration failed: $error")
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
        try {
            socket.use {
                // Nothing waits long for a peer that is not even paired: the
                // whole exchange (TLS, hello, pairing's messages) in
                // HANDSHAKE_DEADLINE, not only each read, and a few such
                // peers at a time (a paired PC is never turned away for them).
                val guard = Wire.WATCHDOG.schedule({ socket.close() }, HANDSHAKE_DEADLINE, TimeUnit.MILLISECONDS)
                socket.soTimeout = HANDSHAKE_TIMEOUT
                socket.startHandshake()
                val pc = socket.session.peerCertificates[0].encoded
                val fingerprint = Identity.fingerprint(pc)
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
                        "bye" -> return
                    }
                }
            }
        } catch (e: Throwable) {
            // Errors too: a peer's deeply nested JSON overflows the parser's
            // stack, and an uncaught Error on a pool thread ends the process.
            Log.i(TAG, "connection ended: $e")
        } finally {
            if (stranger) unpaired.decrementAndGet()
            link?.let { Jobs.detach(it) }  // its jobs wait for the PC to come back
            busy.decrementAndGet()
            lastUse = System.currentTimeMillis()
        }
    }

    // The camera's and the microphone's captures, each with its connection.
    private val captures = mutableMapOf<Capture, Pair<String, SSLSocket>>()
    @Volatile private var elevating: CompletableFuture<Boolean>? = null
    @Volatile private var capturingFor = ""

    /** A program on the PC uses the camera or the microphone: captured and
     * sent on this connection until the PC hangs up. Only if the user lets
     * the PC have it, here. */
    private fun capture(socket: SSLSocket, wire: Wire, request: JSONObject, kind: String, pc: String) {
        val camera = kind == "camera"
        fun refuse(reason: String) = wire.send(JSONObject().put("type", "refused").put("reason", reason))
        if (!(if (camera) Options.camera(this) else Options.microphone(this))) return refuse("off")
        val permission = if (camera) Manifest.permission.CAMERA else Manifest.permission.RECORD_AUDIO
        if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) return refuse("permission")
        // Datagrams if the PC can take them (it falls back to its TLS link if they do not get through).
        val out = if (request.optString("transport") == "udp") DatagramOut(socket.inetAddress, !camera) else WireOut(wire)
        val capture = if (camera) CameraCapture(this, out, request)
            else MicCapture(out, request, Options.noiseReduction(this))
        synchronized(captures) {
            captures[capture] = kind to socket
            capturingFor = pc
        }
        try {
            if (!allowedTypes(kind) && !foreground()) return refuse("background")
            val info = try {
                capture.start()
            } catch (e: Exception) {
                Log.w(TAG, "$kind not available: $e")
                return refuse("unavailable")
            }
            wire.send(if (out is DatagramOut) out.describe(info) else info)
            showCaptures()
            lowLatency(true)
            socket.soTimeout = 0  // the PC holds it as long as its program uses the device
            while (true) {
                val message = wire.receive()
                when (message.optString("type")) {
                    "stop" -> break
                    "key" -> capture.keyFrame()  // a frame lost on the way
                    "stats" -> capture.stats(message.optInt("received"), message.optInt("lost"))
                }
            }
        } finally {
            capture.stop()
            (out as? DatagramOut)?.close()
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
    private fun foreground(): Boolean {
        val done = CompletableFuture<Boolean>()
        elevating = done
        startActivity(Intent(this, CaptureActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        return try {
            done.get(ELEVATE_WAIT, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            Log.w(TAG, "could not come to the front for the camera or microphone: $e")
            false
        }
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
            types = wanted
            showForeground()
        }
        elevating?.complete(true)
    }

    /** The foreground service with its types, and its notification as the captures are now. */
    private fun showForeground() {
        try {
            startForeground(Notifications.SERVICE_ID, notification(), types)
        } catch (e: Exception) {
            Log.w(TAG, "foreground service: $e")
        }
    }

    private fun notification(): android.app.Notification {
        val kinds = synchronized(captures) { captures.values.map { it.first }.toSet() }
        return if (kinds.isEmpty()) Notifications.ready(this) else Notifications.capturing(this, kinds, capturingFor)
    }

    // Wi-Fi without power saving while the PC uses the camera or the microphone:
    // saving, it holds packets back and sends them in bunches every 100-300 ms.
    private val wifiLock by lazy {
        getSystemService(android.net.wifi.WifiManager::class.java)
            .createWifiLock(android.net.wifi.WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "plusone:capture")
            .apply { setReferenceCounted(false) }
    }

    private fun lowLatency(capturing: Boolean) {
        val any = synchronized(captures) { captures.isNotEmpty() }
        try {
            if (capturing || any) wifiLock.acquire() else wifiLock.release()
        } catch (e: Exception) {
            Log.w(TAG, "Wi-Fi lock: $e")
        }
    }

    /** What the notification says, when only that changed. */
    private fun showCaptures() =
        getSystemService(android.app.NotificationManager::class.java).notify(Notifications.SERVICE_ID, notification())

    /** The user took the device back from the PC (MainActivity): its captures
     * end, and the service lets go of it. */
    fun stopCaptures(kind: String) {
        val sockets = synchronized(captures) { captures.filter { it.value.first == kind }.map { it.value.second } }
        sockets.forEach { try { it.close() } catch (e: Exception) {} }
        if (allowedTypes(kind)) {
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
        val accepted = Pairing.ask(this, pcName, code) ?: return false  // another question is up
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
        require(Jobs.waiting().size < MAX_JOBS && Jobs.bytes() + size <= MAX_JOB_BYTES &&
            file.parentFile!!.usableSpace > size + FREE_SPACE) { "no room for another PDF" }
        try {
            file.outputStream().use { wire.readPayload(size, it) }
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
        val message = JSONObject().put("job", job).put("signatures", Signatures.toJson(this))
        if (result != null) {
            wire.send(message.put("type", "signed").put("marks", result))
        } else {
            wire.send(message.put("type", "cancelled"))
        }
        true
    } catch (e: Exception) {
        Log.w(TAG, "could not answer job $job yet: $e")
        false
    }

    companion object {
        const val TAG = "PlusOne"
        const val SERVICE_TYPE = "_plusone._tcp"
        const val PROTOCOL = 3
        const val PORT = 47236                 // companion.py APP_PORT; another if it is taken
        const val ELEVATE_WAIT = 5_000L        // ms for CaptureActivity to bring the app in front
        const val MAX_CONNECTIONS = 32
        const val MAX_UNPAIRED = 4             // connections of peers not yet known, at a time
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
