"""Plus One's window (see app.py): the tablet in use and what to do with it;
pairing, the paired tablets, preferences and the log in dialogs of their own."""
import os
import shutil
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import __version__, companion, i18n, log, once, pen  # noqa: E402
from .app import APP_ID, IN_FLATPAK, set_shell_extension, shell_extension_state  # noqa: E402
from .devices import neural_available  # noqa: E402
from .engine import State  # noqa: E402
from .i18n import _  # noqa: E402
from .rtsp import MODES  # noqa: E402

MODE_LIST = sorted(((w, h, f) for (w, h, f) in MODES if f == 60), key=lambda m: -m[0] * m[1])
TABLET_ICON = "plusone-tablet-symbolic"  # icons/
WEBSITE = "https://github.com/GabrieleSantangelo/PlusOne"
QUALITY_LIST = ["720p", "1080p"]
FACING_LIST = ["front", "back"]
NOISE_LIST = ["off", "light", "neural"]  # devices.NOISE


def device_status(kind, state):
    """What the window says under the camera's or the microphone's switch."""
    if state == "setup":
        return (_("Needs the v4l2loopback module: see the README") if kind == "camera"
                else _("Needs PipeWire's pactl and pw-link"))
    if state == "webrtc":
        return _("Needs WebRTC for GStreamer: gstreamer1.0-nice and gir1.2-gst-plugins-bad-1.0")
    if state == "reload":
        return _("The webcam module is loaded with other settings: sudo modprobe -r v4l2loopback && "
                 "sudo modprobe v4l2loopback")
    if state == "decoder":
        return _("Needs an H.264 decoder: gstreamer1.0-libav")
    camera = kind == "camera"
    return {
        "off": _("As a webcam for this PC's programs") if camera else _("As a microphone for this PC's programs"),
        "ready": _("On: the tablet turns its camera on while a program uses it") if camera
        else _("On: the tablet turns its microphone on while a program uses it"),
        "in use": _("In use"),
        "away": _("A program uses the camera, but the tablet is not reachable") if camera
        else _("A program uses the microphone, but the tablet is not reachable"),
        "tablet-off": _("Not allowed on the tablet: Plus One → For the PC"),
        "tablet-permission": _("On the tablet, allow Plus One to use the camera") if camera
        else _("On the tablet, allow Plus One to use the microphone"),
        "tablet-background": _("On the tablet, let Plus One display over other apps"),
        "tablet-unavailable": _("On the tablet, another app is using the camera") if camera
        else _("On the tablet, another app is using the microphone"),
        "tablet-unpaired": _("The tablet no longer knows this PC: pair it again"),
        "tablet-version": _("Plus One on the tablet and on this PC are different versions: update them"),
    }.get(state, "")
CSS = b".pair-code { font-size: 300%; font-weight: 800; letter-spacing: 0.1em; }"
_resources_added = False


def mode_label(mode):
    w, h, _f = mode
    ratio = "16:10" if w * 10 == h * 16 else "16:9" if w * 9 == h * 16 else ""
    return f"{w} × {h} ({ratio})" if ratio else f"{w} × {h}"


