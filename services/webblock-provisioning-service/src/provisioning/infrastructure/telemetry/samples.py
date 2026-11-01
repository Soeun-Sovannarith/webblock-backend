"""SQL samples read when /metrics is scraped (task H4; decision 0016 section 5 item 4): the dispatch
lag in the worker, the inbox backlog in the build worker. Each is one short read-only transaction
on the process's own login, with the existing columns and grants (no schema change):

- ``dispatch``, as ``provisioning_worker`` (SELECT on ``provisioning_ops.command_dispatch`` and
  ``provisioning.project_hosting``): the age of the oldest unfinished command whose job the broker
  never confirmed (the dispatch lag), the age of the oldest unfinished command, the number of
  unfinished commands, and the number of projects whose route is ``uncertain``;
- ``inbox``, as ``provisioning_intake`` (SELECT on ``provisioning_ops.source_published_events``):
  the "source published" events pending and failed.

Ages are measured against the database clock of the same transaction. Counts and ages only: no
row's identifier ever leaves the database here.
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.infrastructure.postgres import Timeouts, Transactions
from provisioning.infrastructure.postgres.schema import (
    command_dispatch,
    project_hosting,
    source_published_events,
)
from provisioning.infrastructure.postgres.statements import read_clock
from provisioning.infrastructure.telemetry.metrics import Metrics

_UNCERTAIN = "uncertain"


def add_dispatch_sample(metrics: Metrics, engine: AsyncEngine, timeouts: Timeouts) -> None:
    """Register the worker's dispatch sample on ``metrics``."""
    lag = metrics.gauge(
        "provisioning_dispatch_lag_seconds",
        "Age of the oldest unfinished command whose job the broker has not confirmed (0: none).",
    )
    oldest = metrics.gauge(
        "provisioning_unfinished_command_age_seconds",
        "Age of the oldest unfinished command (0: none).",
    )
    unfinished = metrics.gauge("provisioning_unfinished_commands", "Unfinished commands.")
    uncertain = metrics.gauge("provisioning_routes_uncertain", "Projects whose route is uncertain.")
    tx = Transactions(engine, timeouts)

    async def read(conn: AsyncConnection) -> tuple[float, float, int, int]:
        now = await read_clock(conn)
        w = command_dispatch
        row = (
            await conn.execute(
                select(
                    func.min(w.c.created_at).filter(w.c.last_broker_ack_at.is_(None)),
                    func.min(w.c.created_at),
                    func.count(),
                ).where(w.c.finished_at.is_(None))
            )
        ).one()
        routes = (
            await conn.execute(
                select(func.count())
                .select_from(project_hosting)
                .where(project_hosting.c.routing_status == _UNCERTAIN)
            )
        ).scalar_one()
        return _age(now, row[0]), _age(now, row[1]), int(row[2]), int(routes)

    async def sample() -> None:
        values = await tx.run("metrics dispatch sample", read, read_only=True)
        lag.set(values[0])
        oldest.set(values[1])
        unfinished.set(values[2])
        uncertain.set(values[3])

    def unknown() -> None:
        for gauge in (lag, oldest, unfinished, uncertain):
            gauge.set(math.nan)

    metrics.add_sample("dispatch", sample, unknown)


def add_inbox_sample(metrics: Metrics, engine: AsyncEngine, timeouts: Timeouts) -> None:
    """Register the build worker's inbox sample on ``metrics``."""
    events = metrics.gauge(
        "provisioning_inbox_events",
        'Project\'s "source published" events in the inbox, by status (pending, failed).',
        ("status",),
    )
    tx = Transactions(engine, timeouts)
    statuses = ("pending", "failed")

    async def read(conn: AsyncConnection) -> dict[str, int]:
        s = source_published_events
        rows = await conn.execute(
            select(s.c.status, func.count()).where(s.c.status.in_(statuses)).group_by(s.c.status)
        )
        return {str(status): int(count) for status, count in rows}

    async def sample() -> None:
        counts = await tx.run("metrics inbox sample", read, read_only=True)
        for status in statuses:
            events.labels(status).set(counts.get(status, 0))

    def unknown() -> None:
        for status in statuses:
            events.labels(status).set(math.nan)

    metrics.add_sample("inbox", sample, unknown)


def _age(now: datetime, since: datetime | None) -> float:
    if since is None:
        return 0.0
    return max(0.0, (now - since).total_seconds())
