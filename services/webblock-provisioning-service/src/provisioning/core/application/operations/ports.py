"""The read side of the operations' store ports: the release, command and deployment reads, the
listings and diagnostics, their keys and the page limits (cut from the former stores module, design
2026-10-28 section 5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from provisioning.core.application.intake.ports import SourceEventStatus
from provisioning.core.domain import Command, ProjectHosting, Release, ReleaseStep

if TYPE_CHECKING:
    # views.py imports this module; the result type is only an annotation here.
    from provisioning.core.application.operations.views import BuildStarted

# The number of step rows the contract allows in a release (steps maxItems).
MAX_RELEASE_STEPS = 100

# The listings' page bound (decision 0014 section 21 ruling 7; ReleaseList and CommandList
# maxItems) and the page size when the caller names none.
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20

# The fixed bounds of diagnostics (ruling 6): the newest steps of the release and the project's
# newest commands. The contract's Diagnostics maxItems.
DIAGNOSTIC_STEP_LIMIT = 20
DIAGNOSTIC_COMMAND_LIMIT = 10


@dataclass(frozen=True, slots=True)
class ReleaseKey:
    project_id: UUID
    release_id: UUID


@dataclass(frozen=True, slots=True)
class CommandKey:
    project_id: UUID
    command_id: UUID


@dataclass(frozen=True, slots=True)
class ReleaseWithSteps:
    """A release and its steps ordered by step name (enum order) then attempt."""

    release: Release
    steps: tuple[ReleaseStep, ...]


@dataclass(frozen=True, slots=True)
class Deployment:
    """A project's hosting row and, when one exists, its command in flight."""

    hosting: ProjectHosting
    in_flight: Command | None = None


@dataclass(frozen=True, slots=True)
class ReleaseCursor:
    """A position in a project's releases, newest first: the listing continues with the releases
    ordered before (created_at, release_id) in descending order."""

    created_at: datetime
    release_id: UUID


@dataclass(frozen=True, slots=True)
class SourceEventOutcome:
    """The non-sensitive columns of a "source published" event row
    (provisioning_ops.source_published_events) that diagnostics show (decision 0014 section 23
    item 4): no repository, commit or payload."""

    project_id: UUID
    generation_request_id: UUID
    status: SourceEventStatus
    outcome_code: str | None
    release_id: UUID | None
    attempts: int
    mismatched_repeat_count: int
    received_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class DiagnosticFacts:
    """The rows diagnostics are built from, read in one snapshot: the release, its newest steps
    (by step name then attempt, descending), the project's newest commands (by command number,
    descending), and the event row of the release's generation request (None without one)."""

    release: Release
    steps: tuple[ReleaseStep, ...]
    commands: tuple[Command, ...]
    source_event: SourceEventOutcome | None


class ReadStore(Protocol):
    """The three reads (decision 0004 section 6): one read-only transaction each."""

    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        """``NotFound`` when missing or of another project; more than MAX_RELEASE_STEPS steps is
        ``CorruptRow``."""
        ...

    async def read_command(self, key: CommandKey) -> Command:
        """Any command of the project, hosting commands included; ``NotFound`` otherwise."""
        ...

    async def read_deployment(self, project_id: UUID) -> Deployment:
        """The hosting row and the command in flight; ``NotFound`` without a hosting row."""
        ...


class HistoryStore(Protocol):
    """The listing and diagnostics reads (decision 0014 section 21 rulings 6 and 7): one
    read-only transaction each. Every read filters on the project and returns at most the
    ``limit`` it is given."""

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        """At most ``limit`` releases of the project ordered by (created_at, release_id)
        descending, after ``after`` when given. No row is not an error."""
        ...

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        """At most ``limit`` commands of the project, hosting commands included, ordered by
        command number descending, below ``after`` when given. No row is not an error."""
        ...

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        """The release (``NotFound`` when missing or of another project), at most ``step_limit``
        of its steps and ``command_limit`` of the project's commands, and its event row."""
        ...


class BuildStarter(Protocol):
    """Starts or retries the build of a release (``StartBuild.start``, or ``RequestBuild.start``
    on the API), consumed by build_release and by the release intake."""

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted: ...
