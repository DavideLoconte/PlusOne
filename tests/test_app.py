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
