"""The failure-code catalogue (task H5a; decision 0015 section 4 step 1; decision 0011 section 3).

``FAILURE_CODES`` lists every code the service can record in ``releases.failure_code``,
``release_steps.failure_code`` or ``commands.failure_code``: where
it can appear, what it means, and whether it is worth retrying. ``retryable`` means a new deploy or
build of the same input may succeed without a code or configuration change; it is the API's
``failure_retryable``. A code that is not listed (a row written before the catalogue) is not
retryable.

``OTHER_CODES`` lists the codes the service writes elsewhere and never on a business row: the
dispatch row's ``last_failure_code``, audit events, the inbox's ``outcome_code`` and the usage
outbox. They carry no retryable flag.

The codes are written as text here because most of their constants live in outer layers, which the
domain may not import; the existing ``CODE_*`` constants stay where they are. A unit test scans
the package and fails on a written code missing from this module, and on an entry nothing writes
(``tests/unit/core/domain/values/test_failure_codes.py``).
``.claude/docs/runbooks/failure-codes.md`` shows the same tables, checked by that test.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from provisioning.core.domain.enums.failure_codes import FailurePlace, FailureStage


@dataclass(frozen=True, slots=True)
class FailureCode:
    """One catalogued code of a business row."""

    code: str
    stage: FailureStage
    places: frozenset[FailurePlace]
    retryable: bool
    meaning: str


@dataclass(frozen=True, slots=True)
class OtherCode:
    """A code the service writes outside the business rows; ``where`` names the field."""

    code: str
    where: str
    meaning: str


_R = FailurePlace.RELEASE
_S = FailurePlace.STEP
_C = FailurePlace.COMMAND


def _entry(
    code: str, stage: FailureStage, places: set[FailurePlace], retryable: bool, meaning: str
) -> FailureCode:
    return FailureCode(code, stage, frozenset(places), retryable, meaning)


_B = FailureStage.BUILD
_D = FailureStage.DEPLOY
_P = FailureStage.PROMOTE
_H = FailureStage.HOSTING
_RD = FailureStage.REDEPLOY
_A = FailureStage.ANY_COMMAND

FAILURE_CODES: Final[tuple[FailureCode, ...]] = (
    # Build attempts (decision 0008 section 4, decision 0014 sections 3 and 4).
    _entry(
        "build_rejected",
        _B,
        {_S},
        False,
        "The build system refused to start or report the attempt.",
    ),
    _entry(
        "build_lost",
        _B,
        {_S},
        True,
        "The build system no longer knows the attempt, for example after a restart.",
    ),
    _entry(
        "build_result_mismatch",
        _B,
        {_S},
        False,
        "The build system reported a result for another project, release, attempt or commit, or a"
        " malformed digest.",
    ),
    _entry(
        "build_failed",
        _B,
        {_S},
        False,
        "The site's build failed, or the build system reported a failure without a known code.",
    ),
    _entry(
        "invalid_parameters",
        _B,
        {_S},
        False,
        "The build template refused the attempt's parameters.",
    ),
    _entry("clone_failed", _B, {_S}, True, "The build template could not clone the repository."),
    _entry("commit_not_found", _B, {_S}, False, "The recorded commit is not in the repository."),
    _entry(
        "push_failed",
        _B,
        {_S},
        True,
        "The build template could not push the image to the registry.",
    ),
    _entry(
        "pipeline_failed",
        _B,
        {_S},
        True,
        "The build pipeline failed or was aborted without a code of its own.",
    ),
    _entry(
        "result_missing",
        _B,
        {_S},
        False,
        "The build succeeded without archiving its result file.",
    ),
    _entry("result_invalid", _B, {_S}, False, "The build's result file is not a valid result."),
    _entry(
        "cancelled",
        _B,
        {_S},
        True,
        "The build was cancelled in the build system's queue before it started.",
    ),
    _entry("build_attempts_exhausted", _B, {_R}, False, "The release used all its build attempts."),
    # Candidate attempts of a deploy (decision 0011 sections 3 and 6, decision 0014 section 12).
    _entry(
        "candidate_apply_conflict",
        _D,
        {_S, _C},
        False,
        "Another field manager owns a field of the candidate's objects; the worker never forces.",
    ),
    _entry(
        "candidate_fence_ahead",
        _D,
        {_S, _C},
        False,
        "The candidate's objects carry a newer command's number.",
    ),
    _entry(
        "candidate_refused",
        _D,
        {_R, _S, _C},
        False,
        "The cluster refused the release's objects (validation or admission).",
    ),
    _entry(
        "candidate_not_ready",
        _D,
        {_S, _C},
        True,
        "The candidate was not ready before the attempt's deadline, or its Deployment disappeared.",
    ),
    _entry(
        "progress_deadline_exceeded",
        _D,
        {_S, _C},
        True,
        "The candidate's Deployment stopped progressing, with no more precise cause seen.",
    ),
    _entry(
        "insufficient_resources",
        _D,
        {_S, _C},
        True,
        "The candidate's pod cannot be scheduled: no node has the CPU or memory free.",
    ),
    _entry(
        "quota_exceeded",
        _D,
        {_S, _C},
        False,
        "A ResourceQuota of the tenant namespace refuses the candidate's pods.",
    ),
    _entry("image_pull_failed", _D, {_S, _C}, True, "The candidate's image could not be pulled."),
    _entry("crash_loop", _D, {_S, _C}, False, "The candidate's container keeps crashing."),
    _entry(
        "candidate_attempts_exhausted",
        _D,
        {_R, _C},
        False,
        "The release used all its candidate attempts.",
    ),
    _entry(
        "release_not_deployable",
        _D,
        {_C},
        False,
        "The release is not built with an image digest when the deploy runs.",
    ),
    _entry(
        "route_not_stable",
        _D,
        {_C},
        False,
        "The project's route was changing or uncertain when the deploy ran.",
    ),
    _entry(
        "hosting_offline",
        _D,
        {_C},
        False,
        "The project's hosting was offline when the deploy ran.",
    ),
    # Promotion and rollback (decision 0001 section 7, decision 0014 sections 13 and 17).
    _entry(
        "entitlement_lost",
        _P,
        {_C},
        False,
        "Payment refused the promotion on the recheck before the route moved.",
    ),
    _entry(
        "route_refused",
        _P,
        {_R, _C},
        False,
        "The cluster refused the route or the preview route.",
    ),
    _entry(
        "route_not_accepted",
        _P,
        {_R, _C},
        True,
        "The Gateway did not accept the route at the command's number in time.",
    ),
    _entry(
        "candidate_not_answering",
        _P,
        {_R, _C},
        True,
        "The candidate did not answer on its preview hostname with its release header in time.",
    ),
    _entry(
        "dependency_unavailable",
        _P,
        {_R, _C},
        True,
        "The candidate answered its preview probe with 503: a dependency it needs, such as the "
        "catalog, is down.",
    ),
    _entry(
        "site_not_answering",
        _P,
        {_R, _C},
        True,
        "After the switch, the site did not answer with the candidate's release header in time.",
    ),
    _entry(
        "promotion_interrupted",
        _P,
        {_R, _C},
        True,
        "The last go-live try was interrupted; the next claim restored the route and ended the "
        "promotion.",
    ),
    _entry(
        "route_restore_unconfirmed",
        _P,
        {_C},
        False,
        "The route could not be confirmed after a failed promotion or a rollback; it is uncertain"
        " until an operator rolls back.",
    ),
    _entry(
        "operator_rollback",
        FailureStage.ROLLBACK,
        {_R},
        False,
        "An operator rollback ended the promotion this release was in.",
    ),
    # Hosting commands from Payment's and Project's events (decision 0014 section 21).
    _entry(
        "entitlement_restored",
        _H,
        {_C},
        False,
        "Skipped: a later Payment event restored the entitlement before the suspend ran.",
    ),
    _entry(
        "entitlement_ended",
        _H,
        {_C},
        False,
        "Skipped: a later Payment event ended the entitlement before the resume ran.",
    ),
    _entry("hosting_already_offline", _H, {_C}, False, "Skipped: hosting was already offline."),
    _entry("hosting_already_online", _H, {_C}, False, "Skipped: hosting was already online."),
    _entry(
        "route_change_unconfirmed",
        _H,
        {_C},
        False,
        "The hosting change of the route could not be confirmed; it is uncertain until an "
        "operator rolls back.",
    ),
    # An admin's redeploy of the live release (decision 0023 G12).
    _entry(
        "redeploy_unready",
        _RD,
        {_C},
        False,
        "The restarted pods of the live release were not all ready by the command's deadline, or "
        "the cluster refused the restart; the rolling update keeps the old pods serving.",
    ),
    _entry(
        "redeploy_target_changed",
        _RD,
        {_C},
        False,
        "The live release was no longer the command's release when it ran; nothing was restarted.",
    ),
    # Any command (decision 0005 section 5, decision 0011 section 3).
    _entry(
        "deadline_exceeded",
        _A,
        {_R, _S, _C},
        True,
        "The command's or the attempt's deadline passed before it finished.",
    ),
    _entry(
        "attempts_exhausted",
        _A,
        {_R, _S, _C},
        True,
        "The command used its execution budget on transient failures.",
    ),
    _entry(
        "executor_failed",
        _A,
        {_C},
        False,
        "The worker met an unexpected error while executing the command; see the worker log.",
    ),
    _entry(
        "executor_unavailable",
        _A,
        {_C},
        False,
        "The worker has no executor for the command in this build.",
    ),
    _entry(
        "operation_unavailable",
        _A,
        {_C},
        False,
        "The worker does not enable the command's kind.",
    ),
)

OTHER_CODES: Final[tuple[OtherCode, ...]] = (
    OtherCode(
        "tenant_not_ready",
        "command_dispatch.last_failure_code",
        "The tenant namespace or the worker's access to it does not exist yet; the deploy is "
        "retried.",
    ),
    OtherCode(
        "cluster_unavailable",
        "command_dispatch.last_failure_code",
        "The API server was unreachable or slow; the deploy is retried.",
    ),
    OtherCode(
        "transient_failure",
        "command_dispatch.last_failure_code",
        "A transient failure released the claim; the command is retried.",
    ),
    OtherCode(
        "attempt_timeout",
        "command_dispatch.last_failure_code",
        "One execution attempt ran out of time; the command is retried.",
    ),
    OtherCode(
        "worker_stopping",
        "command_dispatch.last_failure_code",
        "The worker stopped and released its claim.",
    ),
    OtherCode(
        "repository_mismatch",
        "source_published_events.outcome_code",
        "The event names a repository other than the project's.",
    ),
    OtherCode(
        "repository_not_registered",
        "source_published_events.outcome_code",
        "Project does not know the event's repository.",
    ),
    OtherCode(
        "repository_unconfirmed",
        "source_published_events.outcome_code",
        "The repository could not be confirmed with Project.",
    ),
    OtherCode(
        "release_conflict",
        "source_published_events.outcome_code",
        "A release with the event's key exists for another source.",
    ),
    OtherCode(
        "cancel_failed",
        "audit event",
        "A build cancel did not reach the build system or was refused.",
    ),
    OtherCode(
        "route_reclaim_unconfirmed",
        "audit event",
        "An operator rollback's forced reclaim of the route was not confirmed.",
    ),
    OtherCode(
        "route_uncertain",
        "audit event",
        "The route was left uncertain; the alert's code when the command has none.",
    ),
    OtherCode(
        "publish_failed",
        "usage_outbox",
        "A usage event could not be published; it is retried.",
    ),
    OtherCode(
        "publish_unconfirmed",
        "usage_outbox",
        "The broker did not confirm a usage event; it is retried.",
    ),
)

_BY_CODE: Final = MappingProxyType({entry.code: entry for entry in FAILURE_CODES})


def failure_code_entry(code: str) -> FailureCode | None:
    """The catalogue entry of a business row's code, None for a code that is not listed."""
    return _BY_CODE.get(code)


def failure_retryable(code: str) -> bool:
    """The API's ``failure_retryable``: the entry's ``retryable``, False for a code that is not
    listed (a row written before the catalogue, or a code no path writes now)."""
    entry = _BY_CODE.get(code)
    return entry is not None and entry.retryable
