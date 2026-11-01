"""The Jenkins adapter against a fake Jenkins (decision 0014 section 3, "Build"; brief proto-C).

The fake is a small stateful Jenkins behind ``httpx2.MockTransport``: a crumb issuer, a queue whose
items turn into builds after a set number of polls, builds with archived ``result.json`` artifacts,
and basic authentication. It answers with absolute URLs under its own root URL, which differs from
the base URL the adapter is configured with, as a Jenkins behind a different host name does.

The timeout and connection-refused cases use real sockets on 127.0.0.1, not the mock.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs
from uuid import UUID

import httpx2
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from webblock_commons.core import context as commons_context
from webblock_commons.core.context import RequestContext
from webblock_commons.core.user import WebBlockUser

from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildState,
    BuildStatus,
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.infrastructure.jenkins import ApiToken, JenkinsBuildSystem
from provisioning.infrastructure.telemetry import correlation_scope
from provisioning.infrastructure.telemetry.tracing import (
    configure_tracing,
    shutdown_tracing,
    span,
)

BASE = "http://jenkins.test:8080"
ROOT = "http://jenkins-root.internal:9090"  # Jenkins' own idea of its URL
JOB = "webblock-site-build"
USER = "provisioning"
TOKEN = "11d0c3f6a8b94e1f9a7c2b5e8d4f6a1c3b"  # noqa: S105 - a fake token for the fake Jenkins
CRUMB = "c7a9e1f0b2d4"
SESSION = "node0x1y2z3"
ERROR_BODY = f"Invalid password/token for user: {USER} {TOKEN}"

PROJECT = UUID("0b0e7f3c-4c1e-4d8a-9a51-6f7d3c2b1a00")
RELEASE = UUID("5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d")
SHA = "3f786850e387550fdab836ed7e6dc881de23001b"
DIGEST = "sha256:" + "ab" * 32

REQUEST = BuildRequest(
    project_id=PROJECT,
    release_id=RELEASE,
    attempt=1,
    repository_url="http://webblock-git/site-a.git",
    commit_sha=SHA,
    image_repository=f"registry.test:5000/webblock/sites/{PROJECT}",
)


def params_of(request: BuildRequest) -> dict[str, str]:
    return {
        "PROJECT_ID": str(request.project_id),
        "RELEASE_ID": str(request.release_id),
        "ATTEMPT": str(request.attempt),
        "REPOSITORY_URL": request.repository_url,
        "COMMIT_SHA": request.commit_sha,
        "IMAGE_REPOSITORY": request.image_repository,
    }


def with_attempt(request: BuildRequest, attempt: int) -> BuildRequest:
    return BuildRequest(
        project_id=request.project_id,
        release_id=request.release_id,
        attempt=attempt,
        repository_url=request.repository_url,
        commit_sha=request.commit_sha,
        image_repository=request.image_repository,
    )


def result_json(**fields: object) -> bytes:
    return json.dumps(fields).encode()


def actions(params: dict[str, str]) -> list[dict[str, object]]:
    return [
        {},
        {
            "_class": "hudson.model.ParametersAction",
            "parameters": [
                {"_class": "hudson.model.StringParameterValue", "name": k, "value": v}
                for k, v in params.items()
            ],
        },
        {"_class": "hudson.model.CauseAction"},
    ]


@dataclass
class FakeBuild:
    number: int
    queue_id: int
    params: dict[str, str]
    building: bool = True
    result: str | None = None
    artifact: bytes | None = None


@dataclass
class FakeQueueItem:
    id: int
    job: str
    params: dict[str, str]
    polls_left: int | None  # None: the item never leaves the queue
    build: int | None = None
    cancelled: bool = False
    expired: bool = False  # Jenkins forgets left items after a few minutes


@dataclass
class FakeJenkins:
    crumb: bool = False
    polls_before_start: int | None = 0
    queue: dict[int, FakeQueueItem] = field(default_factory=dict)
    builds: dict[int, FakeBuild] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)  # method, host, path
    headers_seen: list[dict[str, str]] = field(default_factory=list)  # one per request, lower case
    posted: list[dict[str, str]] = field(default_factory=list)
    crumb_headers_seen: list[str | None] = field(default_factory=list)
    job_trees: list[str] = field(default_factory=list)
    forced_status: int | None = None
    next_queue_id: int = 100
    cancelled_items: list[int] = field(default_factory=list)
    stopped: list[int] = field(default_factory=list)
    artifact_stream: httpx2.AsyncByteStream | None = None
    artifact_headers: dict[str, str] = field(default_factory=dict)
    next_build: int = 1

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def add_queue_item(
        self, params: dict[str, str], *, job: str = JOB, polls_left: int | None = None
    ) -> FakeQueueItem:
        item = FakeQueueItem(self.next_queue_id, job, params, polls_left)
        self.queue[item.id] = item
        self.next_queue_id += 1
        return item

    def add_build(
        self,
        params: dict[str, str],
        *,
        building: bool = True,
        result: str | None = None,
        artifact: bytes | None = None,
        queue_id: int | None = None,
    ) -> FakeBuild:
        if queue_id is None:
            queue_id = self.next_queue_id
            self.next_queue_id += 1
        build = FakeBuild(self.next_build, queue_id, params, building, result, artifact)
        self.builds[build.number] = build
        self.next_build += 1
        return build

    def start_item(self, item: FakeQueueItem) -> None:
        item.build = self.add_build(item.params, queue_id=item.id).number

    def queue_polls(self, item_id: int) -> int:
        return sum(1 for _, _, p in self.calls if p == f"/queue/item/{item_id}/api/json")

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.calls.append((request.method, request.url.host, path))
        self.headers_seen.append({k.lower(): v for k, v in request.headers.items()})
        if self.forced_status is not None:
            return httpx2.Response(self.forced_status, text=ERROR_BODY)
        expected = "Basic " + base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx2.Response(401, text=ERROR_BODY)
        if request.method == "GET" and path == "/crumbIssuer/api/json":
            if not self.crumb:
                return httpx2.Response(404, text="Not Found")
            return httpx2.Response(
                200,
                json={"crumb": CRUMB, "crumbRequestField": "Jenkins-Crumb"},
                headers={"set-cookie": f"JSESSIONID.abc={SESSION}; Path=/; HttpOnly"},
            )
        if request.method == "POST" and path.endswith("/buildWithParameters"):
            return self.trigger(request, path.removesuffix("/buildWithParameters"))
        if request.method == "POST" and path == "/queue/cancelItem":
            return self.cancel_item(request)
        if request.method == "POST" and path.startswith(f"/job/{JOB}/") and path.endswith("/stop"):
            return self.stop(request, path.removeprefix(f"/job/{JOB}/").removesuffix("/stop"))
        if request.method == "GET" and path == "/queue/api/json":
            return httpx2.Response(200, json={"items": self.waiting_items()})
        if request.method == "GET" and path.startswith("/queue/item/"):
            return self.queue_item(int(path.split("/")[3]))
        if request.method == "GET" and path == f"/job/{JOB}/api/json":
            self.job_trees.append(request.url.params.get("tree", ""))
            return httpx2.Response(200, json={"builds": self.build_list()})
        if request.method == "GET" and path.startswith(f"/job/{JOB}/"):
            return self.build_resource(path.removeprefix(f"/job/{JOB}/"))
        return httpx2.Response(404, text="Not Found")

    def trigger(self, request: httpx2.Request, job_path: str) -> httpx2.Response:
        self.crumb_headers_seen.append(request.headers.get("jenkins-crumb"))
        if job_path != f"/job/{JOB}":
            return httpx2.Response(404, text="Not Found")
        if self.crumb and (
            request.headers.get("jenkins-crumb") != CRUMB
            or SESSION not in request.headers.get("cookie", "")
        ):
            return httpx2.Response(403, text="No valid crumb was included in the request")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.posted.append(form)
        item = self.add_queue_item(form, polls_left=self.polls_before_start)
        return httpx2.Response(201, headers={"location": f"{ROOT}/queue/item/{item.id}/"})

    def crumb_ok(self, request: httpx2.Request) -> bool:
        self.crumb_headers_seen.append(request.headers.get("jenkins-crumb"))
        return not self.crumb or (
            request.headers.get("jenkins-crumb") == CRUMB
            and SESSION in request.headers.get("cookie", "")
        )

    def cancel_item(self, request: httpx2.Request) -> httpx2.Response:
        """Jenkins answers 204; an unknown or already left item is 404."""
        if not self.crumb_ok(request):
            return httpx2.Response(403, text="No valid crumb was included in the request")
        raw = request.url.params.get("id", "")
        item = self.queue.get(int(raw)) if raw.isdigit() else None
        if item is None or item.expired or item.build is not None or item.cancelled:
            return httpx2.Response(404, text="Not Found")
        item.cancelled = True
        self.cancelled_items.append(item.id)
        return httpx2.Response(204)

    def stop(self, request: httpx2.Request, number: str) -> httpx2.Response:
        """Jenkins aborts a running build and redirects to the build page; any build answers
        the redirect, an unknown one 404."""
        if not self.crumb_ok(request):
            return httpx2.Response(403, text="No valid crumb was included in the request")
        build = self.builds.get(int(number)) if number.isdigit() else None
        if build is None:
            return httpx2.Response(404, text="Not Found")
        self.stopped.append(build.number)
        if build.building:
            build.building, build.result = False, "ABORTED"
        return httpx2.Response(302, headers={"location": f"{ROOT}/job/{JOB}/{build.number}/"})

    def waiting_items(self) -> list[dict[str, object]]:
        return [
            {
                "id": item.id,
                "task": {"name": item.job, "url": f"{ROOT}/job/{item.job}/"},
                "actions": actions(item.params),
            }
            for item in self.queue.values()
            if item.build is None and not item.cancelled and not item.expired
        ]

    def queue_item(self, item_id: int) -> httpx2.Response:
        item = self.queue.get(item_id)
        if item is None or item.expired:
            return httpx2.Response(404, text="Not Found")
        if item.cancelled:
            return httpx2.Response(
                200,
                json={
                    "_class": "hudson.model.Queue$LeftItem",
                    "cancelled": True,
                    "executable": None,
                },
            )
        if item.build is None and item.polls_left is not None:
            if item.polls_left == 0:
                self.start_item(item)
            else:
                item.polls_left -= 1
        if item.build is None:
            return httpx2.Response(
                200, json={"_class": "hudson.model.Queue$WaitingItem", "why": "Waiting"}
            )
        return httpx2.Response(
            200,
            json={
                "_class": "hudson.model.Queue$LeftItem",
                "cancelled": False,
                "executable": {"number": item.build, "url": f"{ROOT}/job/{JOB}/{item.build}/"},
            },
        )

    def build_list(self) -> list[dict[str, object]]:
        return [
            {
                "number": b.number,
                "url": f"{ROOT}/job/{JOB}/{b.number}/",
                "queueId": b.queue_id,
                "actions": actions(b.params),
            }
            for b in sorted(self.builds.values(), key=lambda b: -b.number)
        ]

    def build_resource(self, rest: str) -> httpx2.Response:
        number, _, tail = rest.partition("/")
        build = self.builds.get(int(number)) if number.isdigit() else None
        if build is None:
            return httpx2.Response(404, text="Not Found")
        if tail == "api/json":
            return httpx2.Response(200, json={"building": build.building, "result": build.result})
        if tail == "artifact/result.json" and self.artifact_stream is not None:
            return httpx2.Response(200, stream=self.artifact_stream, headers=self.artifact_headers)
        if tail == "artifact/result.json" and build.artifact is not None:
            return httpx2.Response(200, content=build.artifact)
        return httpx2.Response(404, text="Not Found")


def make_adapter(
    fake: FakeJenkins | None = None,
    *,
    base_url: str = BASE,
    queue_polls: int = 5,
    timeout: float = 2.0,
    operation_timeout: float = 5.0,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> JenkinsBuildSystem:
    return JenkinsBuildSystem(
        base_url=base_url,
        user=USER,
        api_token=ApiToken(TOKEN),
        job=JOB,
        timeout=httpx2.Timeout(timeout),
        operation_timeout=operation_timeout,
        queue_polls=queue_polls,
        queue_poll_interval=0.0,
        recent_builds=25,
        transport=transport if transport is not None else (fake or FakeJenkins()).transport(),
    )


def assert_sanitized(exc: BaseException) -> None:
    """No token, no response body and no URL in what an error carries."""
    text = f"{exc} {exc!r}"
    assert TOKEN not in text
    assert "Invalid password" not in text
    assert "://" not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__


def build_handle(number: int) -> BuildHandle:
    return BuildHandle(f"jenkins/build/{number}")


def queue_handle(item_id: int) -> BuildHandle:
    return BuildHandle(f"jenkins/queue/{item_id}")


LOG_REF = re.compile(r"[A-Za-z0-9._/-]{1,500}")


def assert_log_ref(handle: BuildHandle) -> None:
    """The rule of revision 19 for ``release_steps.log_ref``: opaque, never a URL."""
    assert LOG_REF.fullmatch(handle.ref), handle.ref
    assert all(segment not in ("", ".", "..") for segment in handle.ref.split("/")), handle.ref
    assert "://" not in handle.ref


# --- start -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_without_crumb_triggers_the_job_and_returns_the_build_ref() -> None:
    fake = FakeJenkins(crumb=False, polls_before_start=2)
    handle = await make_adapter(fake).start(REQUEST)

    assert handle == build_handle(1)
    assert fake.posted == [params_of(REQUEST)]
    assert fake.crumb_headers_seen == [None]
    assert fake.queue_polls(100) == 3
    assert {host for _, host, _ in fake.calls} == {"jenkins.test"}


@pytest.mark.asyncio
async def test_fr8_2_a_jenkins_call_carries_no_user_request_or_trace_header() -> None:
    """FR-8.2: Jenkins is an outside host. Even inside a user's request, with a request ID and a
    recording span, no call to it carries ``x-user-id``, ``x-request-id`` or ``traceparent``."""
    fake = FakeJenkins(crumb=True, polls_before_start=1)
    user = WebBlockUser(user_id=UUID("0199e7a0-0000-7000-8000-0000000000aa"))
    configure_tracing(
        service_name="provisioning-test",
        endpoint="http://c:4318",
        ratio=1.0,
        exporter=InMemorySpanExporter(),
    )
    try:
        with (
            commons_context.bound(RequestContext(request_id="req-82", user=user)),
            correlation_scope("req-82"),
            span("build start"),
        ):
            await make_adapter(fake).start(REQUEST)
    finally:
        shutdown_tracing()

    assert fake.headers_seen
    for headers in fake.headers_seen:
        assert not {"x-user-id", "x-request-id", "traceparent", "tracestate"} & headers.keys()


@pytest.mark.asyncio
async def test_start_with_crumb_sends_the_crumb_and_its_session_cookie() -> None:
    fake = FakeJenkins(crumb=True)
    handle = await make_adapter(fake).start(REQUEST)

    assert handle == build_handle(1)
    assert fake.crumb_headers_seen == [CRUMB]
    assert fake.posted == [params_of(REQUEST)]


@pytest.mark.asyncio
async def test_start_returns_the_queue_item_when_it_stays_waiting() -> None:
    fake = FakeJenkins(polls_before_start=None)
    handle = await make_adapter(fake, queue_polls=3).start(REQUEST)

    assert handle == queue_handle(100)
    assert fake.queue_polls(100) == 3


# --- status of a queue handle ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_of_a_waiting_queue_item_is_queued() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST), polls_left=None)

    status = await make_adapter(fake).status(queue_handle(item.id))

    assert status == BuildStatus(BuildState.QUEUED)


@pytest.mark.asyncio
async def test_status_of_a_queue_item_follows_its_executable() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST), polls_left=0)

    status = await make_adapter(fake).status(queue_handle(item.id))

    assert status == BuildStatus(BuildState.RUNNING)
    assert ("GET", "jenkins.test", f"/job/{JOB}/1/api/json") in fake.calls


@pytest.mark.asyncio
async def test_status_of_a_forgotten_queue_item_finds_its_build_by_queue_id() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST), polls_left=0)
    fake.start_item(item)
    item.expired = True
    fake.add_build(params_of(REQUEST), building=False, result="FAILURE")  # another build
    assert item.build is not None
    fake.builds[item.build].building = False
    fake.builds[item.build].result = "ABORTED"

    status = await make_adapter(fake).status(queue_handle(item.id))

    assert status == BuildStatus(BuildState.FAILED, failure_code="pipeline_failed")
    assert ("GET", "jenkins.test", f"/job/{JOB}/{item.build}/api/json") in fake.calls


@pytest.mark.asyncio
async def test_status_of_a_forgotten_queue_item_without_a_build_is_lost() -> None:
    fake = FakeJenkins()
    fake.add_build(params_of(REQUEST), queue_id=7)

    status = await make_adapter(fake).status(queue_handle(8))

    assert status == BuildStatus(BuildState.LOST)


@pytest.mark.asyncio
async def test_status_of_a_cancelled_queue_item_is_failed() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST), polls_left=None)
    item.cancelled = True

    status = await make_adapter(fake).status(queue_handle(item.id))

    assert status == BuildStatus(BuildState.FAILED, failure_code="cancelled")


# --- find --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_returns_a_build_with_the_same_project_release_and_attempt() -> None:
    fake = FakeJenkins()
    fake.add_build(params_of(with_attempt(REQUEST, 2)), building=False, result="FAILURE")
    wanted = fake.add_build(params_of(REQUEST))
    fake.add_build(params_of(with_attempt(REQUEST, 3)))

    handle = await make_adapter(fake).find(REQUEST)

    assert handle == build_handle(wanted.number)
    assert fake.job_trees == ["builds[url,number,queueId,actions[parameters[name,value]]]{0,25}"]


@pytest.mark.asyncio
async def test_find_returns_a_queue_item_with_the_same_parameters() -> None:
    fake = FakeJenkins()
    fake.add_queue_item(params_of(REQUEST), job="another-job")
    item = fake.add_queue_item(params_of(REQUEST))

    handle = await make_adapter(fake).find(REQUEST)

    assert handle == queue_handle(item.id)


@pytest.mark.asyncio
async def test_find_returns_none_when_nothing_was_started() -> None:
    fake = FakeJenkins()
    other = BuildRequest(
        project_id=UUID("9d1e2f3a-4b5c-4d6e-8f70-819203a4b5c6"),
        release_id=RELEASE,
        attempt=1,
        repository_url=REQUEST.repository_url,
        commit_sha=SHA,
        image_repository=REQUEST.image_repository,
    )
    fake.add_build(params_of(other))

    assert await make_adapter(fake).find(REQUEST) is None


@pytest.mark.asyncio
async def test_find_ignores_a_near_miss_that_differs_only_in_attempt() -> None:
    fake = FakeJenkins()
    fake.add_build(params_of(REQUEST), building=False, result="FAILURE")
    fake.add_queue_item(params_of(REQUEST))

    assert await make_adapter(fake).find(with_attempt(REQUEST, 2)) is None


# --- status of a build handle ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_of_a_running_build_is_running() -> None:
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST))

    assert await make_adapter(fake).status(build_handle(build.number)) == BuildStatus(
        BuildState.RUNNING
    )


@pytest.mark.asyncio
async def test_status_of_a_successful_build_echoes_result_json() -> None:
    fake = FakeJenkins()
    artifact = result_json(
        project_id=str(PROJECT),
        release_id=str(RELEASE),
        attempt=1,
        commit_sha=SHA,
        image_digest=DIGEST,
    )
    build = fake.add_build(params_of(REQUEST), building=False, result="SUCCESS", artifact=artifact)

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(
        BuildState.SUCCEEDED,
        project_id=PROJECT,
        release_id=RELEASE,
        attempt=1,
        commit_sha=SHA,
        image_digest=DIGEST,
    )


@pytest.mark.asyncio
async def test_status_echoes_a_wrong_commit_unchanged_for_the_use_case_to_judge() -> None:
    fake = FakeJenkins()
    other_sha = "0" * 40
    artifact = result_json(
        project_id=str(PROJECT),
        release_id=str(RELEASE),
        attempt="2",
        commit_sha=other_sha,
        image_digest="not-a-digest",
    )
    build = fake.add_build(params_of(REQUEST), building=False, result="SUCCESS", artifact=artifact)

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(
        BuildState.SUCCEEDED,
        project_id=PROJECT,
        release_id=RELEASE,
        attempt=2,
        commit_sha=other_sha,
        image_digest="not-a-digest",
    )


@pytest.mark.asyncio
async def test_status_of_a_success_without_result_json_is_failed() -> None:
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST), building=False, result="SUCCESS")

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(BuildState.FAILED, failure_code="result_missing")


@pytest.mark.asyncio
async def test_status_of_a_failure_takes_the_failure_code_from_result_json() -> None:
    fake = FakeJenkins()
    artifact = result_json(project_id=str(PROJECT), failure_code="commit_not_found")
    build = fake.add_build(params_of(REQUEST), building=False, result="FAILURE", artifact=artifact)

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(BuildState.FAILED, failure_code="commit_not_found")


@pytest.mark.asyncio
async def test_status_of_a_failure_without_result_json_is_pipeline_failed() -> None:
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST), building=False, result="FAILURE")

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(BuildState.FAILED, failure_code="pipeline_failed")


@pytest.mark.asyncio
async def test_status_never_passes_log_text_as_a_failure_code() -> None:
    fake = FakeJenkins()
    artifact = result_json(failure_code="ERROR: npm ci failed\n  at line 12")
    build = fake.add_build(params_of(REQUEST), building=False, result="FAILURE", artifact=artifact)

    status = await make_adapter(fake).status(build_handle(build.number))

    assert status == BuildStatus(BuildState.FAILED, failure_code="pipeline_failed")


@pytest.mark.asyncio
async def test_status_of_an_aborted_build_is_failed() -> None:
    fake = FakeJenkins()
    aborted = fake.add_build(params_of(REQUEST), building=False, result="ABORTED")
    coded = fake.add_build(
        params_of(REQUEST),
        building=False,
        result="ABORTED",
        artifact=result_json(failure_code="build_failed"),
    )
    adapter = make_adapter(fake)

    assert await adapter.status(build_handle(aborted.number)) == BuildStatus(
        BuildState.FAILED, failure_code="pipeline_failed"
    )
    assert await adapter.status(build_handle(coded.number)) == BuildStatus(
        BuildState.FAILED, failure_code="build_failed"
    )


@pytest.mark.asyncio
async def test_status_of_an_unknown_build_is_lost() -> None:
    assert await make_adapter(FakeJenkins()).status(build_handle(42)) == BuildStatus(
        BuildState.LOST
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref",
    [
        f"http://elsewhere.test/job/{JOB}/1/",
        f"{BASE}/job/{JOB}/1/",
        f"{BASE}/queue/item/100/",
        "jenkins/build/../1",
        "jenkins/queue/../../job/other/1",
        "jenkins/build/1/..",
        "jenkins/./build/1",
        "jenkins//build/1",
        "/jenkins/build/1",
        "jenkins/build/1/",
        "jenkins/build/0",
        "jenkins/build/",
        "jenkins/queue/x",
        "jenkins/build/1?tree=x",
        "",
    ],
)
async def test_status_refuses_any_other_ref_before_a_request(ref: str) -> None:
    fake = FakeJenkins()
    with pytest.raises(BuildSystemRejected) as caught:
        await make_adapter(fake).status(BuildHandle(ref))
    assert fake.calls == []
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_every_ref_the_adapter_makes_is_a_valid_log_ref() -> None:
    started = FakeJenkins(polls_before_start=0)
    waiting = FakeJenkins(polls_before_start=None)
    in_queue = FakeJenkins()
    in_queue.add_queue_item(params_of(REQUEST))
    built = FakeJenkins()
    built.add_build(params_of(REQUEST))

    handles = [
        await make_adapter(started).start(REQUEST),
        await make_adapter(waiting, queue_polls=1).start(REQUEST),
        await make_adapter(in_queue).find(REQUEST),
        await make_adapter(built).find(REQUEST),
    ]

    assert handles == [build_handle(1), queue_handle(100), queue_handle(100), build_handle(1)]
    for handle in handles:
        assert handle is not None
        assert_log_ref(handle)


# --- errors ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 401, 403])
async def test_a_refused_request_is_rejected_without_token_or_body(code: int) -> None:
    fake = FakeJenkins()
    fake.forced_status = code
    adapter = make_adapter(fake)

    with pytest.raises(BuildSystemRejected) as caught:
        await adapter.start(REQUEST)
    assert_sanitized(caught.value)
    with pytest.raises(BuildSystemRejected) as caught:
        await adapter.status(build_handle(1))
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_a_wrong_token_is_rejected() -> None:
    fake = FakeJenkins()
    adapter = JenkinsBuildSystem(
        base_url=BASE,
        user=USER,
        api_token=ApiToken("wrong-token"),
        job=JOB,
        timeout=httpx2.Timeout(2.0),
        operation_timeout=5.0,
        queue_polls=1,
        queue_poll_interval=0.0,
        recent_builds=25,
        transport=fake.transport(),
    )
    with pytest.raises(BuildSystemRejected) as caught:
        await adapter.find(REQUEST)
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_an_unknown_job_is_rejected() -> None:
    fake = FakeJenkins()
    adapter = JenkinsBuildSystem(
        base_url=BASE,
        user=USER,
        api_token=ApiToken(TOKEN),
        job="no-such-job",
        timeout=httpx2.Timeout(2.0),
        operation_timeout=5.0,
        queue_polls=1,
        queue_poll_interval=0.0,
        recent_builds=25,
        transport=fake.transport(),
    )
    with pytest.raises(BuildSystemRejected) as caught:
        await adapter.start(REQUEST)
    assert_sanitized(caught.value)
    with pytest.raises(BuildSystemRejected):
        await adapter.find(REQUEST)
    assert fake.posted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 502, 503])
async def test_a_server_error_is_unavailable(code: int) -> None:
    fake = FakeJenkins()
    fake.forced_status = code
    adapter = make_adapter(fake)

    with pytest.raises(BuildSystemUnavailable) as caught:
        await adapter.start(REQUEST)
    assert_sanitized(caught.value)
    with pytest.raises(BuildSystemUnavailable) as caught:
        await adapter.find(REQUEST)
    assert_sanitized(caught.value)
    with pytest.raises(BuildSystemUnavailable) as caught:
        await adapter.status(build_handle(1))
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_a_jenkins_that_never_answers_times_out_as_unavailable() -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()  # never answers; returns once the client gives up
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        adapter = make_adapter(
            base_url=f"http://127.0.0.1:{port}", timeout=0.2, transport=httpx2.AsyncHTTPTransport()
        )
        with pytest.raises(BuildSystemUnavailable) as caught:
            await adapter.status(build_handle(1))
        assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_a_refused_connection_is_unavailable() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    adapter = make_adapter(
        base_url=f"http://127.0.0.1:{port}", transport=httpx2.AsyncHTTPTransport()
    )

    with pytest.raises(BuildSystemUnavailable) as caught:
        await adapter.start(REQUEST)
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_an_operation_past_its_deadline_is_unavailable() -> None:
    never = asyncio.Event()

    async def hang(request: httpx2.Request) -> httpx2.Response:
        await never.wait()
        return httpx2.Response(200)

    adapter = make_adapter(operation_timeout=0.05, transport=httpx2.MockTransport(hang))
    with pytest.raises(BuildSystemUnavailable) as caught:
        await adapter.find(REQUEST)
    assert_sanitized(caught.value)


# --- result.json is read up to its bound (QA-02) -----------------------------------------------

RESULT_LIMIT = 64 * 1024
CHUNK = 8 * 1024


class EndlessBody(httpx2.AsyncByteStream):
    """A result.json that never ends; counts what the adapter pulled from it."""

    def __init__(self, chunk: int = CHUNK, stop_at: int = 96 * 1024 * 1024) -> None:
        self.chunk, self.stop_at, self.sent = chunk, stop_at, 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while self.sent < self.stop_at:  # a safety stop for this test process only
            self.sent += self.chunk
            yield b" " * self.chunk
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        return None


def succeeded_build(fake: FakeJenkins) -> FakeBuild:
    return fake.add_build(params_of(REQUEST), building=False, result="SUCCESS")


@pytest.mark.asyncio
async def test_qa02_status_stops_reading_a_result_json_that_is_far_over_its_limit() -> None:
    """QA's reproduction (tests/qa/test_qa_jenkins_result_unbounded_body.py at d487207), on the
    fake Jenkins and with a tighter bound: at most one chunk past the 64 KiB limit is read."""
    fake = FakeJenkins()
    build = succeeded_build(fake)
    body = EndlessBody()
    fake.artifact_stream = body
    status = await make_adapter(fake).status(build_handle(build.number))
    assert status == BuildStatus(BuildState.FAILED, failure_code="result_invalid")
    assert body.sent <= RESULT_LIMIT + CHUNK, f"the adapter read {body.sent} bytes"


@pytest.mark.asyncio
async def test_a_declared_length_over_the_limit_is_refused_before_any_byte_is_read() -> None:
    fake = FakeJenkins()
    build = succeeded_build(fake)
    body = EndlessBody()
    fake.artifact_stream = body
    fake.artifact_headers = {"content-length": str(10 * 1024 * 1024)}
    status = await make_adapter(fake).status(build_handle(build.number))
    assert status == BuildStatus(BuildState.FAILED, failure_code="result_invalid")
    assert body.sent == 0


@pytest.mark.asyncio
async def test_the_artifact_is_asked_for_without_compression() -> None:
    """A compressed answer would be bounded only after decompression; the adapter asks for
    identity so the bound counts what crosses the network."""
    seen: list[str | None] = []
    fake = FakeJenkins()
    build = fake.add_build(
        params_of(REQUEST), building=False, result="SUCCESS", artifact=b'{"attempt": 1}'
    )

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/artifact/result.json"):
            seen.append(request.headers.get("accept-encoding"))
        return await fake.handle(request)

    await make_adapter(transport=httpx2.MockTransport(handle)).status(build_handle(build.number))
    assert seen == ["identity"]


@pytest.mark.asyncio
async def test_a_compressed_result_json_is_refused_unread() -> None:
    fake = FakeJenkins()
    build = succeeded_build(fake)
    body = EndlessBody()
    fake.artifact_stream = body
    fake.artifact_headers = {"content-encoding": "gzip"}
    status = await make_adapter(fake).status(build_handle(build.number))
    assert status == BuildStatus(BuildState.FAILED, failure_code="result_invalid")
    assert body.sent == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("padding", "accepted"), [(0, True), (1, False)])
async def test_a_result_json_of_exactly_the_limit_is_read_and_one_byte_more_is_not(
    padding: int, accepted: bool
) -> None:
    doc = result_json(
        project_id=str(PROJECT),
        release_id=str(RELEASE),
        attempt=1,
        commit_sha=SHA,
        image_digest=DIGEST,
    )
    raw = doc + b" " * (RESULT_LIMIT - len(doc) + padding)
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST), building=False, result="SUCCESS", artifact=raw)
    status = await make_adapter(fake).status(build_handle(build.number))
    if accepted:
        assert status.state is BuildState.SUCCEEDED
        assert status.image_digest == DIGEST
    else:
        assert status == BuildStatus(BuildState.FAILED, failure_code="result_invalid")


@pytest.mark.asyncio
async def test_a_failed_builds_oversized_result_json_falls_back_to_pipeline_failed() -> None:
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST), building=False, result="FAILURE")
    fake.artifact_stream = EndlessBody()
    status = await make_adapter(fake).status(build_handle(build.number))
    assert status == BuildStatus(BuildState.FAILED, failure_code="pipeline_failed")


# --- cancel ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("crumb", [False, True])
async def test_cancel_aborts_a_running_build(crumb: bool) -> None:
    fake = FakeJenkins(crumb=crumb)
    build = fake.add_build(params_of(REQUEST))
    adapter = make_adapter(fake)
    await adapter.cancel(build_handle(build.number))
    assert fake.stopped == [build.number]
    assert ("POST", "jenkins.test", f"/job/{JOB}/{build.number}/stop") in fake.calls
    if crumb:
        assert fake.crumb_headers_seen == [CRUMB]
    assert await adapter.status(build_handle(build.number)) == BuildStatus(
        BuildState.FAILED, failure_code="pipeline_failed"
    )


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_leaves_a_finished_build_alone() -> None:
    fake = FakeJenkins()
    running = fake.add_build(params_of(REQUEST))
    adapter = make_adapter(fake)
    await adapter.cancel(build_handle(running.number))
    await adapter.cancel(build_handle(running.number))  # already aborted
    finished = fake.add_build(params_of(REQUEST), building=False, result="SUCCESS")
    await adapter.cancel(build_handle(finished.number))
    assert fake.stopped == [running.number]
    assert finished.result == "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("handle", [build_handle(77), queue_handle(4242)])
async def test_cancel_of_a_build_or_queue_item_jenkins_does_not_know_is_not_an_error(
    handle: BuildHandle,
) -> None:
    fake = FakeJenkins()
    await make_adapter(fake).cancel(handle)
    assert fake.stopped == []
    assert fake.cancelled_items == []
    assert not any(method == "POST" for method, _, _ in fake.calls)


@pytest.mark.asyncio
async def test_cancel_of_a_build_that_vanishes_between_the_read_and_the_stop_is_not_an_error() -> (
    None
):
    fake = FakeJenkins()
    build = fake.add_build(params_of(REQUEST))

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            del fake.builds[build.number]  # deleted by Jenkins' build discarder meanwhile
        return await fake.handle(request)

    await make_adapter(transport=httpx2.MockTransport(handle)).cancel(build_handle(build.number))
    assert fake.stopped == []


@pytest.mark.asyncio
async def test_cancel_of_a_waiting_queue_item_cancels_the_item() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST))
    adapter = make_adapter(fake)
    await adapter.cancel(queue_handle(item.id))
    assert fake.cancelled_items == [item.id]
    assert ("POST", "jenkins.test", "/queue/cancelItem") in fake.calls
    await adapter.cancel(queue_handle(item.id))  # already cancelled: no second POST
    assert fake.cancelled_items == [item.id]
    assert await adapter.status(queue_handle(item.id)) == BuildStatus(
        BuildState.FAILED, failure_code="cancelled"
    )


@pytest.mark.asyncio
async def test_cancel_of_a_queue_item_that_became_a_build_aborts_the_build() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST))
    fake.start_item(item)
    await make_adapter(fake).cancel(queue_handle(item.id))
    assert fake.stopped == [item.build]
    assert fake.cancelled_items == []


@pytest.mark.asyncio
async def test_cancel_of_a_forgotten_queue_item_finds_its_build_by_queue_id() -> None:
    fake = FakeJenkins()
    item = fake.add_queue_item(params_of(REQUEST))
    fake.start_item(item)
    item.expired = True
    await make_adapter(fake).cancel(queue_handle(item.id))
    assert fake.stopped == [item.build]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error"),
    [(503, BuildSystemUnavailable), (429, BuildSystemUnavailable), (401, BuildSystemRejected)],
)
async def test_cancel_errors_are_mapped_and_sanitized(code: int, error: type[Exception]) -> None:
    fake = FakeJenkins()
    fake.forced_status = code
    with pytest.raises(error) as caught:
        await make_adapter(fake).cancel(build_handle(1))
    assert_sanitized(caught.value)


@pytest.mark.asyncio
async def test_cancel_refuses_a_foreign_handle_without_a_request() -> None:
    fake = FakeJenkins()
    with pytest.raises(BuildSystemRejected) as caught:
        await make_adapter(fake).cancel(BuildHandle("other/build/1"))
    assert_sanitized(caught.value)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_cancel_of_an_unreachable_jenkins_is_unavailable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(BuildSystemUnavailable) as caught:
        await make_adapter(transport=httpx2.MockTransport(refuse)).cancel(build_handle(1))
    assert_sanitized(caught.value)


# --- configuration and the secret --------------------------------------------------------------


def test_the_api_token_never_shows_in_its_text() -> None:
    token = ApiToken(TOKEN)
    assert TOKEN not in repr(token)
    assert TOKEN not in str(token)
    assert TOKEN not in f"{token}"
    assert token.reveal() == TOKEN


@pytest.mark.parametrize(
    "base_url",
    [
        f"http://{USER}:{TOKEN}@jenkins.test:8080",
        "ftp://jenkins.test",
        "jenkins.test:8080",
        "http://jenkins.test:8080/?token=" + TOKEN,
    ],
)
def test_a_base_url_with_credentials_or_a_wrong_shape_is_refused(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url") as caught:
        make_adapter(base_url=base_url)
    assert TOKEN not in str(caught.value)
    assert "jenkins.test" not in str(caught.value)


def test_a_job_name_outside_the_allowed_characters_is_refused() -> None:
    with pytest.raises(ValueError, match="job"):
        JenkinsBuildSystem(
            base_url=BASE,
            user=USER,
            api_token=ApiToken(TOKEN),
            job="../script",
            timeout=httpx2.Timeout(2.0),
            operation_timeout=5.0,
            queue_polls=1,
            queue_poll_interval=0.0,
            recent_builds=25,
        )
