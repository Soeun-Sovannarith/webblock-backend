"""Bounded fan-out for the use cases."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def for_each_bounded(count: int, limit: int, call: Callable[[int], Awaitable[None]]) -> None:
    """Await ``call(i)`` for every i in ``range(count)``, at most ``limit`` at a time, started in
    index order; return when every call has returned. The tasks are bounded by
    ``min(limit, count)`` and none outlives the call. ``call`` reports its own failures; one that
    raises cancels the others (``TaskGroup``), and cancelling the caller cancels them all."""
    next_index = 0

    async def worker() -> None:
        nonlocal next_index
        while next_index < count:
            index = next_index
            next_index += 1
            await call(index)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(limit, count)):
            group.create_task(worker())
