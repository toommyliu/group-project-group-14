"""Which account on this computer a connection to the service comes from.

The service listens on 127.0.0.1 only, so every caller is a program on the
same computer. On a machine several people reach over SSH that is not the
same as "the owner": a grader's own account can connect too. On Linux the
kernel lists every TCP connection with the user that owns it
(`/proc/net/tcp`), so the service can tell who is calling without trusting
anything the caller says. Elsewhere (macOS, Windows: usually a computer one
person uses) this returns None and the caller's own word is taken.
"""

from __future__ import annotations

import socket
import struct
from pathlib import Path

PROC_NET = Path("/proc/net")


def _encoded(host: str, port: int, family: int) -> str | None:
    """An address as /proc/net/tcp{,6} prints it: each 32-bit word of the
    address in the machine's byte order, then the port, all in hex."""
    try:
        if family == socket.AF_INET:
            packed = socket.inet_pton(socket.AF_INET, host)
            words = struct.unpack("=I", packed)
        else:
            if ":" not in host:
                host = f"::ffff:{host}"
            packed = socket.inet_pton(socket.AF_INET6, host)
            words = struct.unpack("=4I", packed)
    except (OSError, ValueError):
        return None
    return "".join(f"{word:08X}" for word in words) + f":{port:04X}"


def peer_uid(
    client: tuple[str, int] | None,
    server: tuple[str, int] | None,
    *,
    proc_net: Path = PROC_NET,
) -> int | None:
    """The user id that owns the caller's end of this connection, or None
    where the system does not say."""
    if not client or not server:
        return None
    for name, family in (("tcp", socket.AF_INET), ("tcp6", socket.AF_INET6)):
        try:
            lines = (proc_net / name).read_text().splitlines()[1:]
        except OSError:
            continue
        local = _encoded(client[0], int(client[1]), family)
        remote = _encoded(server[0], int(server[1]), family)
        if local is None or remote is None:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 7 and fields[1].upper() == local and fields[2].upper() == remote:
                try:
                    return int(fields[7])
                except ValueError:
                    return None
    return None


def login_of(uid: int) -> str | None:
    try:
        import pwd
    except ImportError:
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def own_uid() -> int | None:
    import os

    getuid = getattr(os, "getuid", None)
    return getuid() if getuid else None
