"""Bounded exponential backoff with jitter (decision 0005 section 7)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

_MICROSECOND = timedelta(microseconds=1)


class InvalidConfig(ValueError):
    """A missing or out-of-range value given to a constructor of this package. Every limit is
    explicit; there are no defaults in code."""


@dataclass(frozen=True, slots=True)
class Backoff:
    """The delay before attempt n+1 is min(max, base * 2^(n-1)) reduced by a random amount of
    less than half, so it is never zero and never above the cap."""

    base: timedelta
    max: timedelta

    def validate(self) -> None:
        """A positive base and a cap at least the base (``InvalidConfig``)."""
        if self.base <= timedelta(0):
            raise InvalidConfig("backoff base must be positive")
        if self.max < self.base:
            raise InvalidConfig("backoff maximum must be at least the base")

    def delay(self, attempt: int) -> timedelta:
        """The jittered delay after attempt ``attempt`` (1, 2, ...; below 1 counts as 1): in
        (d/2, d] for d = min(max, base * 2^(attempt-1)), at least one microsecond."""
        d = self.base
        for _ in range(1, attempt):
            if d >= self.max:
                break
            d = self.max if d > self.max / 2 else d * 2
        d = min(d, self.max)
        micros = d // _MICROSECOND
        if micros <= 1:
            return _MICROSECOND
        half = micros // 2
        return timedelta(microseconds=micros - secrets.randbelow(half))
