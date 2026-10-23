#!/bin/sh
PYTHONPATH="/app/lib/plus-one${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m plusone "$@"
