"""The paired tablets, and the one in use: the only one that gets the
desktop (Second Screen) and the PDFs to sign.

Second Screen cannot say who it is: anything can call itself the tablet
over Wi-Fi Direct. Plus One's app can, with the certificate paired. So once
a tablet joins the Wi-Fi Direct group, before any picture leaves, the PC
asks the app over that same link (verify): no app, another certificate, or
an app reached from elsewhere, and the desktop is not sent.
"""
from dataclasses import dataclass

from . import companion, log


@dataclass(frozen=True)
class Tablet:
    fingerprint: str  # of its app's certificate
    name: str
    wifi_direct: str | None = None  # its address, once Second Screen came from it

    @property
    def app_id(self):
        """What its app announces itself as (nearby.py)."""
        return self.fingerprint[:16]


class Tablets:
    def __init__(self, settings, save=lambda: None, nearby=None, paired=None):
        """settings.tablet: the fingerprint of the one in use; save() stores
        the settings; nearby: where the apps are on the network, if known."""
        self.settings = settings
        self.save = save
        self.nearby = nearby
        self.paired = paired or companion.Paired()

    def all(self):
        """The paired tablets, by name."""
        return sorted((Tablet(fp, entry["name"][:companion.MAX_NAME], entry.get("wifi_direct"))
                       for fp, entry in self.paired.entries().items()),
                      key=lambda t: (t.name.lower(), t.fingerprint))

    def active(self):
        """The tablet in use, or None if none is paired."""
        tablets = self.all()
        chosen = next((t for t in tablets if t.fingerprint == self.settings.tablet), None)
        if chosen is None and tablets:
            # The one in use was forgotten, or never chosen (0.5): the first one.
            chosen = tablets[0]
            self.use(chosen.fingerprint)
        return chosen

    def use(self, fingerprint):
        if self.settings.tablet != fingerprint:
            self.settings.tablet = fingerprint
            self.save()

    def forget(self, fingerprint):
        self.paired.forget(fingerprint)
        if self.settings.tablet == fingerprint:
            self.settings.tablet = None
            self.save()

    def addresses(self, tablet):
        """Where its app is announced on the network: [(address, port)]."""
        return self.nearby.addresses(tablet.app_id) if self.nearby else []

    # -- Second Screen (engine.Controller) ----------------------------------------
    def match(self):
        """What Wi-Fi Direct looks for (p2p.P2PLink): the tablet in use's
        address, and its name (the address may change), or None."""
        tablet = self.active()
        if tablet is None:
            return None
        return [value for value in (tablet.wifi_direct, tablet.name) if value]

    def verify(self, address):
        """None if the device at address (in the Wi-Fi Direct group) is the
        tablet in use, else why not: (English template, its arguments)."""
        tablet = self.active()
        if tablet is None:
            return "Pair a tablet first", {}
        args = {"name": tablet.name}
        # The app's port, and where mDNS says it is (it is announced over
        # Wi-Fi Direct too); only ever at address.
        ports = [companion.APP_PORT] + [port for _a, port in self.addresses(tablet)]
        try:
            companion.verify(address, tablet.fingerprint, ports, self.paired)
        except companion.Impostor as e:
            log("Second Screen:", e)
            return "This is not {name}: the desktop was not sent", args
        except companion.PairingRefused:
            return "{name} no longer knows this PC: pair it again", args
        except companion.Incompatible:
            return "Plus One on {name} and on this PC are different versions: update them", args
        except Exception as e:  # noqa: BLE001 - whatever it was, not the tablet's app answering
            log("Second Screen: the app did not answer:", repr(e))
            return "Plus One does not answer on {name}: open it on the tablet and connect again", args
        log(f"Second Screen: {address} is {tablet.name}")
        return None

    def learned(self, wifi_direct):
        """Second Screen came from the tablet in use at this address."""
        tablet = self.active()
        if tablet and wifi_direct:
            self.paired.learn(tablet.fingerprint, wifi_direct=wifi_direct.lower())
