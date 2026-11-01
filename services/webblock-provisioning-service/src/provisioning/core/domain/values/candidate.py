"""A candidate attempt's failure and the attempt limit (decision 0011 sections 3 and 12; value; cut
from the candidate rules, decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass

from provisioning.core.domain.enums.candidate import CandidateFailureEffect
from provisioning.core.domain.values.build import MAX_BUILD_ATTEMPTS

# The candidate attempts of one release over all its deploys (decision 0011 sections 6 and 12),
# like build attempts. 00010's insert guard refuses an eleventh candidate step.
MAX_CANDIDATE_ATTEMPTS = MAX_BUILD_ATTEMPTS


@dataclass(frozen=True, slots=True)
class CandidateFailure:
    """``code`` is what the command ends with (and the release, under RELEASE_FAILS), or the
    retry's code under RETRY: the attempt's own code, except ``candidate_attempts_exhausted`` when
    the last allowed attempt failed with a code that does not fail the release by itself."""

    effect: CandidateFailureEffect
    code: str
