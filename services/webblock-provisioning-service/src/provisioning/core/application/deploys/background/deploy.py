"""DeployCandidate: the deploy command's use case, run by the worker for a claimed ``deploy``
command (decisions 0011 sections 3, 4 and 12, 0013 section 3, 0014 sections 3 and 4).

It turns a built release into a ready candidate, Deployment and Service ``rel-<releaseId>`` in the
project's tenant namespace, without touching the route:

1. Load the command's rows under its fence and check them again: the release is built with its
   digest, the route is stable and hosting is online. A refusal here makes no cluster call and is a
   ``PermanentFailure`` for the consumer to write.
2. Begin the candidate attempt in SQL (a ``candidate`` step running, with a deadline): the durable
   intent. A release with no attempt left is failed with its command in that write instead. The
   command's own attempt from an earlier claim is resumed, not repeated.
3. Return the project's other ``candidate_ready`` releases to built and delete their objects
   (decision 0011 section 4; best effort).
4. Outside any transaction, apply the workload at the command's number.
5. Poll ``release_status`` until READY, FAILED or the attempt's deadline, renewing the claim before
   every poll. The consumer's claim heartbeat runs beside the use case as well.
6. READY: the step succeeds, the release becomes ``candidate_ready``, the command succeeds.
   FAILED or the deadline: the candidate's objects are deleted (guarded: never the routed
   release), then the step fails and the command and the release follow the domain's
   classification (``fail_candidate_attempt``).

Precise causes (task H5a, decision 0015 section 4 step 1). A FAILED status names its code
(``insufficient_resources`` and ``quota_exceeded`` among them). A PROGRESSING status may carry one
of those two as a diagnosis: it never ends the attempt early, and the latest diagnosis read is the
code when the attempt reaches its own deadline, in place of ``candidate_not_ready``. An unreadable
status keeps the last diagnosis; a read without one clears it.

Errors: ``TenantNotReady`` and ``ClusterUnavailable`` on apply raise ``RetryLater`` with no wait
of its own (the generic backoff) and write nothing (the attempt stays running and the next claim
resumes it). ``ClusterRefused``, ``ApplyConflict`` and ``FenceRejected`` end the attempt with
their codes. A lost claim
(``ClaimLost``) or a stale fence (``StaleFence``) from any store call stops the run at once,
with nothing more written to SQL or to the cluster. No SQL transaction is open across a cluster
call: each store method is its own transaction.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

from provisioning.core.application.deploys.ports import (
    CandidateAttemptStart,
    DeployFacts,
    DeployStore,
    Pauser,
)
from provisioning.core.application.jobs.claims import PermanentFailure, RetryLater
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.dependencies import Clock, IdSource
from provisioning.core.application.ports.site_cluster import (
    DELETE_ERRORS,
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    SiteCluster,
    TenantNotReady,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.domain import (
    CODE_CANDIDATE_APPLY_CONFLICT,
    CODE_CANDIDATE_FENCE_AHEAD,
    CODE_CANDIDATE_NOT_READY,
    CODE_CANDIDATE_REFUSED,
    CODE_CLUSTER_UNAVAILABLE,
    CODE_CRASH_LOOP,
    CODE_DEADLINE_EXCEEDED,
    CODE_HOSTING_OFFLINE,
    CODE_IMAGE_PULL_FAILED,
    CODE_INSUFFICIENT_RESOURCES,
    CODE_PROGRESS_DEADLINE_EXCEEDED,
    CODE_QUOTA_EXCEEDED,
    CODE_RELEASE_NOT_DEPLOYABLE,
    CODE_ROUTE_NOT_STABLE,
    CODE_TENANT_NOT_READY,
    CandidateFailureEffect,
    CandidatePlanKind,
    CommandKind,
    HostingStatus,
    InvalidCommandTarget,
    ProjectMismatch,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepStatus,
    RoutingStatus,
    classify_deploy_failure,
)


@dataclass(frozen=True, slots=True)
class DeployParams:
    """The deploy's configured limits; every value is required (OD-15).

    - ``image_registry``: ``<registry>`` of ``<registry>/webblock/sites/<projectId>``.
    - ``ready_timeout``: how long one attempt waits for readiness.
    - ``poll_interval``: the pause between two status reads, at most a third of
      ``ready_timeout``.
    - ``call_timeout``: bounds each apply and each status read.
    - ``delete_timeout``: bounds one guarded delete.
    - ``finish_margin``: how long before the command's deadline the deploy stops waiting, so that
      the delete and the outcome fit.
    - ``claim_ttl``: what each claim renewal extends the claim by.
    """

    image_registry: str
    ready_timeout: timedelta
    poll_interval: timedelta
    call_timeout: timedelta
    delete_timeout: timedelta
    finish_margin: timedelta
    claim_ttl: timedelta

    def validate(self) -> None:
        """Refuse a missing or out-of-range value (``ValueError``; the message names the field,
        never its value)."""
        registry = self.image_registry
        if registry == "" or registry.endswith("/") or "://" in registry:
            raise ValueError("deploy: the image registry is required, without a scheme or a slash")
        for name in ("ready_timeout", "poll_interval", "call_timeout", "delete_timeout"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"deploy: {name} must be positive")
        for name in ("finish_margin", "claim_ttl"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"deploy: {name} must be positive")
        if self.poll_interval * 3 > self.ready_timeout:
            raise ValueError("deploy: poll_interval must be at most a third of ready_timeout")


def site_image(image_registry: str, project_id: UUID, digest: str) -> str:
    """``<registry>/webblock/sites/<projectId>@<digest>``: a release's image, from configuration
    and the release row only (decision 0009 section 3). The reconciler re-applies the live
    release with the same image the deploy applied."""
    return f"{image_registry}/webblock/sites/{project_id}@{digest}"


class DeployCandidate:
    """The deploy use case (module docstring)."""

    __slots__ = ("_clock", "_cluster", "_ids", "_logger", "_params", "_pauser", "_store")

    def __init__(
        self,
        *,
        store: DeployStore,
        cluster: SiteCluster,
        clock: Clock,
        ids: IdSource,
        pauser: Pauser,
        params: DeployParams,
        logger: logging.Logger,
    ) -> None:
        params.validate()
        self._store = store
        self._cluster = cluster
        self._clock = clock
        self._ids = ids
        self._pauser = pauser
        self._params = params
        self._logger = logger

    def image(self, project_id: UUID, digest: str) -> str:
        """``<registry>/webblock/sites/<projectId>@<digest>``, from configuration and the release
        row only (decision 0009 section 3)."""
        return site_image(self._params.image_registry, project_id, digest)

    async def run(self, fence: Fence, deadline_at: datetime) -> None:
        """Execute one claimed deploy whose command ends at ``deadline_at``. Returns when the run's
        own write ended the command. Raises ``PermanentFailure`` for a refusal before the attempt
        (the consumer ends the command), ``RetryLater`` for a transient failure, and the
        store's ``StaleFence``, ``ClaimLost`` or ``IllegalTransition`` unchanged."""
        fence.validate()
        facts = await self._store.load_deploy_facts(fence)
        release = _check_facts(fence, facts)
        own_deadline = deadline_at - self._params.finish_margin
        now = self._clock.now()
        if now >= own_deadline:
            # QA-05: an attempt an earlier claim left is cleaned up as ``abandon`` does (its
            # objects deleted, its step failed, which ends the command), so a refusal inside the
            # finish margin never leaves the candidate's objects or a running step behind.
            await self._clean_up(fence, facts, CODE_DEADLINE_EXCEEDED)
            raise PermanentFailure(CODE_DEADLINE_EXCEEDED)
        timeout = min(self._params.ready_timeout, own_deadline - now)
        attempt = await self._store.begin_candidate_attempt(
            fence, CandidateAttemptStart(step_id=self._ids.new_id(), timeout=timeout)
        )
        if attempt.kind is CandidatePlanKind.EXHAUSTED or attempt.step is None:
            self._logger.info(
                "release %s of project %s failed: no candidate attempt left (command %s)",
                release.id,
                fence.project_id,
                fence.command_id,
            )
            return
        step = attempt.step
        for replaced in await self._store.return_replaced_candidates(fence):
            self._logger.info(
                "candidate %s of project %s returned to built by command %s",
                replaced,
                fence.project_id,
                fence.command_id,
            )
            await self._delete(fence, replaced)
        if release.container_image_digest is None:  # checked by _check_facts; for the type
            raise PermanentFailure(CODE_RELEASE_NOT_DEPLOYABLE)
        workload = ReleaseWorkload(
            project_id=fence.project_id,
            release_id=release.id,
            image=self.image(fence.project_id, release.container_image_digest),
            command_number=fence.command_number,
        )
        code = await self._apply_and_wait(fence, workload, step, own_deadline)
        await self._end(fence, step, code)

    async def abandon(self, fence: Fence, code: str) -> None:
        """The consumer's hook before it fails a deploy command for its deadline or its execution
        budget (decision 0011 section 3): delete the candidate's objects and end an attempt an
        earlier claim left unfinished with ``code`` (a listed deploy failure code), which ends
        the command as the domain says. Nothing to do when no attempt is unfinished."""
        fence.validate()
        classify_deploy_failure(code)
        await self._clean_up(fence, await self._store.load_deploy_facts(fence), code)

    async def _clean_up(self, fence: Fence, facts: DeployFacts, code: str) -> None:
        """``abandon`` on the rows already loaded: delete the candidate's objects and end the
        attempt an earlier claim left unfinished, if there is one."""
        release = facts.release
        last = facts.last_candidate_step
        if (
            release.status is not ReleaseStatus.BUILT
            or last is None
            or last.status not in (ReleaseStepStatus.PENDING, ReleaseStepStatus.RUNNING)
        ):
            return
        await self._delete(fence, release.id)
        await self._end(fence, last, code)

    async def _apply_and_wait(
        self, fence: Fence, workload: ReleaseWorkload, step: ReleaseStep, own_deadline: datetime
    ) -> str | None:
        """Apply the workload and wait for it. None when it is ready; otherwise the failure code
        the attempt ends with, after the candidate's objects were deleted where that applies."""
        call = self._params.call_timeout.total_seconds()
        await self._store.renew_claim(fence, self._params.claim_ttl)
        try:
            async with asyncio.timeout(call):
                await self._cluster.apply_release(workload)
        except TenantNotReady:
            self._retry_later(fence, CODE_TENANT_NOT_READY)
        except ClusterUnavailable, TimeoutError:
            self._retry_later(fence, CODE_CLUSTER_UNAVAILABLE)
        except FenceRejected:
            return CODE_CANDIDATE_FENCE_AHEAD  # a newer command owns the objects: no delete
        except ApplyConflict:
            return await self._deleted(fence, workload, CODE_CANDIDATE_APPLY_CONFLICT)
        except ClusterRefused:
            return await self._deleted(fence, workload, CODE_CANDIDATE_REFUSED)

        step_deadline = step.deadline_at if step.deadline_at is not None else own_deadline
        ready_end = min(step_deadline, own_deadline)
        end_code = (
            CODE_CANDIDATE_NOT_READY if step_deadline <= own_deadline else CODE_DEADLINE_EXCEEDED
        )
        diagnosis: str | None = None
        while True:
            await self._store.renew_claim(fence, self._params.claim_ttl)
            status: WorkloadStatus | None = None
            try:
                async with asyncio.timeout(call):
                    status = await self._cluster.release_status(workload)
            except FenceRejected:
                return CODE_CANDIDATE_FENCE_AHEAD
            except ClusterUnavailable, TenantNotReady, TimeoutError:
                status = None  # polled through; the attempt's deadline bounds it
            if status is not None:
                if status.state is WorkloadState.READY:
                    return None
                if status.state is WorkloadState.FAILED:
                    return await self._deleted(fence, workload, _failure_code(status.reason))
                if status.state is WorkloadState.MISSING:
                    return await self._deleted(fence, workload, CODE_CANDIDATE_NOT_READY)
                diagnosis = status.reason if status.reason in _DIAGNOSES else None
            left = ready_end - self._clock.now()
            if left <= timedelta(0):
                if end_code == CODE_CANDIDATE_NOT_READY and diagnosis is not None:
                    end_code = diagnosis
                return await self._deleted(fence, workload, end_code)
            await self._pauser.pause(min(self._params.poll_interval, left))

    def _retry_later(self, fence: Fence, code: str) -> NoReturn:
        self._logger.warning(
            "deploy command %s of project %s retried later: %s",
            fence.command_id,
            fence.project_id,
            code,
        )
        raise RetryLater(code) from None

    async def _deleted(self, fence: Fence, workload: ReleaseWorkload, code: str) -> str:
        await self._delete(fence, workload.release_id)
        return code

    async def _delete(self, fence: Fence, release_id: UUID) -> None:
        """The guarded delete of a candidate's objects, after a claim renewal (a lost claim stops
        here). Best effort: a failure is logged by type and the objects stay for the next
        deploy's apply or for an operator."""
        await self._store.renew_claim(fence, self._params.claim_ttl)
        try:
            async with asyncio.timeout(self._params.delete_timeout.total_seconds()):
                await self._cluster.delete_release(
                    fence.project_id, release_id, fence.command_number
                )
        except DELETE_ERRORS as exc:
            self._logger.warning(
                "candidate %s of project %s not deleted by command %s: %s",
                release_id,
                fence.project_id,
                fence.command_id,
                type(exc).__name__,
            )

    async def _end(self, fence: Fence, step: ReleaseStep, code: str | None) -> None:
        failure = await self._store.end_candidate_attempt(fence, step.id, code)
        self._logger.info(
            "candidate attempt %d of command %s of project %s ended: %s",
            step.attempt,
            fence.command_id,
            fence.project_id,
            "ready" if failure is None else f"{failure.effect} {failure.code}",
        )
        if failure is not None and failure.effect is CandidateFailureEffect.RETRY:
            raise RetryLater(failure.code)


