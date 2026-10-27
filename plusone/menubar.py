"""Plus One on macOS: a window of the tablets and the settings, and the menu
bar's item, which stays when the window is closed. The first time, and
whenever a permission is missing, a welcome window asks for them.

python3 -m plusone.menubar
"""
import json
import os
import subprocess
import sys
import threading

import objc
from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSApplication, NSApplicationActivationPolicyAccessory,
                    NSBackingStoreBuffered, NSBox, NSBoxSeparator, NSButton, NSColor, NSEdgeInsetsMake, NSFont, NSImage,
                    NSLayoutAttributeCenterY, NSLayoutAttributeLeading, NSMenu, NSMenuItem, NSPopUpButton,
                    NSStackView, NSStatusBar, NSTextField, NSUserInterfaceLayoutOrientationHorizontal,
                    NSUserInterfaceLayoutOrientationVertical, NSVariableStatusItemLength, NSWindow,
                    NSWindowStyleMaskClosable, NSWindowStyleMaskTitled, NSWorkspace)
from Foundation import NSURL, NSBundle, NSNetService, NSNetServiceBrowser, NSObject, NSTimer
from PyObjCTools import AppHelper

from . import appdisplay, companion, i18n, log, macos, usb
from .i18n import _

SERVICE = "_plusone._tcp."  # nearby.COMPANION, as Bonjour writes it
CABLE = "the tablet on the cable"
MODE = (None, None, 60)     # the size closest to 1920x1200 at the tablet's proportions, 60 fps
BITRATE = 20000             # kbit/s at 1920x1200, appdisplay.main's
SETTINGS = os.path.join(os.path.dirname(companion.CONFIG), "macos.json")
DEFAULTS = {"minimized": False, "language": "auto"}
LANGUAGES = ("auto", "en", "it")
WIDTH = 420                 # points of the windows' text
PRIVACY = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def load_settings():
    try:
        with open(SETTINGS) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(settings, f)


def alert(title, text, buttons=("OK",)):
    """A dialog in front of everything: whether the first button was chosen."""
    dialog = NSAlert.alloc().init()
    dialog.setMessageText_(title)
    dialog.setInformativeText_(text)
    for button in buttons:
        dialog.addButtonWithTitle_(button)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    return dialog.runModal() == NSAlertFirstButtonReturn


def error_text(e):
    """An exception as the user reads it: its message translated, if it is one of ours."""
    return _(str(e)) or repr(e)


# -- the windows' parts ---------------------------------------------------------
def label(text, bold=False, small=False, wrap=False):
    field = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
    if wrap:
        field.setPreferredMaxLayoutWidth_(WIDTH)
    if bold:
        field.setFont_(NSFont.boldSystemFontOfSize_(13))
    if small:
        field.setFont_(NSFont.systemFontOfSize_(11))
        field.setTextColor_(NSColor.secondaryLabelColor())
    return field


def button(title, target, action, key=None):
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setIdentifier_(key or "")
    return b


def stack(views, vertical=True, spacing=8):
    s = NSStackView.stackViewWithViews_(views)
    s.setOrientation_(NSUserInterfaceLayoutOrientationVertical if vertical
                      else NSUserInterfaceLayoutOrientationHorizontal)
    s.setAlignment_(NSLayoutAttributeLeading if vertical else NSLayoutAttributeCenterY)
    s.setSpacing_(spacing)
    return s


def separator():
    box = NSBox.alloc().initWithFrame_(((0, 0), (WIDTH, 1)))
    box.setBoxType_(NSBoxSeparator)
    return box


def window(title):
    w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (WIDTH + 40, 100)), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False)
    w.setTitle_(title)
    w.setReleasedWhenClosed_(False)  # closed is hidden: shown again from the menu bar
    w.center()
    return w


def fill(w, views):
    """The window's content made again, its top left where it was."""
    content = stack(views, spacing=12)
    content.setEdgeInsets_(NSEdgeInsetsMake(20, 20, 20, 20))
    content.widthAnchor().constraintGreaterThanOrEqualToConstant_(WIDTH + 40).setActive_(True)
    top = w.frame().origin.y + w.frame().size.height
    w.setContentView_(content)
    w.setContentSize_(content.fittingSize())
    frame = w.frame()
    w.setFrameTopLeftPoint_((frame.origin.x, top))


