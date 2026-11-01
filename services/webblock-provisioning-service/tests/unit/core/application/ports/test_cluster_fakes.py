"""The wave 6 methods of the shared ``FakeCluster`` (task W6-S) behave as the port says, so the
reconciler, cleanup and operator-rollback tests built on it see the real contract: ``apply_route``
refuses a higher command number, ``reclaim_route`` (forced) does not and takes the route back from
a foreign manager; ``inventory`` lists what the fake holds."""

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    HOSTNAME,
    LIVE,
    PROJECT,
    SUBDOMAIN,
    FakeCluster,
    Route,
)

from provisioning.core.application.ports.site_cluster import (
    FenceRejected,
    RouteTarget,
    RouteTargetKind,
    SiteObject,
    SiteObjectKind,
)

ROUTE = f"route-{PROJECT}"
LIVE_TARGET = RouteTarget(RouteTargetKind.RELEASE, LIVE)


@pytest.mark.asyncio
async def test_reclaim_route_is_forced_where_apply_route_is_refused() -> None:
    cluster = FakeCluster(log=[], route=Route(RouteTarget(RouteTargetKind.OFFLINE), 9))
    cluster.foreign_managers[ROUTE] = ("kubectl-edit",)
    with pytest.raises(FenceRejected):
        await cluster.apply_route(PROJECT, LIVE_TARGET, (HOSTNAME,), 5, subdomain=SUBDOMAIN)
    await cluster.reclaim_route(PROJECT, LIVE_TARGET, (HOSTNAME,), 5, subdomain=SUBDOMAIN)
    assert cluster.route == Route(LIVE_TARGET, 5)
    assert ROUTE not in cluster.foreign_managers
    assert cluster.log[-1] == f"reclaim_route release {LIVE} #5"


@pytest.mark.asyncio
async def test_inventory_lists_the_routes_and_each_serving_release() -> None:
    cluster = FakeCluster(log=[], serving={LIVE, CANDIDATE}, route=Route(LIVE_TARGET, 4))
    cluster.foreign_managers[f"rel-{LIVE}"] = ("other",)
    assert await cluster.list_tenant_projects() == (PROJECT,)
    inventory = await cluster.inventory(PROJECT)
    assert inventory.project_id == PROJECT
    by_name = {(o.kind, o.name): o for o in inventory.objects}
    assert by_name[(SiteObjectKind.HTTP_ROUTE, ROUTE)] == SiteObject(
        SiteObjectKind.HTTP_ROUTE, ROUTE, None, 4, ()
    )
    for release in (LIVE, CANDIDATE):
        for kind in (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE):
            assert by_name[(kind, f"rel-{release}")].release_id == release
    assert by_name[(SiteObjectKind.DEPLOYMENT, f"rel-{LIVE}")].foreign_managers == ("other",)
    assert len(inventory.objects) == 5
