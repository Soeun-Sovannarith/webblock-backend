"""The releases row and its transition table (decision 0001 section 2 as amended by decision 0011
sections 3 and 4)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.enums.states import ReleaseStatus, parse_release_status
from provisioning.core.domain.errors import (
    IllegalTransition,
    ImmutableField,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    InvalidImageDigest,
    MissingFailureCode,
    MissingImageDigest,
)
from provisioning.core.domain.values.identifiers import (
    parse_commit_sha,
    parse_image_digest,
    parse_repository_id,
)
from provisioning.core.domain.values.release_transition import ReleaseTransition
from provisioning.core.domain.values.text import validate_code, validate_failure_summary


@dataclass(frozen=True, slots=True)
class Release:
    """The columns of provisioning.releases (revision 19 names). A release carries no build input,
    no predecessor and no command number: the rollback target is the project's live release."""

    id: UUID
    project_id: UUID
    generation_request_id: UUID
    repository_id: int
    git_commit_sha: str
    status: ReleaseStatus
    created_at: datetime
    updated_at: datetime
    container_image_digest: str | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    promoted_at: datetime | None = None

    def transition(self, to: ReleaseStatus, inp: ReleaseTransition, now: datetime) -> Release:
        """Return the release moved to ``to``. ``building -> built`` is reached only through
        ``apply_build_result`` and ``candidate_ready -> built`` only through
        ``return_replaced_candidate``; both are ``IllegalTransition`` here."""
        if self.status == ReleaseStatus.BUILDING and to == ReleaseStatus.BUILT:
            raise IllegalTransition(
                f"release {self.id} {self.status} -> {to} only through the build result"
            )
        if self.status == ReleaseStatus.CANDIDATE_READY and to == ReleaseStatus.BUILT:
            raise IllegalTransition(
                f"release {self.id} {self.status} -> {to} only when a newer deploy replaces it"
            )
        return self.transition_unchecked(to, inp, now)

    def transition_unchecked(
        self, to: ReleaseStatus, inp: ReleaseTransition, now: datetime
    ) -> Release:
        """The transition table and its conditions, without the two entry-point restrictions of
        ``transition``. Domain-internal: ``apply_build_result`` and ``return_replaced_candidate``
        call it; nothing outside the domain should."""
        from_ = self.status
        if not legal_release_transition(from_, to):
            raise IllegalTransition(f"release {self.id} {from_} -> {to}")
        digest = self._apply_digest(from_, to, inp.container_image_digest)
        failure_code, failure_summary = self._apply_failure(
            from_, to, inp.failure_code, inp.failure_summary
        )
        if from_ == ReleaseStatus.PROMOTING:
            self._check_promoting_exit(to, inp.live_release_id)
        promoted_at = self.promoted_at
        if to == ReleaseStatus.SUPERSEDED:
            newer = inp.newer_live_release_id
            if newer is None or newer.int == 0 or newer == self.id:
                raise IllegalTransition(
                    f"release {self.id} {from_} -> {to} needs a different newer live release"
                )
        elif to == ReleaseStatus.LIVE:
            promoted_at = now
        return replace(
            self,
            status=ReleaseStatus(to),
            updated_at=now,
            container_image_digest=digest,
            failure_code=failure_code,
            failure_summary=failure_summary,
            promoted_at=promoted_at,
        )

    def _check_promoting_exit(self, to: str, live: UUID | None) -> None:
        if live is None:
            if to == ReleaseStatus.ROLLED_BACK:
                raise IllegalTransition(
                    f"release {self.id} promoting -> rolled_back without a live release"
                )
            return
        if live.int == 0:
            raise InvalidId(f"live release of release {self.id}'s project")
        if live == self.id:
            raise IllegalTransition(
                f"release {self.id} is promoting and cannot be the live release"
            )
        if to == ReleaseStatus.FAILED:
            raise IllegalTransition(
                f"release {self.id} promoting -> failed while a live release exists"
            )

    def _apply_failure(
        self, from_: str, to: str, code: str, summary: str
    ) -> tuple[str | None, str | None]:
        if to not in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK):
            if code != "":
                raise InvalidFailureCode(f"release {self.id} {from_} -> {to} takes no failure code")
            if summary != "":
                raise InvalidFailureSummary(
                    f"release {self.id} {from_} -> {to} takes no failure summary"
                )
            return self.failure_code, self.failure_summary
        validate_code(code)
        validate_failure_summary(summary)
        return code, (summary if summary != "" else self.failure_summary)

    def _apply_digest(self, from_: str, to: str, supplied: str | None) -> str | None:
        """A digest may be supplied only on building -> built; anywhere else supplying one is
        ``ImmutableField``, even when it equals the recorded value. A non-empty digest that does
        not parse is ``InvalidImageDigest`` first."""
        if supplied:
            parse_image_digest(supplied)
        records = from_ == ReleaseStatus.BUILDING and to == ReleaseStatus.BUILT
        if supplied is not None and not records:
            raise ImmutableField(
                f"image digest of release {self.id} can only be supplied at building -> built"
            )
        if not records:
            return self.container_image_digest
        if not supplied:
            raise MissingImageDigest(f"release {self.id}")
        if self.container_image_digest is not None and supplied != self.container_image_digest:
            raise ImmutableField(f"image digest of release {self.id}")
        return supplied

    def validate(self) -> None:
        """Check a row loaded from storage: identity, formats, the status label, and the
        constraints that tie the digest, promoted_at and the failure fields to the status."""
        _validate_identity(
            self.id,
            self.project_id,
            self.generation_request_id,
            self.repository_id,
            self.git_commit_sha,
        )
        status = parse_release_status(self.status)
        if self.container_image_digest is not None:
            parse_image_digest(self.container_image_digest)
        if status in (ReleaseStatus.ACCEPTED, ReleaseStatus.BUILDING):
            if self.container_image_digest is not None:  # releases_no_image_before_built
                raise InvalidImageDigest(f"release {self.id} holds a digest while {status}")
        elif status != ReleaseStatus.FAILED and self.container_image_digest is None:
            raise MissingImageDigest(f"release {self.id} is {status}")  # releases_image_after_build
        promoted = status in (ReleaseStatus.LIVE, ReleaseStatus.SUPERSEDED)
        if promoted != (self.promoted_at is not None):
            raise IllegalTransition(f"release {self.id} promoted_at does not match status {status}")
        self._validate_failure(status)

    def _validate_failure(self, status: ReleaseStatus) -> None:
        if status not in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK):
            if self.failure_code is not None:
                raise InvalidFailureCode(f"release {self.id} holds a failure code while {status}")
            if self.failure_summary is not None:
                raise InvalidFailureSummary(
                    f"release {self.id} holds a failure summary while {status}"
                )
            return
        if self.failure_code is None:
            raise MissingFailureCode(f"release {self.id} is {status}")
        validate_code(self.failure_code)
        if self.failure_summary is not None:
            # An absent summary is NULL, never an empty string.
            if self.failure_summary == "":
                raise InvalidFailureSummary(f"release {self.id} holds an empty failure summary")
            validate_failure_summary(self.failure_summary)


