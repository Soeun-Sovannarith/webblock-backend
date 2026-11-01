"""The build system port: Jenkins in the prototype (decision 0014 section 3, "Build").

Provisioning starts a build of one release attempt at one exact commit, stores the handle (the
Jenkins build number as an opaque reference) in the build step's ``log_ref``, and polls the handle
for the result. The build system pushes the image; Provisioning only records the digest it reports.

A crash between ``start`` and storing the handle leaves a running step without a handle. ``find``
looks the attempt up again by its parameters, so a restart never starts a second build for the same
attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """What one build attempt builds. ``commit_sha`` is the full 40-character lowercase SHA."""

    project_id: UUID
    release_id: UUID
    attempt: int
    repository_url: str
    commit_sha: str
    image_repository: (
        str  # <registry>/webblock/sites/<project_id>, from configuration and the project ID
    )


@dataclass(frozen=True, slots=True)
class BuildHandle:
    """Where the build system tracks one attempt; ``ref`` is stored in ``release_steps.log_ref``.

    ``ref`` is an opaque reference, **never a URL** (revision 19, ``release_steps.log_ref``):
    at most 500 characters of letters, digits, ``.``, ``_``, ``-`` and ``/``, with no empty, ``.``
    or ``..`` segment. The adapter builds it (Jenkins: ``jenkins/build/<number>`` or
    ``jenkins/queue/<id>``) and turns it back into a request against its own configured base URL.
    """

    ref: str


class BuildState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # the pipeline ran and failed, or was aborted
    LOST = "lost"  # the build system no longer knows the handle


@dataclass(frozen=True, slots=True)
class BuildStatus:
    """What the build system reports for a handle.

    For ``SUCCEEDED``, ``project_id``, ``release_id``, ``attempt`` and ``commit_sha`` echo what the
    pipeline actually built, and ``image_digest`` is ``sha256:<64 hex>``. The use case accepts the
    result only when all of them match the attempt (CLAUDE.md section 3); the adapter never decides
    that. ``failure_code`` is a short machine code, never log text.
    """

    state: BuildState
    project_id: UUID | None = None
    release_id: UUID | None = None
    attempt: int | None = None
    commit_sha: str | None = None
    image_digest: str | None = None
    failure_code: str | None = None


class BuildSystemUnavailable(Exception):
    """The build system was unreachable or answered with a server error. Transient."""


class BuildSystemRejected(Exception):
    """The build system refused the request (authentication, job, parameters). Not transient."""


class BuildSystem(Protocol):
    async def start(self, request: BuildRequest) -> BuildHandle:
        """Queue one build. Raises BuildSystemUnavailable or BuildSystemRejected."""
        ...

    async def find(self, request: BuildRequest) -> BuildHandle | None:
        """Find a build already started for exactly this project, release and attempt, or None."""
        ...

    async def status(self, handle: BuildHandle) -> BuildStatus:
        """Read the current state of a build. Raises BuildSystemUnavailable, or BuildSystemRejected
        (authentication refused, or a handle outside the configured build system)."""
        ...

    async def cancel(self, handle: BuildHandle) -> None:
        """Abort the build behind ``handle``, queued or running, for an attempt that failed by its
        deadline (decision 0014 section 23). Idempotent: a finished build, an already aborted one
        and a handle the build system no longer knows are not errors. Raises
        BuildSystemUnavailable, or BuildSystemRejected (authentication refused, or a handle
        outside the configured build system)."""
        ...
