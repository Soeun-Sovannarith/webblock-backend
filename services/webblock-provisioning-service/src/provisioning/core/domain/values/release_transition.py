"""The facts a release transition's conditions depend on (value; cut from the releases row module,
decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ReleaseTransition:
    """The facts a release transition's conditions depend on.

    - ``container_image_digest`` is recorded only on building -> built (through
      ``apply_build_result``). ``None`` is "not supplied"; any other value, ``""`` included, counts
      as supplied and is ``ImmutableField`` on every other transition.
    - ``failure_code`` (and the optional ``failure_summary``) are required on a transition to
      failed or rolled_back and refused on every other one.
    - ``newer_live_release_id`` is the release that supersedes this one (live -> superseded).
    - ``live_release_id`` is the project's live release, read on every transition out of
      promoting: ``None`` means the project has none (a first publish).
    """

    container_image_digest: str | None = None
    failure_code: str = ""
    failure_summary: str = ""
    newer_live_release_id: UUID | None = None
    live_release_id: UUID | None = None
