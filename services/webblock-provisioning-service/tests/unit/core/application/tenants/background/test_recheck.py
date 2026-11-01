"""QA-04: the bootstrap loop forgets its "ready" cache every ``recheck_rounds`` rounds, so a tenant
namespace deleted by hand is rebuilt without a restart (``BOOTSTRAP_RECHECK_ROUNDS``).

The first test is QA's reproduction (``tests/qa/test_qa_bootstrap_never_rechecks_a_ready_tenant.py``
at ``d487207``) unchanged; the others pin the bound and the settings. The host's test (the
bootstrap process passes BOOTSTRAP_RECHECK_ROUNDS to the loop) is in
tests/unit/hosts/bootstrap/test_loop_settings.py.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import (
    DEFAULT_RECHECK_ROUNDS,
    LoopSettings,
    TenantLoop,
)

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
LOG = logging.getLogger("test")


class Projects:
    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        return [] if after is not None else [TenantProject(PROJECT, f"site-{PROJECT.hex}")]


class Cluster:
    """A cluster with one namespace that somebody can delete; ``ensure_tenant`` is idempotent."""

    def __init__(self) -> None:
        self.namespace_exists = False
        self.ensure_calls = 0

    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        self.ensure_calls += 1
        self.namespace_exists = True
        return TenantState.READY


def _settings(recheck_rounds: int = DEFAULT_RECHECK_ROUNDS) -> LoopSettings:
    return LoopSettings(
        interval=timedelta(seconds=5),
        batch_size=10,
        concurrency=2,
        call_timeout=timedelta(seconds=5),
        recheck_rounds=recheck_rounds,
    )


@pytest.mark.asyncio
async def test_a_tenant_whose_namespace_was_deleted_is_ensured_again_in_a_later_round() -> None:
    cluster = Cluster()
    loop = TenantLoop(
        LoopSettings(
            interval=timedelta(seconds=5),
            batch_size=10,
            concurrency=2,
            call_timeout=timedelta(seconds=5),
        ),
        projects=Projects(),
        bootstrap=cluster,
        logger=logging.getLogger("qa"),
    )

    await loop.round()
    assert cluster.namespace_exists

    cluster.namespace_exists = False  # somebody deletes wb-<projectId>
    for _ in range(50):  # any number of later rounds
        await loop.round()

    assert cluster.namespace_exists, (
        "the namespace stayed deleted: the tenant is never checked again"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("recheck_rounds", [1, 3, 10])
async def test_a_ready_tenant_is_ensured_again_exactly_every_recheck_rounds(
    recheck_rounds: int,
) -> None:
    cluster = Cluster()
    loop = TenantLoop(_settings(recheck_rounds), projects=Projects(), bootstrap=cluster, logger=LOG)
    await loop.round()
    assert cluster.ensure_calls == 1
    # The tenant is remembered for recheck_rounds - 1 rounds, then ensured once more, and so on.
    calls: list[int] = []
    for _ in range(3 * recheck_rounds):
        await loop.round()
        calls.append(cluster.ensure_calls)
    assert calls[recheck_rounds - 1] == 2
    assert calls[2 * recheck_rounds - 1] == 3
    assert cluster.ensure_calls == 4
    if recheck_rounds > 1:
        assert calls[recheck_rounds - 2] == 1  # not earlier than the bound


@pytest.mark.asyncio
async def test_the_after_ready_hook_runs_again_on_a_recheck() -> None:
    """A rebuilt namespace has no catalog Secret: access_setup runs again with the ensure."""
    cluster = Cluster()
    hooked: list[UUID] = []

    async def after_ready(project_id: UUID) -> None:
        hooked.append(project_id)

    loop = TenantLoop(
        _settings(2), projects=Projects(), bootstrap=cluster, logger=LOG, after_ready=after_ready
    )
    for _ in range(5):
        await loop.round()
    assert hooked == [PROJECT, PROJECT, PROJECT]


def test_the_default_recheck_is_the_configuration_default() -> None:
    assert DEFAULT_RECHECK_ROUNDS == 10
    assert (
        LoopSettings(
            interval=timedelta(seconds=1),
            batch_size=1,
            concurrency=1,
            call_timeout=timedelta(seconds=1),
        ).recheck_rounds
        == DEFAULT_RECHECK_ROUNDS
    )


@pytest.mark.parametrize("recheck_rounds", [0, -1])
def test_the_settings_refuse_a_recheck_below_one_round(recheck_rounds: int) -> None:
    with pytest.raises(ValueError, match="recheck"):
        _settings(recheck_rounds).validate()
