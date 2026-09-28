"""Whether cupsd.conf keeps job files."""

from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path


def parse_retention(body: bytes) -> bool:
    """Does this config body leave CUPS keeping job files?

    Pure, so the precedence rule can be tested without a filesystem or a CUPS
    install, rather than only by an end-to-end run on a machine that happens
    to have the right config.
    """
    # cupsd honours the LAST matching directive. Returning on the first would
    # make a hand-edited config with "No" followed by "Yes" report RETENTION:
    # OFF on a host that is retaining documents: danger reported as safety.
    setting: bytes | None = None
    for line in body.splitlines():
        m = re.match(rb"^\s*PreserveJobFiles\s+(\S+)", line, re.IGNORECASE)
        if m:
            setting = m.group(1).lower()
    if setting is not None:
        return setting not in (b"no", b"off", b"false", b"0")
    # Unset means the compiled default, which on the hosts measured here keeps
    # documents. Reporting "off" on an absent directive would be a guess in the
    # dangerous direction.
    return True


def retention_state(conf: str) -> bool | None:
    """Is CUPS configured to keep job files? None if the config is unreadable.

    Nothing but the read lives here, and it reads bytes: a printer config is
    not guaranteed UTF-8, and decoding it is a step that can fail or corrupt
    for no benefit when all that is wanted is a directive match.
    """
    try:
        body = _read_conf(conf)
    except OSError:
        # reason: None is the documented "config unreadable", which render()
        # and purge_outcome() show as such rather than as a retention verdict
        return None
    return parse_retention(body)


def _read_conf(path: str) -> bytes:
    """Read a config file, refusing the types that would hang.

    Path.read_bytes() on a FIFO BLOCKS FOREVER waiting for a writer, and a hang
    in a security tool reads as "still checking" rather than as a failure.
    --conf takes an arbitrary path, so the type is checked before opening.

    Character devices are deliberately still allowed: `--conf /dev/null` is a
    legitimate "empty config", used throughout the suites, and it reads as
    EOF immediately. A writer would have to demand a regular file, because
    REPLACING /dev/null is a very different act from reading it; a reader
    need not.
    """
    st = os.stat(path)
    if stat.S_ISFIFO(st.st_mode) or stat.S_ISSOCK(st.st_mode):
        raise OSError(errno.EINVAL, "would block on a fifo or socket", path)
    return Path(path).read_bytes()
