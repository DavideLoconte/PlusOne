package io.github.gabrielesantangelo.plusone

import android.app.Activity
import android.hardware.usb.UsbAccessory
import android.hardware.usb.UsbManager
import android.os.Bundle

/**
 * Nothing to see: Android opens it when the PC on the cable asks for Plus
 * One (Android Open Accessory, res/xml/accessory_filter.xml), once the user
 * lets it the first time. The accessory goes to the service (UsbTunnel).
 */
class UsbActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent.getParcelableExtra(UsbManager.EXTRA_ACCESSORY, UsbAccessory::class.java)
            ?.let { CompanionService.startUsb(this, it) }
        finish()
    }
}
