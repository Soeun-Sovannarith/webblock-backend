"""The deploy failure list (decision 0011 section 3).

``fail_without_route_change`` needs it to decide which codes fail a deploy's release. The codes
once named after Crossplane objects are renamed for plain Kubernetes objects (decision 0014
section 10.6): ``candidate_xr_conflict`` is ``candidate_apply_conflict``,
``candidate_generation_ahead`` is ``candidate_fence_ahead``, ``candidate_xr_refused`` is
``candidate_refused``, and ``candidate_refused_by_composition`` is gone with the composition
function (the API server's validation and admission refusals are ``candidate_refused``). The three
workload failure reasons of ``SiteCluster.release_status`` are codes of their own, and so are the
deploy's refusals at execution time. The summaries of the codes come from the candidate rules
(decision 0025 CQ1).
"""

from __future__ import annotations

from dataclasses import dataclass

from provisioning.core.domain.errors import InvalidFailureCode
from provisioning.core.domain.values.build import CODE_DEADLINE_EXCEEDED
from provisioning.core.domain.values.text import validate_code

CODE_TENANT_NOT_READY = "tenant_not_ready"
CODE_CLUSTER_UNAVAILABLE = "cluster_unavailable"
CODE_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
CODE_TENANT_MISMATCH = "tenant_mismatch"
CODE_LIMIT_REACHED = "limit_reached"
CODE_ENTITLEMENT_UNAVAILABLE = "entitlement_unavailable"
CODE_IMAGE_LOCATION_UNAVAILABLE = "image_location_unavailable"
CODE_ACCESS_SETUP_UNAVAILABLE = "access_setup_unavailable"
CODE_RUNTIME_SECRET_UNAVAILABLE = "runtime_secret_unavailable"  # noqa: S105 - a failure code, not a secret
CODE_ACCESS_SETUP_INVALID = "access_setup_invalid"
CODE_RUNTIME_SECRET_MISSING = "runtime_secret_missing"  # noqa: S105 - a failure code, not a secret
# Server-side apply met another field manager (409); never forced.
CODE_CANDIDATE_APPLY_CONFLICT = "candidate_apply_conflict"
# The release's objects carry a higher command number: a newer command owns them.
CODE_CANDIDATE_FENCE_AHEAD = "candidate_fence_ahead"
# The candidate was not ready before the attempt's deadline, or its Deployment disappeared.
CODE_CANDIDATE_NOT_READY = "candidate_not_ready"
# The workload failure reasons of SiteCluster.release_status.
CODE_PROGRESS_DEADLINE_EXCEEDED = "progress_deadline_exceeded"
CODE_IMAGE_PULL_FAILED = "image_pull_failed"
CODE_CRASH_LOOP = "crash_loop"
# Precise causes of a candidate that never becomes ready (decision 0015 section 4 step 1): its pod
# cannot be scheduled (PodScheduled False, reason Unschedulable), or a ResourceQuota refuses its
# pods (ReplicaFailure FailedCreate naming a quota). They win over candidate_not_ready and
# progress_deadline_exceeded when their evidence is present; the outcome is the same.
CODE_INSUFFICIENT_RESOURCES = "insufficient_resources"
CODE_QUOTA_EXCEEDED = "quota_exceeded"
# Refusals at execution time, before any cluster call: the release is not built with a digest,
# the route is not stable, hosting is not online.
CODE_RELEASE_NOT_DEPLOYABLE = "release_not_deployable"
CODE_ROUTE_NOT_STABLE = "route_not_stable"
CODE_HOSTING_OFFLINE = "hosting_offline"
CODE_ATTEMPTS_EXHAUSTED = "attempts_exhausted"
# The API server refused the objects: validation (400, 422) or admission.
CODE_CANDIDATE_REFUSED = "candidate_refused"
CODE_CANDIDATE_ATTEMPTS_EXHAUSTED = "candidate_attempts_exhausted"


@dataclass(frozen=True, slots=True)
class DeployFailure:
    """The classification of one deploy failure code. ``transient``: retried inside the same
    command. ``fails_release``: the release goes built -> failed, because retrying cannot help."""

    code: str
    transient: bool = False
    fails_release: bool = False


_DEPLOY_FAILURES: tuple[DeployFailure, ...] = (
    DeployFailure(CODE_TENANT_NOT_READY, transient=True),
    DeployFailure(CODE_CLUSTER_UNAVAILABLE, transient=True),
    DeployFailure(CODE_DEPENDENCY_UNAVAILABLE, transient=True),
    DeployFailure(CODE_TENANT_MISMATCH),
    DeployFailure(CODE_LIMIT_REACHED),
    DeployFailure(CODE_ENTITLEMENT_UNAVAILABLE),
    DeployFailure(CODE_IMAGE_LOCATION_UNAVAILABLE),
    DeployFailure(CODE_ACCESS_SETUP_UNAVAILABLE),
    DeployFailure(CODE_RUNTIME_SECRET_UNAVAILABLE),
    DeployFailure(CODE_ACCESS_SETUP_INVALID),
    DeployFailure(CODE_RUNTIME_SECRET_MISSING),
    DeployFailure(CODE_CANDIDATE_APPLY_CONFLICT),
    DeployFailure(CODE_CANDIDATE_FENCE_AHEAD),
    DeployFailure(CODE_CANDIDATE_NOT_READY),
    DeployFailure(CODE_PROGRESS_DEADLINE_EXCEEDED),
    DeployFailure(CODE_IMAGE_PULL_FAILED),
    DeployFailure(CODE_CRASH_LOOP),
    DeployFailure(CODE_INSUFFICIENT_RESOURCES),
    DeployFailure(CODE_QUOTA_EXCEEDED),
    DeployFailure(CODE_RELEASE_NOT_DEPLOYABLE),
    DeployFailure(CODE_ROUTE_NOT_STABLE),
    DeployFailure(CODE_HOSTING_OFFLINE),
    DeployFailure(CODE_DEADLINE_EXCEEDED),
    DeployFailure(CODE_ATTEMPTS_EXHAUSTED),
    DeployFailure(CODE_CANDIDATE_REFUSED, fails_release=True),
    DeployFailure(CODE_CANDIDATE_ATTEMPTS_EXHAUSTED, fails_release=True),
)


def deploy_failures() -> tuple[DeployFailure, ...]:
    """Every deploy failure code with its classification, in a fixed order: transient codes, the
    permanent codes that leave the release built, then those that fail it."""
    return _DEPLOY_FAILURES


def classify_deploy_failure(code: str) -> DeployFailure:
    """The classification of a listed code. A malformed code raises ``validate_code``'s error; a
    well-formed code that is not listed is ``InvalidFailureCode``."""
    validate_code(code)
    for failure in _DEPLOY_FAILURES:
        if failure.code == code:
            return failure
    raise InvalidFailureCode(f"{code} is not a deploy failure code")


# Fixed summaries of the codes that fail a release (decision 0001 section 9): no text from the
# API server is ever stored.
_SUMMARIES: dict[str, str] = {
    CODE_CANDIDATE_REFUSED: "The cluster refused the release's objects.",
    CODE_CANDIDATE_ATTEMPTS_EXHAUSTED: "The release used all its candidate attempts.",
}


def deploy_failure_summary(code: str) -> str:
    """The fixed summary of a code that fails the release, ``""`` for any other code."""
    return _SUMMARIES.get(code, "")