def _validate_identity(
    release_id: UUID,
    project_id: UUID,
    generation_request_id: UUID,
    repository_id: int,
    git_commit_sha: str,
) -> None:
    if release_id.int == 0 or project_id.int == 0 or generation_request_id.int == 0:
        raise InvalidId("release, project and generation request IDs are required")
    parse_repository_id(repository_id)
    parse_commit_sha(git_commit_sha)


def new_release(
    *,
    release_id: UUID,
    project_id: UUID,
    generation_request_id: UUID,
    repository_id: int,
    git_commit_sha: str,
    now: datetime,
) -> Release:
    """A release in status accepted created at ``now``, with no digest, failure or promotion
    time (decision 0001 section 2)."""
    _validate_identity(release_id, project_id, generation_request_id, repository_id, git_commit_sha)
    return Release(
        id=release_id,
        project_id=project_id,
        generation_request_id=generation_request_id,
        repository_id=repository_id,
        git_commit_sha=git_commit_sha,
        status=ReleaseStatus.ACCEPTED,
        created_at=now,
        updated_at=now,
    )


_RELEASE_TRANSITIONS: dict[str, frozenset[str]] = {
    ReleaseStatus.ACCEPTED: frozenset({ReleaseStatus.BUILDING, ReleaseStatus.FAILED}),
    ReleaseStatus.BUILDING: frozenset({ReleaseStatus.BUILT, ReleaseStatus.FAILED}),
    ReleaseStatus.BUILT: frozenset({ReleaseStatus.CANDIDATE_READY, ReleaseStatus.FAILED}),
    ReleaseStatus.CANDIDATE_READY: frozenset(
        {ReleaseStatus.PROMOTING, ReleaseStatus.FAILED, ReleaseStatus.BUILT}
    ),
    ReleaseStatus.PROMOTING: frozenset(
        {ReleaseStatus.LIVE, ReleaseStatus.ROLLED_BACK, ReleaseStatus.FAILED}
    ),
    ReleaseStatus.LIVE: frozenset({ReleaseStatus.SUPERSEDED}),
}


def legal_release_transition(from_: ReleaseStatus, to: ReleaseStatus) -> bool:
    """Whether ``from_ -> to`` appears in the transition table, ignoring conditions."""
    return str(to) in _RELEASE_TRANSITIONS.get(str(from_), frozenset())
