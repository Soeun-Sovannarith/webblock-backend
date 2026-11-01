"""The build-system port over Jenkins' REST API (decision 0014 section 3, "Build").

One job, parameterized by ``PROJECT_ID``, ``RELEASE_ID``, ``ATTEMPT``, ``REPOSITORY_URL``,
``COMMIT_SHA`` and ``IMAGE_REPOSITORY``, builds and pushes the image and archives ``result.json``
(task A's pipeline). This adapter triggers it, finds it again, and reads its state.

Handles. A handle's ``ref`` is opaque and never a URL (revision 19, ``release_steps.log_ref``):
either a build, ``jenkins/build/<number>``, or a queue item, ``jenkins/queue/<id>``. ``start``
follows the queue item for a bounded number of polls and returns the build when Jenkins has
assigned one, otherwise the queue item. ``status`` resolves a
queue item each time it is read: while it waits it is ``QUEUED``; once it has an executable the
build's state is reported; once Jenkins has forgotten the item (it keeps left items for a few
minutes only) the build is found by its ``queueId`` among the job's recent builds, and a queue item
that is neither in the queue nor among those builds is ``LOST``.

Every request URL is built from the configured base URL and a number parsed from the ref, never
from the absolute URLs Jenkins puts in its answers (they carry Jenkins' own root URL, which may name
another host). ``status`` refuses any other ref before sending a request, so the credentials are
only ever sent to the configured Jenkins.

Errors. Connection failures, timeouts, 429, 5xx and unreadable answers raise
``BuildSystemUnavailable``; other refusals (400, 401, 403, an unknown job, a foreign handle) raise
``BuildSystemRejected``. Messages name the operation and the HTTP status only: never the token, a
response body or a URL; the client's own exception is dropped (``from None``). The adapter does not
log.

Results. The adapter echoes ``result.json`` as the pipeline wrote it. Comparing the result with the
attempt is the use case's job (see the port). The file is streamed, asked for without compression,
and reading stops at 64 KiB: a larger file (declared or read) is ``result_invalid`` and is never
held in memory whole (QA-02).

Cancel. ``cancel`` aborts the build behind a handle (``POST <build>/stop``), or cancels a queue
item that has no build yet (``POST /queue/cancelItem``); a queue item that became a build is
resolved first, as ``status`` does. It reads the build or item before it writes, so a finished,
aborted, cancelled or unknown build sends no request that changes anything and is not an error.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Literal, NoReturn
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx2

from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildState,
    BuildStatus,
    BuildSystemRejected,
    BuildSystemUnavailable,
)

PIPELINE_FAILED: Final = "pipeline_failed"  # failed or aborted without a usable failure_code
RESULT_MISSING: Final = "result_missing"  # SUCCESS without an archived result.json
RESULT_INVALID: Final = "result_invalid"  # SUCCESS with a result.json that is not a JSON object
CANCELLED: Final = "cancelled"  # the queue item was cancelled before it started

_JOB_SEGMENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FAILURE_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BUILD_TAIL: Final = re.compile(r"([1-9][0-9]{0,9})/")
_QUEUE_TAIL: Final = re.compile(r"queue/item/([1-9][0-9]{0,18})/")
_BUILD_REF: Final = re.compile(r"jenkins/build/([1-9][0-9]{0,9})")
_QUEUE_REF: Final = re.compile(r"jenkins/queue/([1-9][0-9]{0,18})")
_ATTEMPT: Final = re.compile(r"[0-9]{1,9}")
_RESULT_LIMIT: Final = 64 * 1024  # bytes; result.json holds five short fields
_BUILDS_TREE: Final = "builds[url,number,queueId,actions[parameters[name,value]]]{{0,{n}}}"
_QUEUE_TREE: Final = "items[id,task[url],actions[parameters[name,value]]]"

type _Json = dict[str, Any]


class ApiToken:
    """A Jenkins API token. Its text never shows the value; ``reveal`` is the only way to it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("api_token is empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "ApiToken('<redacted>')"

    __str__ = __repr__


class JenkinsBuildSystem:
    """Implements ``BuildSystem`` for one Jenkins job.

    All arguments are required (no defaults, OD-15) except ``transport``, which tests use to put a
    fake Jenkins behind the client. Each operation opens its own client, and closes it, and with it
    the transport, when it ends.

    - ``timeout``: the client's connect, read, write and pool timeouts for each request.
    - ``operation_timeout``: seconds one call of ``start``, ``find`` or ``status`` may take in all,
      queue polls included.
    - ``queue_polls`` and ``queue_poll_interval``: how often ``start`` reads the queue item, and
      how many seconds it waits before each read.
    - ``recent_builds``: how many of the job's newest builds ``find`` and ``status`` search.
    """

    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        api_token: ApiToken,
        job: str,
        timeout: httpx2.Timeout,
        operation_timeout: float,
        queue_polls: int,
        queue_poll_interval: float,
        recent_builds: int,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or "@" in parts.netloc
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "base_url must be an http or https URL without credentials, query or fragment"
            )
        segments = job.split("/")
        if not all(_JOB_SEGMENT.fullmatch(s) for s in segments):
            raise ValueError("job must be one or more '/'-separated names of [A-Za-z0-9._-]")
        if not user:
            raise ValueError("user is empty")
        if operation_timeout <= 0 or queue_polls < 1 or queue_poll_interval < 0:
            raise ValueError("operation_timeout, queue_polls and queue_poll_interval out of range")
        if recent_builds < 1:
            raise ValueError("recent_builds must be at least 1")

        self._base_path = parts.path.rstrip("/")
        self._base = f"{parts.scheme}://{parts.netloc}{self._base_path}"
        self._job_path = "".join(f"/job/{s}" for s in segments)
        self._job_url = self._base + self._job_path
        self._user = user
        self._token = api_token
        self._timeout = timeout
        self._operation_timeout = operation_timeout
        self._queue_polls = queue_polls
        self._queue_poll_interval = queue_poll_interval
        self._builds_tree = _BUILDS_TREE.format(n=recent_builds)
        self._transport = transport

    # --- the port ------------------------------------------------------------------------------

    async def start(self, request: BuildRequest) -> BuildHandle:
        """Queue one build and follow its queue item for a bounded number of polls."""
        return await self._run("start", lambda client: self._start(client, request))

    async def find(self, request: BuildRequest) -> BuildHandle | None:
        """Search the queue, then the job's recent builds, for this project, release and attempt."""
        return await self._run("find", lambda client: self._find(client, request))

    async def status(self, handle: BuildHandle) -> BuildStatus:
        """Read a build's state, resolving a queue item to its build first."""
        kind, ident = _parse_handle(handle)
        if kind == "queue":
            return await self._run("status", lambda client: self._queue_status(client, ident))
        return await self._run("status", lambda client: self._build_status(client, ident))

    async def cancel(self, handle: BuildHandle) -> None:
        """Abort the build or cancel the queue item behind ``handle``; idempotent."""
        kind, ident = _parse_handle(handle, "cancel")
        if kind == "queue":
            await self._run("cancel", lambda client: self._cancel_queue_item(client, ident))
        else:
            await self._run("cancel", lambda client: self._stop_build(client, ident))

    # --- operations ----------------------------------------------------------------------------

    async def _crumb(self, client: httpx2.AsyncClient, op: str) -> dict[str, str]:
        """The CSRF crumb header for a POST, or none when Jenkins issues no crumbs. The client
        keeps the session cookie the crumb is bound to."""
        crumb = await _get_json_or_none(client, op, f"{self._base}/crumbIssuer/api/json")
        if crumb is None:
            return {}
        field, value = crumb.get("crumbRequestField"), crumb.get("crumb")
        if not (isinstance(field, str) and field and isinstance(value, str)):
            raise BuildSystemUnavailable(f"jenkins: {op}: unreadable crumb")
        return {field: value}

    async def _start(self, client: httpx2.AsyncClient, request: BuildRequest) -> BuildHandle:
        headers = await self._crumb(client, "start")
        response = await client.post(
            f"{self._job_url}/buildWithParameters",
            params={"delay": "0sec"},
            data={
                "PROJECT_ID": str(request.project_id),
                "RELEASE_ID": str(request.release_id),
                "ATTEMPT": str(request.attempt),
                "REPOSITORY_URL": request.repository_url,
                "COMMIT_SHA": request.commit_sha,
                "IMAGE_REPOSITORY": request.image_repository,
            },
            headers=headers,
        )
        if not 200 <= response.status_code < 300:
            _fail("start", response.status_code)
        queue_id = self._queue_id_of(response.headers.get("location"))
        if queue_id is None:
            # The build may be queued all the same; the caller's retry finds it with find().
            raise BuildSystemUnavailable("jenkins: start: no queue item in the answer")

        for _ in range(self._queue_polls):
            await asyncio.sleep(self._queue_poll_interval)
            item = await _get_json_or_none(client, "start", self._queue_url(queue_id) + "api/json")
            if item is None:
                break  # already forgotten by Jenkins; status() finds the build by queueId
            number = self._executable_of(item)
            if number is not None:
                return BuildHandle(f"jenkins/build/{number}")
            if item.get("cancelled") is True:
                break
        return BuildHandle(f"jenkins/queue/{queue_id}")

    async def _find(self, client: httpx2.AsyncClient, request: BuildRequest) -> BuildHandle | None:
        wanted = {
            "PROJECT_ID": str(request.project_id),
            "RELEASE_ID": str(request.release_id),
            "ATTEMPT": str(request.attempt),
        }
        # The queue first: an item that leaves the queue between the two reads is a build by then.
        queue = await _get_json(
            client, "find", f"{self._base}/queue/api/json", params={"tree": _QUEUE_TREE}
        )
        task_path = f"{self._base_path}{self._job_path}/"
        for item in _objects(queue.get("items")):
            task = item.get("task")
            task_url = task.get("url") if isinstance(task, dict) else None
            ident = item.get("id")
            if (
                isinstance(task_url, str)
                and urlsplit(task_url).path == task_path
                and _is_positive_int(ident)
                and _matches(item, wanted)
            ):
                return BuildHandle(f"jenkins/queue/{ident}")
        for build in await self._builds(client, "find"):
            url = build.get("url")
            if isinstance(url, str) and _matches(build, wanted):
                number = self._build_number_of(url)
                if number is not None:
                    return BuildHandle(f"jenkins/build/{number}")
        return None

    async def _queue_status(self, client: httpx2.AsyncClient, queue_id: str) -> BuildStatus:
        item = await _get_json_or_none(client, "status", self._queue_url(queue_id) + "api/json")
        if item is None:
            number = await self._build_of_queue_id(client, int(queue_id), "status")
            if number is None:
                return BuildStatus(BuildState.LOST)
            return await self._build_status(client, number)
        number = self._executable_of(item)
        if number is not None:
            return await self._build_status(client, number)
        if item.get("cancelled") is True:
            return BuildStatus(BuildState.FAILED, failure_code=CANCELLED)
        return BuildStatus(BuildState.QUEUED)

    async def _build_status(self, client: httpx2.AsyncClient, number: str) -> BuildStatus:
        build_url = self._build_url(number)
        info = await _get_json_or_none(
            client, "status", build_url + "api/json", params={"tree": "building,result"}
        )
        if info is None:
            return BuildStatus(BuildState.LOST)
        result = info.get("result")
        if info.get("building") is True or result is None:
            return BuildStatus(BuildState.RUNNING)
        doc = await _result_json(client, build_url)
        if result == "SUCCESS":
            if isinstance(doc, str):
                return BuildStatus(BuildState.FAILED, failure_code=doc)
            return BuildStatus(
                BuildState.SUCCEEDED,
                project_id=_uuid(doc.get("project_id")),
                release_id=_uuid(doc.get("release_id")),
                attempt=_attempt(doc.get("attempt")),
                commit_sha=_text(doc.get("commit_sha")),
                image_digest=_text(doc.get("image_digest")),
            )
        # FAILURE, ABORTED, UNSTABLE, NOT_BUILT: the pipeline did not deliver an image.
        code = doc.get("failure_code") if isinstance(doc, dict) else None
        if not (isinstance(code, str) and _FAILURE_CODE.fullmatch(code)):
            code = PIPELINE_FAILED
        return BuildStatus(BuildState.FAILED, failure_code=code)

    async def _cancel_queue_item(self, client: httpx2.AsyncClient, queue_id: str) -> None:
        item = await _get_json_or_none(client, "cancel", self._queue_url(queue_id) + "api/json")
        if item is None:  # forgotten by Jenkins: it either became a build or never existed
            number = await self._build_of_queue_id(client, int(queue_id), "cancel")
            if number is not None:
                await self._stop_build(client, number)
            return
        number = self._executable_of(item)
        if number is not None:
            await self._stop_build(client, number)
            return
        if item.get("cancelled") is True:
            return
        response = await client.post(
            f"{self._base}/queue/cancelItem",
            params={"id": queue_id},
            headers=await self._crumb(client, "cancel"),
        )
        if response.status_code == 404:
            # It left the queue in between: when it became a build, abort that build.
            item = await _get_json_or_none(client, "cancel", self._queue_url(queue_id) + "api/json")
            number = None if item is None else self._executable_of(item)
            if number is not None:
                await self._stop_build(client, number)
            return
        _accept_write("cancel", response.status_code)

    async def _stop_build(self, client: httpx2.AsyncClient, number: str) -> None:
        build_url = self._build_url(number)
        info = await _get_json_or_none(
            client, "cancel", build_url + "api/json", params={"tree": "building,result"}
        )
        if info is None or (info.get("building") is not True and info.get("result") is not None):
            return  # unknown, or already finished or aborted
        response = await client.post(
            build_url + "stop", headers=await self._crumb(client, "cancel")
        )
        if response.status_code == 404:
            return  # discarded in between
        _accept_write("cancel", response.status_code)

    # --- Jenkins resources ---------------------------------------------------------------------

    async def _builds(self, client: httpx2.AsyncClient, op: str) -> list[_Json]:
        job = await _get_json(
            client, op, f"{self._job_url}/api/json", params={"tree": self._builds_tree}
        )
        return _objects(job.get("builds"))

    async def _build_of_queue_id(
        self, client: httpx2.AsyncClient, queue_id: int, op: str
    ) -> str | None:
        for build in await self._builds(client, op):
            url = build.get("url")
            if build.get("queueId") == queue_id and isinstance(url, str):
                return self._build_number_of(url)
        return None

    # --- handles and URLs ----------------------------------------------------------------------

    def _build_url(self, number: str) -> str:
        return f"{self._job_url}/{number}/"

    def _queue_url(self, queue_id: str) -> str:
        return f"{self._base}/queue/item/{queue_id}/"

    def _queue_id_of(self, location: str | None) -> str | None:
        if not location:
            return None
        path = urlsplit(urljoin(self._base + "/", location)).path
        match = _QUEUE_TAIL.fullmatch(path.removeprefix(self._base_path + "/"))
        if not path.startswith(self._base_path + "/") or match is None:
            return None
        return match.group(1)

    def _build_number_of(self, url: str) -> str | None:
        """The number of a build URL Jenkins reported, or None when it is not of our job."""
        prefix = f"{self._base_path}{self._job_path}/"
        path = urlsplit(url).path
        if not path.startswith(prefix):
            return None
        match = _BUILD_TAIL.fullmatch(path.removeprefix(prefix))
        return match.group(1) if match is not None else None

    def _executable_of(self, item: _Json) -> str | None:
        executable = item.get("executable")
        url = executable.get("url") if isinstance(executable, dict) else None
        if not isinstance(url, str):
            return None
        number = self._build_number_of(url)
        if number is None:
            raise BuildSystemUnavailable("jenkins: the queue item names a build of another job")
        return number

    # --- transport -----------------------------------------------------------------------------

    async def _run[T](self, op: str, work: Callable[[httpx2.AsyncClient], Awaitable[T]]) -> T:
        """Run one operation in its own client, under the operation deadline, mapping failures."""
        try:
            async with (
                asyncio.timeout(self._operation_timeout),
                httpx2.AsyncClient(
                    auth=httpx2.BasicAuth(self._user, self._token.reveal()),
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                return await work(client)
        except TimeoutError:
            raise BuildSystemUnavailable(f"jenkins: {op}: deadline exceeded") from None
        except httpx2.TimeoutException:
            raise BuildSystemUnavailable(f"jenkins: {op}: timed out") from None
        except httpx2.RequestError:
            raise BuildSystemUnavailable(f"jenkins: {op}: connection failed") from None


def _fail(op: str, status_code: int) -> NoReturn:
    if status_code == 429 or status_code >= 500:
        raise BuildSystemUnavailable(f"jenkins: {op}: HTTP {status_code}")
    raise BuildSystemRejected(f"jenkins: {op}: HTTP {status_code}")


def _accept_write(op: str, status_code: int) -> None:
    """A POST that Jenkins answered with a success or a redirect (it redirects to the page of
    what it changed; redirects are not followed) is done; anything else fails as ``_fail``."""
    if not 200 <= status_code < 400:
        _fail(op, status_code)


def _json_object(op: str, response: httpx2.Response) -> _Json:
    try:
        doc = response.json()
    except ValueError:
        raise BuildSystemUnavailable(f"jenkins: {op}: unreadable answer") from None
    if not isinstance(doc, dict):
        raise BuildSystemUnavailable(f"jenkins: {op}: unreadable answer")
    return doc


def _objects(value: object) -> list[_Json]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parameters(entry: _Json) -> dict[str, str]:
    found: dict[str, str] = {}
    for action in _objects(entry.get("actions")):
        for parameter in _objects(action.get("parameters")):
            name, value = parameter.get("name"), parameter.get("value")
            if isinstance(name, str) and isinstance(value, str):
                found[name] = value
    return found


def _matches(entry: _Json, wanted: Mapping[str, str]) -> bool:
    parameters = _parameters(entry)
    return all(parameters.get(name) == value for name, value in wanted.items())


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _attempt(value: object) -> int | None:
    """An integer, or a string of digits (Jenkins parameters are strings)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _ATTEMPT.fullmatch(value):
        return int(value)
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _result_json(
    client: httpx2.AsyncClient, build_url: str
) -> _Json | Literal["result_missing", "result_invalid"]:
    """The archived result.json, read as a stream and never past ``_RESULT_LIMIT`` bytes."""
    body = bytearray()
    async with client.stream(
        "GET", build_url + "artifact/result.json", headers={"accept-encoding": "identity"}
    ) as response:
        if response.status_code == 404:
            return RESULT_MISSING
        if response.status_code != 200:
            _fail("status", response.status_code)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > _RESULT_LIMIT:
            return RESULT_INVALID
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            return RESULT_INVALID  # asked for identity: a compressed body is not counted
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > _RESULT_LIMIT:
                return RESULT_INVALID
    try:
        doc = json.loads(body)
    except ValueError:
        return RESULT_INVALID
    return doc if isinstance(doc, dict) else RESULT_INVALID


async def _get_json(
    client: httpx2.AsyncClient,
    op: str,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
) -> _Json:
    """GET a JSON object; any status but 200 fails."""
    response = await client.get(url, params=params)
    if response.status_code != 200:
        _fail(op, response.status_code)
    return _json_object(op, response)


async def _get_json_or_none(
    client: httpx2.AsyncClient,
    op: str,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
) -> _Json | None:
    """GET a JSON object, or None when the resource does not exist (404)."""
    response = await client.get(url, params=params)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        _fail(op, response.status_code)
    return _json_object(op, response)


def _parse_handle(handle: BuildHandle, op: str = "status") -> tuple[Literal["build", "queue"], str]:
    queue = _QUEUE_REF.fullmatch(handle.ref)
    if queue is not None:
        return "queue", queue.group(1)
    build = _BUILD_REF.fullmatch(handle.ref)
    if build is not None:
        return "build", build.group(1)
    raise BuildSystemRejected(f"jenkins: {op}: the handle is not a Jenkins build or queue ref")
