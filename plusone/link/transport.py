"""How a connection to a tablet's app is opened: a transport makes a
stream socket to a target, which the link then secures (TLS) and speaks
over. Today TCP, over whatever IP path reaches the tablet (the home Wi-Fi;
Wi-Fi Direct would be another path, not another transport). Another kind
(USB, say) is a class with the same connect(), named in its targets:
(kind, ...) where TCP's are plain (host, port)."""
import socket


class Tcp:
    name = "tcp"

    def connect(self, target, timeout):
        sock = socket.create_connection(target, timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # small messages at once
        return sock


TRANSPORTS = {Tcp.name: Tcp()}


def open_stream(target, timeout):
    """A connected stream socket to target: (host, port) over TCP, or
    (kind, *where) over the transport of that kind."""
    if len(target) == 2 and isinstance(target[1], int):
        return TRANSPORTS["tcp"].connect(target, timeout)
    kind, *where = target
    return TRANSPORTS[kind].connect(tuple(where), timeout)
