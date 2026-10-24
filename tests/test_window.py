"""The main window's choice of tablet for signing.

Needs a display: CI runs it on GTK's Broadway backend (tools/ci.sh sets
PLUSONE_GUI_TESTS=1); on a desktop it is skipped so no window pops up.
"""
import os
import types

import pytest

if not os.environ.get("PLUSONE_GUI_TESTS"):
    pytest.skip("GUI tests run in CI (set PLUSONE_GUI_TESTS=1)", allow_module_level=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from plusone import companion, gui  # noqa: E402
from plusone.engine import Settings, State  # noqa: E402


def window(monkeypatch, tmp_path, paired):
    monkeypatch.setattr(companion, "Paired", lambda: types.SimpleNamespace(all=lambda: paired))
    monkeypatch.setattr(gui, "shell_extension_state", lambda callback: None)
    app = Adw.Application(application_id="io.github.gabrielesantangelo.PlusOne.Test")
    app.register(None)
    chosen = []
    app.settings = Settings()
    app.controller = types.SimpleNamespace(state=State.IDLE, running=False, detail=("", {}))
    app.nearby = types.SimpleNamespace(known=False, tablets={}, names=lambda: [])
    app.listeners, app.log_listeners, app.log_lines = [], [], []
    app.status_text = lambda: ("Ready", "")
    app.set_sign_tablet = lambda app_id: chosen.append(app_id) or setattr(app.settings, "sign_tablet", app_id)
    return gui.Window(app), chosen


def test_no_choice_without_a_paired_tablet(monkeypatch, tmp_path):
    win, _ = window(monkeypatch, tmp_path, {})
    assert not win.tablet_row.get_visible()


def test_the_paired_tablets_to_choose_from(monkeypatch, tmp_path):
    win, chosen = window(monkeypatch, tmp_path, {"bbbb" + "0" * 60: "Office", "aaaa" + "0" * 60: "Kitchen"})
    assert win.tablet_row.get_visible()
    model = win.tablet_row.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    assert labels[1:] == ["Kitchen", "Office"] and win.tablet_row.get_selected() == 0
    win.tablet_row.set_selected(2)
    assert chosen == ["bbbb000000000000"]
    win.tablet_row.set_selected(0)
    assert chosen[-1] is None
