"""The facts a release step transition's conditions depend on (value; cut from the release_steps
row module, decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StepInput:
    """The facts a step transition depends on.

    ``failure_code`` is required on a transition to failed and refused otherwise. ``deadline_at``
    is read on ``-> running`` when the row holds none. ``log_ref`` is the build system's reference
    to the attempt, ``""`` for none; write-once."""

    failure_code: str = ""
    deadline_at: datetime | None = None
    log_ref: str = ""
