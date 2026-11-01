"""The generic loader: every key is required, an empty value is missing, every problem is collected,
and no reason carries a value (decision 0006 section 7, OD-15; review-port-arch.md M8)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from provisioning.infrastructure.config import (
    Loader,
    LoadError,
    Lookup,
    Problem,
    load,
    parse_duration,
    plain_text,
    required_keys,
    split_list,
)

MARKER = "VALUE-MARKER-7f3a"


def _lookup(env: dict[str, str]) -> Lookup:
    return env.get


def test_missing_and_empty_are_both_required() -> None:
    loader = Loader(_lookup({"B": ""}))
    assert loader.raw("A") is None
    assert loader.raw("B") is None
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert exc_info.value.problems == (
        Problem("A", "is required"),
        Problem("B", "is required"),
    )


def test_problems_are_sorted_and_a_key_is_reported_once_with_its_first_reason() -> None:
    loader = Loader(_lookup({}))
    loader.fail("Z", "first")
    loader.fail("A", "only")
    loader.fail("Z", "second")
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    err = exc_info.value
    assert err.keys() == ("A", "Z")
    assert str(err) == "invalid configuration: A: only; Z: first"


def test_finish_without_problems_returns() -> None:
    loader = Loader(_lookup({"A": "x"}))
    assert loader.text("A", plain_text(10)) == "x"
    loader.finish()


def test_ok_reports_only_keys_that_did_not_fail() -> None:
    loader = Loader(_lookup({"A": "1"}))
    loader.integer("A", 0, 5)
    loader.integer("B", 0, 5)
    assert loader.ok("A")
    assert not loader.ok("A", "B")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("0", timedelta(0)),
        ("30s", timedelta(seconds=30)),
        ("1.5s", timedelta(seconds=1.5)),
        ("300ms", timedelta(milliseconds=300)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("2m3s", timedelta(minutes=2, seconds=3)),
        ("10us", timedelta(microseconds=10)),
        ("10µs", timedelta(microseconds=10)),
        ("1500ns", timedelta(microseconds=1)),
    ],
)
def test_parse_duration_accepts_the_go_form(text: str, want: timedelta) -> None:
    assert parse_duration(text) == want


@pytest.mark.parametrize(
    "text",
    ["", "30", "s", "1d", "-1s", "+1s", " 1s", "1s ", "1.s5", ".s", "1e3s", "1,5s", "ms30"],
)
def test_parse_duration_refuses(text: str) -> None:
    assert parse_duration(text) is None


def test_duration_bounds_and_reasons_carry_no_value() -> None:
    loader = Loader(_lookup({"A": "2h", "B": "nonsense" + MARKER, "C": "5s"}))
    assert loader.duration("C", timedelta(seconds=1), timedelta(minutes=1)) == timedelta(seconds=5)
    loader.duration("A", timedelta(seconds=1), timedelta(minutes=1))
    loader.duration("B", timedelta(seconds=1), timedelta(minutes=1))
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    text = str(exc_info.value)
    assert "A: must be between 0:00:01 and 0:01:00" in text
    assert "B: must be a duration with a unit, such as 30s" in text
    assert MARKER not in text
    assert "nonsense" not in text


@pytest.mark.parametrize("value", ["+1", " 1", "1 ", "0x10", "1_000", "1.0", "1e3", "", "\u0661"])
def test_integer_refuses_other_notations(value: str) -> None:
    loader = Loader(_lookup({"N": value}))
    loader.integer("N", -10, 10_000)
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    reason = exc_info.value.problems[0].reason
    assert reason in ("must be a decimal integer", "is required")


def test_integer_bounds() -> None:
    loader = Loader(_lookup({"LO": "-1", "HI": "11", "OK": "-0", "BIG": "9" * 40}))
    assert loader.integer("OK", -1, 10) == 0
    loader.integer("LO", 0, 10)
    loader.integer("HI", 0, 10)
    loader.integer("BIG", 0, 10)
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert exc_info.value.problems == (
        Problem("BIG", "must be between 0 and 10"),
        Problem("HI", "must be between 0 and 10"),
        Problem("LO", "must be between 0 and 10"),
    )


@pytest.mark.parametrize(("value", "want"), [("true", True), ("false", False)])
def test_boolean(value: str, want: bool) -> None:
    loader = Loader(_lookup({"B": value}))
    assert loader.boolean("B") is want
    loader.finish()


@pytest.mark.parametrize("value", ["True", "1", "yes", "FALSE"])
def test_boolean_refuses(value: str) -> None:
    loader = Loader(_lookup({"B": value}))
    loader.boolean("B")
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert exc_info.value.problems == (Problem("B", "must be true or false"),)


def test_choice_accepts_exactly_the_listed_values() -> None:
    loader = Loader(_lookup({"M": "dev-tokens", "N": "Dev-Tokens" + MARKER}))
    assert loader.choice("M", ("dev-tokens",)) == "dev-tokens"
    loader.choice("N", ("dev-tokens",))
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert exc_info.value.problems == (Problem("N", "must be one of dev-tokens"),)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("x" * 11, "must be valid UTF-8 of at most 10 bytes"),
        ("ééééé" + "é", "must be valid UTF-8 of at most 10 bytes"),
        ("a\tb", "must not contain control characters"),
        ("a\x7fb", "must not contain control characters"),
        (" ab", "must not be blank or start or end with whitespace"),
        ("ab ", "must not be blank or start or end with whitespace"),
        ("\u00a0", "must not be blank or start or end with whitespace"),
    ],
)
def test_plain_text(value: str, reason: str) -> None:
    assert plain_text(10)(value) == reason
    assert plain_text(10)("abc") is None


def test_split_list() -> None:
    assert split_list("a,b") == ("a", "b")
    assert split_list("a") == ("a",)
    assert split_list("a,,b") is None
    assert split_list("a,") is None
    assert split_list(",a") is None


def test_load_and_required_keys_share_one_reader() -> None:
    def read(loader: Loader) -> tuple[str, int]:
        return (loader.text("NAME", plain_text(10)), loader.integer("COUNT", 1, 5))

    assert load({"NAME": "n", "COUNT": "3"}.get, read) == ("n", 3)
    assert required_keys(read) == ("COUNT", "NAME")
    with pytest.raises(LoadError) as exc_info:
        load(lambda _key: None, read)
    assert exc_info.value.keys() == ("COUNT", "NAME")


def test_a_check_that_raises_is_a_bug_not_a_value_leak() -> None:
    # A check returns a reason; the loader never formats the value into it.
    loader = Loader(_lookup({"K": MARKER}))
    loader.text("K", lambda v: "must be something else")
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert MARKER not in str(exc_info.value)
    assert MARKER not in repr(exc_info.value)


def test_optional_readers_return_the_default_when_unset_or_empty() -> None:
    loader = Loader(_lookup({"EMPTY_D": "", "EMPTY_N": ""}))
    second, hour = timedelta(seconds=1), timedelta(hours=1)
    thirty, five = timedelta(seconds=30), timedelta(seconds=5)
    assert loader.optional_duration("UNSET_D", thirty, second, hour) == thirty
    assert loader.optional_duration("EMPTY_D", five, second, hour) == five
    assert loader.optional_integer("UNSET_N", 50, 1, 1000) == 50
    assert loader.optional_integer("EMPTY_N", 7, 1, 1000) == 7
    loader.finish()


def test_optional_readers_check_a_set_value_like_a_required_one() -> None:
    env = {"D": "2m", "N": "12", "BAD_D": "soon" + MARKER, "BAD_N": "0x1", "BIG_N": "1001"}
    loader = Loader(_lookup(env))
    second, hour = timedelta(seconds=1), timedelta(hours=1)
    assert loader.optional_duration("D", timedelta(0), second, hour) == timedelta(minutes=2)
    assert loader.optional_integer("N", 1, 1, 1000) == 12
    loader.optional_duration("BAD_D", timedelta(seconds=30), second, hour)
    loader.optional_integer("BAD_N", 1, 1, 1000)
    loader.optional_integer("BIG_N", 1, 1, 1000)
    with pytest.raises(LoadError) as exc_info:
        loader.finish()
    assert exc_info.value.problems == (
        Problem("BAD_D", "must be a duration with a unit, such as 30s"),
        Problem("BAD_N", "must be a decimal integer"),
        Problem("BIG_N", "must be between 1 and 1000"),
    )
    assert MARKER not in str(exc_info.value)


def test_optional_keys_are_not_required_keys() -> None:
    def read(loader: Loader) -> tuple[int, timedelta]:
        return (
            loader.optional_integer("OPT_N", 1, 1, 10),
            loader.optional_duration("OPT_D", timedelta(1), timedelta(0), timedelta(days=2)),
        )

    assert required_keys(read) == ()
