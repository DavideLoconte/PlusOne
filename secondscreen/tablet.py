"""Optional adb conveniences: open Second Screen and keep its low-latency config.

Everything here is best effort; without adb the user taps the Second Screen
tile on the tablet.
"""
import shutil
import subprocess

from . import log

TILE = "com.samsung.android.secondscreen/.tile.ScreenSharingTile"
PACKAGE = "com.samsung.android.secondscreen"
# Read by libremotedisplay_wfd.so's WFDConfigParser on every session.
CONFIG_PATH = "/sdcard/Documents/wfd/wfdconfig.txt"
CONFIG = "sink_latency_mode low\n"


def _adb(*args, timeout=10):
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)


def device():
    """Serial of the first connected Samsung tablet, or None."""
    if not shutil.which("adb"):
        return None
    try:
        out = _adb("devices", "-l").stdout
    except subprocess.TimeoutExpired:
        return None
    for line in out.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 1 and fields[1] == "device":
            serial = fields[0]
            if _adb("-s", serial, "shell", "pm", "path", PACKAGE).stdout.strip():
                return serial
    return None


def prepare(serial, restart=True):
    current = _adb("-s", serial, "shell", "cat", CONFIG_PATH).stdout
    if "sink_latency_mode low" not in current:
        _adb("-s", serial, "shell", f"mkdir -p {CONFIG_PATH.rsplit('/', 1)[0]} && "
             f"printf '{CONFIG}' > {CONFIG_PATH}")
        log("tablet: wrote low-latency sink config")
    if restart:
        # A fresh Second Screen instance negotiates Wi-Fi Direct reliably.
        _adb("-s", serial, "shell", "am", "force-stop", PACKAGE)
    _adb("-s", serial, "shell", "cmd", "statusbar", "click-tile", TILE)
    log("tablet: opened Second Screen")


def close(serial):
    _adb("-s", serial, "shell", "am", "force-stop", PACKAGE)