def _add_resources(display):
    """Plus One's icons and style, once."""
    global _resources_added
    if not _resources_added:
        Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(os.path.dirname(__file__), "icons"))
        style = Gtk.CssProvider()
        style.load_from_data(CSS, len(CSS))
        Gtk.StyleContext.add_provider_for_display(display, style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _resources_added = True


def _pill(label, *styles, width=-1):
    return Gtk.Button(label=label, halign=Gtk.Align.CENTER, css_classes=["pill", *styles], width_request=width)


def _spinner(text):
    box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
    box.append(Gtk.Spinner(spinning=True))
    box.append(Gtk.Label(label=text, wrap=True))
    return box


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Plus One", default_width=440, default_height=660)
        self.app = app
        self.controller = app.controller
        _add_resources(self.get_display())
        for name, callback in (("preferences", lambda *a: PreferencesDialog(self.app, self).present(self)),
                               ("signatures", lambda *a: self._show_signatures()),
                               ("tablets", lambda *a: TabletsDialog(self.app, self).present(self)),
                               ("pair", lambda *a: self.pair()),
                               ("log", lambda *a: LogDialog(self.app).present(self)),
                               ("about", lambda *a: self._about())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        # The tablet in use, picked from the menu under its name.
        self.use_action = Gio.SimpleAction.new_stateful("use-tablet", GLib.VariantType("s"), GLib.Variant("s", ""))
        self.use_action.connect("change-state", self._on_use)
        self.add_action(self.use_action)
        self._build()
        app.listeners.append(self._refresh)
        self.connect("close-request", self._on_close)

    def _build(self):
        """The content, in the current language (again after it changes)."""
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Preferences"), "win.preferences")
        section.append(_("My signatures"), "win.signatures")
        section.append(_("Paired tablets"), "win.tablets")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Log"), "win.log")
        section.append(_("About Plus One"), "win.about")
        menu.append_section(None, section)
        header = Adw.HeaderBar()
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text=_("Main menu")))
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        empty = Adw.StatusPage(icon_name=TABLET_ICON, title=_("No tablet"),
                               description=_("Pair your tablet to use it as a second screen and to sign PDFs on it."))
        pair = _pill(_("Pair a tablet"), "suggested-action")
        pair.connect("clicked", lambda b: self.pair())
        empty.set_child(pair)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self._tablet_page(), "tablet")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)
        self._menu_tablets = None
        self._refresh()

    def _tablet_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                      margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        box.append(Gtk.Image(icon_name=TABLET_ICON, pixel_size=128, css_classes=["dim-label"], margin_bottom=12))
        # Its name opens the choice of tablet (and pairing another).
        self.name_label = Gtk.Label(css_classes=["title-1"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)
        self.name_button = Gtk.MenuButton(child=self.name_label, halign=Gtk.Align.CENTER, css_classes=["flat"],
                                          always_show_arrow=True, tooltip_text=_("Choose the tablet"))
        box.append(self.name_button)
        self.status_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"])
        box.append(self.status_label)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, halign=Gtk.Align.CENTER)
        # The same width for both, whatever the labels say.
        self.screen_button = _pill("", width=260)
        self.screen_button.connect("clicked", self._toggle)
        sign = _pill(_("Sign a PDF…"), width=260)
        sign.connect("clicked", lambda b: self.app.choose_and_sign(self))
        for button in (self.screen_button, sign):
            buttons.append(button)
        box.append(buttons)
        # The tablet's camera and microphone, for this PC's programs.
        devices = Adw.PreferencesGroup(margin_top=24)
        self.device_rows = {}
        for kind, title in (("camera", _("Tablet camera")), ("microphone", _("Tablet microphone"))):
            row = Adw.SwitchRow(title=title)
            row.handler = row.connect("notify::active", lambda r, _p, kind=kind: self.app.set_device(kind, r.get_active()))
            devices.add(row)
            self.device_rows[kind] = row
        box.append(Adw.Clamp(maximum_size=420, child=devices))
        return box

    # -- state -----------------------------------------------------------------------
    def _refresh(self):
        tablet = self.app.tablets.active()
        self.stack.set_visible_child_name("tablet" if tablet else "empty")
        if tablet is None:
            return
        self.name_label.set_label(tablet.name)
        self._refresh_menu(tablet)
        for kind, row in self.device_rows.items():
            with row.handler_block(row.handler):
                row.set_active(getattr(self.app.settings, kind))
            row.set_subtitle(device_status(kind, getattr(self.app.devices, kind).state))
        state = self.controller.state
        text = self._status(tablet)
        self.status_label.set_label(text)
        self.status_label.set_css_classes(["error"] if state == State.ERROR else ["dim-label"])
        if not self.controller.running:
            self.screen_button.set_label(_("Use as second screen"))
            self.screen_button.set_css_classes(["pill", "suggested-action"])
        else:
            self.screen_button.set_label(_("Disconnect") if state == State.STREAMING else _("Stop"))
            self.screen_button.set_css_classes(["pill", "destructive-action"])

    def _status(self, tablet):
        title, detail = self.app.status_text()
        state = self.controller.state
        if state == State.IDLE and not self.controller.detail[0]:
            # Nothing happening: where the tablet is.
            return _("On your network") if self.app.tablets.addresses(tablet) else _("Not on your network now")
        mode = getattr(self.controller, "mode", None)
        if state == State.STREAMING and mode:
            # Its name is right above.
            return _("Second screen · {width} × {height} at {fps} Hz").format(width=mode[0], height=mode[1],
                                                                              fps=mode[2])
        return detail or title

    def _refresh_menu(self, active):
        tablets = [(t.fingerprint, t.name) for t in self.app.tablets.all()]
        if tablets != self._menu_tablets:
            self._menu_tablets = tablets
            menu = Gio.Menu()
            choices = Gio.Menu()
            for fingerprint, name in tablets:
                item = Gio.MenuItem.new(name, None)
                item.set_action_and_target_value("win.use-tablet", GLib.Variant("s", fingerprint))
                choices.append_item(item)
            menu.append_section(None, choices)
            more = Gio.Menu()
            more.append(_("Pair another tablet…"), "win.pair")
            menu.append_section(None, more)
            self.name_button.set_menu_model(menu)
        self.use_action.set_state(GLib.Variant("s", active.fingerprint))
        # Another tablet while one is connected: not until it is done.
        self.use_action.set_enabled(not self.controller.running)

    def _on_use(self, action, value):
        action.set_state(value)
        self.app.use_tablet(value.get_string())

    def _toggle(self, _button):
        if self.controller.running:
            self.controller.stop()
        else:
            self.controller.start()

    # -- dialogs ---------------------------------------------------------------------
    def pair(self):
        PairDialog(self.app, on_paired=self._paired).present(self)

    def _paired(self, name):
        # The name is the tablet's own: shown as text, not markup.
        self.toasts.add_toast(Adw.Toast(title=_("Paired with {name}").format(name=GLib.markup_escape_text(name))))

    def _show_signatures(self):
        from .ink import load_pen
        from .signature_pad import SignaturesWindow
        from .signing import PEN
        color, width, _smoothing = load_pen(PEN)
        SignaturesWindow(self, color, width).present()

    def _about(self):
        Adw.AboutDialog(application_name="Plus One", application_icon=APP_ID, version=__version__,
                        comments=_("Your Galaxy Tab as a second monitor, and to sign PDFs with its S Pen"),
                        website=WEBSITE, issue_url=WEBSITE + "/issues",
                        license_type=Gtk.License.MIT_X11).present(self)

    def relabel(self):
        """The language changed."""
        self._build()

    def _on_close(self, _window):
        self.app.listeners.remove(self._refresh)
        self.app.window_closed()
        return False


