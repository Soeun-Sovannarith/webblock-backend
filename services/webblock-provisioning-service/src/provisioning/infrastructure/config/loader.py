"""The configuration loader (decision 0006 section 7,
OD-15, review-port-arch.md M8).

Every key is required and has no default; a key set to the empty string is missing. The exceptions
are read with ``optional``, ``optional_duration`` or ``optional_integer``: LOG_LEVEL, and the wave 6
keys whose default keeps the earlier behaviour (decision 0014 section 21). A load collects
every problem instead of stopping at the first and reports them sorted by key. A reason is fixed
text chosen here or by a section: it names bounds and other keys, never the configured value.

Each process's configuration is a *section reader*: a function that takes a ``Loader`` and returns
its configuration object. ``load`` runs one reader against an environment; ``required_keys`` runs it
against an empty one, so the list of required keys cannot drift from what the reader reads. A new
process (worker, build worker, bootstrap) adds its own reader built from the same ``Loader`` methods
and the shared section readers (database, Jenkins); nothing here changes.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from provisioning.infrastructure.config.secret import Secret

type Lookup = Callable[[str], str | None]
"""Returns the value of an environment variable, or None when it is not set (``os.environ.get``
has this shape; tests pass ``dict.get``)."""

type Check = Callable[[str], str | None]
"""Returns None when a value is acceptable, or a reason that never contains the value."""

_REQUIRED = "is required"


@dataclass(frozen=True, slots=True)
class Problem:
    """One refused key and the reason, which never contains the value."""

    key: str
    reason: str


class LoadError(Exception):
    """Every missing or invalid key of one load, sorted by key. Its text names keys and reasons
    only; no value, secret or not, is ever part of it."""

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = tuple(sorted(problems, key=lambda p: p.key))
        super().__init__(
            "invalid configuration: " + "; ".join(f"{p.key}: {p.reason}" for p in self.problems)
        )

    def keys(self) -> tuple[str, ...]:
        """The names of the refused keys, sorted."""
        return tuple(p.key for p in self.problems)


class Loader:
    """Reads and checks keys, collecting every problem. A method returns a zero value for a key
    that failed; the configuration built from it is never returned, because ``finish`` raises."""

    __slots__ = ("_failed", "_lookup", "_problems")

    def __init__(self, lookup: Lookup) -> None:
        self._lookup = lookup
        self._problems: list[Problem] = []
        self._failed: set[str] = set()

    def fail(self, key: str, reason: str) -> None:
        """Record a problem; a key is reported once, with its first reason."""
        if key in self._failed:
            return
        self._failed.add(key)
        self._problems.append(Problem(key, reason))

    def ok(self, *keys: str) -> bool:
        """Whether none of the keys has failed so far, so that a cross-check runs only on values
        that were read and checked."""
        return not any(k in self._failed for k in keys)

    def finish(self) -> None:
        """Raise ``LoadError`` with every collected problem, or return when there is none."""
        if self._problems:
            raise LoadError(tuple(self._problems))

    def optional(self, key: str) -> str | None:
        """The value of an optional key, or None when it is unset or empty."""
        value = self._lookup(key)
        return value if value else None

    def raw(self, key: str) -> str | None:
        """The value of a required key; an unset key and an empty one are both "is required"."""
        value = self._lookup(key)
        if not value:
            self.fail(key, _REQUIRED)
            return None
        return value

    def duration(self, key: str, lo: timedelta, hi: timedelta) -> timedelta:
        """A duration in Go's form ("30s", "1h30m", "250ms") within [lo, hi]."""
        value = self.raw(key)
        if value is None:
            return timedelta(0)
        return self._duration(key, value, lo, hi)

    def optional_duration(
        self, key: str, default: timedelta, lo: timedelta, hi: timedelta
    ) -> timedelta:
        """As ``duration``, but ``default`` when the key is unset or empty (wave 6 keys whose
        default keeps the earlier behaviour). A value that is set is checked like a required one."""
        value = self.optional(key)
        if value is None:
            return default
        return self._duration(key, value, lo, hi)

    def _duration(self, key: str, value: str, lo: timedelta, hi: timedelta) -> timedelta:
        parsed = parse_duration(value)
        if parsed is None:
            self.fail(key, "must be a duration with a unit, such as 30s")
            return timedelta(0)
        if not lo <= parsed <= hi:
            self.fail(key, f"must be between {lo} and {hi}")
            return timedelta(0)
        return parsed

    def integer(self, key: str, lo: int, hi: int) -> int:
        """A decimal integer within [lo, hi]. A plus sign, spaces, underscores, other digits and
        any other notation are refused."""
        value = self.raw(key)
        if value is None:
            return 0
        return self._integer(key, value, lo, hi)

    def optional_integer(self, key: str, default: int, lo: int, hi: int) -> int:
        """As ``integer``, but ``default`` when the key is unset or empty."""
        value = self.optional(key)
        if value is None:
            return default
        return self._integer(key, value, lo, hi)

    def _integer(self, key: str, value: str, lo: int, hi: int) -> int:
        if _INTEGER.fullmatch(value) is None:
            self.fail(key, "must be a decimal integer")
            return 0
        number = int(value)
        if not lo <= number <= hi:
            self.fail(key, f"must be between {lo} and {hi}")
            return 0
        return number

    def boolean(self, key: str) -> bool:
        """Exactly "true" or "false"."""
        value = self.raw(key)
        if value is None:
            return False
        if value in ("true", "false"):
            return value == "true"
        self.fail(key, "must be true or false")
        return False

    def choice(self, key: str, allowed: tuple[str, ...]) -> str:
        """Exactly one of ``allowed``, compared with case."""
        value = self.raw(key)
        if value is None:
            return ""
        if value in allowed:
            return value
        self.fail(key, "must be one of " + ", ".join(allowed))
        return ""

    def text(self, key: str, check: Check) -> str:
        """A required string that ``check`` accepts."""
        value = self.raw(key)
        if value is None:
            return ""
        reason = check(value)
        if reason is not None:
            self.fail(key, reason)
            return ""
        return value

    def secret(self, key: str, check: Check) -> Secret:
        """A required string that ``check`` accepts, wrapped so that it prints redacted."""
        return Secret(self.text(key, check))


