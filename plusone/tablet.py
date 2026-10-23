"""Optional adb conveniences: open Second Screen and keep its low-latency config.

Everything here is best effort; without adb the user taps the Second Screen
tile on the tablet.
"""
import shutil
import subprocess
import time

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
    elif _foreground_is_second_screen(serial):
        # The tile is a toggle: clicking it now would close the receiver.
        log("tablet: Second Screen already open")
        return True
    # Right after a force-stop the tile service is still rebinding and may
    # swallow the click, so check that the receiver actually came up.
    for _ in range(3):
        _adb("-s", serial, "shell", "cmd", "statusbar", "click-tile", TILE)
        for _ in range(8):
            time.sleep(0.5)
            if _foreground_is_second_screen(serial):
                log("tablet: opened Second Screen")
                return True
    log("tablet: could not open Second Screen, open it by hand")
    return False


def _foreground_is_second_screen(serial):
    out = _adb("-s", serial, "shell", "dumpsys", "activity", "activities").stdout
    top = next((line for line in out.splitlines() if "topResumedActivity" in line), "")
    return PACKAGE in top


def close(serial):
    _adb("-s", serial, "shell", "am", "force-stop", PACKAGE)
