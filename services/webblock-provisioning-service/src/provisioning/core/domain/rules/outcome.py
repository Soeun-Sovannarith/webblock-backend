"""Command outcomes: the rows each outcome commits together (decision 0001 section 7).
Every function checks the fence, that the command is running, that the rows
belong together and that the command's kind fits, and returns new rows; the input is never
changed."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from provisioning.core.domain.entities.command import Command
from provisioning.core.domain.entities.project_hosting import (
    ProjectHosting,
    legal_hosting_transition,
    legal_routing_transition,
)
from provisioning.core.domain.entities.release import Release
from provisioning.core.domain.enums.states import (
    CommandKind,
    CommandStatus,
    OfflineReason,
    ReleaseStatus,
    RoutingStatus,
)
from provisioning.core.domain.errors import (
    IllegalTransition,
    InvalidCommandTarget,
    InvalidFailureCode,
    InvalidFailureSummary,
    ProjectMismatch,
)
from provisioning.core.domain.rules.command_preconditions import (
    RELEASE_STATUS_FOR,
    check_live_release,
)
from provisioning.core.domain.values.deploy_failures import classify_deploy_failure
from provisioning.core.domain.values.failure import Failure
from provisioning.core.domain.values.hosting_reason import apply_hosting_reason, hosting_status_for
from provisioning.core.domain.values.identifiers import check_fence
from provisioning.core.domain.values.release_transition import ReleaseTransition


@dataclass(frozen=True, slots=True)
class OutcomeInput:
    """Rule input. The rows an outcome reads, loaded under the project row lock: the hosting row,
    the command, the command's release (None for a hosting kind), the live release (None when
    ``hosting.live_release_id`` is null), and the project's promoting release (None when it has
    none). Only an operator rollback reads ``promoting_release`` (decision 0014 section 13): it may
    name the live release while a promoting release is still recorded."""

    hosting: ProjectHosting
    command: Command
    release: Release | None = None
    live_release: Release | None = None
    promoting_release: Release | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """Rule result. The new values of every row an outcome commits together. ``release`` is None
    when the command's release is not written; ``superseded_release`` is the old live release when a
    release is confirmed live, written before ``release``."""

    hosting: ProjectHosting
    command: Command
    release: Release | None = None
    superseded_release: Release | None = None


def deploy_succeeded(inp: OutcomeInput, now: datetime) -> Outcome:
    """Release built -> candidate_ready, command succeeded; the route is untouched."""
    o = _Working.begin(inp, CommandKind.DEPLOY)
    o.require_routing(RoutingStatus.STABLE)
    o.move_release(ReleaseStatus.CANDIDATE_READY, ReleaseTransition(), now)
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


def fail_without_route_change(
    inp: OutcomeInput, failure: Failure, release_failed: bool, now: datetime
) -> Outcome:
    """End a running command failed before its work moved the route. With ``release_failed`` a
    deploy's release goes built -> failed (only with a code that ``classify_deploy_failure`` marks
    ``fails_release``) and a promote's release candidate_ready -> failed."""
    o = _Working.begin(inp, *CommandKind)
    kind = inp.command.kind
    routing = RoutingStatus.UNCERTAIN if kind == CommandKind.ROLLBACK else RoutingStatus.STABLE
    o.require_routing(routing)
    if kind in RELEASE_STATUS_FOR:  # a rollback names no single status (0014 section 13)
        o.require_release_status(RELEASE_STATUS_FOR[kind])
    if release_failed:
        if kind not in (CommandKind.DEPLOY, CommandKind.PROMOTE):
            raise IllegalTransition(
                f"a {kind} command does not fail its release before the route changes"
            )
        if kind == CommandKind.DEPLOY and not classify_deploy_failure(failure.code).fails_release:
            raise InvalidFailureCode(f"{failure.code} does not fail a deploy's release")
        o.move_release(
            ReleaseStatus.FAILED,
            ReleaseTransition(failure_code=failure.code, failure_summary=failure.summary),
            now,
        )
    else:
        _refuse_summary(failure)
    return o.finish_command(CommandStatus.FAILED, failure.code, now)


