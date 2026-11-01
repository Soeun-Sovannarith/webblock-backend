"""The build attempt parameter types and the sweep outcomes (decision 0008 section 4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application import (
    BuildAttemptParams,
    BuildStepKey,
    StartBuildParams,
    SweepOutcome,
)
from provisioning.core.domain import (
    NIL_ID,
    BuildAttemptPolicy,
    DomainError,
    InvalidAttempt,
    InvalidId,
    MissingDeadline,
)

_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")

_KEY = BuildStepKey(project_id=_ID_PROJECT, release_id=_ID_RELEASE, step_id=_ID_STEP)


def test_build_step_key_validate_accepts_a_valid_key() -> None:
    _KEY.validate()


@pytest.mark.parametrize(
    "key",
    [
        replace(_KEY, project_id=NIL_ID),
        replace(_KEY, release_id=NIL_ID),
        replace(_KEY, step_id=NIL_ID),
    ],
    ids=["zero project", "zero release", "zero step"],
)
def test_build_step_key_validate(key: BuildStepKey) -> None:
    with pytest.raises(InvalidId):
        key.validate()


def test_start_build_params_validate() -> None:
    StartBuildParams(step=_KEY, duration=timedelta(minutes=1)).validate()
    # One microsecond, the precision of timestamptz, is the shortest duration.
    StartBuildParams(step=_KEY, duration=timedelta(microseconds=1)).validate()
    zero_key = BuildStepKey(project_id=NIL_ID, release_id=NIL_ID, step_id=NIL_ID)
    with pytest.raises(InvalidId):
        StartBuildParams(step=zero_key, duration=timedelta(minutes=1)).validate()


@pytest.mark.parametrize(
    "duration", [timedelta(0), timedelta(seconds=-1)], ids=["zero", "negative"]
)
def test_start_build_params_validate_duration(duration: timedelta) -> None:
    with pytest.raises(MissingDeadline):
        StartBuildParams(step=_KEY, duration=duration).validate()


def test_build_attempt_params_validate_accepts_valid_params() -> None:
    BuildAttemptParams(policy=BuildAttemptPolicy(max_attempts=3), next_step_id=_ID_STEP).validate()


@pytest.mark.parametrize(
    ("params", "want"),
    [
        (BuildAttemptParams(policy=BuildAttemptPolicy(0), next_step_id=_ID_STEP), InvalidAttempt),
        (BuildAttemptParams(policy=BuildAttemptPolicy(11), next_step_id=_ID_STEP), InvalidAttempt),
        (BuildAttemptParams(policy=BuildAttemptPolicy(3), next_step_id=NIL_ID), InvalidId),
    ],
    ids=["policy 0", "policy 11", "zero next ID"],
)
def test_build_attempt_params_validate(params: BuildAttemptParams, want: type[DomainError]) -> None:
    with pytest.raises(want):
        params.validate()


def test_sweep_outcomes() -> None:
    assert {o: str(o) for o in SweepOutcome} == {
        SweepOutcome.FAILED: "failed",
        SweepOutcome.BUSY: "busy",
        SweepOutcome.STALE: "stale",
    }
    assert len(SweepOutcome) == 3
