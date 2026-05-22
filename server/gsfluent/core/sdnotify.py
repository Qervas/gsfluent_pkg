"""Manual sd_notify implementation - no `systemd` Python package needed.

Sends datagrams to systemd's notification socket on Linux. The protocol is
trivial: open an AF_UNIX SOCK_DGRAM, send newline-separated `key=value`
strings. Documented at `man sd_notify(3)` and
https://www.freedesktop.org/software/systemd/man/sd_notify.html .

Used by the backend lifespan to:
  - notify_ready()       on startup once crash recovery finishes
  - notify_watchdog()    every 15s while /api/health is healthy
  - notify_status(text)  to surface human-readable state in `systemctl status`

All functions are no-ops when $NOTIFY_SOCKET is unset (dev runs, tests,
non-systemd hosts). They never raise - the backend must keep running even
when the notification listener is unreachable.
"""
from __future__ import annotations

import os
import socket


def notify(payload: str) -> bool:
    """Send a raw notification payload to systemd.

    payload is a string of newline-separated `key=value` pairs:
        "READY=1"
        "WATCHDOG=1"
        "READY=1\\nSTATUS=ok"

    Returns True iff the datagram was sent successfully. Returns False
    on missing $NOTIFY_SOCKET, send failure, or any other error.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False

    # systemd encodes abstract sockets with a leading '@' in the env var;
    # the kernel-level address is a NUL-prefixed name.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    except OSError:
        return False

    try:
        sock.sendto(payload.encode("utf-8"), addr)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def notify_ready() -> bool:
    """Tell systemd the service has finished startup and is ready to serve.
    Required when the unit uses Type=notify."""
    return notify("READY=1")


def notify_watchdog() -> bool:
    """Reset systemd's WatchdogSec timer. Call at half the configured
    interval (e.g. every 15s when WatchdogSec=30s)."""
    return notify("WATCHDOG=1")


def notify_status(text: str) -> bool:
    """Set the human-readable status text shown by `systemctl status`.
    Newlines in `text` are replaced with spaces to keep the protocol
    single-datagram-friendly."""
    safe = text.replace("\n", " ").replace("\r", " ")
    return notify(f"STATUS={safe}")