def skip_hosting_command(inp: OutcomeInput, failure: Failure, now: datetime) -> Outcome:
    """End a running hosting command skipped; the route is untouched."""
    o = _Working.begin(inp, CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)
    o.require_routing(RoutingStatus.STABLE)
    _refuse_summary(failure)
    return o.finish_command(CommandStatus.SKIPPED, failure.code, now)


def start_route_change(inp: OutcomeInput, now: datetime) -> Outcome:
    """The first fenced write of a command that moves the route; the command stays running."""
    o = _Working.begin(
        inp,
        CommandKind.PROMOTE,
        CommandKind.ROLLBACK,
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
    )
    kind = inp.command.kind
    if kind == CommandKind.PROMOTE:
        o.require_routing(RoutingStatus.STABLE)
        o.move_release(ReleaseStatus.PROMOTING, ReleaseTransition(), now)
    elif kind == CommandKind.ROLLBACK:
        # Any uncertain route, with or without a promoting release (decision 0014 section 13).
        o.require_routing(RoutingStatus.UNCERTAIN)
    else:
        o.require_routing(RoutingStatus.STABLE)
        target = hosting_status_for(_reasons_after(o))
        if not legal_hosting_transition(inp.hosting.hosting_status, target):
            raise IllegalTransition(f"{kind} while hosting is {inp.hosting.hosting_status}")
    o.move_routing(RoutingStatus.CHANGING, now)
    return o.outcome()


def confirm_live(inp: OutcomeInput, now: datetime) -> Outcome:
    """A promotion confirmed live: the old live release superseded, this release live, the
    project's live release set to it, the route stable, the command succeeded."""
    o = _Working.begin(inp, CommandKind.PROMOTE)
    o.require_routing(RoutingStatus.CHANGING)
    release = o.release()
    if inp.live_release is not None:
        o.superseded_release = inp.live_release.transition_unchecked(
            ReleaseStatus.SUPERSEDED, ReleaseTransition(newer_live_release_id=release.id), now
        )
    o.move_release(ReleaseStatus.LIVE, ReleaseTransition(), now)
    o.hosting = replace(o.hosting, live_release_id=release.id)
    o.move_routing(RoutingStatus.STABLE, now)
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


def restore_verified(inp: OutcomeInput, failure: Failure, now: datetime) -> Outcome:
    """A verified restore of the route to the live release: the promoting release rolled_back,
    the route stable. A promote ends failed; an operator rollback ends succeeded."""
    o = _Working.begin(inp, CommandKind.PROMOTE, CommandKind.ROLLBACK)
    o.require_routing(RoutingStatus.CHANGING)
    return _route_restored(o, inp, ReleaseStatus.ROLLED_BACK, failure, now)


def route_removed_verified(inp: OutcomeInput, failure: Failure, now: datetime) -> Outcome:
    """A route removed and verified when the project has no live release: the promoting release
    failed, the route stable. A promote ends failed; an operator rollback ends succeeded."""
    o = _Working.begin(inp, CommandKind.PROMOTE, CommandKind.ROLLBACK)
    o.require_routing(RoutingStatus.CHANGING)
    # built -> failed and candidate_ready -> failed are legal release transitions, so the status
    # this event fits is checked explicitly.
    o.require_release_status(ReleaseStatus.PROMOTING)
    return _route_restored(o, inp, ReleaseStatus.FAILED, failure, now)


def _route_restored(
    o: _Working, inp: OutcomeInput, status: ReleaseStatus, failure: Failure, now: datetime
) -> Outcome:
    """The end of a verified restore: the promoting release moved to ``status`` with ``failure``,
    the route stable. A promote ends failed; an operator rollback ends succeeded."""
    o.move_release(
        status,
        ReleaseTransition(failure_code=failure.code, failure_summary=failure.summary),
        now,
    )
    o.move_routing(RoutingStatus.STABLE, now)
    if inp.command.kind == CommandKind.ROLLBACK:
        return o.finish_command(CommandStatus.SUCCEEDED, "", now)
    return o.finish_command(CommandStatus.FAILED, failure.code, now)


