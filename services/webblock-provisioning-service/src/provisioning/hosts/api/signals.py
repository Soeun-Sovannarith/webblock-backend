"""Stop signals of the API process: the first
SIGINT or SIGTERM sets the stop event, which starts a graceful shutdown; a second one restores the
default action and re-raises the signal, so the process ends at once."""

from __future__ import annotations

import asyncio
import signal
from typing import Final

STOP_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)


def install_stop_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Install the handlers on ``loop`` (the main thread's loop)."""

    def second(sig: signal.Signals) -> None:
        for other in STOP_SIGNALS:
            loop.remove_signal_handler(other)
        signal.signal(sig, signal.SIG_DFL)
        signal.raise_signal(sig)

    def first(_sig: signal.Signals) -> None:
        stop.set()
        for other in STOP_SIGNALS:
            loop.add_signal_handler(other, second, other)

    for s in STOP_SIGNALS:
        loop.add_signal_handler(s, first, s)
