"""Where a failure code is written and at which stage (cut from the failure-code catalogue, design
2026-10-28 section 4.2)."""

from __future__ import annotations

from enum import StrEnum


class FailurePlace(StrEnum):
    """Where a code can be recorded."""

    RELEASE = "release"  # releases.failure_code
    STEP = "step"  # release_steps.failure_code
    COMMAND = "command"  # commands.failure_code


class FailureStage(StrEnum):
    """The part of the lifecycle that records a code."""

    BUILD = "build"
    DEPLOY = "deploy"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    HOSTING = "hosting"
    REDEPLOY = "redeploy"
    ANY_COMMAND = "any command"
