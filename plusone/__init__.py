"""Use a Samsung Galaxy Tab's built-in Second Screen app as a Linux monitor."""
import time

__version__ = "0.1.0"

_handlers = []


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(time.strftime("%H:%M:%S"), line, flush=True)
    for handler in _handlers:
        handler(line)


def once(fn, *args):
    """Wrap fn for GLib.idle_add / timeout_add so it runs a single time."""
    def run():
        fn(*args)
        return False
    return run


def add_log_handler(handler):
    """handler(line) is called from whichever thread logged; marshal as needed."""
    _handlers.append(handler)
