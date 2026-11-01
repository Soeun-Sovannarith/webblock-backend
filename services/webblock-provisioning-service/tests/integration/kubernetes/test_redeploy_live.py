"""The redeploy's rolling restart against the Kind stack (decision 0023 G12).

Written by Phase C task 8 and **not run by it**: the lead runs it on the stack of ``make proto-up``
(marker ``kind``). It uses the fixtures of ``conftest.py`` here (the project-local worker and
bootstrap kubeconfigs, the poisoned ``HOME`` and ``KUBECONFIG``, ``WEBBLOCK_KIND_SITE_IMAGE`` and
``WEBBLOCK_KIND_GATEWAY_URL``). The SQL rows are the in-memory world of the unit tests, which
applies the real domain outcome functions; the use cases are the real ``Routing`` (to make a site
live) and ``Redeploy`` over the real ``KubernetesSiteCluster``, so the admission policies of
``deploy/prototype/policy`` see the restart annotation the worker writes.

What it shows: after a redeploy of the live release the pods are new (other UIDs), they run the
same image digest, the Deployment's pod template carries ``restart-command`` with the command's
number, the site answers 200 with the live release's header through the Gateway during the whole
rollout, the release stays ``live``, and the only row written is the command's end (no release
write, so no usage event: the PostgreSQL store writes one only for a release that goes live,
``tests/integration/test_routing_store.py``). Command numbers come from the clock, so a rerun is
never fenced by an earlier run.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import pytest
from tests.integration.kubernetes.test_routing_live import SITE_DOMAIN, Site, ensure_tenant

from provisioning.core.application.deploys.background.deploy_executor import AsyncioPauser
from provisioning.core.application.deploys.background.redeploy import Redeploy, RedeploySettings
from provisioning.core.application.ports.site_cluster import RELEASE_HEADER
from provisioning.core.domain import CommandKind, CommandStatus, ReleaseStatus, RoutingStatus
from provisioning.infrastructure.kubernetes import KubeApi, Resource
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_RESTART_COMMAND,
    release_name,
    release_selector,
    tenant_namespace,
)
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

pytestmark = pytest.mark.timeout(900)  # a promotion, then a rolling restart, each bounded
ANSWER_GAP = 0.25  # seconds between two requests of the availability poll


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def registry_and_digest(image: str) -> tuple[str, str]:
    """``<registry>/webblock/sites/<projectId>@<digest>`` split into its registry and digest."""
    registry, _, rest = image.partition("/webblock/sites/")
    _, _, digest = rest.partition("@")
    return registry, digest


async def live_pods(api: KubeApi, stack: Stack, release_id: UUID) -> list[dict[str, Any]]:
    """The release's pods that are not terminating."""
    pods = await api.list_objects(
        Resource.POD,
        tenant_namespace(stack.project_id),
        release_selector(stack.project_id, release_id),
    )
    return [p for p in pods if not p["metadata"].get("deletionTimestamp")]


def image_ids(pods: list[dict[str, Any]]) -> set[str]:
    """The image digests the pods' ``site`` containers run (``imageID`` ends with the digest)."""
    return {
        status["imageID"].rpartition("@")[2]
        for pod in pods
        for status in pod.get("status", {}).get("containerStatuses", [])
        if status.get("name") == "site"
    }


async def watch_answers(stack: Stack, stop: asyncio.Event, answers: list[tuple[int, str]]) -> None:
    """Ask the site's ``/healthz`` (200 while the process is up, decision 0014 section 15)
    through the Gateway every ``ANSWER_GAP`` seconds until ``stop``; record each status and
    release header, or status 0 for a transport error."""
    async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
        while not stop.is_set():
            try:
                response = await http.get(
                    stack.gateway_url.rstrip("/") + "/healthz",
                    headers={"Host": f"{stack.subdomain}.{SITE_DOMAIN}", "Connection": "close"},
                    follow_redirects=False,
                )
                answers.append(
                    (response.status_code, ",".join(response.headers.get_list(RELEASE_HEADER)))
                )
            except httpx2.HTTPError:
                answers.append((0, ""))
            await asyncio.sleep(ANSWER_GAP)


@pytest.mark.asyncio
async def test_a_redeploy_restarts_the_live_pods_and_the_site_keeps_answering(
    cluster: KubernetesSiteCluster,
    bootstrap: KubernetesTenantBootstrap,
    worker_api: KubeApi,
    stack: Stack,
) -> None:
    await ensure_tenant(bootstrap, stack)
    site = Site(cluster, stack)
    registry, digest = registry_and_digest(stack.site_image)

    # 1. A live site: the sample image, promoted. The release row carries the image's digest, so
    # the redeploy derives the same image from it.
    live = await site.candidate(stack.site_image, wait_ready=True)
    site.world.releases[live] = replace(site.world.releases[live], container_image_digest=digest)
    assert await site.promote(live) is CommandStatus.SUCCEEDED
    assert site.world.release(live).status is ReleaseStatus.LIVE
    before = await live_pods(worker_api, stack, live)
    assert before, "the live release has no running pod"
    before_uids = {p["metadata"]["uid"] for p in before}
    before_images = image_ids(before)
    assert before_images == {digest}

    # 2. Redeploy it while asking the site all along.
    command = site.world.accept(CommandKind.REDEPLOY, live)
    claim = replace(site.world.claim(command), deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    hosting, writes = site.world.hosting, len(site.world.writes)
    redeploy = Redeploy(
        store=site.world,
        cluster=cluster,
        clock=RealClock(),
        pauser=AsyncioPauser(),
        settings=RedeploySettings(
            image_registry=registry,
            poll_interval=timedelta(seconds=1),
            call_timeout=timedelta(seconds=20),
            finish_margin=timedelta(seconds=10),
        ),
    )
    stop = asyncio.Event()
    answers: list[tuple[int, str]] = []
    watcher = asyncio.create_task(watch_answers(stack, stop, answers))
    try:
        await asyncio.sleep(1.0)  # a few answers before the restart
        await redeploy.run(claim)
        await asyncio.sleep(2.0)  # and a few after it
    finally:
        stop.set()
        await watcher

    # 3. The command succeeded and wrote nothing else.
    assert site.world.command(command.id).status is CommandStatus.SUCCEEDED
    assert site.world.writes[writes:] == ["command redeploy succeeded"]
    assert site.world.hosting == hosting
    assert site.world.routing() is RoutingStatus.STABLE
    assert site.world.release(live).status is ReleaseStatus.LIVE

    # 4. New pods, same image digest, and the restart annotation at the command's number.
    after = await live_pods(worker_api, stack, live)
    assert after
    assert {p["metadata"]["uid"] for p in after}.isdisjoint(before_uids)
    assert image_ids(after) == before_images
    deployment = await worker_api.read(
        Resource.DEPLOYMENT, release_name(live), tenant_namespace(stack.project_id)
    )
    assert deployment is not None
    template = deployment["spec"]["template"]
    assert template["metadata"]["annotations"][ANNOTATION_RESTART_COMMAND] == str(
        command.command_number
    )
    assert template["spec"]["containers"][0]["image"] == stack.site_image

    # 5. The site answered 200 from the live release during the whole rollout.
    assert len(answers) >= 8, answers
    assert all(answer == (200, str(live)) for answer in answers), [
        a for a in answers if a != (200, str(live))
    ]