def route_uncertain(inp: OutcomeInput, failure: Failure, now: datetime) -> Outcome:
    """A restore that failed or is unknown: the route uncertain and the command failed."""
    o = _Working.begin(
        inp,
        CommandKind.PROMOTE,
        CommandKind.ROLLBACK,
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
    )
    o.require_routing(RoutingStatus.CHANGING)
    if inp.command.kind == CommandKind.PROMOTE:
        o.require_release_status(ReleaseStatus.PROMOTING)
    _refuse_summary(failure)
    o.move_routing(RoutingStatus.UNCERTAIN, now)
    return o.finish_command(CommandStatus.FAILED, failure.code, now)


def rollback_verified(inp: OutcomeInput, failure: Failure, now: datetime) -> Outcome:
    """An operator rollback whose route is confirmed where the recorded state says (decision 0014
    section 13): the route stable and the command succeeded; the project's promoting release, when
    one is recorded (the command's release, or ``inp.promoting_release``), becomes rolled_back
    with ``failure``, or failed when the project has no live release (decision 0001 section 2).
    The hosting status and the live release are unchanged. ``Outcome.release`` is the release
    written, which may not be the command's own."""
    o = _Working.begin(inp, CommandKind.ROLLBACK)
    o.require_routing(RoutingStatus.CHANGING)
    promoting = _promoting_of(inp)
    if promoting is not None:
        to = ReleaseStatus.ROLLED_BACK if inp.hosting.live_release_id else ReleaseStatus.FAILED
        o.release_out = promoting.transition_unchecked(
            to,
            ReleaseTransition(
                failure_code=failure.code,
                failure_summary=failure.summary,
                live_release_id=inp.hosting.live_release_id,
            ),
            now,
        )
    o.move_routing(RoutingStatus.STABLE, now)
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


def hosting_verified(inp: OutcomeInput, now: datetime) -> Outcome:
    """A verified suspend or resume, the route stable and the command succeeded. The command's
    reason is added (suspend) or removed (resume) and the hosting status follows from the reasons
    left; it must differ from the current one (online -> offline or offline -> online)."""
    o = _Working.begin(inp, CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)
    o.require_routing(RoutingStatus.CHANGING)
    reasons = _reasons_after(o)
    to = hosting_status_for(reasons)
    if not legal_hosting_transition(o.hosting.hosting_status, to):
        raise IllegalTransition(
            f"project {o.hosting.project_id} hosting {o.hosting.hosting_status} -> {to}"
        )
    o.hosting = replace(o.hosting, hosting_status=to, offline_reasons=reasons)
    o.move_routing(RoutingStatus.STABLE, now)
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


def record_hosting_reason(inp: OutcomeInput, now: datetime) -> Outcome:
    """A suspend or resume that only records its reason, with no route change (decision 0021 D6
    and D7): the route stays stable and untouched, the reason is added or removed and the command
    succeeded. The hosting status follows from the reasons left; it may differ from the current
    one only when the project has no live release (never routed), else it must be unchanged."""
    o = _Working.begin(inp, CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)
    o.require_routing(RoutingStatus.STABLE)
    reasons = _reasons_after(o)
    to = hosting_status_for(reasons)
    if to != o.hosting.hosting_status and o.hosting.live_release_id is not None:
        raise IllegalTransition(
            f"project {o.hosting.project_id} hosting {o.hosting.hosting_status} -> {to} "
            "needs a route change"
        )
    o.hosting = replace(o.hosting, hosting_status=to, offline_reasons=reasons, updated_at=now)
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


def redeploy_succeeded(inp: OutcomeInput, now: datetime) -> Outcome:
    """A redeploy whose restarted workload is ready (decision 0023 G12): the command succeeded.
    The route, ``routing_status``, the hosting row and every release are untouched. Only the live
    release is ever redeployed: a command whose release is not the live one, or a route that is
    not stable, is ``IllegalTransition``."""
    o = _Working.begin(inp, CommandKind.REDEPLOY)
    o.require_routing(RoutingStatus.STABLE)
    release = o.release()
    if release.status != ReleaseStatus.LIVE or inp.hosting.live_release_id != release.id:
        raise IllegalTransition(f"redeploy of release {release.id}, which is not the live release")
    return o.finish_command(CommandStatus.SUCCEEDED, "", now)


