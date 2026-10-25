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
import shutil
import tempfile
import threading
import uuid

from gi.repository import Gio, GLib

from . import companion, log, once, signatures
from .engine import NOT_ANSWERING, State
from .i18n import _

MONITOR_WAIT = 5  # seconds for the tablet's monitor to appear once streaming
NOTIFICATION = "sign"
UPDATE = "update"
# Shared with the signing window (sign.py).
PEN = os.path.join(GLib.get_user_config_dir(), "plus-one", "pen.json")
# The PDFs as they were sent to the tablet's app: signed, that copy is what
# the marks go on (the original may change or move meanwhile).
SENT = os.path.join(GLib.get_user_cache_dir(), "plus-one", "signing")


class Signing:
    def __init__(self, app):
        self.app = app
        self.queue = []            # paths waiting for the tablet
        self.opening = set()       # paths waiting for the tablet's monitor
        self.windows = []
        self.in_app = {}           # paths the tablet's app is signing -> that request's token
        self.tablet = None         # its name
        self.signed = []           # signed in a row (several PDFs sent at once): one notification
        self.connected_for_us = False
        for name, callback in (("sign-here", self._sign_here), ("open-file", self._open_file),
                               ("show-file", self._show_file)):
            action = Gio.SimpleAction.new(name, GLib.VariantType("s"))
            action.connect("activate", callback)
            app.add_action(action)

    def request(self, path):
        """Sign the PDF at path on the tablet in use: in its app if that is on
        the network, else through Second Screen."""
        path = os.path.abspath(path)
        log("signing", path)
        from .pdf import problem
        wrong = problem(path)
        if wrong:
            # Not a PDF, too big, locked: said now, not after signing it.
            template, args = wrong
            note = Gio.Notification.new(_("Cannot sign {name}").format(name=os.path.basename(path)))
            note.set_body(_(template).format(name=os.path.basename(path), **args))
            self.app.send_notification(NOTIFICATION, note)
            return
        if self.app.tablets.active() is None:
            note = Gio.Notification.new(_("No tablet paired"))
            note.set_body(_("Pair your tablet in Plus One to sign {name} on it")
                          .format(name=os.path.basename(path)))
            note.set_default_action("app.show")
            note.add_button_with_target(_("Sign on this PC"), "app.sign-here", GLib.Variant("s", path))
            self.app.send_notification(NOTIFICATION, note)
            return
        if not (self.in_app or self.windows or self.queue or self.opening):
            self.signed = []  # nothing on the way: a new batch
        # Asked again while the tablet takes its time: one window per
        # document, or Done would leave the others open, and the tablet with them.
        for window in self.windows:
            if window.path == path:
                window.present()
                return
        if path in self.opening or path in self.in_app:
            return
        controller = self.app.controller
        target = self._find_app() if self.app.settings.sign_on_tablet else None
        # Showing the desktop through the app, the app signs too (the ink
        # under the nib); the desktop rests meanwhile and comes back after.
        if target and (controller.state != State.STREAMING or controller.app_display is not None):
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
        targets, tablet, app_id = target
        log(f"signing in the app on {tablet}: {path}")
        try:
            os.makedirs(SENT, mode=0o700, exist_ok=True)
            fd, sent = tempfile.mkstemp(suffix=".pdf", dir=SENT)
            with os.fdopen(fd, "wb") as f, open(path, "rb") as original:
                shutil.copyfileobj(original, f)
        except OSError as e:
            note = Gio.Notification.new(_("Cannot open {name}").format(name=os.path.basename(path)))
            note.set_body(e.strerror or str(e))
            self.app.send_notification(NOTIFICATION, note)
            return
        token = uuid.uuid4().hex
        self.in_app[path] = token
        # Plus One stays until the answer comes, window closed or not.
        self.app.hold()
        self._notify_in_app(path, _("Sending {name} to Plus One on {tablet}"), tablet)

        from .ink import load_pen
        color, width, _smoothing = load_pen(PEN)
        pen = {"color": list(color), "width": width}
        ours = signatures.message()

        def theirs(found):
            GLib.idle_add(once(self._merge_signatures, found))

        def opened(tablet):
            GLib.idle_add(once(self._notify_in_app, path, _("Sign {name} on {tablet}"), tablet))

        # Taken now, in the order the PDFs were asked for: they reach the tablet in it.
        turn = companion.next_turn()

        def work():
            result = error = None
            try:
                result = companion.sign(targets, sent, pen=pen, signatures=ours, expect=app_id,
                                        on_signatures=theirs, on_opened=opened,
                                        locate=lambda: self._locate_app(app_id),
                                        turn=turn, title=os.path.basename(path))
            except companion.PairingRefused:
                error = "refused"
            except companion.Lost:
                error = "lost"
            except companion.Incompatible as e:
                log("companion:", e)
                error = "incompatible"
            except Exception as e:  # noqa: BLE001 - whatever it was, Second Screen instead
                error = e
            finally:
                GLib.idle_add(once(self._app_done, path, token, sent, result, error))
        threading.Thread(target=work, daemon=True).start()

    def _find_app(self):
        """(addresses, name, app id) of the tablet in use's app on the
        network, or None. Every address it is announced at: someone may
        announce it too, and only the real one proves it (TLS)."""
        tablet = self.app.tablets.active()
        where = self.app.tablets.addresses(tablet) if tablet else []
        return (where, tablet.name, tablet.app_id) if where else None

    def _locate_app(self, app_id):
        """Where the app the PDF went to is now (its port changes when it
        restarts): that tablet's addresses, not another's."""
        return self.app.nearby.addresses(app_id) or None

    def _app_done(self, path, token, sent, result, error):
        saving = False
        try:
            saving = self._answer(path, token, sent, result, error)
        finally:
            if not saving:
                self._forget_sent(sent)

    def _forget_sent(self, sent):
        try:
            os.unlink(sent)
        except OSError:
            pass
        self.app.release()

    def _answer(self, path, token, sent, result, error):
        """What the tablet's app answered for one PDF: True while its signed
        copy is being saved (which ends the request), else done."""
        if self.in_app.get(path) != token:
            return False  # the user chose to sign on the PC meanwhile (or asked again since)
        del self.in_app[path]
        if error == "refused":
            log("companion: the tablet does not know this PC")
            note = Gio.Notification.new(_("Not paired"))
            note.set_body(_("{name} no longer knows this PC: pair it again").format(name=self.tablet))
            note.set_default_action("app.show")
            self.app.send_notification(NOTIFICATION, note)
            return False
        if error == "incompatible":
            note = Gio.Notification.new(_("Update Plus One"))
            note.set_body(_("The app on the tablet and Plus One on this PC are different versions: "
                            "signing with Second Screen instead"))
            self.app.send_notification(UPDATE, note)
            self._second_screen(path)
            return False
        if error == "lost":
            note = Gio.Notification.new(_("Lost the tablet"))
            note.set_body(_("What was signed on the tablet did not reach the PC: {name} is unchanged")
                          .format(name=os.path.basename(path)))
            self.app.send_notification(NOTIFICATION, note)
            return False
        if error is not None:
            # The app did not answer: Second Screen instead.
            log("signing in the app failed:", repr(error))
            self._second_screen(path)
            return False
        if isinstance(result, dict):
            self._keep_from_app(result)
        marks = companion.to_marks(result) if result else {}
        if not marks:
            # Not signed: what still waits on the tablet, if anything, or
            # what was signed before it.
            if self.in_app:
                left = next(iter(self.in_app))
                self._notify_in_app(left, _("Sign {name} on {tablet}"), self.tablet)
            elif not self.signed:
                self.app.withdraw_notification(NOTIFICATION)
            return False

        def save():
            # On the copy that was signed, next to the original; where that
            # folder takes nothing (read-only, gone), in Documents: what was
            # signed on the tablet is not lost for it.
            from .pdf import digitally_signed, save_signed
            try:
                done = save_signed(sent, marks, like=path)
            except OSError as e:
                log("signing: not next to the original:", repr(e))
                folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or GLib.get_home_dir()
                try:
                    done = save_signed(sent, marks, like=os.path.join(folder, os.path.basename(path)))
                except Exception as e2:  # noqa: BLE001 - shown to the user
                    log("signing: could not save:", repr(e2))
                    done = e2
            except Exception as e:  # noqa: BLE001 - shown to the user
                log("signing: could not save:", repr(e))
                done = e
            certified = digitally_signed(sent)
            GLib.idle_add(once(self._app_saved, done, certified, sent))
        threading.Thread(target=save, daemon=True).start()
        return True

    @staticmethod
    def _merge_signatures(found):
        """Signatures added or removed on the tablet."""
        try:
            signatures.merge(found)
        except OSError as e:
            log("signing: signatures from the tablet not kept:", repr(e))

    def _keep_from_app(self, result):
        """The pen chosen on the tablet, for next time (on the PC too)."""
        from .ink import load_pen, save_pen
        pen = result.get("pen")
        if isinstance(pen, dict):
            try:
                _color, _width, smoothing = load_pen(PEN)
                save_pen(PEN, tuple(float(c) for c in pen["color"]), float(pen["width"]), smoothing)
            except (KeyError, TypeError, ValueError, OSError) as e:
                log("signing: pen from the tablet not kept:", repr(e))

    def _app_saved(self, result, certified=False, sent=None):
        if sent:
            self._forget_sent(sent)
        if isinstance(result, Exception):
            note = Gio.Notification.new(_("Could not save: {error}").format(error=result))
            self.app.send_notification(NOTIFICATION, note)
            return
        self._signed(result, certified)

    def _notify_in_app(self, path, template, tablet):
        """What the tablet's app has to sign: this PDF (template takes its
        name and the tablet's), or how many when there are several."""
        self.tablet = tablet
        if len(self.in_app) > 1:
            note = Gio.Notification.new(_("Sign on the tablet"))
            note.set_body(_("{count} PDFs to sign on {tablet}").format(count=len(self.in_app), tablet=tablet))
            self.app.send_notification(NOTIFICATION, note)
        elif path in self.in_app:
            self._notify_waiting(path, template.format(name=os.path.basename(path), tablet=tablet))

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
        except Exception as e:  # noqa: BLE001 - GLib.Error mostly (damaged, a password), but whatever it is
            note = Gio.Notification.new(_("Cannot open {name}").format(name=os.path.basename(path)))
            note.set_body(getattr(e, "message", None) or str(e))
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
            from .pdf import digitally_signed
            self._signed(result, digitally_signed(path))
        # Let the window go away first; the display it is on may follow.
        GLib.idle_add(once(self._maybe_disconnect))

    def _signed(self, result, certified=False):
        """One notification for the PDFs signed in a row. certified: the
        original carried a digital signature, which the copy cannot keep."""
        log("signed:", result)
        self.signed.append(result)
        if len(self.signed) > 1:
            note = Gio.Notification.new(_("Signed {count} PDFs").format(count=len(self.signed)))
            note.set_body(", ".join(os.path.basename(p) for p in self.signed))
            note.set_default_action_and_target("app.show-file", GLib.Variant("s", result))
        else:
            note = Gio.Notification.new(_("Signed"))
            note.set_body(os.path.basename(result) + ("\n" + _("It had a digital signature: in the signed copy, "
                                                                 "it no longer holds") if certified else ""))
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
        self.in_app.pop(path, None)  # whatever the app sends back now is ignored
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
