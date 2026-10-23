"""An older Plus One left running after an upgrade is replaced, unless busy.

Runs on a private D-Bus daemon (no services, no settings) with a fake
"older instance" owning the application's bus name.
"""
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

if not shutil.which("dbus-daemon"):
    pytest.skip("dbus-daemon not installed", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Owns the bus name; optionally answers State like 0.3 did (no Version).
FAKE = textwrap.dedent("""
    import sys
    from gi.repository import Gio, GLib
    state = sys.argv[1]
    xml = '''<node><interface name="io.github.gabrielesantangelo.PlusOne1">
      <property name="State" type="s" access="read"/></interface></node>'''
    def acquired(conn, name):
        print("ready", flush=True)
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if state != "none":
        info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
        conn.register_object("/io/github/gabrielesantangelo/PlusOne", info, None,
                             lambda *a: GLib.Variant("s", state), None)
    Gio.bus_own_name_on_connection(conn, "io.github.gabrielesantangelo.PlusOne",
                                   Gio.BusNameOwnerFlags.NONE, acquired, None)
    GLib.MainLoop().run()
""")


@pytest.fixture
def bus():
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"],
                              stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address, PYTHONPATH=ROOT)
    yield env
    daemon.terminate()
    daemon.wait()


def start_fake(env, state):
    fake = subprocess.Popen([sys.executable, "-c", FAKE, state], env=env,
                            stdout=subprocess.PIPE, text=True)
    assert fake.stdout.readline().strip() == "ready"
    return fake


def replace(env):
    subprocess.run([sys.executable, "-c", "from plusone.app import replace_older_instance as r; r()"],
                   env=env, check=True, timeout=20, capture_output=True)


@pytest.mark.parametrize("state", ["none", "idle", "waiting"])
def test_idle_older_instance_is_replaced(bus, state):
    fake = start_fake(bus, state)
    replace(bus)
    assert fake.wait(timeout=5) == -15  # SIGTERM


@pytest.mark.parametrize("state", ["connecting", "streaming"])
def test_busy_older_instance_is_left_alone(bus, state):
    fake = start_fake(bus, state)
    replace(bus)
    time.sleep(0.3)
    assert fake.poll() is None
    fake.terminate()
    fake.wait()


def test_nothing_running(bus):
    replace(bus)  # returns at once, no error
