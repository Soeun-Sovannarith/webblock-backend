"""Correlation IDs: a caller-supplied value is kept only when it has the safe form; the current one
travels in a context variable."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from provisioning.core.application import MAX_CORRELATION_ID_LEN
from provisioning.infrastructure.telemetry import (
    MAX_CORRELATION_ID_LEN as TELEMETRY_MAX,
)
from provisioning.infrastructure.telemetry import (
    correlation_id,
    correlation_scope,
    current_correlation_id,
    new_correlation_id,
    valid_correlation_id,
)


def test_the_limit_matches_the_application_limit() -> None:
    assert TELEMETRY_MAX == MAX_CORRELATION_ID_LEN == 128


@pytest.mark.parametrize("value", ["a", "A-b_c.d:e", "0" * 128, "f" * 32])
def test_valid(value: str) -> None:
    assert valid_correlation_id(value)


@pytest.mark.parametrize("value", ["", "0" * 129, "a b", "a\nb", 'a"b', "a/b", "é", "a\x00"])
def test_invalid(value: str) -> None:
    assert not valid_correlation_id(value)


def test_new_ids_are_distinct_uuid_v7() -> None:
    ids = {new_correlation_id() for _ in range(100)}
    assert len(ids) == 100
    for value in ids:
        assert UUID(value).version == 7


def test_a_valid_supplied_value_is_kept_and_another_replaced() -> None:
    assert correlation_id("given-1") == "given-1"
    replaced = correlation_id("bad value")
    assert replaced != "bad value"
    assert valid_correlation_id(replaced)


def test_the_scope_sets_and_restores_the_current_id() -> None:
    assert current_correlation_id() is None
    with correlation_scope("outer"):
        assert current_correlation_id() == "outer"
        with correlation_scope("bad id"):
            inner = current_correlation_id()
            assert inner is not None
            assert inner != "bad id"
            assert valid_correlation_id(inner)
        assert current_correlation_id() == "outer"
    assert current_correlation_id() is None


@pytest.mark.asyncio
async def test_tasks_do_not_see_each_others_ids() -> None:
    seen: dict[str, str | None] = {}

    async def run(name: str) -> None:
        with correlation_scope(name):
            await asyncio.sleep(0)
            seen[name] = current_correlation_id()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(run("one"))
        tg.create_task(run("two"))
    assert seen == {"one": "one", "two": "two"}
