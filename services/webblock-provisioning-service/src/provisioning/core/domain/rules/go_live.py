"""The go-live tries (FR-21, FR-22; TR-57; decision 0027 T1).

One ``promote`` command makes at most ``GO_LIVE_TRIES`` tries; try n is the command's claim n.
After a failed try 1 the next try waits 30 s, after try 2 it waits 60 s, and the last try has no
next one. These are code constants, not settings: FR-21 fixes the numbers, so TR-37's required
retry settings do not apply to them (TR-57). The try codes are defined here once; the routing job
imports them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from provisioning.core.domain.values.deploy_failures import CODE_DEPENDENCY_UNAVAILABLE

GO_LIVE_TRIES: Final = 3

# The codes a failed try ends with (decision 0001 section 9 format).
CODE_ROUTE_REFUSED: Final = "route_refused"
CODE_ROUTE_NOT_ACCEPTED: Final = "route_not_accepted"
CODE_CANDIDATE_NOT_ANSWERING: Final = "candidate_not_answering"
CODE_SITE_NOT_ANSWERING: Final = "site_not_answering"

GO_LIVE_TRY_CODES: Final[frozenset[str]] = frozenset(
    {
        CODE_ROUTE_REFUSED,
        CODE_ROUTE_NOT_ACCEPTED,
        CODE_CANDIDATE_NOT_ANSWERING,
        CODE_SITE_NOT_ANSWERING,
        CODE_DEPENDENCY_UNAVAILABLE,
    }
)

# The wait before try n + 1, by the number n of the try that failed.
_WAITS: Final = (timedelta(seconds=30), timedelta(seconds=60))


def wait_after_failed_try(try_number: int) -> timedelta | None:
    """The wait before the next try after try ``try_number`` failed: 30 s after try 1, 60 s after
    try 2, None from ``GO_LIVE_TRIES`` on (no next try). A number below 1 raises ``ValueError``."""
    if try_number < 1:
        raise ValueError(f"try number {try_number} is below 1")
    if try_number >= GO_LIVE_TRIES:
        return None
    return _WAITS[try_number - 1]
