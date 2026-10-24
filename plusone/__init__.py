"""Use a Samsung Galaxy Tab's built-in Second Screen app as a Linux monitor."""
import json
import os
import time

__version__ = "0.4.0"

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


def write_private(path, data):
    """Writes data (JSON-able) to path as a whole, replacing it at once, for
    this user only (the directory too): signatures are handwriting."""
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    tmp = f"{path}.{os.getpid()}.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_log_handler(handler):
    """handler(line) is called from whichever thread logged; marshal as needed."""
    _handlers.append(handler)

