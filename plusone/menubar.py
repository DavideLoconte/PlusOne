"""Plus One in the macOS menu bar: the tablets with Plus One's app open on
the network, pairing with one, and the desktop on the paired one.

python3 -m plusone.menubar
"""
import threading

import objc
from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSApplication, NSApplicationActivationPolicyAccessory,
                    NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
from Foundation import NSNetService, NSNetServiceBrowser, NSObject
from PyObjCTools import AppHelper

from . import appdisplay, companion, log, macos, usb

SERVICE = "_plusone._tcp."  # nearby.COMPANION, as Bonjour writes it
CABLE = "the tablet on the cable"
MODE = (None, None, 60)     # the size closest to 1920x1200 at the tablet's proportions, 60 fps
BITRATE = 20000             # kbit/s at 1920x1200, appdisplay.main's


def alert(title, text, buttons=("OK",)):
    """A dialog in front of everything: whether the first button was chosen."""
    dialog = NSAlert.alloc().init()
    dialog.setMessageText_(title)
    dialog.setInformativeText_(text)
    for button in buttons:
        dialog.addButtonWithTitle_(button)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    return dialog.runModal() == NSAlertFirstButtonReturn


class Menubar(NSObject):
    def init(self):
        self = objc.super(Menubar, self).init()
        self.apps = {}      # Bonjour name -> (app id, [(address, port)], tablet name)
        self.services = {}  # Bonjour name -> its NSNetService, kept: it may resolve again
        self.session = None
        self.cable = None   # usb.Bridge, while the link over the cable is open
        self.state = "No tablet with Plus One open"
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.setAutosaveName_("PlusOne")  # where it is ⌘-dragged to, remembered
        self.item.button().setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "ipad.landscape", "Plus One"))
        self.menu = NSMenu.alloc().init()
        self.menu.setDelegate_(self)  # made again each time it opens
        self.item.setMenu_(self.menu)
        # ponytail: NSNetServiceBrowser is deprecated for the Network framework's
        # NWBrowser, which PyObjC binds poorly; it still browses.
        self.browser = NSNetServiceBrowser.alloc().init()
        self.browser.setDelegate_(self)
        self.browser.searchForServicesOfType_inDomain_(SERVICE, "local.")
        return self

    # -- the menu --------------------------------------------------------------
    def menuNeedsUpdate_(self, menu):
        menu.removeAllItems()
        state = menu.addItemWithTitle_action_keyEquivalent_(self.state, None, "")
        state.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        if self.session is not None:
            self._add(menu, "Disconnect", "disconnect:")
        else:
            paired = companion.Paired().all()
            for key, (app_id, _targets, name) in sorted(self.apps.items(), key=lambda item: item[1][2].lower()):
                if app_id and any(fp.startswith(app_id) for fp in paired):
                    self._add(menu, f"Show the desktop on {name}", "connect:", key)
                else:
                    self._add(menu, f"Pair with {name}…", "pair:", key)
            if self.cable is not None or usb.tablet() is not None:
                if paired:
                    self._add(menu, "Show the desktop over the cable", "connectCable:")
                self._add(menu, "Pair over the cable…", "pairCable:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._add(menu, "Quit Plus One", "quit:")

    @objc.python_method
    def _add(self, menu, title, action, key=None):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self)
        item.setRepresentedObject_(key)

    @objc.python_method
    def _show(self, state):
        self.state = state
        log("menu bar:", state)

    # -- the tablets on the network ---------------------------------------------
    def netServiceBrowser_didFindService_moreComing_(self, _browser, service, _more):
        service.setDelegate_(self)
        self.services[service.name()] = service
        service.resolveWithTimeout_(5.0)

    def netServiceDidResolveAddress_(self, service):
        # Called once per address found: IPv6 first, often, then IPv4.
        fields = {key: bytes(memoryview(value)).decode(errors="replace")
                  for key, value in NSNetService.dictionaryFromTXTRecordData_(service.TXTRecordData()).items()}
        # PyObjC gives each address as a tuple: (host, port) for IPv4, 4 long for IPv6.
        targets = sorted({(address[0].decode(), address[1]) for address in service.addresses()
                          if len(address) == 2})
        if not targets:
            log(f"menu bar: {service.name()} has no IPv4 address yet")
            return
        name = fields.get("name", "")[:64] or service.name()
        self.apps[service.name()] = (fields.get("id", "").lower()[:64], targets, name)
        log(f"menu bar: Plus One's app on {name} at {targets[0][0]}:{targets[0][1]}")
        if self.session is None and self.state.startswith("No tablet"):
            self._show("A tablet is ready")

    def netService_didNotResolve_(self, service, error):
        log(f"menu bar: {service.name()} not resolved: {dict(error)}")

    def netServiceBrowser_didRemoveService_moreComing_(self, _browser, service, _more):
        self.apps.pop(service.name(), None)
        self.services.pop(service.name(), None)
        if not self.apps and self.session is None:
            self._show("No tablet with Plus One open")

    # -- pairing -----------------------------------------------------------------
    def pair_(self, sender):
        app = self.apps.get(sender.representedObject())
        if app is None:
            return
        self._pair(app[2], app[1])

    def pairCable_(self, _sender):
        self._over_cable(lambda bridge: self._pair(CABLE, [bridge.target]))

    @objc.python_method
    def _pair(self, name, targets):
        def ask(tablet, code):
            """The link's thread: the code compared on the main thread."""
            done, answer = threading.Event(), []

            def show():
                answer.append(alert(f"Pair with {tablet}?",
                                    f"Pair only if {tablet} shows the same code:\n\n{code}", ("Pair", "Cancel")))
                done.set()
            macos.later(show)
            done.wait(companion.PAIRING_TIMEOUT)
            return bool(answer and answer[0])

        def run():
            try:
                _fp, tablet = companion.pair(targets, ask)
            except companion.PairingRefused:
                macos.later(self._show, f"Not paired with {name}")
            except Exception as e:  # noqa: BLE001 - not there, or gone
                macos.later(alert, f"Could not pair with {name}", str(e) or repr(e))
            else:
                macos.later(self._show, f"Paired with {tablet}")
        self._show(f"Pairing with {name}…")
        threading.Thread(target=run, daemon=True).start()

    # -- the desktop on the tablet ---------------------------------------------
    def connect_(self, sender):
        app = self.apps.get(sender.representedObject())
        if app is None or self.session is not None:
            return
        app_id, _targets, name = app
        targets = sorted({t for found, ts, _n in self.apps.values() if found == app_id for t in ts})
        self._start(name, targets, app_id)

    def connectCable_(self, _sender):
        if self.session is None:
            # No id from Bonjour here: any paired tablet's app, which TLS proves.
            self._over_cable(lambda bridge: self._start(CABLE, [bridge.target], None, bridge.relay))

    @objc.python_method
    def _start(self, name, targets, app_id, relay=None):
        if self.session is not None:
            return
        self.session = appdisplay.AppDisplay(
            targets, app_id, MODE, BITRATE, "videotoolbox", relay=relay,
            on_started=lambda mode, _screen: self._show(f"On {name}, {mode[0]}x{mode[1]}"),
            on_ended=lambda error, started: self._ended(name, error),
            on_paused=lambda paused: self._show(f"On {name}" + (", paused" if paused else "")))
        self._show(f"Connecting to {name}…")
        self.session.start()

    @objc.python_method
    def _over_cable(self, then):
        """then(bridge) on the main thread, once the cable's link is open
        (kept open after: pairing, then the desktop)."""
        if self.cable is not None:
            return then(self.cable)

        def lost(error):
            macos.later(setattr, self, "cable", None)

        def run():
            try:
                bridge = usb.Bridge(usb.Link(), on_lost=lost)
            except Exception as e:  # noqa: BLE001 - no libusb, unplugged, not allowed on the tablet
                macos.later(self._show, "No link over the cable")
                macos.later(alert, "Plus One could not use the cable", str(e) or repr(e))
                return

            def opened():
                self.cable = bridge
                then(bridge)
            macos.later(opened)
        self._show("Opening the link over the cable…")
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _ended(self, name, error):
        self.session = None
        self._show(f"Ended on {name}" if error is None else f"Could not show the desktop on {name}")
        if error is not None:
            alert(f"The desktop on {name} ended", str(error) or repr(error))

    def disconnect_(self, _sender):
        if self.session is not None:
            self.session.stop()  # its on_ended is not called: said here
            self.session = None
            self._show("Disconnected")

    def quit_(self, _sender):
        self.disconnect_(None)
        if self.cable is not None:
            self.cable.close()
        NSApplication.sharedApplication().terminate_(None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    menubar = Menubar.alloc().init()  # noqa: F841 - kept for the run loop
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
