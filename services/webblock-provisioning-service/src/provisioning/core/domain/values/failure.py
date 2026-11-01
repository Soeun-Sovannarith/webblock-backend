"""The failure code and summary an outcome records (value; cut from the outcome rules, design
2026-10-28 section 4.2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Failure:
    """The reason an outcome records. A summary is accepted only where a release is written."""

    code: str
    summary: str = ""
