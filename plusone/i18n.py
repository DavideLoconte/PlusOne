"""Tiny translation table: English source strings, Italian translations.

The app is small enough that a dictionary beats a gettext toolchain.
"""
import os

LANGUAGES = ("auto", "en", "it")
LANGUAGE_NAMES = {"auto": "System", "en": "English", "it": "Italiano"}

_IT = {
    # Window
    "Ready": "Pronto",
    "Waiting for the tablet": "In attesa del tablet",
    "Connecting…": "Collegamento…",
    "Connected": "Connesso",
    "Problem": "Problema",
    "Press Start, then tap “Second screen” in the tablet's quick settings":
        "Premi Avvia, poi tocca «Second screen» nel pannello rapido del tablet",
    "Start": "Avvia",
    "Stop": "Ferma",
    "Display": "Schermo",
    "Resolution": "Risoluzione",
    "Quality": "Qualità",
    "Mbit/s: higher is sharper but uses more bandwidth": "Mbit/s: più alto = più nitido, più banda",
    "Options": "Opzioni",
    "Touch and S Pen": "Touch e S Pen",
    "Stay ready": "Resta pronto",
    "Reconnects when you reopen Second Screen": "Si ricollega quando riapri Second Screen",
    "Open Second Screen from here": "Apri Second Screen da qui",
    "Uses adb: needs wireless debugging on the tablet": "Usa adb: serve il debug wireless sul tablet",
    "Language": "Lingua",
    "System": "Sistema",
    "Log": "Registro",
    # Engine states
    "Mode {mode} is not supported": "La modalità {mode} non è supportata",
    "Close {apps}: it also uses Wi-Fi Direct": "Chiudi {apps}: usa anche lui il Wi-Fi Direct",
    "Opening Second Screen on the tablet…": "Apro Second Screen sul tablet…",
    "Tap “Second screen” on the tablet": "Tocca «Second screen» sul tablet",
    "Connecting to {name}…": "Collegamento a {name}…",
    "The tablet is not answering: close and reopen Second Screen":
        "Il tablet non risponde: chiudi e riapri Second Screen",
    "The tablet is not answering: reopen Second Screen": "Il tablet non risponde: riapri Second Screen",
}

# Templates that read the same in every language.
NEUTRAL = {"{error}", "{name} · {width}×{height} @ {fps} Hz"}

_current = "en"


def system_language():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "it" if value.startswith("it") else "en"
    return "en"


def set_language(code):
    global _current
    _current = system_language() if code == "auto" else code


def _(text):
    return _IT.get(text, text) if _current == "it" else text
