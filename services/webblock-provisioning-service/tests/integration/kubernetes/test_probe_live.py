"""The promotion probe's bounds through the real Gateway (task W6-M), with site variants built
through the stack's Jenkins from ``sites/``. ``qa-huge-readyz`` and ``qa-flap`` are QA's
reproductions (QA branch ``d487207``, ``deploy/prototype/sample-site/``); ``qa-huge-chunked`` is
``qa-huge-readyz`` sent without a length.

- QA-01: ``/readyz`` answers 200 with a 1 GiB body. QA measured the release worker growing from
  138 MiB to 2153 MiB while one promotion probed such a site. Here the probe runs in this process,
  through the same adapter code the worker runs, and the process's resident memory is sampled every
  10 ms while it does: the probe fails (the body is above the bound) within its time limit, and the
  process grows by far less than the body. ``qa-huge-readyz`` declares its length, so the probe
  refuses it from the header; ``qa-huge-chunked`` does not, so the probe reads and stops at its
  bound.
- QA-08: ``qa-flap`` answers ``/readyz`` with 200 in the first half of each second and 503 in the
  second half. With one answer per probe, some probes pass and some fail; with three answers half a
  second apart, none passes. Since task H5a a 503 that names the expected release raises
  ``SiteDependencyUnavailable`` (a failed answer for a promotion): only the 503s raise, a 200
  never does, and a probe expecting another release gets False from the same 503s.

Each test points the preview route at its release, as a promotion does, and probes the preview
hostname with the probe token."""

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx2
import pytest
from tests.integration.kubernetes.conftest import SITE_DOMAIN, build_site

from provisioning.core.application.ports.site_cluster import (
    PREVIEW_HEADER,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    SiteDependencyUnavailable,
    TenantState,
    WorkloadState,
)
from provisioning.core.application.routing.background.routing import preview_hostname
from provisioning.infrastructure.kubernetes import KubeApi
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import preview_token_for
from provisioning.infrastructure.kubernetes.site_cluster import (
    KubernetesSiteCluster,
    SiteClusterSettings,
)

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

POLL = 2.0
# Each test may build a site through Jenkins (minutes) before it waits for readiness.
pytestmark = pytest.mark.timeout(1800)


def command_number() -> int:
    return time.time_ns() // 1_000_000


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool], within: float) -> T:
    value = await read()
    try:
        async with asyncio.timeout(within):
            while not done(value):
                await asyncio.sleep(POLL)
                value = await read()
    except TimeoutError:
        pass
    return value


def rss_mib() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise AssertionError("no VmRSS in /proc/self/status")


async def previewed(
    cluster: KubernetesSiteCluster, stack: Stack, image: str
) -> tuple[ReleaseWorkload, str]:
    """A ready release of ``image`` behind the preview route; returns it and the preview host."""
    number = command_number()
    workload = ReleaseWorkload(stack.project_id, uuid4(), image, number)
    await cluster.apply_release(workload)
    status = await until(
        lambda: cluster.release_status(workload),
        lambda s: s.state is not WorkloadState.PROGRESSING,
        240.0,
    )
    assert status.state is WorkloadState.READY, status
    host = preview_hostname(stack.subdomain, SITE_DOMAIN)
    await cluster.apply_preview(
        stack.project_id, workload.release_id, host, number, subdomain=stack.subdomain
    )
    target = RouteTarget(RouteTargetKind.RELEASE, workload.release_id)
    preview = await until(
        lambda: cluster.preview_status(stack.project_id),
        lambda s: s.accepted and s.target == target and s.command_number == number,
        60.0,
    )
    assert preview.accepted and preview.target == target, preview
    return workload, host


async def readyz_headers(
    stack: Stack, host: str, settings: SiteClusterSettings, release: str
) -> httpx2.Headers:
    """The headers of ``/readyz`` once the Gateway serves the preview route to ``release``; the
    body is not read."""
    token = preview_token_for(settings.preview_token, stack.project_id)  # decision 0023 G18c
    headers = {"Host": host, PREVIEW_HEADER: token}
    async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
        for _ in range(30):
            async with http.stream(
                "GET", stack.gateway_url + "/readyz", headers=headers
            ) as response:
                if response.headers.get("X-Webblock-Release") == release:
                    return response.headers
            await asyncio.sleep(POLL)
    raise AssertionError("the Gateway never served the preview route to the release")