class PairDialog(Adw.Dialog):
    """Pick a tablet with the app on the network, compare the code, done."""

    def __init__(self, app, on_paired=None):
        super().__init__(title=_("Pair a tablet"), content_width=420, content_height=480)
        self.app = app
        self.on_paired = on_paired or (lambda name: None)
        self.answer = None      # (threading.Event, [bool]) while the user compares the code
        self.closed = False
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)

        page = Adw.PreferencesPage()
        self.found = Adw.PreferencesGroup(title=_("With Plus One on your Wi-Fi"),
                                          description=_("Not there? Open Plus One on the tablet, on the same "
                                                        "Wi-Fi as this PC."))
        page.add(self.found)
        self.rows = []
        self.searching = Adw.ActionRow(title=_("Looking for tablets…"))
        self.searching.add_prefix(Gtk.Spinner(spinning=True))
        self.nav.add(self._page(page, _("Pair a tablet")))
        self._listing = None
        self._list()
        app.listeners.append(self._list)
        self.connect("closed", self._on_closed)

    @staticmethod
    def _page(content, title):
        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(Adw.HeaderBar())
        return Adw.NavigationPage(child=toolbar, title=title)

    def _list(self):
        apps = [(app_id, name) for app_id, _a, _p, name in self.app.nearby.apps() if app_id]
        if apps == self._listing:
            return
        self._listing = apps
        for row in self.rows + [self.searching]:
            if row.get_parent() is not None:
                self.found.remove(row)
        self.rows = []
        paired = {t.app_id for t in self.app.tablets.all()}
        for app_id, name in apps:
            row = Adw.ActionRow(title=name, use_markup=False, activatable=True,
                                subtitle=_("Paired") if app_id in paired else "")
            row.add_prefix(Gtk.Image(icon_name=TABLET_ICON))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda r, app_id=app_id, name=name: self._pair(app_id, name))
            self.found.add(row)
            self.rows.append(row)
        self.found.add(self.searching)  # last: more may come

    def _pair(self, app_id, name):
        """The code page; pairing runs in a thread, asking self._ask."""
        self.stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.CENTER,
                             margin_start=24, margin_end=24)
        self.stage.append(_spinner(_("Connecting to {name}…").format(name=name)))
        self.nav.push(self._page(self.stage, name))
        targets = self.app.nearby.addresses(app_id)

        def work():
            try:
                fingerprint, paired_name = companion.pair(targets, self._ask)
            except companion.PairingRefused:
                GLib.idle_add(once(self._failed, _("Not paired: the code was not confirmed on both screens")))
            except Exception as e:  # noqa: BLE001 - shown to the user
                log("pairing:", repr(e))
                GLib.idle_add(once(self._failed, _("{name} did not answer").format(name=name)))
            else:
                GLib.idle_add(once(self._done, fingerprint, paired_name))
        threading.Thread(target=work, daemon=True).start()

    def _ask(self, name, code):
        """From the pairing thread: blocks until the user compares the code."""
        answered, answer = self.answer = (threading.Event(), [])
        GLib.idle_add(once(self._show_code, code))
        if not answered.wait(companion.PAIRING_TIMEOUT):
            log("pairing: the code was not confirmed in time")
        return bool(answer and answer[0])

    def _answered(self, yes):
        if self.answer and not self.answer[0].is_set():
            log("pairing:", "confirmed on the PC" if yes else "refused on the PC")
            self.answer[1].append(yes)
            self.answer[0].set()

    def _show_code(self, code):
        self._clear()
        self.stage.append(Gtk.Label(label=f"{code[:3]} {code[3:]}", selectable=True,
                                    css_classes=["title-1", "numeric", "pair-code"]))
        self.stage.append(Gtk.Label(label=_("Pair only if the tablet shows this same code. Confirm there too."),
                                    wrap=True, justify=Gtk.Justification.CENTER))
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        same = _pill(_("Same code"), "suggested-action", width=220)
        same.connect("clicked", lambda b: self._confirmed())
        cancel = _pill(_("Cancel"), width=220)
        cancel.connect("clicked", lambda b: (self._answered(False), self.close()))
        buttons.append(same)
        buttons.append(cancel)
        self.stage.append(buttons)

    def _confirmed(self):
        self._answered(True)
        self._clear()
        self.stage.append(_spinner(_("Confirm on the tablet…")))

    def _done(self, fingerprint, name):
        if self.closed:
            return
        self.app.use_tablet(fingerprint)
        self.on_paired(name)
        self.close()

    def _failed(self, text):
        if self.closed:
            return
        self._clear()
        self.stage.append(Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER))
        again = _pill(_("Try again"))
        again.connect("clicked", lambda b: self.nav.pop())
        self.stage.append(again)

    def _clear(self):
        while (child := self.stage.get_first_child()) is not None:
            self.stage.remove(child)

    def _on_closed(self, _dialog):
        self.closed = True
        self._answered(False)  # closed while comparing: not paired
        self.app.listeners.remove(self._list)


