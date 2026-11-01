"""A newer deploy returns an idle candidate to built (decision 0011 section 4; rule input and rule;
cut from the releases row module, decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from provisioning.core.domain.entities.command import Command
from provisioning.core.domain.entities.project_hosting import ProjectHosting
from provisioning.core.domain.entities.release import Release
from provisioning.core.domain.enums.states import CommandKind, CommandStatus, ReleaseStatus
from provisioning.core.domain.errors import IllegalTransition, InvalidCommandTarget, ProjectMismatch
from provisioning.core.domain.values.identifiers import check_fence
from provisioning.core.domain.values.release_transition import ReleaseTransition


@dataclass(frozen=True, slots=True)
class ReplacedCandidateInput:
    """Rule input. The rows ``return_replaced_candidate`` reads, loaded under the deploy's fence."""

    hosting: ProjectHosting
    command: Command
    release: Release


def return_replaced_candidate(inp: ReplacedCandidateInput, now: datetime) -> Release:
    """Return an idle candidate to built because a newer deploy of the same project replaces it
    (decision 0011 section 4). The command passes the fence (``StaleFence``), is a running deploy
    (``IllegalTransition``) naming a release (``InvalidCommandTarget``); the rows belong to one
    project (``ProjectMismatch``); the release is candidate_ready, not the deploy's own release
    and not the live release (``IllegalTransition``). It keeps its image digest."""
    cmd, release, hosting = inp.command, inp.release, inp.hosting
    check_fence(cmd.command_number, hosting.last_command_number)
    if cmd.kind != CommandKind.DEPLOY or cmd.status != CommandStatus.RUNNING:
        raise IllegalTransition(
            f"only a running deploy replaces a candidate, not a {cmd.status} {cmd.kind} command"
        )
    if cmd.release_id is None:
        raise InvalidCommandTarget(f"deploy command {cmd.id} names no release")
    if cmd.project_id != hosting.project_id or release.project_id != hosting.project_id:
        raise ProjectMismatch(f"command {cmd.id} and release {release.id}")
    if cmd.release_id == release.id:
        raise IllegalTransition(f"release {release.id} is the deploy's own release")
    if hosting.live_release_id is not None and hosting.live_release_id == release.id:
        raise IllegalTransition(f"release {release.id} is the project's live release")
    if release.status != ReleaseStatus.CANDIDATE_READY:
        raise IllegalTransition(f"release {release.id} {release.status} -> built")
    return release.transition_unchecked(ReleaseStatus.BUILT, ReleaseTransition(), now)
