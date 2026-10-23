"""Use a Samsung Galaxy Tab's built-in Second Screen app as a Linux monitor."""
import time


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)
