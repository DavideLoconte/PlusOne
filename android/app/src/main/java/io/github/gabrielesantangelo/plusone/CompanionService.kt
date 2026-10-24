package io.github.gabrielesantangelo.plusone

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

/**
 * Always ready for the PC: listens over TLS on the home network and says so
 * with mDNS (_plusone._tcp), so Plus One on the PC can hand it a PDF to sign.
 * A foreground service (Android shows it as a quiet notification); it does
 * nothing until the PC connects.
 */
class CompanionService : Service() {
    private var server: SSLServerSocket? = null
    private var registration: NsdManager.RegistrationListener? = null
    private val connections = Executors.newCachedThreadPool()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifications.channels(this)
        startForeground(Notifications.SERVICE_ID, Notifications.ready(this),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
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
    private val busy = java.util.concurrent.atomic.AtomicInteger()  // PCs connected

    private fun idleCheck() {
        if (!Options.alwaysReady(this) && busy.get() == 0 && System.currentTimeMillis() - lastUse > IDLE_STOP) {
            stopSelf()
            return
        }
        handler.postDelayed(::idleCheck, IDLE_CHECK)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        registration?.let { (getSystemService(NSD_SERVICE) as NsdManager).unregisterService(it) }
        server?.close()
        connections.shutdownNow()
        super.onDestroy()
    }

    private fun serve() {
        val socket = try {
            Identity.sslContext().serverSocketFactory.createServerSocket(0) as SSLServerSocket
        } catch (e: Exception) {
            Log.e(TAG, "cannot listen", e)
            return
        }
        socket.needClientAuth = true
        server = socket
        advertise(socket.localPort)
        while (!socket.isClosed) {
            val client = try { socket.accept() as SSLSocket } catch (e: Exception) { break }
            connections.execute { handle(client) }
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

    /** One PC: hello, pairing if either side does not know the other, then jobs. */
    private fun handle(socket: SSLSocket) {
        busy.incrementAndGet()
        lastUse = System.currentTimeMillis()
        try {
            socket.use {
                socket.startHandshake()
                val pc = socket.session.peerCertificates[0].encoded
                val fingerprint = Identity.fingerprint(pc)
                val wire = Wire(socket)
                val (hello, _) = wire.receive()
                require(hello.optString("type") == "hello") { "expected hello" }
                val pcName = hello.optString("name", "PC")
                val known = Paired.contains(this, fingerprint)
                wire.send(JSONObject().put("type", "hello").put("name", deviceName(this))
                    .put("version", PROTOCOL).put("paired", known))
                if (!known || !hello.optBoolean("paired", true)) {
                    val code = Identity.pairingCode(pc, Identity.certificate.encoded)
                    val accepted = Pairing.ask(this, pcName, code)
                    wire.send(JSONObject().put("type", if (accepted) "pair-ok" else "pair-no"))
                    val (answer, _) = wire.receive()
                    if (!accepted || answer.optString("type") != "pair-ok") return
                    Paired.add(this, fingerprint, pcName)
                }
                while (true) {
                    val (message, payload) = wire.receive()
                    when (message.optString("type")) {
                        "sign" -> sign(wire, message, payload ?: ByteArray(0))
                        "resume" -> {
                            val job = message.optString("job")
                            if (Jobs.resume(job) { answer(wire, job, it.result) }) {
                                wire.send(JSONObject().put("type", "resumed").put("job", job))
                            } else {
                                wire.send(JSONObject().put("type", "unknown-job").put("job", job))
                            }
                        }
                        "ping" -> wire.send(JSONObject().put("type", "pong"))
                        "bye" -> return
                    }
                }
            }
        } catch (e: Exception) {
            Log.i(TAG, "connection ended: $e")
        } finally {
            busy.decrementAndGet()
            lastUse = System.currentTimeMillis()
        }
    }

    /** A PDF to sign: the signing screen opens on it; its answer goes back. */
    private fun sign(wire: Wire, message: JSONObject, pdf: ByteArray) {
        val job = message.getString("job")
        val file = File(File(cacheDir, "jobs").apply { mkdirs() }, "$job.pdf")
        file.writeBytes(pdf)
        Jobs.start(job) { answer(wire, job, it.result) }
        val title = message.optString("title", file.name)
        val open = Intent(this, SignActivity::class.java).apply {
            putExtra(SignActivity.EXTRA_PATH, file.path)
            putExtra(SignActivity.EXTRA_JOB, job)
            putExtra(SignActivity.EXTRA_TITLE, title)
            // The PC's pen and saved signature, so both sides sign alike.
            message.optJSONObject("pen")?.let { putExtra(SignActivity.EXTRA_PEN, it.toString()) }
            message.optJSONObject("signature")?.let { putExtra(SignActivity.EXTRA_SIGNATURE, it.toString()) }
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        // Opening over whatever is on screen needs "Display over other apps";
        // without it, a notification to tap.
        if (Settings.canDrawOverlays(this)) startActivity(open) else Notifications.signJob(this, title, open)
        wire.send(JSONObject().put("type", "opened").put("job", job))
    }

    /** Sends a job's answer; false if the link is gone (the PC asks again). */
    private fun answer(wire: Wire, job: String, result: JSONObject?): Boolean = try {
        if (result != null) {
            wire.send(JSONObject().put("type", "signed").put("job", job).put("marks", result))
        } else {
            wire.send(JSONObject().put("type", "cancelled").put("job", job))
        }
        File(File(cacheDir, "jobs"), "$job.pdf").delete()
        true
    } catch (e: Exception) {
        Log.w(TAG, "could not answer job $job yet: $e")
        false
    }

    companion object {
        const val TAG = "PlusOne"
        const val SERVICE_TYPE = "_plusone._tcp"
        const val PROTOCOL = 1
        const val IDLE_CHECK = 60_000L       // ms
        const val IDLE_STOP = 10 * 60_000L   // ms without a PC when not always ready

        fun deviceName(context: Context): String =
            Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME) ?: Build.MODEL

        fun start(context: Context) {
            context.startForegroundService(Intent(context, CompanionService::class.java))
        }
    }
}