def load[T](lookup: Lookup, read: Callable[[Loader], T]) -> T:
    """Run a section reader against ``lookup``; ``LoadError`` names every problem."""
    loader = Loader(lookup)
    config = read(loader)
    loader.finish()
    return config


def required_keys(read: Callable[[Loader], object]) -> tuple[str, ...]:
    """The keys a section reader requires, sorted: what it reports against an empty environment.
    Optional keys are not among them."""
    loader = Loader(lambda _key: None)
    read(loader)
    try:
        loader.finish()
    except LoadError as err:
        return err.keys()
    return ()


_INTEGER = re.compile(r"-?[0-9]+", re.ASCII)

# Go's time.ParseDuration without a sign: one or more decimal numbers, each with a unit.
_DURATION_PART = re.compile(r"([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(ns|us|µs|μs|ms|s|m|h)")
_UNIT_NANOS = {
    "ns": 1,
    "us": 1_000,
    "µs": 1_000,
    "μs": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
}


def parse_duration(text: str) -> timedelta | None:
    """A Go duration string ("0", "30s", "1h30m", "1.5s", "300ms") as a timedelta, truncated to
    microseconds; None for anything else, including a sign and a number without a unit."""
    if text == "0":
        return timedelta(0)
    if not text:
        return None
    nanos = 0
    pos = 0
    while pos < len(text):
        match = _DURATION_PART.match(text, pos)
        if match is None:
            return None
        number, unit = match.group(1), match.group(2)
        whole, _, fraction = number.partition(".")
        scale = _UNIT_NANOS[unit]
        nanos += int(whole or "0") * scale
        if fraction:
            nanos += int(fraction) * scale // 10 ** len(fraction)
        pos = match.end()
    return timedelta(microseconds=nanos // 1_000)


def plain_text(max_len: int) -> Check:
    """Accept 1 to ``max_len`` bytes of UTF-8 without control characters and without leading or
    trailing whitespace."""

    def check(value: str) -> str | None:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            return f"must be valid UTF-8 of at most {max_len} bytes"
        if size > max_len:
            return f"must be valid UTF-8 of at most {max_len} bytes"
        if has_control(value):
            return "must not contain control characters"
        if value.strip() != value or not value.strip():
            return "must not be blank or start or end with whitespace"
        return None

    return check


def has_control(value: str) -> bool:
    """Whether ``value`` holds a control character (Unicode category Cc)."""
    return any(unicodedata.category(c) == "Cc" for c in value)


def split_list(value: str) -> tuple[str, ...] | None:
    """Split a comma-separated list; None when any element is empty (a stray or trailing comma)."""
    items = tuple(value.split(","))
    if any(item == "" for item in items):
        return None
    return items
