"""Command numbers (revision 19)."""

from __future__ import annotations

import pytest

from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    CommandNumberOverflow,
    InvalidCommandNumber,
    StaleFence,
    check_fence,
    is_error,
    next_command_number,
    validate_command_number,
    validate_last_command_number,
)

_INT64_MAX = (1 << 63) - 1
_INT64_MIN = -(1 << 63)


def test_max_generation_is_2_pow_53_minus_1() -> None:
    assert MAX_COMMAND_NUMBER == 9007199254740991


@pytest.mark.parametrize(
    ("current", "want"),
    [(0, 1), (1, 2), (MAX_COMMAND_NUMBER - 1, MAX_COMMAND_NUMBER)],
    ids=["0", "1", "max-1"],
)
def test_next_generation(current: int, want: int) -> None:
    assert next_command_number(current) == want


def test_next_generation_overflow() -> None:
    with pytest.raises(CommandNumberOverflow):
        next_command_number(MAX_COMMAND_NUMBER)


@pytest.mark.parametrize(
    "current", [MAX_COMMAND_NUMBER + 1, _INT64_MAX], ids=["max+1", "int64 max"]
)
def test_next_generation_out_of_range(current: int) -> None:
    with pytest.raises(InvalidCommandNumber):
        next_command_number(current)


def test_next_generation_negative_is_invalid_only() -> None:
    with pytest.raises(InvalidCommandNumber) as exc_info:
        next_command_number(-1)
    assert not is_error(exc_info.value, StaleFence)


def test_validate_generation_ranges() -> None:
    for g in (0, 1, MAX_COMMAND_NUMBER):
        validate_last_command_number(g)
    for g in (-1, _INT64_MIN, MAX_COMMAND_NUMBER + 1, _INT64_MAX):
        with pytest.raises(InvalidCommandNumber):
            validate_last_command_number(g)
    for g in (1, 2, MAX_COMMAND_NUMBER):
        validate_command_number(g)
    # 0 is a legal project counter but never a command's number.
    for g in (0, -1, MAX_COMMAND_NUMBER + 1, _INT64_MAX):
        with pytest.raises(InvalidCommandNumber):
            validate_command_number(g)


_FENCE_CASES: list[tuple[str, int, int, type[Exception] | None]] = [
    ("equal", 5, 5, None),
    ("one", 1, 1, None),
    ("max equal", MAX_COMMAND_NUMBER, MAX_COMMAND_NUMBER, None),
    ("older command", 4, 5, StaleFence),
    ("counter behind", 6, 5, StaleFence),
    ("one against zero", 1, 0, StaleFence),
    ("fencing zero", 0, 0, InvalidCommandNumber),
    ("fencing negative", -1, 5, InvalidCommandNumber),
    ("fencing above max", MAX_COMMAND_NUMBER + 1, MAX_COMMAND_NUMBER + 1, InvalidCommandNumber),
    ("current negative", 1, -1, InvalidCommandNumber),
    ("current above max", 1, MAX_COMMAND_NUMBER + 1, InvalidCommandNumber),
    ("int64 max", _INT64_MAX, _INT64_MAX, InvalidCommandNumber),
]


@pytest.mark.parametrize(
    ("command_number", "last_command_number", "want"),
    [c[1:] for c in _FENCE_CASES],
    ids=[c[0] for c in _FENCE_CASES],
)
def test_check_fence(
    command_number: int, last_command_number: int, want: type[Exception] | None
) -> None:
    if want is None:
        check_fence(command_number, last_command_number)
        return
    with pytest.raises(want) as exc_info:
        check_fence(command_number, last_command_number)
    if want is InvalidCommandNumber:
        assert not is_error(exc_info.value, StaleFence), "invalid input reported as stale"
