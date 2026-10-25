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
    "Connect": "Collega",
    "Disconnect": "Scollega",
    "Resolution": "Risoluzione",
    "Automatic": "Automatica",
    "Quality": "Qualità",
    "Mbit/s: higher is sharper but uses more bandwidth": "Mbit/s: più alto = più nitido, più banda",
    "Touch and S Pen": "Touch e S Pen",
    "Sign on Tablet": "Firma sul tablet",
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
    "Cannot sign {name}": "Impossibile firmare {name}",
    "{name} is not a file": "{name} non è un file",
    "{name} is too big to sign: more than {size} MB": "{name} è troppo grande da firmare: più di {size} MB",
    "{name} is not a PDF": "{name} non è un PDF",
    "{name} cannot be read: {error}": "{name} non si può leggere: {error}",
    "{name} needs a password to open: unlock it first": "{name} richiede una password: sbloccalo prima",
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
    "Sign {name} on {tablet}": "Firma {name} su {tablet}",
    "{count} PDFs to sign on {tablet}": "{count} PDF da firmare su {tablet}",
    "It had a digital signature: in the signed copy, it no longer holds":
        "Aveva una firma digitale: nella copia firmata non vale più",
    "Forget": "Dimentica",
    "Paired tablets": "Tablet abbinati",
    "Forget {tablet}?": "Dimenticare {tablet}?",
    "The S Pen has no pressure: sudo usermod -aG plus-one $USER, then log in again":
        "La S Pen non ha la pressione: sudo usermod -aG plus-one $USER, poi rientra nella sessione",
    "Log out and back in for the S Pen's pressure": "Esci e rientra nella sessione per la pressione della S Pen",
    "Without access to /dev/uhid the S Pen works as a mouse, without pressure":
        "Senza accesso a /dev/uhid la S Pen funziona come un mouse, senza pressione",
    "Signed {count} PDFs": "{count} PDF firmati",
    "Update Plus One": "Aggiorna Plus One",
    "The app on the tablet and Plus One on this PC are different versions: signing with Second Screen instead":
        "L'app sul tablet e Plus One su questo PC sono versioni diverse: si firma con Second Screen",
    "Lost the tablet": "Tablet perso",
    "What was signed on the tablet did not reach the PC: {name} is unchanged":
        "Quanto firmato sul tablet non è arrivato al PC: {name} è rimasto com'era",
    "Opening Second Screen on the tablet…": "Apro Second Screen sul tablet…",
    "Tap “Second screen” on the tablet": "Tocca «Second screen» sul tablet",
    "Opening the desktop on {name}…": "Apro il desktop su {name}…",
    "Second screen through": "Secondo schermo tramite",
    "Plus One's app, when it is there": "L'app Plus One, se c'è",
    "Samsung's Second Screen": "Second Screen di Samsung",
    "{name}: paused while Plus One is not on its screen": "{name}: in pausa finché Plus One non è sul suo schermo",
    "The tablet closed the desktop": "Il tablet ha chiuso il desktop",
    "The tablet did not answer: open Second Screen on it and connect again":
        "Il tablet non ha risposto: apri Second Screen e collega di nuovo",
    "Connecting to {name}…": "Collegamento a {name}…",
    "The tablet is not answering: close and reopen Second Screen":
        "Il tablet non risponde: chiudi e riapri Second Screen",
    # 0.6: pairing from the window, the tablet in use
    "Preferences": "Preferenze",
    "About Plus One": "Informazioni su Plus One",
    "Main menu": "Menu principale",
    "Pair a tablet": "Abbina un tablet",
    "No tablet": "Nessun tablet",
    "Pair your tablet to use it as a second screen and to sign PDFs on it.":
        "Abbina il tuo tablet per usarlo come secondo schermo e per firmarci i PDF.",
    "Choose the tablet": "Scegli il tablet",
    "Use as second screen": "Usa come secondo schermo",
    "Stop": "Interrompi",
    "Sign a PDF…": "Firma un PDF…",
    "Second screen · {width} × {height} at {fps} Hz": "Secondo schermo · {width} × {height} a {fps} Hz",
    "Tablet camera": "Fotocamera del tablet",
    "Tablet microphone": "Microfono del tablet",
    "Needs the v4l2loopback module: see the README": "Serve il modulo v4l2loopback: vedi il README",
    "Needs PipeWire's pactl and pw-link": "Servono pactl e pw-link di PipeWire",
    "Needs an H.264 decoder: gstreamer1.0-libav": "Serve un decodificatore H.264: gstreamer1.0-libav",
    "The webcam module is loaded with other settings: sudo modprobe -r v4l2loopback && sudo modprobe v4l2loopback":
        "Il modulo della webcam è caricato con altre impostazioni: sudo modprobe -r v4l2loopback && "
        "sudo modprobe v4l2loopback",
    "Needs WebRTC for GStreamer: gstreamer1.0-nice and gir1.2-gst-plugins-bad-1.0":
        "Serve WebRTC per GStreamer: gstreamer1.0-nice e gir1.2-gst-plugins-bad-1.0",
    "As a webcam for this PC's programs": "Come webcam per i programmi di questo PC",
    "As a microphone for this PC's programs": "Come microfono per i programmi di questo PC",
    "On: the tablet turns its camera on while a program uses it":
        "Attiva: il tablet accende la fotocamera mentre un programma la usa",
    "On: the tablet turns its microphone on while a program uses it":
        "Attivo: il tablet accende il microfono mentre un programma lo usa",
    "In use": "In uso",
    "A program uses the camera, but the tablet is not reachable":
        "Un programma usa la fotocamera, ma il tablet non è raggiungibile",
    "A program uses the microphone, but the tablet is not reachable":
        "Un programma usa il microfono, ma il tablet non è raggiungibile",
    "Not allowed on the tablet: Plus One → For the PC": "Non consentito sul tablet: Plus One → Per il PC",
    "On the tablet, allow Plus One to use the camera": "Sul tablet, consenti a Plus One di usare la fotocamera",
    "On the tablet, allow Plus One to use the microphone": "Sul tablet, consenti a Plus One di usare il microfono",
    "On the tablet, let Plus One display over other apps":
        "Sul tablet, consenti a Plus One di comparire sopra le altre app",
    "On the tablet, another app is using the camera": "Sul tablet un'altra app sta usando la fotocamera",
    "On the tablet, another app is using the microphone": "Sul tablet un'altra app sta usando il microfono",
    "Camera": "Fotocamera",
    "Camera and microphone": "Fotocamera e microfono",
    "The tablet's microphone, as a microphone for this PC's programs (switched on in the window).":
        "Il microfono del tablet, come microfono per i programmi di questo PC (si accende nella finestra).",
    "Noise reduction": "Riduzione del rumore",
    "Off": "Spenta",
    "Light, on the tablet": "Leggera, sul tablet",
    "Neural, on this PC": "Neurale, su questo PC",
    "Neural takes out more of the room's noise, with about half a processor core":
        "La neurale toglie più rumore della stanza, con circa mezzo core del processore",
    "Neural needs DeepFilterNet's plugin in ~/.ladspa: light until then":
        "La neurale richiede il plugin di DeepFilterNet in ~/.ladspa: fino ad allora, leggera",
    "The tablet's camera, as a webcam for this PC's programs (switched on in the window).":
        "La fotocamera del tablet, come webcam per i programmi di questo PC (si attiva dalla finestra).",
    "Front": "Frontale",
    "Back": "Posteriore",
    "Picture": "Immagine",
    "1080p takes about twice the bandwidth. A change waits until no program uses the camera":
        "1080p usa circa il doppio della banda. Un cambio attende che nessun programma usi la fotocamera",
    "On your network": "Sulla tua rete",
    "Not on your network now": "Ora non è sulla tua rete",
    "Pair another tablet…": "Abbina un altro tablet…",
    "With Plus One on your Wi-Fi": "Con Plus One sulla tua Wi-Fi",
    "Not there? Open Plus One on the tablet, on the same Wi-Fi as this PC.":
        "Non c'è? Apri Plus One sul tablet, sulla stessa Wi-Fi di questo PC.",
    "Looking for tablets…": "Cerco i tablet…",
    "Paired": "Abbinato",
    "Pair only if the tablet shows this same code. Confirm there too.":
        "Abbina solo se il tablet mostra lo stesso codice. Conferma anche lì.",
    "Same code": "Il codice è uguale",
    "Confirm on the tablet…": "Conferma sul tablet…",
    "Paired with {name}": "Abbinato a {name}",
    "Not paired: the code was not confirmed on both screens":
        "Non abbinato: il codice non è stato confermato su entrambi gli schermi",
    "{name} did not answer": "{name} non ha risposto",
    "Try again": "Riprova",
    "Allow the camera on the tablet": "Consenti la fotocamera sul tablet",
    "Allow the microphone on the tablet": "Consenti il microfono sul tablet",
    "A program wants it: tap Plus One's notification on the tablet": "Un programma del PC ne ha bisogno: tocca la notifica di Plus One sul tablet",
    "Add a “Tablet” button to Quick Settings?": "Aggiungere un pulsante «Tablet» alle impostazioni rapide?",
    "Add": "Aggiungi",
    "Balanced": "Equilibrata",
    "Sharper text": "Testo più nitido",
    "For weak Wi-Fi": "Per Wi-Fi debole",
    "Sharper text takes more of the Wi-Fi": "Il testo più nitido usa più Wi-Fi",
    "Second screen": "Secondo schermo",
    "Advanced": "Avanzate",
    "Without Plus One's pen helper (the .deb installs it) the S Pen works as a mouse, without pressure":
        "Senza l'aiutante di Plus One per la penna (lo installa il .deb) la S Pen funziona come un mouse, "
        "senza pressione",
    "In use by {programs}": "In uso da {programs}",
    "Install Plus One on the tablet too, then pair it to use it as a second screen, to sign PDFs and as a webcam.":
        "Installa Plus One anche sul tablet, poi abbinalo per usarlo come secondo schermo, per firmare i PDF "
        "e come webcam.",
    "Get the tablet app": "Scarica l'app per il tablet",
    "Not found: open Plus One on the tablet": "Non trovato: apri Plus One sul tablet",
    "No tablet found yet. Open Plus One on the tablet, on the same Wi-Fi as this PC.":
        "Ancora nessun tablet. Apri Plus One sul tablet, sulla stessa Wi-Fi di questo PC.",
    "The one in use gets the desktop and the PDFs. Forget one you no longer use.":
        "Quello in uso riceve lo schermo e i PDF. Dimentica quelli che non usi più.",
    "No tablet paired": "Nessun tablet abbinato",
    "To use it again, pair it again: both screens show a code.":
        "Per usarlo di nuovo, abbinalo di nuovo: entrambi gli schermi mostreranno un codice.",
    "Screen": "Schermo",
    "Signing": "Firma",
    "General": "Generale",
    "In Files and Open With, for PDFs": "In File e Apri con, per i PDF",
    "Copy": "Copia",
    "Your Galaxy Tab as a second monitor, and to sign PDFs with its S Pen":
        "Il tuo Galaxy Tab come secondo monitor, e per firmare i PDF con la S Pen",
    "Pair your tablet in Plus One to sign {name} on it": "Abbina il tuo tablet in Plus One per firmarci {name}",
    "{name} no longer knows this PC: pair it again": "{name} non riconosce più questo PC: abbinalo di nuovo",
    "The tablet no longer knows this PC: pair it again": "Il tablet non riconosce più questo PC: abbinalo di nuovo",
    "Plus One on the tablet and on this PC are different versions: update them":
        "Plus One sul tablet e su questo PC sono versioni diverse: aggiornali",
    "Pair a tablet first": "Prima abbina un tablet",
    "This is not {name}: the desktop was not sent": "Questo non è {name}: lo schermo non è stato inviato",
    "Plus One on {name} and on this PC are different versions: update them":
        "Plus One su {name} e su questo PC sono versioni diverse: aggiornali",
    "Plus One does not answer on {name}: open it on the tablet and connect again":
        "Plus One non risponde su {name}: aprilo sul tablet e collega di nuovo",
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
