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
    "Press Connect, then tap “Second screen” in the tablet's quick settings":
        "Premi Collega, poi tocca «Second screen» nel pannello rapido del tablet",
    "Connect": "Collega",
    "Disconnect": "Scollega",
    "Display": "Schermo",
    "Resolution": "Risoluzione",
    "Automatic": "Automatica",
    "Quality": "Qualità",
    "Mbit/s: higher is sharper but uses more bandwidth": "Mbit/s: più alto = più nitido, più banda",
    "Options": "Opzioni",
    "Touch and S Pen": "Touch e S Pen",
    "Sign on Tablet": "Firma sul tablet",
    "In Files, for PDFs, while a tablet is on the network":
        "In File, per i PDF, quando c'è un tablet in rete",
    "On the network now: {name}": "In rete ora: {name}",
    "A tablet is on the network now": "Ora c'è un tablet in rete",
    "No tablet on the network now": "Ora nessun tablet in rete",
    "Ready in the background": "Pronto in background",
    "Starts at login and stays ready after you close this window":
        "Parte all'accesso e resta pronto anche dopo aver chiuso questa finestra",
    "Quick Settings toggle": "Interruttore nelle impostazioni rapide",
    "Adds a “Tablet” button to GNOME's Quick Settings":
        "Aggiunge un pulsante «Tablet» alle impostazioni rapide di GNOME",
    "Open Second Screen from here": "Apri Second Screen da qui",
    "Uses adb: needs wireless debugging on the tablet": "Usa adb: serve il debug wireless sul tablet",
    "adb is not available": "adb non è disponibile",
    "Language": "Lingua",
    "System": "Sistema",
    "Log": "Registro",
    # Notifications and background
    "Tablet connected": "Tablet collegato",
    "Keeps the Quick Settings toggle ready to connect the tablet":
        "Tiene pronto l'interruttore nelle impostazioni rapide per collegare il tablet",
    # Signing
    "Cancel": "Annulla",
    "Done": "Fatto",
    "Undo": "Annulla tratto",
    "Clear page": "Pulisci pagina",
    "My signature": "La mia firma",
    "My signatures": "Le mie firme",
    "Select": "Seleziona",
    "Drag it, turn it, or resize from a corner": "Trascina, ruota o ridimensiona dagli angoli",
    "Delete": "Elimina",
    "Done with the selection": "Fine selezione",
    "Placed with one tap when signing, here and on the tablet":
        "Si mettono con un tocco quando firmi, qui e sul tablet",
    "New signature": "Nuova firma",
    "Sign here with the pen or the mouse": "Firma qui con la penna o il mouse",
    "Start over": "Ricomincia",
    "Save": "Salva",
    "No signature saved yet": "Ancora nessuna firma salvata",
    "Place this signature": "Metti questa firma",
    "Remove": "Rimuovi",
    "Remove this signature?": "Rimuovere questa firma?",
    "It goes from the tablet too, the next time you sign there.":
        "Sparisce anche dal tablet, la prossima volta che firmi lì.",
    "Keep as my signature": "Salva come mia firma",
    "Pen": "Penna",
    "Eraser": "Gomma",
    "Previous page": "Pagina precedente",
    "Next page": "Pagina successiva",
    "Write with the pen · hold its button to erase": "Scrivi con la penna · tieni premuto il tasto per cancellare",
    "Thickness": "Spessore",
    "Stabilization": "Stabilizzazione",
    "Blue ink": "Inchiostro blu",
    "Black": "Nero",
    "Blue": "Blu",
    "Red": "Rosso",
    "Green": "Verde",
    "Date": "Data",
    "Text": "Testo",
    "Fit": "Adatta",
    "Tap where your signature goes": "Tocca dove va la firma",
    "Saved: next time, “My signature” places it with one tap":
        "Salvata: la prossima volta «La mia firma» la mette con un tocco",
    "Tap where the date goes": "Tocca dove va la data",
    "Text to add": "Testo da aggiungere",
    "Place": "Inserisci",
    "Tap where the text goes": "Tocca dove va il testo",
    "Could not save: {error}": "Impossibile salvare: {error}",
    "Discard the signature?": "Scartare la firma?",
    "Choose where to save the signed copy": "Scegli dove salvare la copia firmata",
    "Save the signed PDF": "Salva il PDF firmato",
    "Keep signing": "Continua a firmare",
    "Discard": "Scarta",
    "Sign on the tablet": "Firma sul tablet",
    "Tap “Second screen” on the tablet to sign {name}": "Tocca «Second screen» sul tablet per firmare {name}",
    "Sign on this PC": "Firma su questo PC",
    "The tablet did not connect": "Il tablet non si è collegato",
    "You can sign {name} on this PC instead": "Puoi firmare {name} su questo PC",
    "Cannot open {name}": "Impossibile aprire {name}",
    "Signed": "Firmato",
    "Open": "Apri",
    "Show in folder": "Mostra nella cartella",
    "Choose the PDFs to sign": "Scegli i PDF da firmare",
    # Engine states
    "Mode {mode} is not supported": "La modalità {mode} non è supportata",
    "Close {apps}: it also uses Wi-Fi Direct": "Chiudi {apps}: usa anche lui il Wi-Fi Direct",
    "This PC's Wi-Fi cannot do Wi-Fi Direct": "Il Wi-Fi di questo PC non supporta il Wi-Fi Direct",
    "Sending {name} to Plus One on {tablet}": "Invio di {name} a Plus One su {tablet}",
    "Not paired": "Non abbinato",
    "The codes were not confirmed on both the PC and the tablet":
        "I codici non sono stati confermati sia sul PC sia sul tablet",
    "Pair with {tablet}?": "Abbinare {tablet}?",
    "Check that the tablet shows the same code: {code}":
        "Controlla che il tablet mostri lo stesso codice: {code}",
    "Pair": "Abbina",
    "Sign {name} on {tablet}": "Firma {name} su {tablet}",
    "{count} PDFs to sign on {tablet}": "{count} PDF da firmare su {tablet}",
    "Tablet for signing": "Tablet per firmare",
    "It had a digital signature: in the signed copy, it no longer holds":
        "Aveva una firma digitale: nella copia firmata non vale più",
    "Only this tablet gets the desktop": "Solo questo tablet riceve lo schermo",
    "Forget": "Dimentica",
    "With more than one on the network, Files asks too": "Con più di uno in rete, lo chiede anche File",
    "Automatic: the last one used": "Automatico: l'ultimo usato",
    "A tablet no longer paired": "Un tablet non più abbinato",
    "Signed {count} PDFs": "{count} PDF firmati",
    "Update Plus One": "Aggiorna Plus One",
    "The app on the tablet and Plus One on this PC are different versions: signing with Second Screen instead":
        "L'app sul tablet e Plus One su questo PC sono versioni diverse: si firma con Second Screen",
    "Lost the tablet": "Tablet perso",
    "What was signed on the tablet did not reach the PC: {name} is unchanged":
        "Quanto firmato sul tablet non è arrivato al PC: {name} è rimasto com'era",
    "Pair only if the tablet shows this same code, and confirm there too:":
        "Abbina solo se il tablet mostra questo stesso codice, e conferma anche lì:",
    "Opening Second Screen on the tablet…": "Apro Second Screen sul tablet…",
    "Tap “Second screen” on the tablet": "Tocca «Second screen» sul tablet",
    "The tablet did not answer: open Second Screen on it and connect again":
        "Il tablet non ha risposto: apri Second Screen e collega di nuovo",
    "Connecting to {name}…": "Collegamento a {name}…",
    "The tablet is not answering: close and reopen Second Screen":
        "Il tablet non risponde: chiudi e riapri Second Screen",
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
