"""Measurement ports (task H4; decision 0016 section 5 item 4; the lead's ruling 3 grants this
file).

The edges measure: the command consumer, the build poller and the public HTTP listener. They
depend on these small protocols only; ``infrastructure.telemetry.Metrics`` implements them with
``prometheus-client``, which neither the core nor the middle layers import. Every argument is a
fixed word of a bounded set (a command kind, an outcome, a route template), never an identifier:
the adapter maps any value outside its allow-list to ``other``. A measurement never raises and
never changes the work it measures.
"""

from __future__ import annotations

from typing import Protocol


class CommandMetrics(Protocol):
    """The command consumer's measurements."""

    def command_executed(self, kind: str, result: str, seconds: float) -> None:
        """One execution attempt of a claimed command ended: its kind, how the attempt ended (a
        word of the consumer's fixed set) and how long the executor ran."""
        ...


class BuildRoundMetrics(Protocol):
    """The build poller's measurements."""

    def build_round_result(self, round_name: str, outcome: str) -> None:
        """One result of a poll, pending-start or sweep round (``round_name``), by its outcome,
        or the round's own failure."""
        ...


class HTTPMetrics(Protocol):
    """The public listener's measurements."""

    def http_request(self, route: str, method: str, status: int | None, seconds: float) -> None:
        """One request: its route template (never the raw path), its method, the status sent
        (None when no response started) and its duration."""
        ...


class NoMetrics:
    """Measures nothing: the default where no adapter is wired (tools, tests)."""

    __slots__ = ()

    def command_executed(self, kind: str, result: str, seconds: float) -> None:
        del kind, result, seconds

    def build_round_result(self, round_name: str, outcome: str) -> None:
        del round_name, outcome

    def http_request(self, route: str, method: str, status: int | None, seconds: float) -> None:
        del route, method, status, seconds
