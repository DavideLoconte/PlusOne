package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager

/** The PC's desktop, on the whole screen: the session in progress
 * (DisplaySession.current) decodes into this activity's surface while it
 * is shown. One activity: a new session takes it over (onNewIntent), as its
 * surface stays the same. */
class DisplayActivity : Activity(), SurfaceHolder.Callback {
    private var surface: Surface? = null
    private var session: DisplaySession? = null  // the one decoding into the surface

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instance = this
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.attributes = window.attributes.apply {
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        }
        val view = SurfaceView(this)
        view.holder.addCallback(this)
        setContentView(view)
        DisplayInput(view) { session }
        window.insetsController?.apply {
            hide(WindowInsets.Type.systemBars())
            systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        if (DisplaySession.current == null) finish()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        bind()
    }

    // The session in progress, not the bound one: the surface (and with it
    // the binding) goes before onStop().
    override fun onStart() {
        super.onStart()
        DisplaySession.current?.show(true)
    }

    override fun onStop() {
        super.onStop()
        DisplaySession.current?.show(false)  // another app, the screen off: the PC rests
    }

    override fun onDestroy() {
        // Back, or swiped away from Recents: the desktop is no longer wanted here.
        if (isFinishing) (session ?: DisplaySession.current)?.end()
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun surfaceCreated(holder: SurfaceHolder) {
        surface = holder.surface
        bind()
    }

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        session?.detach()
        session = null
        surface = null
    }

    /** The session in progress into the surface, if both are there. */
    private fun bind() {
        val current = DisplaySession.current ?: return finish()
        if (current === session) return
        session?.detach()
        session = null
        val surface = surface ?: return  // surfaceCreated() binds it
        current.attach(surface)
        session = current
        current.show(true)
    }

    companion object {
        // On the main thread only.
        private var instance: DisplayActivity? = null

        /** On the main thread: a session ended. The activity goes, unless a
         * newer session took it over meanwhile. */
        fun ended(session: DisplaySession) {
            val activity = instance ?: return
            if (DisplaySession.current == null || activity.session === session) activity.finish()
        }
    }
}
