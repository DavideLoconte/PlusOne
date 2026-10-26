package io.github.gabrielesantangelo.plusone.link

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import org.json.JSONObject
import java.io.OutputStream
import java.net.InetAddress
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

const val TAG = "PlusOne"

/**
 * Plus One's connectivity on the tablet (the PC's is plusone/link): the
 * links of the paired PCs, under the app's services (signing, the desktop,
 * the speakers, the camera and the microphone, updates), which never touch
 * a socket of their own.
 *
 * It listens over TLS 1.3 (Identity: the app's certificate; the PC's must be
 * a paired one, Paired) and says so with mDNS (_plusone._tcp: where, the
 * app's id, name and version). A PC's connection: the hello each way, the
 * pairing when the user asked for it on the PC (numeric comparison, the
 * user's consent here), then its requests, each to the Handler of its type.
 * A PC of another protocol version may only update the app.
 */
class LinkServer(
    private val context: Context,
    private val handlers: List<Handler>,
    private val host: Host,
) {
    /** What the app around the links decides. */
    interface Host {
        /** This tablet's name, as the PC shows it. */
        fun deviceName(): String
        /** The app's versionCode: in the hello and the mDNS record. */
        fun appVersion(): Long
        /** The user's answer to pairing with pc (whose code is code, from
         * the address from): null when no question may be asked now. */
        fun askPairing(pc: String, code: String, from: String): Boolean?
        /** The PC withdrew the question (said no there, or went). */
        fun pairingWithdrawn(code: String)
        /** A PC connected (+1) or went (-1): for when the app may rest. */
        fun connected(delta: Int)
    }

    @Volatile private var server: SSLServerSocket? = null
    @Volatile private var registration: NsdManager.RegistrationListener? = null
    @Volatile private var stopped = false
    // A connection per PDF: a PC sending a batch has a few dozen at most
    // (sign_jobs.py MAX_JOBS); more connections are turned away.
    private val connections = ThreadPoolExecutor(0, MAX_CONNECTIONS, 60, TimeUnit.SECONDS, SynchronousQueue())
    private val unpaired = AtomicInteger()     // connections not yet known to be a paired PC
    private val handshaking = AtomicInteger()  // connections still in their TLS handshake
    private val links = ConcurrentHashMap<String, MutableSet<Link>>()  // each paired PC's open links
    private val byType = handlers.flatMap { handler -> handler.types.map { it to handler } }.toMap()

    fun start() = Thread(::serve, "plusone-server").start()

    fun stop() {
        synchronized(this) {
            stopped = true
            registration?.let {
                runCatching { (context.getSystemService(Context.NSD_SERVICE) as NsdManager).unregisterService(it) }
            }
            registration = null
            server?.close()
        }
        connections.shutdownNow()
    }

    /** The user forgot this PC: what it has open ends now (a capture in
     * progress, a PDF being signed), not when it happens to hang up. */
    fun disconnect(fingerprint: String) {
        links.remove(fingerprint)?.forEach { it.close() }
    }

    private fun serve() {
        // Not listening is not ready: tried again (the keystore's first key
        // may fail once) instead of a "Ready" nobody can reach.
        var socket: SSLServerSocket? = null
        while (socket == null) {
            if (stopped) return
            socket = try {
                val factory = Identity.sslContext().serverSocketFactory
                // A port the PC knows: where it tries first, before mDNS has
                // said where the app listens (and where it was last).
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
                announce(socket.localPort)
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
            // Never the PC: it comes over the network. Another app here, maybe,
            // posing as one to be paired with a tap on a question it timed.
            if (fromHere(client.inetAddress)) {
                Log.w(TAG, "a connection from this tablet itself: closed")
                runCatching { client.close() }
                continue
            }
            // Small messages go at once (pairing's, the answers to the PC's
            // pings), not held back by Nagle's algorithm for delayed ACKs.
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

    /** Discovery: the app on the network, where it listens, its id (its
     * certificate's fingerprint, the start), name and version. */
    private fun announce(port: Int) {
        val name = host.deviceName()
        val version = host.appVersion()
        val info = NsdServiceInfo().apply {
            serviceName = name.take(63)  // a DNS label's most
            serviceType = SERVICE_TYPE
            setPort(port)
            setAttribute("id", Identity.fingerprint(Identity.certificate.encoded).take(16))
            // A TXT entry, key and value, is 255 bytes at most.
            setAttribute("name", name.take(80))
            // The app's version: an older one than the PC's gets its update.
            setAttribute("v", version.toString())
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.i(TAG, "on the network as ${info.serviceName}, port $port")
            }
            override fun onRegistrationFailed(info: NsdServiceInfo, error: Int) {
                Log.e(TAG, "mDNS registration failed: $error")
                synchronized(this@LinkServer) { if (registration === this) registration = null }
            }
            override fun onServiceUnregistered(info: NsdServiceInfo) {}
            override fun onUnregistrationFailed(info: NsdServiceInfo, error: Int) {}
        }
        registration = listener
        (context.getSystemService(Context.NSD_SERVICE) as NsdManager).registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
    }

    /** One PC: hello, pairing if either side does not know the other and the
     * PC asks to pair (its user chose this tablet), then its requests. */
    private fun handle(socket: SSLSocket) {
        host.connected(1)
        var link: Link? = null
        var stranger = false  // counted in unpaired: not (yet) a paired PC
        var shaking = true  // counted in handshaking
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
                val fingerprint = Identity.fingerprint(pc)
                val known = Paired.contains(context, fingerprint)
                if (!known) {
                    stranger = true
                    if (unpaired.incrementAndGet() > MAX_UNPAIRED) return
                }
                val wire = Wire(socket)
                val hello = wire.receive(Wire.UNPAIRED_HEADER)
                require(hello.optString("type") == "hello") { "expected hello" }
                val pcName = readable(hello.optString("name"))
                // "at": the address the PC reached, so it can tell this
                // tablet answered itself, not through someone passing the
                // connection on.
                wire.send(JSONObject().put("type", "hello").put("name", host.deviceName())
                    .put("version", PROTOCOL).put("paired", known).put("at", address(socket.localAddress))
                    .put("app", host.appVersion()))
                // Another version: the PC sees ours and says so to its user;
                // a PC that knows this tablet, and this tablet it, may still
                // hand over its update (and nothing else).
                val otherVersion = hello.optInt("version") != PROTOCOL
                if (otherVersion && !(known && hello.optBoolean("paired"))) return
                if (!known || !hello.optBoolean("paired")) {
                    // Only when the user pairs, in Plus One's window on the PC.
                    if (!hello.optBoolean("pair")) return
                    if (!pair(socket, wire, pc, pcName, guard)) return
                    Paired.add(context, fingerprint, pcName)
                }
                guard.cancel(false)
                val opened = Link(socket, wire, pcName, fingerprint)
                link = opened
                // Its links, closed the moment the user forgets it (disconnect()).
                links.computeIfAbsent(fingerprint) { ConcurrentHashMap.newKeySet() }.add(opened)
                if (!Paired.contains(context, fingerprint)) return  // forgotten just now
                if (stranger) {
                    unpaired.decrementAndGet()
                    stranger = false
                }
                // The PC asks "ping" every 15 s while the user signs.
                socket.soTimeout = PAIRED_TIMEOUT
                while (true) {
                    val message = wire.receive()
                    val type = message.optString("type")
                    val size = message.optLong("size", 0)
                    val handler = byType[type]
                    require(size == 0L || size <= (handler?.maxPayload(type) ?: 0)) { "unexpected payload" }
                    when (type) {
                        "ping" -> { wire.send(JSONObject().put("type", "pong")); continue }
                        "bye" -> return
                    }
                    if (otherVersion && handler?.acrossVersions != true) return
                    if (handler == null) continue  // a request of a later PC: not known here
                    if (!handler.handle(opened, message, size)) return
                }
            }
        } catch (e: Throwable) {
            // Errors too: a peer's deeply nested JSON overflows the parser's
            // stack, and an uncaught Error on a pool thread ends the process.
            Log.i(TAG, "connection ended: $e")
        } finally {
            if (shaking) handshaking.decrementAndGet()
            if (stranger) unpaired.decrementAndGet()
            link?.let { gone ->
                links[gone.fingerprint]?.remove(gone)
                handlers.forEach { it.closed(gone) }  // a job waits for the PC to come back, say
            }
            host.connected(-1)
        }
    }

    /** Numeric comparison: the app commits to its random number, learns the
     * PC's, shows its own; both screens then show a code made of the two
     * certificates and the two numbers, and the user confirms it on both. */
    private fun pair(socket: SSLSocket, wire: Wire, pc: ByteArray, pcName: String, guard: ScheduledFuture<*>): Boolean {
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
        val answer = AtomicReference<String>()
        val listening = Thread {
            val type = try { wire.receive(Wire.UNPAIRED_HEADER).optString("type") } catch (e: Throwable) { "" }
            answer.set(type)
            if (type != "pair-ok") host.pairingWithdrawn(code)
        }.apply { start() }
        val from = address(socket.inetAddress)
        val accepted = host.askPairing(pcName, code, from) ?: return false  // another question is up, or too soon
        wire.send(JSONObject().put("type", if (accepted) "pair-ok" else "pair-no"))
        listening.join(PAIRING_TIMEOUT.toLong())
        return accepted && answer.get() == "pair-ok"
    }

    /** Whether a connection comes from this tablet: loopback, or one of its
     * own addresses (an app here can reach the Wi-Fi one too). */
    private fun fromHere(address: InetAddress?): Boolean =
        address == null || address.isLoopbackAddress || address.isAnyLocalAddress ||
            runCatching { java.net.NetworkInterface.getByInetAddress(address) != null }.getOrDefault(false)

    /** The PC's name as it says it, to be read: no control characters, none
     * that turn the text around them (U+202E and the like), not too long. */
    private fun readable(name: String): String =
        name.filter { !Character.isISOControl(it) && Character.getType(it) != Character.FORMAT.toInt() }
            .trim().take(64).ifEmpty { "PC" }

    /** An address as the PC writes it: IPv4 without the "::ffff:" prefix, no scope. */
    private fun address(address: InetAddress): String =
        address.hostAddress.orEmpty().substringBefore('%').removePrefix("::ffff:")

    private fun hex(s: String): ByteArray? =
        if (s.length % 2 != 0 || !s.all { it in "0123456789abcdef" }) null
        else ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }

    companion object {
        const val SERVICE_TYPE = "_plusone._tcp"
        const val PROTOCOL = 3
        const val PORT = 47236                 // plusone/link/link.py APP_PORT; another if it is taken
        const val MAX_CONNECTIONS = 32
        const val MAX_UNPAIRED = 4             // connections of peers not yet known, at a time
        const val MAX_HANDSHAKING = 8          // connections still in their TLS handshake, at a time
        const val LISTEN_RETRY = 30_000L       // ms before trying to listen again
        const val HANDSHAKE_DEADLINE = 20_000L // ms for an unknown peer's whole handshake
        const val HANDSHAKE_TIMEOUT = 20_000   // ms for TLS, hello and the pairing messages
        const val PAIRING_TIMEOUT = 150_000    // ms: the user compares the codes
        const val PAIRED_TIMEOUT = 90_000      // ms without a word from a paired PC
        // ms without a word from the PC (it pings every 5 s, link.py
        // KEEPALIVE) before a session ends: a PC gone without closing the
        // link (asleep, off the network) does not keep the microphone on.
        const val SILENT_PC = 15_000
    }
}