def show(w):
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    w.makeKeyAndOrderFront_(None)


class Menubar(NSObject):
    def init(self):
        self = objc.super(Menubar, self).init()
        self.settings = load_settings()
        i18n.set_language(self.settings["language"])
        self.apps = {}      # Bonjour name -> (app id, [(address, port)], tablet name)
        self.services = {}  # Bonjour name -> its NSNetService, kept: it may resolve again
        self.session = None
        self.cable = None   # usb.Bridge, while the link over the cable is open
        self.plugged = usb.tablet() is not None
        self.state = _("No tablet with Plus One open")
        self.asked = set()  # the permissions macOS was asked for, this run
        self.window = window("Plus One")
        self.welcome = window(_("Welcome to Plus One"))
        self.shown = {}     # window -> what its content shows, made again when that changes
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
        # The cable and the permissions have no notifications: looked at.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(2.0, self, "tick:", None, True)
        return self

    @objc.python_method
    def launched(self):
        if not self._permitted():
            self.openWelcome_(None)
        elif not self.settings["minimized"]:
            self.openWindow_(None)

    def tick_(self, _timer):
        self.plugged = self.cable is not None or usb.tablet() is not None
        self._refresh()

    # -- what can be done, in the menu and the window ----------------------------
    @objc.python_method
    def _actions(self):
        """[(title, selector, key)] for the tablets as they are now."""
        if self.session is not None:
            return [(_("Disconnect"), "disconnect:", None)]
        actions = []
        paired = companion.Paired().all()
        for key, (app_id, _targets, name) in sorted(self.apps.items(), key=lambda item: item[1][2].lower()):
            if app_id and any(fp.startswith(app_id) for fp in paired):
                # ponytail: a tablet on the cable takes the cable (Wi-Fi's tail
                # latency is ~10x); a second tablet on Wi-Fi meanwhile is left out.
                if not self.plugged:
                    actions.append((_("Show the desktop on {name}").format(name=name), "connect:", key))
            else:
                actions.append((_("Pair with {name}…").format(name=name), "pair:", key))
        if self.plugged:
            if paired:
                actions.append((_("Show the desktop over the cable"), "connectCable:", None))
            actions.append((_("Pair over the cable…"), "pairCable:", None))
        return actions

    def menuNeedsUpdate_(self, menu):
        menu.removeAllItems()
        state = menu.addItemWithTitle_action_keyEquivalent_(self.state, None, "")
        state.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        for title, action, key in self._actions():
            self._add(menu, title, action, key)
        menu.addItem_(NSMenuItem.separatorItem())
        self._add(menu, _("Open Plus One…"), "openWindow:")
        self._add(menu, _("Quit Plus One"), "quit:")

    @objc.python_method
    def _add(self, menu, title, action, key=None):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self)
        item.setIdentifier_(key or "")

    @objc.python_method
    def _show(self, state):
        self.state = state
        log("menu bar:", state)
        self._refresh()

    # -- the window ----------------------------------------------------------------
    def openWindow_(self, _sender):
        self.shown.pop(self.window, None)
        self._refresh()
        show(self.window)

    @objc.python_method
    def _refresh(self):
        """The visible windows' content, made again if what it shows changed."""
        if self.window.isVisible() or self.window not in self.shown:
            actions = self._actions()
            if self.shown.get(self.window) != (self.state, actions, i18n._current):
                self.shown[self.window] = (self.state, actions, i18n._current)
                self._fill_window(actions)
        if self.welcome.isVisible():
            granted = (macos.screen_recording(), macos.accessibility(prompt=False))
            if self.shown.get(self.welcome) != (granted, i18n._current):
                self.shown[self.welcome] = (granted, i18n._current)
                self._fill_welcome(granted)

    @objc.python_method
    def _fill_window(self, actions):
        devices = [label(_("Devices"), bold=True), label(self.state)]
        devices += [button(title, self, action, key) for title, action, key in actions] or [
            label(_("Open Plus One on the tablet, on the same Wi-Fi as this Mac or on the cable."), small=True,
                  wrap=True)]
        minimized = NSButton.checkboxWithTitle_target_action_(_("Open minimized in the menu bar"), self, "minimized:")
        minimized.setState_(int(self.settings["minimized"]))
        language = NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (160, 26)), False)
        language.addItemsWithTitles_([_("Automatic"), "English", "Italiano"])
        language.selectItemAtIndex_(LANGUAGES.index(self.settings["language"]))
        language.setTarget_(self)
        language.setAction_("language:")
        fill(self.window, devices + [
            separator(), label(_("Settings"), bold=True), minimized,
            stack([label(_("Language")), language], vertical=False),
            button(_("Permissions…"), self, "openWelcome:")])

    def minimized_(self, sender):
        self.settings["minimized"] = bool(sender.state())
        save_settings(self.settings)

    def language_(self, sender):
        self.settings["language"] = LANGUAGES[sender.indexOfSelectedItem()]
        save_settings(self.settings)
        i18n.set_language(self.settings["language"])
        self.welcome.setTitle_(_("Welcome to Plus One"))
        self._refresh()

    # -- the permissions -------------------------------------------------------------
    @objc.python_method
    def _permitted(self):
        return macos.screen_recording() and macos.accessibility(prompt=False)

    def openWelcome_(self, _sender):
        self.shown.pop(self.welcome, None)
        show(self.welcome)
        self._refresh()

    @objc.python_method
    def _fill_welcome(self, granted):
        rows = []
        for (name, why, action), allowed in zip(
                ((_("Screen Recording"), _("To send the desktop to the tablet."), "allowScreen:"),
                 (_("Accessibility"), _("So the tablet's touches and the S Pen move the pointer."),
                  "allowAccessibility:")), granted):
            text = stack([label(name, bold=True), label(why, small=True)], spacing=2)
            text.widthAnchor().constraintEqualToConstant_(WIDTH - 110).setActive_(True)  # the answers in a column
            rows.append(stack([
                text,
                label("✓ " + _("Allowed")) if allowed else button(_("Allow…"), self, action)],
                vertical=False, spacing=24))
        done = button(_("Continue"), self, "closeWelcome:")
        done.setKeyEquivalent_("\r")
        done.setEnabled_(all(granted))
        fill(self.welcome, [
            label(_("To show this Mac's desktop on the tablet, Plus One needs two permissions."), wrap=True),
            *rows,
            label(_("macOS may also ask to use the local network: allow it, to find the tablet over Wi-Fi."),
                  small=True, wrap=True),
            separator(),
            label(_("Once Screen Recording is allowed, Plus One may need to reopen."), small=True, wrap=True),
            stack([button(_("Reopen Plus One"), self, "reopen:"), button(_("Later"), self, "closeWelcome:"), done],
                  vertical=False)])

    def allowScreen_(self, _sender):
        # macOS asks once; after that, its settings are where it is allowed.
        if "screen" in self.asked:
            self._privacy("ScreenCapture")
        else:
            self.asked.add("screen")
            import Quartz
            Quartz.CGRequestScreenCaptureAccess()

    def allowAccessibility_(self, _sender):
        if "accessibility" in self.asked:
            self._privacy("Accessibility")
        else:
            self.asked.add("accessibility")
            macos.accessibility(prompt=True)

    @objc.python_method
    def _privacy(self, pane):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(PRIVACY + pane))

    def closeWelcome_(self, _sender):
        self.welcome.orderOut_(None)
        self.openWindow_(None)

    def reopen_(self, _sender):
        """Plus One again, a moment after this one quits: a permission given
        meanwhile is seen at start only."""
        path = NSBundle.mainBundle().bundlePath()
        if path.endswith(".app") and NSBundle.mainBundle().bundleIdentifier() != "org.python.python":
            subprocess.Popen(["/bin/sh", "-c", 'sleep 1; open -n "$0"', path], start_new_session=True)
        else:  # from the sources: python3 -m plusone.menubar
            subprocess.Popen(["/bin/sh", "-c", 'sleep 1; exec "$0" -m plusone.menubar', sys.executable],
                             start_new_session=True, cwd=os.getcwd())
        self.quit_(None)

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
        if self.session is None and self.state == _("No tablet with Plus One open"):
            self._show(_("A tablet is ready"))
        self._refresh()

    def netService_didNotResolve_(self, service, error):
        log(f"menu bar: {service.name()} not resolved: {dict(error)}")

    def netServiceBrowser_didRemoveService_moreComing_(self, _browser, service, _more):
        self.apps.pop(service.name(), None)
        self.services.pop(service.name(), None)
        if not self.apps and self.session is None:
            self._show(_("No tablet with Plus One open"))
        self._refresh()

    # -- pairing -----------------------------------------------------------------
    def pair_(self, sender):
        app = self.apps.get(sender.identifier())
        if app is None:
            return
        self._pair(app[2], app[1])

    def pairCable_(self, _sender):
        self._over_cable(lambda bridge: self._pair(_(CABLE), [bridge.target]))

    @objc.python_method
    def _pair(self, name, targets):
        def ask(tablet, code):
            """The link's thread: the code compared on the main thread."""
            done, answer = threading.Event(), []

            def show_code():
                answer.append(alert(_("Pair with {name}?").format(name=tablet),
                                    _("Pair only if {name} shows the same code:\n\n{code}").format(
                                        name=tablet, code=code), (_("Pair"), _("Cancel"))))
                done.set()
            macos.later(show_code)
            done.wait(companion.PAIRING_TIMEOUT)
            return bool(answer and answer[0])

        def run():
            try:
                _fp, tablet = companion.pair(targets, ask)
            except companion.PairingRefused:
                macos.later(self._show, _("Not paired with {name}").format(name=name))
            except Exception as e:  # noqa: BLE001 - not there, or gone
                macos.later(alert, _("Could not pair with {name}").format(name=name), error_text(e))
            else:
                macos.later(self._show, _("Paired with {name}").format(name=tablet))
        self._show(_("Pairing with {name}…").format(name=name))
        threading.Thread(target=run, daemon=True).start()

    # -- the desktop on the tablet ---------------------------------------------
    def connect_(self, sender):
        app = self.apps.get(sender.identifier())
        if app is None or self.session is not None:
            return
        app_id, _targets, name = app
        targets = sorted({t for found, ts, _n in self.apps.values() if found == app_id for t in ts})
        self._start(name, targets, app_id)

    def connectCable_(self, _sender):
        if self.session is None:
            # No id from Bonjour here: any paired tablet's app, which TLS proves.
            self._over_cable(lambda bridge: self._start(_(CABLE), [bridge.target], None, bridge.relay))

    @objc.python_method
    def _start(self, name, targets, app_id, relay=None):
        if self.session is not None:
            return
        self.session = appdisplay.AppDisplay(
            targets, app_id, MODE, BITRATE, "videotoolbox", relay=relay,
            on_started=lambda mode, _screen: self._show(
                _("On {name}, {width}x{height}").format(name=name, width=mode[0], height=mode[1])),
            on_ended=lambda error, started: self._ended(name, error),
            on_paused=lambda paused: self._show((_("On {name}, paused") if paused else _("On {name}")).format(
                name=name)))
        self._show(_("Connecting to {name}…").format(name=name))
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
                macos.later(self._show, _("No link over the cable"))
                macos.later(alert, _("Plus One could not use the cable"), error_text(e))
                return

            def opened():
                self.cable = bridge
                then(bridge)
            macos.later(opened)
        self._show(_("Opening the link over the cable…"))
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _ended(self, name, error):
        self.session = None
        if error is None:
            self._show(_("Ended on {name}").format(name=name))
        else:
            self._show(_("Could not show the desktop on {name}").format(name=name))
            alert(_("The desktop on {name} ended").format(name=name), error_text(error))

    def disconnect_(self, _sender):
        if self.session is not None:
            self.session.stop()  # its on_ended is not called: said here
            self.session = None
            self._show(_("Disconnected"))

    def quit_(self, _sender):
        self.disconnect_(None)
        if self.cable is not None:
            self.cable.close()
        NSApplication.sharedApplication().terminate_(None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    menubar = Menubar.alloc().init()
    menubar.launched()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