class _Working:
    """The working copy every outcome function builds on, after the checks every outcome shares:
    the fence, the command running, the rows belonging together, and the kind fitting."""

    def __init__(self, inp: OutcomeInput) -> None:
        self.inp = inp
        self.hosting = inp.hosting
        self.command = inp.command
        self.release_out: Release | None = None
        self.superseded_release: Release | None = None

    @classmethod
    def begin(cls, inp: OutcomeInput, *kinds: CommandKind) -> _Working:
        cmd, hosting = inp.command, inp.hosting
        check_fence(cmd.command_number, hosting.last_command_number)
        if cmd.status != CommandStatus.RUNNING:
            raise IllegalTransition(f"command {cmd.id} is {cmd.status}, not running")
        if (
            cmd.project_id != hosting.project_id
            or (inp.release is not None and inp.release.project_id != hosting.project_id)
            or (inp.live_release is not None and inp.live_release.project_id != hosting.project_id)
        ):
            raise ProjectMismatch(f"command {cmd.id}")
        hosting_kind = cmd.kind.names_no_release()
        if hosting_kind != (inp.release is None) or (
            inp.release is not None and cmd.release_id != inp.release.id
        ):
            raise InvalidCommandTarget(f"release passed for command {cmd.id}")
        check_live_release(hosting, inp.live_release)
        promoting = inp.promoting_release
        if promoting is not None:
            if promoting.project_id != hosting.project_id:
                raise ProjectMismatch(f"promoting release {promoting.id}")
            if promoting.status != ReleaseStatus.PROMOTING:
                raise IllegalTransition(f"release {promoting.id} is {promoting.status}")
        if cmd.kind not in kinds:
            raise IllegalTransition(f"outcome does not fit a {cmd.kind} command")
        return cls(inp)

    def release(self) -> Release:
        if self.inp.release is None:  # begin() refuses it for every kind that names a release
            raise InvalidCommandTarget(f"command {self.command.id} names no release")
        return self.inp.release

    def require_routing(self, want: RoutingStatus) -> None:
        if self.inp.hosting.routing_status != want:
            raise IllegalTransition(
                f"{self.command.kind} outcome while routing is {self.inp.hosting.routing_status}"
            )

    def require_release_status(self, want: ReleaseStatus) -> None:
        release = self.release()
        if release.status != want:
            raise IllegalTransition(
                f"{self.command.kind} outcome on release {release.id} in status {release.status}"
            )

    def move_release(self, to: ReleaseStatus, inp: ReleaseTransition, now: datetime) -> None:
        self.release_out = self.release().transition_unchecked(
            to, replace(inp, live_release_id=self.inp.hosting.live_release_id), now
        )

    def move_routing(self, to: RoutingStatus, now: datetime) -> None:
        if not legal_routing_transition(self.hosting.routing_status, to):
            raise IllegalTransition(
                f"project {self.hosting.project_id} routing {self.hosting.routing_status} -> {to}"
            )
        self.hosting = replace(self.hosting, routing_status=to, updated_at=now)

    def outcome(self) -> Outcome:
        return Outcome(
            hosting=self.hosting,
            command=self.command,
            release=self.release_out,
            superseded_release=self.superseded_release,
        )

    def finish_command(self, to: CommandStatus, code: str, now: datetime) -> Outcome:
        self.command = self.command.transition(to, code, now)
        return self.outcome()


def _promoting_of(inp: OutcomeInput) -> Release | None:
    """The project's promoting release: the command's own when it is promoting, else
    ``inp.promoting_release``. Two different promoting releases are ``IllegalTransition``."""
    own = (
        inp.release
        if inp.release is not None and inp.release.status == ReleaseStatus.PROMOTING
        else None
    )
    other = inp.promoting_release
    if own is not None and other is not None and own.id != other.id:
        raise IllegalTransition(f"releases {own.id} and {other.id} are both promoting")
    return own if own is not None else other


def _refuse_summary(failure: Failure) -> None:
    """A command has no summary column: a summary where no release is written is refused."""
    if failure.summary != "":
        raise InvalidFailureSummary("no release is written, and a command carries no summary")


def _reasons_after(o: _Working) -> frozenset[OfflineReason]:
    """The hosting row's reasons after the running hosting command's own reason is applied."""
    reason = o.command.hosting_reason
    if reason is None:
        raise InvalidCommandTarget(f"command {o.command.id} has no offline reason")
    return apply_hosting_reason(o.hosting.offline_reasons, o.command.kind, reason)
