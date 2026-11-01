"""The promotion probe's bounds (task W6-M):

- QA-01: ``probe_site`` streams the answer and stops at ``probe_max_body_bytes``. A larger answer
  is a failed probe and is never read whole, so a site whose ``/readyz`` never ends cannot fill the
  release worker's memory (reproduction ``tests/qa/test_qa_probe_reads_unbounded_body.py`` of the
  QA branch, adopted here).
- QA-08: a probe succeeds only after ``probe_successes`` consecutive answers, each a 200 that
  carries the expected ``X-Webblock-Release``, each on a fresh connection and
  ``probe_success_gap_seconds`` after the previous one, so a ``/readyz`` that answers 200 about
  half of the time is not taken for a working site.
"""

import asyncio
import dataclasses
import itertools
import time
from collections.abc import AsyncIterator
from uuid import UUID

import httpx2
import pytest

from provisioning.core.application.ports.site_cluster import (
    RELEASE_HEADER,
    RouteTarget,
    RouteTargetKind,
    SiteDependencyUnavailable,
)
from provisioning.infrastructure.kubernetes import KubeApi
from provisioning.infrastructure.kubernetes.render import SiteSettings
from provisioning.infrastructure.kubernetes.site_cluster import (
    KubernetesSiteCluster,
    SiteClusterSettings,
)

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
RELEASE = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")
HOST = "unit-shop.sites.webblock.test"  # <subdomain>.<site domain> (decision 0023 G18)
TO_RELEASE = RouteTarget(RouteTargetKind.RELEASE, RELEASE)
GOOD = [(RELEASE_HEADER, str(RELEASE))]
TOKEN = "preview-token-for-unit-tests-0123456789"  # noqa: S105 - a test value
MIB = 1024 * 1024
BASE = SiteClusterSettings(
    site=SiteSettings(
        cpu_millicores=100,
        memory_mib=128,
        progress_deadline_seconds=60,
        readiness_deadline_seconds=120,
        offline_service_port=80,
    ),
    gateway_url="http://gateway.webblock.test:8080",
    probe_path="/readyz",
    probe_timeout_seconds=0.5,
    delete_retry_base_seconds=0.0,
    site_domain="sites.webblock.test",
    preview_token=TOKEN,
    probe_success_gap_seconds=0.0,
)


class EndlessBody(httpx2.AsyncByteStream):
    """1 MiB chunks without end (stopped at 96 MiB, a safety stop for this test process)."""

    def __init__(self) -> None:
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunk = b"x" * MIB
        while self.sent < 96 * MIB:
            self.sent += len(chunk)
            yield chunk
            await asyncio.sleep(0)  # a network read suspends; the time limit can fire

    async def aclose(self) -> None:
        return None


class CountedBody(httpx2.AsyncByteStream):
    """``size`` bytes in chunks of ``chunk``; counts what was handed out."""

    def __init__(self, size: int, chunk: int = 4096) -> None:
        self.size = size
        self.chunk = chunk
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while self.sent < self.size:
            part = min(self.chunk, self.size - self.sent)
            self.sent += part
            yield b"r" * part

    async def aclose(self) -> None:
        return None


async def probe(api: KubeApi, settings: SiteClusterSettings, handler: httpx2.MockTransport) -> bool:
    async with httpx2.AsyncClient(transport=handler) as http:
        return await KubernetesSiteCluster(api, settings, http).probe_site(
            PROJECT, HOST, TO_RELEASE
        )


# --- QA-01: a bounded, streamed read --------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_site_stops_reading_a_site_that_never_ends_its_answer(
    worker_api: KubeApi,
) -> None:
    """The QA reproduction: a 200 with the right header and an endless body is a failed probe,
    and the probe reads at most one transport chunk past its bound."""
    body = EndlessBody()

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers=GOOD, stream=body)

    started = time.monotonic()
    assert await probe(worker_api, BASE, httpx2.MockTransport(answer)) is False
    assert body.sent <= BASE.probe_max_body_bytes + MIB, f"read {body.sent // MIB} MiB"
    assert time.monotonic() - started < BASE.probe_timeout_seconds


@pytest.mark.parametrize(
    ("size", "result"),
    [(0, True), (65536, True), (65537, False), (10 * MIB, False)],
    ids=["empty", "at-the-bound", "one-byte-over", "ten-mib"],
)
@pytest.mark.asyncio
async def test_a_body_up_to_the_bound_passes_and_a_larger_one_fails(
    worker_api: KubeApi, size: int, result: bool
) -> None:
    body = CountedBody(size)

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers=GOOD, stream=body)

    assert BASE.probe_max_body_bytes == 65536
    assert await probe(worker_api, BASE, httpx2.MockTransport(answer)) is result
    assert body.sent <= BASE.probe_max_body_bytes + body.chunk


@pytest.mark.asyncio
async def test_a_declared_length_above_the_bound_fails_before_any_body_is_read(
    worker_api: KubeApi,
) -> None:
    body = CountedBody(10 * MIB)

    def answer(request: httpx2.Request) -> httpx2.Response:
        headers = [*GOOD, ("Content-Length", str(10 * MIB))]
        return httpx2.Response(200, headers=headers, stream=body)

    assert await probe(worker_api, BASE, httpx2.MockTransport(answer)) is False
    assert body.sent == 0


