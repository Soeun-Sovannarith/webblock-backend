"""FR-21, TR-57 (design G3): the go-live rule. One promote makes at most 3 tries; the wait after
a failed try 1 is 30 s, after try 2 60 s, and the last try has none. The try codes are defined
once."""

from __future__ import annotations

from datetime import timedelta

import pytest

from provisioning.core.domain import (
    CODE_CANDIDATE_NOT_ANSWERING,
    CODE_DEPENDENCY_UNAVAILABLE,
    CODE_ROUTE_NOT_ACCEPTED,
    CODE_ROUTE_REFUSED,
    CODE_SITE_NOT_ANSWERING,
    GO_LIVE_TRIES,
    GO_LIVE_TRY_CODES,
    FailurePlace,
    FailureStage,
    failure_code_entry,
    wait_after_failed_try,
)


def test_tr57_three_tries() -> None:
    """TR-57: one promote command makes at most 3 tries (FR-21)."""
    assert GO_LIVE_TRIES == 3


def test_fr21_2_the_wait_after_try_1_is_30_s() -> None:
    """FR-21.2: try 2 starts no sooner than 30 s after try 1 failed."""
    assert wait_after_failed_try(1) == timedelta(seconds=30)


def test_fr21_4_the_wait_after_try_2_is_60_s() -> None:
    """FR-21.4: try 3 starts no sooner than 60 s after try 2 failed."""
    assert wait_after_failed_try(2) == timedelta(seconds=60)


@pytest.mark.parametrize("try_number", [3, 4])
def test_tr57_no_wait_after_the_last_try(try_number: int) -> None:
    """TR-57: try 3 is the last; from it on there is no next try, so no wait."""
    assert try_number >= GO_LIVE_TRIES
    assert wait_after_failed_try(try_number) is None


@pytest.mark.parametrize("try_number", [0, -1])
def test_tr57_a_try_number_below_1_is_refused(try_number: int) -> None:
    """TR-57: try n is claim n, which starts at 1; a smaller number is a bug, never a wait."""
    with pytest.raises(ValueError, match="try number"):
        wait_after_failed_try(try_number)


def test_tr57_the_try_codes_are_five_release_and_command_codes() -> None:
    """TR-57: the codes a failed try ends with are the four route and answer codes and
    ``dependency_unavailable``; each is a catalogued promote code of the release and the command."""
    assert GO_LIVE_TRY_CODES == frozenset(
        {
            CODE_ROUTE_REFUSED,
            CODE_ROUTE_NOT_ACCEPTED,
            CODE_CANDIDATE_NOT_ANSWERING,
            CODE_SITE_NOT_ANSWERING,
            CODE_DEPENDENCY_UNAVAILABLE,
        }
    )
    assert len(GO_LIVE_TRY_CODES) == 5
    for code in GO_LIVE_TRY_CODES:
        entry = failure_code_entry(code)
        assert entry is not None, code
        assert entry.stage is FailureStage.PROMOTE, code
        assert entry.places == frozenset({FailurePlace.RELEASE, FailurePlace.COMMAND}), code


def test_tr57_the_try_codes_keep_their_values() -> None:
    """TR-57: the moved constants keep the strings rows and audits already hold."""
    assert CODE_ROUTE_REFUSED == "route_refused"
    assert CODE_ROUTE_NOT_ACCEPTED == "route_not_accepted"
    assert CODE_CANDIDATE_NOT_ANSWERING == "candidate_not_answering"
    assert CODE_SITE_NOT_ANSWERING == "site_not_answering"


def test_tr57_every_try_but_the_last_has_a_wait_and_the_waits_are_the_frs() -> None:
    """TR-57: ``GO_LIVE_TRIES`` and the table of waits stay in step. Each try below the last has a
    positive wait (more tries than waits raises here), and those waits are exactly FR-21's 30 s
    and 60 s (fewer tries than waits leaves one out and fails the list)."""
    waits = [wait_after_failed_try(n) for n in range(1, GO_LIVE_TRIES)]
    assert waits == [timedelta(seconds=30), timedelta(seconds=60)]
    assert all(wait is not None and wait > timedelta(0) for wait in waits)
    assert wait_after_failed_try(GO_LIVE_TRIES) is None
