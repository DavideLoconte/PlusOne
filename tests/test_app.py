"""Background application helpers: settings file and autostart entry."""
import json

from plusone.app import APP_ID, load_settings, set_autostart
from plusone.engine import Settings


def test_settings_from_older_versions(tmp_path):
    # 0.2 wrote "stay_ready"; unknown keys must not make the file unreadable.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"bitrate": 15000, "stay_ready": False}))
    settings = load_settings(str(path))
    assert settings.bitrate == 15000
    assert settings.background is True


def test_unreadable_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load_settings(str(path)) == Settings()


def test_autostart_entry(tmp_path):
    set_autostart(True, config_dir=tmp_path)
    entry = (tmp_path / "autostart" / f"{APP_ID}.desktop").read_text()
    assert "--gapplication-service" in entry
    assert "NoDisplay=true" in entry
    set_autostart(False, config_dir=tmp_path)
    assert not (tmp_path / "autostart" / f"{APP_ID}.desktop").exists()


def test_sign_entry_hides_the_system_one(tmp_path):
    from plusone.app import SIGN_ENTRY, set_sign_entry
    path = tmp_path / "applications" / SIGN_ENTRY
    set_sign_entry(True, data_dir=tmp_path)
    assert not path.exists()  # the system entry shows as it is
    set_sign_entry(False, data_dir=tmp_path)
    text = path.read_text()
    assert "Type=Application" in text and "Hidden=true" in text
    set_sign_entry(False, data_dir=tmp_path)
    assert path.read_text() == text
    set_sign_entry(True, data_dir=tmp_path)
    assert not path.exists()


def test_sign_entry_of_a_per_user_install_comes_back(tmp_path):
    from gi.repository import GLib

    from plusone.app import SIGN_ENTRY, set_sign_entry
    path = tmp_path / "applications" / SIGN_ENTRY
    path.parent.mkdir()
    path.write_text("[Desktop Entry]\nType=Application\nName=Sign on Tablet\nName[it]=Firma sul tablet\n"
                    "Exec=/home/me/plus-one/bin/plus-one --sign %F\nMimeType=application/pdf;\n")
    set_sign_entry(False, data_dir=tmp_path)
    entry = GLib.KeyFile()
    entry.load_from_file(str(path), GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    assert entry.get_boolean("Desktop Entry", "Hidden")
    assert entry.get_string("Desktop Entry", "Exec") == "/home/me/plus-one/bin/plus-one --sign %F"
    set_sign_entry(True, data_dir=tmp_path)
    entry.load_from_file(str(path), GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    assert "Hidden" not in entry.get_keys("Desktop Entry")[0]
    assert entry.get_locale_string("Desktop Entry", "Name", "it") == "Firma sul tablet"


def test_a_broken_settings_file_is_not_trusted(tmp_path):
    from plusone.engine import Settings
    path = tmp_path / "settings.json"
    for content in ("[]", "null", "3"):
        path.write_text(content)
        assert load_settings(str(path)) == Settings()
    # (fps, display_via: settings of Second Screen, gone since 0.9, ignored)
    path.write_text('{"background": "no", "bitrate": "9999; rm", "fps": true, "encoder": 3, '
                    '"tablet": 7, "display_via": "second-screen", "touch": false}')
    settings = load_settings(str(path))
    assert settings.background is True and settings.bitrate == Settings().bitrate
    assert settings.encoder == "auto" and settings.tablet is None and settings.touch is False


def test_settings_are_private(tmp_path, monkeypatch):
    import os
    from plusone import app
    from plusone.engine import Settings
    monkeypatch.setattr(app, "CONFIG", str(tmp_path / "plus-one" / "settings.json"))
    app.save_settings(Settings())
    assert os.stat(app.CONFIG).st_mode & 0o077 == 0


def test_forgetting_the_tablet_in_use_ends_its_session(tmp_path):
    import types
    from plusone import app, link
    from plusone.engine import Settings
    from plusone.link import Tablets
    paired = link.Paired(str(tmp_path))
    kitchen, office = "abcd" * 16, "ef" * 32
    paired.add(kitchen, "Kitchen")
    paired.add(office, "Office")
    stopped = []
    fake = types.SimpleNamespace(tablets=Tablets(Settings(tablet=kitchen), paired=paired),
                                 controller=types.SimpleNamespace(stop=lambda: stopped.append(True)),
                                 devices=types.SimpleNamespace(apply=lambda: None), _on_nearby=lambda: None)
    app.PlusOneApp.forget_tablet(fake, office)
    assert stopped == [] and paired.all() == {kitchen: "Kitchen"}
    app.PlusOneApp.forget_tablet(fake, kitchen)
    assert stopped == [True] and paired.all() == {} and fake.tablets.active() is None


def test_exec_lines_survive_the_key_file(tmp_path):
    # A launcher path with what the Desktop Entry spec and key files escape.
    from gi.repository import GLib
    from plusone.app import exec_arg
    command = "/home/a b/$x`y\\z\"w%/bin/plus-one"
    keyfile = GLib.KeyFile()
    data = f"[Desktop Entry]\nExec={exec_arg(command)} --sign %F\n"
    keyfile.load_from_data(data, len(data.encode()), GLib.KeyFileFlags.NONE)
    exec_line = keyfile.get_string("Desktop Entry", "Exec")
    ok, argv = GLib.shell_parse_argv(exec_line.replace("%%", "%"))
    assert ok and argv[0] == command and argv[1:] == ["--sign", "%F"]
