"""The labels of the candidate rules: a failure's effect and the planned attempt's kind (cut from
the candidate rules, decision 0025 CQ1)."""

from __future__ import annotations

from enum import StrEnum


class CandidateFailureEffect(StrEnum):
    """What a failed candidate attempt does to its command and release."""

    RETRY = "retry"  # a transient code: the command stays running and is retried
    COMMAND_FAILS = "command_fails"  # the command ends failed; the release stays built
    RELEASE_FAILS = "release_fails"  # the command ends failed and the release built -> failed


class CandidatePlanKind(StrEnum):
    START = "start"  # insert ``step`` (pending) and start it
    RESUME = "resume"  # ``step`` is a running attempt whose deadline has not passed: go on with it
    EXHAUSTED = "exhausted"  # no attempt is left: the command and the release fail