class TabletsDialog(Adw.PreferencesDialog):
    """The paired tablets: which one is in use, forget one, pair another."""

    def __init__(self, app, window):
        super().__init__(title=_("Paired tablets"), content_width=440, content_height=440)
        self.app = app
        self.window = window
        page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(description=_("The one in use gets the desktop and the PDFs. "
                                                        "Forget one you no longer use."))
        page.add(self.group)
        more = Adw.PreferencesGroup()
        pair = Adw.ActionRow(title=_("Pair another tablet…"), activatable=True)
        pair.add_prefix(Gtk.Image(icon_name="list-add-symbolic"))
        pair.connect("activated", lambda r: (self.close(), window.pair()))
        more.add(pair)
        page.add(more)
        self.add(page)
        self.rows = []
        self._shown = None
        self._refresh()
        app.listeners.append(self._refresh)
        self.connect("closed", lambda d: app.listeners.remove(self._refresh))

    def _refresh(self):
        active = self.app.tablets.active()
        shown = ([(t.fingerprint, t.name) for t in self.app.tablets.all()],
                 active.fingerprint if active else None, self.app.controller.running)
        if shown == self._shown:
            return
        self._shown = shown
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        tablets, active, running = shown
        first = None
        for fingerprint, name in tablets:
            row = Adw.ActionRow(title=name, use_markup=False, activatable=not running)
            check = Gtk.CheckButton(active=fingerprint == active, sensitive=not running, valign=Gtk.Align.CENTER,
                                    css_classes=["selection-mode"])
            if first is None:
                first = check
            else:
                check.set_group(first)
            # Not from inside the signal: the rows are made again for it.
            check.connect("toggled", lambda c, fp=fingerprint: c.get_active()
                          and GLib.idle_add(once(self.app.use_tablet, fp)))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            forget = Gtk.Button(label=_("Forget"), valign=Gtk.Align.CENTER, css_classes=["flat"])
            forget.connect("clicked", lambda b, fp=fingerprint, name=name: self._ask_forget(fp, name))
            row.add_suffix(forget)
            self.group.add(row)
            self.rows.append(row)
        self.group.set_title(_("Paired tablets") if tablets else _("No tablet paired"))

    def _ask_forget(self, fingerprint, name):
        dialog = Adw.AlertDialog(heading=_("Forget {tablet}?").format(tablet=name),
                                 body=_("To use it again, pair it again: both screens show a code."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("forget", _("Forget"))
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(_d, response):
            if response == "forget":
                self.app.forget_tablet(fingerprint)
        dialog.connect("response", answered)
        dialog.present(self)
        return dialog


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app, window):
        super().__init__(title=_("Preferences"))
        self.app = app
        self.window = window
        self.settings = app.settings
        s = self.settings

        screen = Adw.PreferencesPage(title=_("Screen"), icon_name="video-display-symbolic")
        group = Adw.PreferencesGroup()
        # Entry 0 is "Automatic" (from the tablet's EDID); the rest follow MODE_LIST.
        self.mode_row = Adw.ComboRow(title=_("Resolution"), model=Gtk.StringList.new(
            [_("Automatic")] + [mode_label(m) for m in MODE_LIST]))
        current = (s.width, s.height, s.fps)
        self.mode_row.set_selected(0 if s.auto_mode or current not in MODE_LIST else 1 + MODE_LIST.index(current))
        self.bitrate_row = Adw.SpinRow.new_with_range(5, 60, 1)
        self.bitrate_row.set_title(_("Quality"))
        self.bitrate_row.set_subtitle(_("Mbit/s: higher is sharper but uses more bandwidth"))
        self.bitrate_row.set_value(s.bitrate / 1000)
        # Its subtitle says how to get the pen's pressure: a command to copy.
        self.touch_row = Adw.SwitchRow(title=_("Touch and S Pen"), active=s.touch, subtitle_selectable=True,
                                       subtitle={
            "join": _("The S Pen has no pressure: sudo usermod -aG plus-one $USER, then log in again"),
            "log in": _("Log out and back in for the S Pen's pressure"),
            "set up": _("Without access to /dev/uhid the S Pen works as a mouse, without pressure"),
        }.get(pen.uhid_missing(), ""))
        has_adb = shutil.which("adb") is not None
        self.adb_row = Adw.SwitchRow(title=_("Open Second Screen from here"), active=s.use_adb and has_adb,
                                     sensitive=has_adb,
                                     subtitle=_("Uses adb: needs wireless debugging on the tablet")
                                     if has_adb else _("adb is not available"))
        for row in (self.mode_row, self.bitrate_row, self.touch_row, self.adb_row):
            group.add(row)
        screen.add(group)
        self.add(screen)
        # Read when a connection starts: locked while one is up.
        self.session_rows = [self.mode_row, self.bitrate_row, self.touch_row, self.adb_row]
        for row, signal in ((self.mode_row, "notify::selected"), (self.bitrate_row, "notify::value"),
                            (self.touch_row, "notify::active"), (self.adb_row, "notify::active")):
            row.connect(signal, self._apply)

        signing = Adw.PreferencesPage(title=_("Signing"), icon_name="document-edit-symbolic")
        group = Adw.PreferencesGroup()
        # Files and Open With are outside a Flatpak's reach.
        sign_row = Adw.SwitchRow(title=_("Sign on Tablet"), active=s.sign_on_tablet, visible=not IN_FLATPAK,
                                 subtitle=_("In Files and Open With, for PDFs"))
        sign_row.connect("notify::active", lambda row, _p: app.set_sign_on_tablet(row.get_active()))
        signatures = Adw.ActionRow(title=_("My signatures"), activatable=True,
                                   subtitle=_("Placed with one tap when signing, here and on the tablet"))
        signatures.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        signatures.connect("activated", lambda r: window._show_signatures())
        group.add(sign_row)
        group.add(signatures)
        signing.add(group)
        self.add(signing)

        devices = Adw.PreferencesPage(title=_("Camera and microphone"), icon_name="camera-web-symbolic")
        group = Adw.PreferencesGroup(description=_("The tablet's camera, as a webcam for this PC's programs "
                                                   "(switched on in the window)."))
        self.facing_row = Adw.ComboRow(title=_("Camera"), model=Gtk.StringList.new([_("Front"), _("Back")]),
                                       selected=FACING_LIST.index(s.camera_facing)
                                       if s.camera_facing in FACING_LIST else 0)
        self.quality_row = Adw.ComboRow(title=_("Picture"), model=Gtk.StringList.new(
            ["720p (1280 × 720)", "1080p (1920 × 1080)"]),
            selected=QUALITY_LIST.index(s.camera_quality) if s.camera_quality in QUALITY_LIST else 0,
            subtitle=_("1080p takes about twice the bandwidth. A change waits until no program uses the camera"))
        for row in (self.facing_row, self.quality_row):
            row.connect("notify::selected", self._camera_options)
            group.add(row)
        devices.add(group)
        group = Adw.PreferencesGroup(description=_("The tablet's microphone, as a microphone for this PC's "
                                                   "programs (switched on in the window)."))
        neural = neural_available()
        self.noise_row = Adw.ComboRow(
            title=_("Noise reduction"),
            model=Gtk.StringList.new([_("Off"), _("Light, on the tablet"), _("Neural, on this PC")]),
            selected=NOISE_LIST.index(s.microphone_noise) if s.microphone_noise in NOISE_LIST else 2,
            subtitle=_("Neural takes out more of the room's noise, with about half a processor core")
            if neural else _("Neural needs DeepFilterNet's plugin in ~/.ladspa: light until then"))
        self.noise_row.connect("notify::selected", lambda row, _p: app.set_microphone_noise(
            NOISE_LIST[row.get_selected()]))
        group.add(self.noise_row)
        devices.add(group)
        self.add(devices)

        general = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        group = Adw.PreferencesGroup()
        background = Adw.SwitchRow(title=_("Ready in the background"), active=s.background,
                                   subtitle=_("Starts at login and stays ready after you close this window"))
        background.connect("notify::active", lambda row, _p: app.set_background(row.get_active()))
        # Shown once the shell says the extension is installed (never in a Flatpak).
        self.extension_row = Adw.SwitchRow(title=_("Quick Settings toggle"), visible=False,
                                           subtitle=_("Adds a “Tablet” button to GNOME's Quick Settings"))
        self._extension_handler = self.extension_row.connect(
            "notify::active", lambda row, _p: set_shell_extension(row.get_active()))
        shell_extension_state(self._show_extension_state)
        language = s.language if s.language in i18n.LANGUAGES else "auto"
        self.language_row = Adw.ComboRow(title=_("Language"), model=Gtk.StringList.new(
            [_(i18n.LANGUAGE_NAMES[code]) for code in i18n.LANGUAGES]),
            selected=i18n.LANGUAGES.index(language))
        self.language_row.connect("notify::selected", self._on_language)
        for row in (background, self.extension_row, self.language_row):
            group.add(row)
        general.add(group)
        self.add(general)

        self._refresh()
        app.listeners.append(self._refresh)
        self.connect("closed", lambda d: app.listeners.remove(self._refresh))

    def _refresh(self):
        in_session = self.app.controller.state in (State.CONNECTING, State.STREAMING)
        for row in self.session_rows:
            row.set_sensitive(not in_session and (row is not self.adb_row or shutil.which("adb") is not None))

    def _apply(self, *_args):
        s = self.settings
        selected = self.mode_row.get_selected()
        s.auto_mode = selected == 0
        if not s.auto_mode:
            s.width, s.height, s.fps = MODE_LIST[selected - 1]
        s.bitrate = int(self.bitrate_row.get_value() * 1000)
        s.touch = self.touch_row.get_active()
        s.use_adb = self.adb_row.get_active()
        self.app.save_settings()

    def _camera_options(self, *_args):
        self.app.set_camera_options(FACING_LIST[self.facing_row.get_selected()],
                                    QUALITY_LIST[self.quality_row.get_selected()])

    def _show_extension_state(self, enabled):
        if enabled is None:
            return  # not installed, or not GNOME Shell
        with self.extension_row.handler_block(self._extension_handler):
            self.extension_row.set_active(enabled)
        self.extension_row.set_visible(True)

    def _on_language(self, row, _pspec):
        self.settings.language = i18n.LANGUAGES[row.get_selected()]
        i18n.set_language(self.settings.language)
        self.app.save_settings()
        # Everything in the new language: the window again, this dialog closed.
        GLib.idle_add(once(self._relabel))

    def _relabel(self):
        self.close()
        self.window.relabel()


class LogDialog(Adw.Dialog):
    def __init__(self, app):
        super().__init__(title=_("Log"), content_width=640, content_height=480)
        self.app = app
        self.view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12, bottom_margin=12,
                                 left_margin=12, right_margin=12)
        buf = self.view.get_buffer()
        buf.set_text("".join(line + "\n" for line in app.log_lines))
        self.end = buf.create_mark(None, buf.get_end_iter(), False)  # follows what is added
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=_("Copy"))
        copy.connect("clicked", lambda b: self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(self._text().encode()))))
        header = Adw.HeaderBar()
        header.pack_start(copy)
        toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.view, vexpand=True))
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        app.log_listeners.append(self._append)
        self.connect("closed", lambda d: app.log_listeners.remove(self._append))
        GLib.idle_add(once(self._to_end))

    def _text(self):
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _append(self, line):
        buf = self.view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        if buf.get_line_count() > 400:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(100)[1])
        self._to_end()

    def _to_end(self):
        self.view.scroll_to_mark(self.end, 0, False, 0, 0)
