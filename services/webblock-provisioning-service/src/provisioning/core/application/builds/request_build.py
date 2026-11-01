"""The API's build_release (decision 0016 section 5 item 2): it checks the release can start and
sends the build hint. At the feature root, not in background/, because the API host uses it
(decision 0025 CQ6; cut from start_build.py)."""

from __future__ import annotations

import logging
from uuid import UUID

from provisioning.core.application.builds.ports import BuildRequestStore, BuildTarget
from provisioning.core.application.operations.views import BuildDispatch, BuildStarted
from provisioning.core.application.shared.errors import BuildInProgress, ReleaseNotBuildable
from provisioning.core.domain import ReleaseStatus, ReleaseStep, ReleaseStepStatus


def _startable_step(target: BuildTarget) -> ReleaseStep:
    """The pending build step of an accepted release, or of a building release whose last attempt
    failed; ``BuildInProgress`` while an attempt runs; ``ReleaseNotBuildable`` otherwise."""
    release, step = target.release, target.step
    if release.status not in (ReleaseStatus.ACCEPTED, ReleaseStatus.BUILDING) or step is None:
        raise ReleaseNotBuildable(f"release {release.id} is {release.status}")
    if step.status == ReleaseStepStatus.RUNNING:
        raise BuildInProgress(f"build attempt {step.attempt} of release {release.id} is running")
    if step.status != ReleaseStepStatus.PENDING:
        raise ReleaseNotBuildable(f"release {release.id} has no pending build attempt")
    return step


class RequestBuild:
    """The API's build_release (decision 0016 section 5 item 2): records nothing, because the
    release's pending build step is already the durable record that an attempt is wanted. It
    refuses as ``StartBuild`` does (``NotFound``, ``BuildInProgress``, ``ReleaseNotBuildable``),
    then sends the hint. A hint that cannot be sent is logged without its error text; the request
    still succeeds, because the build worker's grace scan starts the attempt anyway. The result's
    dispatch is ``PENDING``: the build worker, not the API, hands the attempt to the build
    system."""

    __slots__ = ("_logger", "_store")

    def __init__(self, *, store: BuildRequestStore, logger: logging.Logger) -> None:
        self._store = store
        self._logger = logger

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        step = _startable_step(await self._store.load_build_target(project_id, release_id))
        try:
            await self._store.build_wanted(project_id, release_id)
        except Exception as exc:  # the pending step is the record; the grace scan starts it
            self._logger.warning(
                "build hint not sent; the build worker starts the attempt after its grace "
                "(project_id=%s release_id=%s error=%s)",
                project_id,
                release_id,
                type(exc).__name__,
            )
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=step.id,
            attempt=step.attempt,
            dispatch=BuildDispatch.PENDING,
        )