def _check_facts(fence: Fence, facts: DeployFacts) -> Release:
    """The rows belong to the fence (else internal errors), and the deploy can run: the release
    is built with its digest, the route stable and hosting online (else ``PermanentFailure``)."""
    command, release, hosting = facts.command, facts.release, facts.hosting
    if (
        command.id != fence.command_id
        or command.project_id != fence.project_id
        or release.project_id != fence.project_id
        or hosting.project_id != fence.project_id
    ):
        raise ProjectMismatch(f"deploy command {fence.command_id}: rows of another project")
    if command.kind is not CommandKind.DEPLOY or command.release_id != release.id:
        raise InvalidCommandTarget(f"command {command.id} is not a deploy of release {release.id}")
    if release.status is not ReleaseStatus.BUILT or release.container_image_digest is None:
        raise PermanentFailure(CODE_RELEASE_NOT_DEPLOYABLE)
    if hosting.routing_status is not RoutingStatus.STABLE:
        raise PermanentFailure(CODE_ROUTE_NOT_STABLE)
    if hosting.hosting_status is not HostingStatus.ONLINE:
        raise PermanentFailure(CODE_HOSTING_OFFLINE)
    return release


# The FAILED reasons of SiteCluster.release_status that are codes of their own.
_WORKLOAD_FAILURES = frozenset(
    {
        CODE_IMAGE_PULL_FAILED,
        CODE_CRASH_LOOP,
        CODE_PROGRESS_DEADLINE_EXCEEDED,
        CODE_INSUFFICIENT_RESOURCES,
        CODE_QUOTA_EXCEEDED,
    }
)
# The PROGRESSING diagnoses: a precise cause the attempt ends with if it reaches its deadline.
_DIAGNOSES = frozenset({CODE_INSUFFICIENT_RESOURCES, CODE_QUOTA_EXCEEDED})


def _failure_code(reason: str | None) -> str:
    """The workload's failure reason when it is one of the listed reasons; any other reason, or
    none, is ``candidate_not_ready``. The reason is never stored as free text."""
    if reason is not None and reason in _WORKLOAD_FAILURES:
        return reason
    return CODE_CANDIDATE_NOT_READY
