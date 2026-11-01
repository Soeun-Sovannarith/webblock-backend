"""web-block-commons' log records as the identity's own events (decision 0024 G8).

The library logs on the standard logger ``webblock_commons``: a refused token at DEBUG and a failed
key set fetch at WARNING, each with a fixed cause as ``auth_cause``. ``attach`` sends that logger's
records to the API's logger instead of the root handlers:

- a refusal becomes ``token refused`` (INFO) and a failed fetch ``jwks fetch failed`` (WARNING),
  with ``component=identity`` and the cause up to any ``:`` (the library appends an exception's
  class name there), so the identity metrics count them by cause whatever LOG_LEVEL is;
- any other record keeps its level and message, with ``component=webblock_commons``.

The library logs no token, claim, key, ``kid`` or URL, and its fields other than the cause are not
copied.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from provisioning.infrastructure.telemetry import Logger

LIBRARY_LOGGER: Final = "webblock_commons"
KEY_SET_FETCH_FAILED: Final = "key set fetch failed"
"""The library's message for a failed fetch of the discovery document or the key set."""

_LEVELS: Final = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


class LibraryLogBridge(logging.Handler):
    """Writes the library's records through ``logger`` (see the module's docstring)."""

    def __init__(self, logger: Logger) -> None:
        super().__init__(logging.DEBUG)
        self._log = logger

    def emit(self, record: logging.LogRecord) -> None:
        cause = getattr(record, "auth_cause", None)
        if isinstance(cause, str) and cause:
            cause = cause.partition(":")[0]
            if record.msg == KEY_SET_FETCH_FAILED:
                self._log.warning("jwks fetch failed", component="identity", auth_cause=cause)
            else:
                self._log.info("token refused", component="identity", auth_cause=cause)
            return
        method = _LEVELS.get(record.levelno, "error" if record.levelno > logging.ERROR else "debug")
        try:
            message = record.getMessage()
        except Exception:  # an unformattable record; its arguments are not written either
            message = "unformattable log record"
        getattr(self._log, method)(message, component=LIBRARY_LOGGER)

    def handleError(self, record: logging.LogRecord) -> None:
        # The default prints a traceback with the record's arguments to stderr.
        del record


def attach(logger: Logger) -> Callable[[], None]:
    """Send the library's records to ``logger`` (and no longer to the root handlers), in place of
    any bridge attached before; returns the function that undoes it. The library's logger is opened
    to DEBUG so that its refusal records are made; ``logger`` applies LOG_LEVEL."""
    library = logging.getLogger(LIBRARY_LOGGER)
    for earlier in [h for h in library.handlers if isinstance(h, LibraryLogBridge)]:
        library.removeHandler(earlier)
    bridge = LibraryLogBridge(logger)
    level, propagate = library.level, library.propagate
    library.addHandler(bridge)
    library.setLevel(logging.DEBUG)
    library.propagate = False

    def detach() -> None:
        library.removeHandler(bridge)
        library.setLevel(level)
        library.propagate = propagate

    return detach
