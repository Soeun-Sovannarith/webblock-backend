"""Project's "source published" event (decision 0008 section 2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.entities.release import Release, new_release
from provisioning.core.domain.errors import DomainError, IdempotencyConflict, InvalidId
from provisioning.core.domain.values.identifiers import (
    parse_commit_sha,
    parse_id,
    parse_repository_id,
)


@dataclass(frozen=True, slots=True)
class SourcePublishedFields:
    """The raw content of the event as a decoder hands it over: the four fields Provisioning
    reads, untyped."""

    project_id: str
    generation_request_id: str
    repository_id: int
    git_commit_sha: str


@dataclass(frozen=True, slots=True)
class SourcePublished:
    """A parsed event: the project, the generation request (with the project, the event's natural
    key) and the accepted source."""

    project_id: UUID
    generation_request_id: UUID
    repository_id: int
    git_commit_sha: str

    def validate(self) -> None:
        """Non-nil IDs (``InvalidId``), the repository ID range (``InvalidRepositoryId``) and the
        commit SHA format (``InvalidCommitSha``)."""
        if self.project_id.int == 0 or self.generation_request_id.int == 0:
            raise InvalidId("source published needs project and generation request IDs")
        parse_repository_id(self.repository_id)
        parse_commit_sha(self.git_commit_sha)

    def check_same_source(self, other: SourcePublished) -> None:
        """Return when ``other`` is the same event (a duplicate); any difference is
        ``IdempotencyConflict``."""
        if (
            self.project_id != other.project_id
            or self.generation_request_id != other.generation_request_id
        ):
            raise IdempotencyConflict("source published events of different keys")
        if self.repository_id != other.repository_id or self.git_commit_sha != other.git_commit_sha:
            raise IdempotencyConflict(
                f"generation request {self.generation_request_id} of project {self.project_id} "
                "published with another source"
            )

    def check_release(self, release: Release) -> None:
        """Return when ``release`` holds this event's project, generation request, repository and
        commit, whatever its status; otherwise ``IdempotencyConflict``."""
        if (
            release.project_id != self.project_id
            or release.generation_request_id != self.generation_request_id
        ):
            raise IdempotencyConflict(
                f"release {release.id} is not the release of this event's key"
            )
        if (
            release.repository_id != self.repository_id
            or release.git_commit_sha != self.git_commit_sha
        ):
            raise IdempotencyConflict(
                f"release {release.id} of generation request {self.generation_request_id} "
                "holds another source"
            )

    def new_release(self, release_id: UUID, now: datetime) -> Release:
        """The accepted release this event creates."""
        return new_release(
            release_id=release_id,
            project_id=self.project_id,
            generation_request_id=self.generation_request_id,
            repository_id=self.repository_id,
            git_commit_sha=self.git_commit_sha,
            now=now,
        )


def parse_source_published(fields: SourcePublishedFields) -> SourcePublished:
    """Parse the four fields in order (project, generation request, repository, commit) and raise
    the first error: ``InvalidId``, ``InvalidRepositoryId`` or ``InvalidCommitSha``. Errors name
    the field, never its value."""
    project_id = _parse_field("project", parse_id, fields.project_id)
    request_id = _parse_field("generation request", parse_id, fields.generation_request_id)
    repository_id = _parse_field("repository", parse_repository_id, fields.repository_id)
    commit_sha = _parse_field("commit", parse_commit_sha, fields.git_commit_sha)
    return SourcePublished(
        project_id=project_id,
        generation_request_id=request_id,
        repository_id=repository_id,
        git_commit_sha=commit_sha,
    )


def _parse_field[T, V](field: str, parse: Callable[[V], T], value: V) -> T:
    """Parse one field; the error names the field, never its value, and keeps its class."""
    try:
        return parse(value)
    except DomainError as exc:
        raise type(exc)(f"source published {field}: {exc}") from None
