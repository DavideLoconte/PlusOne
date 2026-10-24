package io.github.gabrielesantangelo.plusone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent

object Notifications {
    private const val SERVICE = "service"
    private const val ASK = "ask"
    private const val QUEUE = "queue"
    const val SERVICE_ID = 1

    fun channels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(SERVICE,
            context.getString(R.string.channel_service), NotificationManager.IMPORTANCE_MIN))
        manager.createNotificationChannel(NotificationChannel(ASK,
            context.getString(R.string.channel_ask), NotificationManager.IMPORTANCE_HIGH))
        manager.createNotificationChannel(NotificationChannel(QUEUE,
            context.getString(R.string.channel_queue), NotificationManager.IMPORTANCE_LOW))
    }

    fun ready(context: Context): Notification =
        Notification.Builder(context, SERVICE)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.ready_title))
            .setContentText(context.getString(R.string.ready_text))
            .setContentIntent(PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE))
            .setOngoing(true)
            .build()

    /** One per code (several PCs may pair at once); 1000 and up, clear of SERVICE_ID. */
    private fun pairingId(code: String) = 1000 + code.toInt()

    fun pairing(context: Context, pc: String, code: String, open: Intent) {
        fun action(action: String) = PendingIntent.getBroadcast(context, pairingId(code),
            Intent(context, PairingReceiver::class.java).setAction(action).putExtra(PairActivity.EXTRA_CODE, code),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = Notification.Builder(context, ASK)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.pair_title, pc))
            .setContentText(context.getString(R.string.pair_message, code.chunked(3).joinToString(" ")))
            .setContentIntent(PendingIntent.getActivity(context, pairingId(code), open,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .addAction(Notification.Action.Builder(null, context.getString(R.string.pair),
                action(PairingReceiver.ACTION_ACCEPT)).build())
            .addAction(Notification.Action.Builder(null, context.getString(R.string.cancel),
                action(PairingReceiver.ACTION_REJECT)).build())
            .setAutoCancel(true)
            .build()
        context.getSystemService(NotificationManager::class.java).notify(pairingId(code), notification)
    }

    fun cancelPairing(context: Context, code: String) =
        context.getSystemService(NotificationManager::class.java).cancel(pairingId(code))

    /** What waits to be signed: one notification for the whole queue, gone
     * when it is empty. alert: it pops up, once per batch (not when updated
     * while shown); otherwise it only sits there (the screen opened by itself). */
    fun waiting(context: Context, jobs: List<Jobs.Job>, alert: Boolean) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (jobs.isEmpty()) return manager.cancel(WAITING_ID)
        val titles = Notification.InboxStyle()
        jobs.take(6).forEach { titles.addLine(it.title) }
        if (jobs.size > 6) titles.setSummaryText(context.resources.getQuantityString(R.plurals.and_more, jobs.size - 6, jobs.size - 6))
        val notification = Notification.Builder(context, if (alert) ASK else QUEUE)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.resources.getQuantityString(R.plurals.to_sign, jobs.size, jobs.size))
            .setContentText(jobs.joinToString(", ") { it.title })
            .setStyle(titles)
            .setNumber(jobs.size)
            .setContentIntent(PendingIntent.getActivity(context, WAITING_ID, SignActivity.open(context, null),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setOnlyAlertOnce(true)
            .build()
        manager.notify(WAITING_ID, notification)
    }

    private const val WAITING_ID = 2
}