@pytest.mark.parametrize(
    ("variant", "declares_length"),
    [("qa-huge-readyz", True), ("qa-huge-chunked", False)],
)
@pytest.mark.asyncio
async def test_qa01_a_one_gib_readyz_fails_the_probe_and_the_memory_stays_bounded(
    bootstrap: KubernetesTenantBootstrap,
    cluster: KubernetesSiteCluster,
    cluster_settings: SiteClusterSettings,
    stack: Stack,
    variant: str,
    declares_length: bool,
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    image = await asyncio.to_thread(build_site, stack, variant)
    workload, host = await previewed(cluster, stack, image)
    target = RouteTarget(RouteTargetKind.RELEASE, workload.release_id)
    try:
        headers = await readyz_headers(stack, host, cluster_settings, str(workload.release_id))
        print(f"{variant}: Content-Length {headers.get('Content-Length')}")
        assert ("Content-Length" in headers) is declares_length
        if declares_length:
            assert int(headers["Content-Length"]) == 1024 * 1024 * 1024

        before = rss_mib()
        peak = before
        started = time.monotonic()
        async with asyncio.TaskGroup() as group:
            probe = group.create_task(
                cluster.probe_site(stack.project_id, host, target, preview_of=stack.subdomain)
            )
            while not probe.done():
                peak = max(peak, rss_mib())
                await asyncio.sleep(0.01)
        took = time.monotonic() - started
        print(
            f"{variant}: probe {probe.result()} in {took:.2f}s; rss before {before:.0f} MiB, "
            f"peak {peak:.0f} MiB (bound {cluster_settings.probe_max_body_bytes} bytes)"
        )
        assert probe.result() is False
        assert peak - before < 32, f"the probe grew this process by {peak - before:.0f} MiB"
        assert took < cluster_settings.probe_timeout_seconds
    finally:
        await cluster.delete_release(stack.project_id, workload.release_id, command_number())


@pytest.mark.asyncio
async def test_qa08_a_flapping_readyz_passes_one_answer_but_never_three_in_a_row(
    bootstrap: KubernetesTenantBootstrap,
    cluster: KubernetesSiteCluster,
    cluster_settings: SiteClusterSettings,
    worker_api: KubeApi,
    stack: Stack,
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    image = await asyncio.to_thread(build_site, stack, "qa-flap")
    workload, host = await previewed(cluster, stack, image)
    target = RouteTarget(RouteTargetKind.RELEASE, workload.release_id)
    three = dataclasses.replace(cluster_settings, probe_successes=3, probe_success_gap_seconds=0.5)
    try:
        await readyz_headers(stack, host, cluster_settings, str(workload.release_id))
        singles: list[bool] = []
        raised = 0
        for _ in range(16):
            try:
                singles.append(
                    await cluster.probe_site(
                        stack.project_id, host, target, preview_of=stack.subdomain
                    )
                )
            except SiteDependencyUnavailable:  # the 503 half, naming this release
                singles.append(False)
                raised += 1
            await asyncio.sleep(0.13)  # spread the starts over the second
        runs: list[bool] = []
        async with httpx2.AsyncClient(trust_env=False) as http:
            strict = KubernetesSiteCluster(worker_api, three, http)
            for _ in range(10):
                try:
                    runs.append(
                        await strict.probe_site(
                            stack.project_id, host, target, preview_of=stack.subdomain
                        )
                    )
                except SiteDependencyUnavailable:
                    runs.append(False)
                await asyncio.sleep(0.13)
        # The same 503s expected from another release: never a raise, always False.
        other = RouteTarget(RouteTargetKind.RELEASE, uuid4())
        for_other: list[bool] = []
        for _ in range(8):
            for_other.append(
                await cluster.probe_site(stack.project_id, host, other, preview_of=stack.subdomain)
            )
            await asyncio.sleep(0.13)
        print(
            f"qa-flap: one answer {sum(singles)}/16 passed, {raised} raised on 503; "
            f"three in a row {sum(runs)}/10"
        )
        assert any(singles), "the site never answered 200: not the qa-flap variant"
        assert not all(singles), "the site always answered 200: not the qa-flap variant"
        # One answer per probe: the failed singles are the raised 503s (at most one transport
        # hiccup allowed), and a 200 never raised (it passed).
        assert raised >= 1, singles
        assert 0 <= singles.count(False) - raised <= 1, (raised, singles)
        assert not any(runs), "a flapping /readyz passed three answers in a row"
        assert for_other == [False] * 8, for_other
    finally:
        await cluster.delete_release(stack.project_id, workload.release_id, command_number())
