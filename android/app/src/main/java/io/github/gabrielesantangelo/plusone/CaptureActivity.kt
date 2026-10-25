package io.github.gabrielesantangelo.plusone

import android.app.Activity

/**
 * Nothing to see: for an instant the app is in front, so that its service
 * may use the camera or the microphone for the PC (CompanionService.foreground).
 * It opens over other apps with "display over other apps", and closes at once.
 */
class CaptureActivity : Activity() {
    // No animation either way: its theme has none.
    override fun onResume() {
        super.onResume()
        CompanionService.instance?.elevate()
        finish()
    }
}
