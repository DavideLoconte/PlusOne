"""Sign a PDF on the tablet in one step.

With Plus One's app on the tablet (companion.py), the PDF goes to it over
the home network and the ink is drawn on the tablet itself. Otherwise
Second Screen: connects the tablet if needed, opens the signing window full
screen on the tablet's monitor as soon as it appears, and disconnects again
if the connection was opened for this. Either way the result comes back
with a notification; if the tablet does not come, the notification offers
signing on the PC.
"""
import os
import ssl
import threading

from gi.repository import Gio, GLib

from . import companion, log, once
from .engine import NOT_ANSWERING, State
from .i18n import _

MONITOR_WAIT = 5  # seconds for the tablet's monitor to appear once streaming
NOTIFICATION = "sign"
PAIRING = "pair"


class Signing:
    def __init__(self, app):
        self.app = app
        self.queue = []            # paths waiting for the tablet
        self.opening = set()       # paths waiting for the tablet's monitor
        self.windows = []
        self.in_app = set()        # paths the tablet's app is signing
        self.pairing = {}          # code -> (threading.Event, answer list), while the user compares
        self._pair_dialogs = {}    # code -> its window
        self.connected_for_us = False
        for name, callback in (("sign-here", self._sign_here), ("open-file", self._open_file),
                               ("show-file", self._show_file), ("companion-pair", self._pair_answer),
                               ("companion-no", self._pair_answer)):
            action = Gio.SimpleAction.new(name, GLib.VariantType("s"))
            action.connect("activate", callback)
            app.add_action(action)

    def request(self, path):
        path = os.path.abspath(path)
        log("signing", path)
        # Asked again while the tablet takes its time: one window per
        # document, or Done would leave the others open, and the tablet with them.
        for window in self.windows:
            if window.path == path:
                window.present()
                return
        if path in self.opening or path in self.in_app:
            return
        controller = self.app.controller
        target = self.app.nearby.companion() if self.app.settings.sign_on_tablet else None
        if target and controller.state != State.STREAMING:
            self._sign_in_app(path, target)
            return
        self._second_screen(path)

    def _second_screen(self, path):
        controller = self.app.controller
        if controller.state == State.STREAMING:
            self._open_on_tablet(path)
            return
        if path not in self.queue:
            self.queue.append(path)
        if not controller.running:
            self.connected_for_us = True
            controller.start()
            if path not in self.queue:
                return  # it could not start, and on_state() said so
        self._notify_waiting(path, _("Tap “Second screen” on the tablet to sign {name}")
                             .format(name=os.path.basename(path)))

    # -- in the tablet's app ---------------------------------------------------
    def _sign_in_app(self, path, target):
        address, port, tablet = target
        log(f"signing in the app on {tablet}: {path}")
        self.in_app.add(path)
        self._notify_waiting(path, _("Sending {name} to Plus One on {tablet}")
                             .format(name=os.path.basename(path), tablet=tablet))

        def work():
            error = None
            try:
                result = companion.sign((address, port), path, self._ask_pairing)
            except companion.PairingRefused:
                result, error = None, "refused"
            except (OSError, EOFError, ValueError, ssl.SSLError) as e:
                result, error = None, e
            GLib.idle_add(once(self._app_done, path, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _app_done(self, path, result, error):
        if path not in self.in_app:
            return  # the user chose to sign on the PC meanwhile
        self.in_app.discard(path)
        if error == "refused":
            log("companion: not paired")
            note = Gio.Notification.new(_("Not paired"))
            note.set_body(_("The codes were not confirmed on both the PC and the tablet"))
            self.app.send_notification(NOTIFICATION, note)
            return
        if error is not None:
            # The app did not answer: Second Screen instead.
            log("signing in the app failed:", repr(error))
            self._second_screen(path)
            return
        marks = companion.to_marks(result) if result else {}
        if not marks:
            self.app.withdraw_notification(NOTIFICATION)
            return

        def save():
            from .pdf import save_signed
            try:
                done = save_signed(path, marks)
            except Exception as e:  # noqa: BLE001 - shown to the user
                log("signing: could not save:", repr(e))
                done = e
            GLib.idle_add(once(self._app_saved, done))
        threading.Thread(target=save, daemon=True).start()

    def _app_saved(self, result):
        if isinstance(result, Exception):
            note = Gio.Notification.new(_("Could not save: {error}").format(error=result))
            self.app.send_notification(NOTIFICATION, note)
            return
        self._signed(result)

    def _ask_pairing(self, tablet, code):
        """From the app's thread: asks this PC's user to compare the code
        (the tablet shows it too); blocks until they answer."""
        answered = threading.Event()
        answer = []
        self.pairing[code] = (answered, answer)
        GLib.idle_add(once(self._show_pairing, tablet, code))
        if not answered.wait(companion.PAIRING_TIMEOUT):
            log("companion: pairing not confirmed on the PC in time")
        self.pairing.pop(code, None)
        GLib.idle_add(once(self._pairing_over, code))
        return bool(answer and answer[0])

    def _pairing_over(self, code):
        self.app.withdraw_notification(PAIRING)
        dialog = self._pair_dialogs.pop(code, None)
        if dialog is not None:
            dialog.close()

    def _show_pairing(self, tablet, code):
        """A window (a notification went unseen) and a notification."""
        from gi.repository import Adw
        dialog = Adw.MessageDialog(application=self.app, heading=_("Pair with {tablet}?").format(tablet=tablet),
                                   body_use_markup=True,
                                   body=_("Pair only if the tablet shows this same code, and confirm "
                                          "there too:") + f"\n\n<span size='xx-large' font_family='monospace'>"
                                        f"{code[:3]} {code[3:]}</span>")
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("pair", _("Pair"))
        dialog.set_response_appearance("pair", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, response: self._answer_pairing(code, response == "pair"))
        self._pair_dialogs[code] = dialog
        dialog.present()
        note = Gio.Notification.new(_("Pair with {tablet}?").format(tablet=tablet))
        note.set_body(_("Check that the tablet shows the same code: {code}")
                      .format(code=f"{code[:3]} {code[3:]}"))
        note.set_priority(Gio.NotificationPriority.HIGH)
        note.add_button_with_target(_("Pair"), "app.companion-pair", GLib.Variant("s", code))
        note.add_button_with_target(_("Cancel"), "app.companion-no", GLib.Variant("s", code))
        self.app.send_notification(PAIRING, note)

    def _pair_answer(self, action, param):
        self._answer_pairing(param.get_string(), action.get_name() == "companion-pair")

    def _answer_pairing(self, code, accepted):
        log("companion: pairing", "confirmed on the PC" if accepted else "refused on the PC")
        dialog = self._pair_dialogs.pop(code, None)
        if dialog is not None:
            dialog.close()
        waiting = self.pairing.get(code)
        if waiting:
            answered, answer = waiting
            answer.append(accepted)
            answered.set()

    # -- Second Screen ---------------------------------------------------------
    def _notify_waiting(self, path, body):
        note = Gio.Notification.new(_("Sign on the tablet"))
        note.set_body(body)
        note.add_button_with_target(_("Sign on this PC"), "app.sign-here", GLib.Variant("s", path))
        self.app.send_notification(NOTIFICATION, note)

    def on_state(self, state):
        """Returns True if it told the user about this state itself."""
        if not self.queue:
            return False
        if state == State.STREAMING:
            queue, self.queue = self.queue, []
            self.app.withdraw_notification(NOTIFICATION)
            for path in queue:
                self._open_on_tablet(path)
        elif state == State.CONNECTING and self.app.controller.detail[0] == NOT_ANSWERING:
            self._notify_waiting(self.queue[-1], _(NOT_ANSWERING))
            return True
        elif state in (State.IDLE, State.ERROR) and not self.app.controller.running:
            # Gave up, failed or was stopped: offer the PC instead.
            queue, self.queue = self.queue, []
            self.connected_for_us = False
            for path in queue:
                note = Gio.Notification.new(_("The tablet did not connect"))
                note.set_body(_("You can sign {name} on this PC instead")
                              .format(name=os.path.basename(path)))
                note.add_button_with_target(_("Sign on this PC"), "app.sign-here",
                                            GLib.Variant("s", path))
                self.app.send_notification(NOTIFICATION, note)
            return True
        return False

    # -- windows ---------------------------------------------------------------
    def _open_on_tablet(self, path, tries=0):
        from .sign import tablet_monitor
        self.opening.add(path)
        monitor = tablet_monitor()
        if monitor is None and tries < MONITOR_WAIT * 4:
            GLib.timeout_add(250, once(self._open_on_tablet, path, tries + 1))
            return
        self._open(path, monitor)

    def _open(self, path, monitor=None):
        from .sign import SignWindow
        self.opening.discard(path)
        try:
            window = SignWindow(self.app, path, lambda result: self._done(window, path, result))
        except GLib.Error as e:
            note = Gio.Notification.new(_("Cannot open {name}").format(name=os.path.basename(path)))
            note.set_body(e.message)
            self.app.send_notification(NOTIFICATION, note)
            self._maybe_disconnect()
            return
        self.windows.append(window)
        if monitor is not None:
            window.fullscreen_on_monitor(monitor)
        else:
            window.maximize()
        window.present()

    def _done(self, window, path, result):
        if window in self.windows:
            self.windows.remove(window)
        if result:
            self._signed(result)
        # Let the window go away first; the display it is on may follow.
        GLib.idle_add(once(self._maybe_disconnect))

    def _signed(self, result):
        log("signed:", result)
        note = Gio.Notification.new(_("Signed"))
        note.set_body(os.path.basename(result))
        note.set_default_action_and_target("app.open-file", GLib.Variant("s", result))
        note.add_button_with_target(_("Open"), "app.open-file", GLib.Variant("s", result))
        note.add_button_with_target(_("Show in folder"), "app.show-file", GLib.Variant("s", result))
        self.app.send_notification(NOTIFICATION, note)

    def _maybe_disconnect(self):
        if self.connected_for_us and not self.windows and not self.queue and not self.opening:
            self.connected_for_us = False
            self.app.controller.stop()

    # -- notification actions --------------------------------------------------
    def _sign_here(self, _action, param):
        path = param.get_string()
        self.in_app.discard(path)  # whatever the app sends back now is ignored
        if path in self.queue:
            self.queue.remove(path)
        self.app.withdraw_notification(NOTIFICATION)
        if self.connected_for_us and not self.queue and self.app.controller.state != State.STREAMING:
            # Stop searching for the tablet: the radio is not needed any more.
            self.connected_for_us = False
            self.app.controller.stop()
        self._open(path)

    def _open_file(self, _action, param):
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(param.get_string()).get_uri(), None)

    def _show_file(self, _action, param):
        uri = Gio.File.new_for_path(param.get_string()).get_uri()
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call(
            "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", ([uri], "")),
            None, Gio.DBusCallFlags.NONE, -1, None, None, None)
