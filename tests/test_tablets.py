"""The paired tablets, the one in use, and the check of Second Screen's tablet."""
import pytest

from plusone import companion
from plusone.engine import Settings
from plusone.tablets import Tablets

KITCHEN, OFFICE = "aaaa" + "0" * 60, "bbbb" + "0" * 60


@pytest.fixture
def paired(tmp_path):
    store = companion.Paired(str(tmp_path))
    store.add(OFFICE, "Office")
    store.add(KITCHEN, "Kitchen")
    return store


class Nearby:
    def __init__(self, found=()):
        self.found = list(found)  # (address, port, app id)

    def addresses(self, app_id):
        return [(a, p) for a, p, i in self.found if i == app_id]


def test_the_tablet_in_use(paired):
    saved = []
    settings = Settings()
    tablets = Tablets(settings, lambda: saved.append(settings.tablet), paired=paired)
    assert [t.name for t in tablets.all()] == ["Kitchen", "Office"]
    assert tablets.active().fingerprint == KITCHEN and saved == [KITCHEN]  # none chosen yet: the first
    tablets.use(OFFICE)
    assert tablets.active().name == "Office" and tablets.active().app_id == OFFICE[:16]
    tablets.forget(OFFICE)
    assert tablets.active().name == "Kitchen" and OFFICE not in paired.all()
    tablets.forget(KITCHEN)
    assert tablets.active() is None and settings.tablet is None


def test_pairings_of_0_5_are_kept(tmp_path):
    (tmp_path / "paired.json").write_text('{"%s": "Tab", "%s": 3}' % (KITCHEN, OFFICE))
    store = companion.Paired(str(tmp_path))
    assert store.all() == {KITCHEN: "Tab"}
    store.learn(KITCHEN, wifi_direct="56:dd:4f:07:78:78")
    store.add(KITCHEN, "Tab S10")  # paired again: what was learned stays
    assert store.entries() == {KITCHEN: {"name": "Tab S10", "wifi_direct": "56:dd:4f:07:78:78"}}


def test_wi_fi_direct_looks_for_the_tablet_in_use(paired):
    tablets = Tablets(Settings(tablet=OFFICE), paired=paired)
    assert tablets.match() == ["Office"]
    tablets.learned("56:DD:4F:07:78:78")
    assert tablets.match() == ["56:dd:4f:07:78:78", "Office"]
    assert Tablets(Settings(), paired=companion.Paired(str(paired.path) + ".none")).match() is None


def test_second_screen_is_checked_with_the_app(monkeypatch, paired):
    asked = []

    def verify(address, fingerprint, ports, store):
        asked.append((address, fingerprint, ports))
        return "Office"
    monkeypatch.setattr(companion, "verify", verify)
    nearby = Nearby([("192.168.49.1", 40001, OFFICE[:16]), ("10.0.0.5", 40001, OFFICE[:16]),
                     ("10.0.0.9", 50000, KITCHEN[:16])])
    tablets = Tablets(Settings(tablet=OFFICE), nearby=nearby, paired=paired)
    assert tablets.verify("192.168.49.1") is None
    # Only ever at the group's address; its known port first, then where mDNS says.
    assert asked == [("192.168.49.1", OFFICE, [companion.APP_PORT, 40001, 40001])]


@pytest.mark.parametrize("error, template", [
    (companion.Impostor("someone else"), "This is not {name}: the desktop was not sent"),
    (companion.PairingRefused("Office"), "{name} no longer knows this PC: pair it again"),
    (companion.Incompatible("v4"), "Plus One on {name} and on this PC are different versions: update them"),
    (ConnectionRefusedError(), "Plus One does not answer on {name}: open it on the tablet and connect again"),
])
def test_why_second_screen_is_refused(monkeypatch, paired, error, template):
    def verify(*args):
        raise error
    monkeypatch.setattr(companion, "verify", verify)
    tablets = Tablets(Settings(tablet=OFFICE), paired=paired)
    assert tablets.verify("192.168.49.1") == (template, {"name": "Office"})


def test_no_tablet_no_second_screen(tmp_path):
    tablets = Tablets(Settings(), paired=companion.Paired(str(tmp_path)))
    assert tablets.verify("192.168.49.1") == ("Pair a tablet first", {})