@pytest.mark.parametrize(("size", "result"), [(65536, True), (65537, False)])
@pytest.mark.asyncio
async def test_a_body_the_transport_already_holds_is_bounded_too(
    worker_api: KubeApi, size: int, result: bool
) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers=GOOD, content=b"r" * size)

    assert await probe(worker_api, BASE, httpx2.MockTransport(answer)) is result


@pytest.mark.asyncio
async def test_the_probe_asks_for_an_unencoded_answer(worker_api: KubeApi) -> None:
    """The bound counts the bytes on the wire; a compressed answer is never inflated."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, headers=GOOD)

    assert await probe(worker_api, BASE, httpx2.MockTransport(answer)) is True
    assert seen[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("bound", [0, -1])
def test_settings_refuse_a_body_bound_below_one(bound: int) -> None:
    with pytest.raises(ValueError, match="body bound"):
        dataclasses.replace(BASE, probe_max_body_bytes=bound)


# --- QA-08: consecutive successes on fresh connections --------------------------------------------


def scripted(codes: list[int], seen: list[httpx2.Request]) -> httpx2.MockTransport:
    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        code = codes[len(seen) - 1] if len(seen) <= len(codes) else 200
        return httpx2.Response(code, headers=GOOD)

    return httpx2.MockTransport(answer)


@pytest.mark.parametrize(
    ("codes", "result", "requests"),
    [
        ([200, 200, 200], True, 3),
        ([200, 500], False, 2),
        ([200, 200, 500], False, 3),
        ([500], False, 1),
    ],
    ids=["three-in-a-row", "one-then-500", "two-then-500", "500-first"],
)
@pytest.mark.asyncio
async def test_three_successes_are_needed_when_the_setting_is_three(
    worker_api: KubeApi, codes: list[int], result: bool, requests: int
) -> None:
    seen: list[httpx2.Request] = []
    settings = dataclasses.replace(BASE, probe_successes=3)

    assert await probe(worker_api, settings, scripted(codes, seen)) is result
    assert len(seen) == requests
    for request in seen:
        assert request.headers["Connection"] == "close"
        # Security review S1: a site-host probe never carries the preview header.
        assert "X-Webblock-Preview" not in request.headers
        assert request.headers["Host"] == HOST


@pytest.mark.asyncio
async def test_a_wrong_release_header_breaks_the_run(worker_api: KubeApi) -> None:
    seen: list[httpx2.Request] = []
    other = UUID("9c8b7a69-5847-4362-9150-0a1b2c3d4e5f")

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        value = str(other) if len(seen) == 2 else str(RELEASE)
        return httpx2.Response(200, headers=[(RELEASE_HEADER, value)])

    settings = dataclasses.replace(BASE, probe_successes=3)
    assert await probe(worker_api, settings, httpx2.MockTransport(answer)) is False
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_one_success_is_enough_when_the_setting_is_one(worker_api: KubeApi) -> None:
    seen: list[httpx2.Request] = []

    assert await probe(worker_api, BASE, scripted([200, 500], seen)) is True
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_the_answers_are_spaced_by_the_gap(worker_api: KubeApi) -> None:
    times: list[float] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        times.append(time.monotonic())
        return httpx2.Response(200, headers=GOOD)

    settings = dataclasses.replace(BASE, probe_successes=3, probe_success_gap_seconds=0.05)
    assert await probe(worker_api, settings, httpx2.MockTransport(answer)) is True
    assert len(times) == 3
    assert all(later - earlier >= 0.05 for earlier, later in itertools.pairwise(times))


@pytest.mark.asyncio
async def test_a_flapping_readyz_is_not_a_working_site(worker_api: KubeApi) -> None:
    """The qa-flap variant answers 200 in the first half of every second and 503 in the second
    half. Three answers half a second apart never all land in the first half. Its 503 names the
    expected release, so since task H5a a probe that meets it raises SiteDependencyUnavailable,
    which a promotion counts as a failed run; a probe never passes."""
    seen: list[float] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        now = time.time()
        seen.append(now)
        return httpx2.Response(200 if now % 1.0 < 0.5 else 503, headers=GOOD)

    settings = dataclasses.replace(BASE, probe_successes=3, probe_success_gap_seconds=0.5)
    for _ in range(3):
        try:
            passed = await probe(worker_api, settings, httpx2.MockTransport(answer))
        except SiteDependencyUnavailable:
            passed = False
        assert passed is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"probe_successes": 0}, "successes"),
        ({"probe_successes": 11}, "successes"),
        ({"probe_success_gap_seconds": -0.1}, "gap"),
        ({"probe_success_gap_seconds": 11.0}, "gap"),
    ],
)
def test_settings_refuse_impossible_probe_runs(change: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(BASE, **change)  # type: ignore[arg-type]  # each value is a field's type


def test_the_default_gap_is_half_a_second() -> None:
    defaults = dataclasses.replace(BASE)
    fields = {f.name: f.default for f in dataclasses.fields(SiteClusterSettings)}
    assert fields["probe_success_gap_seconds"] == 0.5
    assert fields["probe_successes"] == 1
    assert defaults.probe_max_body_bytes == 65536
