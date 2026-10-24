"""The user's saved signatures, on the PC and in the tablet's app.

signatures.json holds {"signatures": [...], "removed": [...]}. A signature
is ink.py's normalized form (top-left at 0,0, width 1, and the width in
points it was drawn at) with an "id". The order is this side's own, the
most recently used first: "My signature" places the first.

The tablet's app keeps its own list. Each job takes this one to the app and
brings the app's back, and the two merge: every signature either side has,
except those either side removed. Removed ids are remembered, so a signature
removed on one side does not come back from the other.
"""
import json
import os
import uuid

from gi.repository import GLib

PATH = os.path.join(GLib.get_user_data_dir(), "plus-one", "signatures.json")
OLD = os.path.join(GLib.get_user_data_dir(), "plus-one", "signature.json")  # 0.4 kept one
MAX_SIGNATURES = 50
KEEP_REMOVED = 500  # removed ids remembered


def valid(signature):
    """Whether signature is a normalized signature with an id."""
    try:
        return (isinstance(signature["id"], str) and 0 < len(signature["id"]) <= 64
                and float(signature.get("width", 1.0)) > 0
                and len(signature["strokes"]) > 0
                and all(len(pt) == 3 and all(isinstance(v, (int, float)) for v in pt)
                        for s in signature["strokes"] for pt in s))
    except (KeyError, TypeError, ValueError):
        return False


def _parse(data):
    if not isinstance(data, dict):
        return [], []
    found = data.get("signatures")
    removed = data.get("removed")
    return ([s for s in found if valid(s)] if isinstance(found, list) else [],
            [r for r in removed if isinstance(r, str)] if isinstance(removed, list) else [])


def load(path=None, old=None):
    """(signatures, removed ids)."""
    path, old = path or PATH, old or OLD
    try:
        with open(path) as f:
            return _parse(json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return [], []
    try:  # Plus One 0.4 kept a single signature
        with open(old) as f:
            single = json.load(f)
        if isinstance(single, dict) and single.get("strokes"):
            single = dict(single, id=uuid.uuid4().hex)
            if valid(single):
                save([single], [], path)  # its id must stay the same
                return [single], []
    except (OSError, ValueError):
        pass
    return [], []


def save(signatures, removed, path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump({"signatures": signatures[:MAX_SIGNATURES], "removed": removed[-KEEP_REMOVED:]}, f)
    os.replace(tmp, path)


def add(signature, path=None):
    """Keeps a normalized signature, first in the list; returns it with its id."""
    signatures, removed = load(path)
    kept = dict(signature, id=uuid.uuid4().hex)
    save([kept] + signatures, removed, path)
    return kept


def remove(signature_id, path=None):
    signatures, removed = load(path)
    save([s for s in signatures if s["id"] != signature_id], removed + [signature_id], path)


def use(signature_id, path=None):
    """Moves the signature to the front: "My signature" places it next time."""
    signatures, removed = load(path)
    chosen = [s for s in signatures if s["id"] == signature_id]
    if chosen and signatures[0] is not chosen[0]:
        save(chosen + [s for s in signatures if s["id"] != signature_id], removed, path)


def merged(ours, our_removed, theirs, their_removed):
    """(signatures, removed): ours in our order then theirs we lack, minus
    those either side removed."""
    removed = list(dict.fromkeys(our_removed + their_removed))
    gone = set(removed)
    known = {s["id"] for s in ours}
    return ([s for s in ours if s["id"] not in gone]
            + [s for s in theirs if s["id"] not in gone and s["id"] not in known]), removed


def merge(theirs, path=None):
    """Merges the tablet's list ({"signatures", "removed"}) into ours."""
    signatures, removed = load(path)
    their_signatures, their_removed = _parse(theirs)
    result = merged(signatures, removed, their_signatures, their_removed)
    if result != (signatures, removed):
        save(*result, path)


def message(path=None):
    """Our list as a job carries it to the tablet."""
    signatures, removed = load(path)
    return {"signatures": signatures, "removed": removed}
