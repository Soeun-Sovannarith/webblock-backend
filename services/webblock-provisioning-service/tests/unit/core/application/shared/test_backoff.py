"""Bounded backoff with jitter (decision 0005 section 7)."""

from datetime import timedelta

import pytest

from provisioning.core.application.shared.backoff import Backoff, InvalidConfig

MS = timedelta(milliseconds=1)


@pytest.mark.parametrize(
    ("attempt", "cap"),
    [(-3, 10), (0, 10), (1, 10), (2, 20), (3, 40), (4, 80), (5, 100), (6, 100), (1000, 100)],
)
def test_the_delay_is_bounded_jittered_and_never_zero(attempt: int, cap: int) -> None:
    b = Backoff(base=10 * MS, max=100 * MS)
    seen = {b.delay(attempt) for _ in range(200)}
    for d in seen:
        assert cap * MS / 2 < d <= cap * MS
    assert len(seen) > 1, "the delay carries jitter"


def test_the_smallest_delay_is_one_microsecond() -> None:
    tiny = timedelta(microseconds=1)
    b = Backoff(base=tiny, max=tiny)
    assert {b.delay(n) for n in (1, 2, 50)} == {tiny}


def test_huge_caps_do_not_overflow() -> None:
    b = Backoff(base=timedelta(days=1), max=timedelta(days=999_999_999))
    d = b.delay(10_000)
    assert timedelta(days=999_999_999) / 2 < d <= timedelta(days=999_999_999)


@pytest.mark.parametrize(
    ("base", "cap"),
    [(timedelta(0), MS), (-MS, MS), (2 * MS, MS)],
)
def test_validate_refuses_a_bad_backoff(base: timedelta, cap: timedelta) -> None:
    with pytest.raises(InvalidConfig):
        Backoff(base=base, max=cap).validate()


def test_validate_accepts_equal_base_and_cap() -> None:
    Backoff(base=MS, max=MS).validate()