/** A service of the app on the links: the request types it answers. */
interface Handler {
    val types: Set<String>
    /** The most a request of type may carry (0: none). */
    fun maxPayload(type: String): Long = 0
    /** Whether it answers a PC of another protocol version too (an update). */
    val acrossVersions: Boolean get() = false
    /** One request on link: true to read the link's next one, false when
     * this one took the link (a session, over now: the link closes). */
    fun handle(link: Link, request: JSONObject, size: Long): Boolean
    /** The link went, whatever ended it. */
    fun closed(link: Link) {}
}

/** A paired PC's link: pc its name, fingerprint its certificate's. */
class Link(private val socket: SSLSocket, private val wire: Wire, val pc: String, val fingerprint: String) {
    /** The PC's address. */
    val address: InetAddress get() = socket.inetAddress

    fun send(message: JSONObject) = wire.send(message)

    fun receive(maxHeader: Int = Wire.MAX_HEADER): JSONObject = wire.receive(maxHeader)

    fun readPayload(size: Long, out: OutputStream) = wire.readPayload(size, out)

    fun refuse(reason: String) = send(JSONObject().put("type", "refused").put("reason", reason))

    /** A session held: until the PC says "stop" or the link goes, or
     * nothing comes for silentMs (the PC pings while it holds one). */
    fun hold(silentMs: Int = LinkServer.SILENT_PC) {
        socket.soTimeout = silentMs
        while (wire.receive().optString("type") != "stop") continue
    }

    /** From any thread: the link ends (whatever waits on it wakes). */
    fun close() {
        runCatching { socket.close() }
    }
}
