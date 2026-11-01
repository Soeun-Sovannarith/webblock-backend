"""Build attempt values: the attempt policy, the build system's result and the build failure codes
(decision 0011; cut from the build rules, decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.domain.errors import InvalidAttempt

# Phase 3 outcome codes (decision 0008). The first four end a source_published_events row; the
# last two are a build step's failure code and a release's failure code.
CODE_REPOSITORY_MISMATCH = "repository_mismatch"
CODE_REPOSITORY_NOT_REGISTERED = "repository_not_registered"
CODE_REPOSITORY_UNCONFIRMED = "repository_unconfirmed"
CODE_RELEASE_CONFLICT = "release_conflict"
CODE_DEADLINE_EXCEEDED = "deadline_exceeded"
CODE_BUILD_ATTEMPTS_EXHAUSTED = "build_attempts_exhausted"

# Build-system outcome codes of the prototype (decision 0014 section 4), a build step's
# failure_code: the build system reported a result for another project, release, attempt or
# commit, or a malformed digest; it refused the request; it no longer knows the build; it reported
# a failure without a well-formed code of its own.
CODE_BUILD_RESULT_MISMATCH = "build_result_mismatch"
CODE_BUILD_REJECTED = "build_rejected"
CODE_BUILD_LOST = "build_lost"
CODE_BUILD_FAILED = "build_failed"

# The largest BUILD_MAX_ATTEMPTS, so that build attempts fit the 100-step read limit.
MAX_BUILD_ATTEMPTS = 10


@dataclass(frozen=True, slots=True)
class BuildAttemptPolicy:
    """How many build attempts a release gets (BUILD_MAX_ATTEMPTS, required configuration)."""

    max_attempts: int

    def validate(self) -> None:
        """Require 1 <= max_attempts <= MAX_BUILD_ATTEMPTS (``InvalidAttempt``)."""
        if not 1 <= self.max_attempts <= MAX_BUILD_ATTEMPTS:
            raise InvalidAttempt(f"build attempts must be 1 to {MAX_BUILD_ATTEMPTS}")


@dataclass(frozen=True, slots=True)
class BuildResult:
    """What the build system reports for one build attempt. It names the attempt (``step_id`` and
    ``attempt``) and the source it built; each must equal the stored rows. A success carries the
    digest and no failure code; a failure carries a failure code and no digest. ``log_ref`` is
    optional (``""`` for none) and recorded on the step."""

    project_id: UUID
    release_id: UUID
    step_id: UUID
    attempt: int
    git_commit_sha: str
    repository_id: int
    container_image_digest: str | None = None
    failure_code: str = ""
    log_ref: str = ""
