"""The Jenkins adapter against the prototype's local Jenkins (task A; decision 0014 section 3).

Needs ``make proto-up`` (.claude/docs/runbooks/prototype-stack.md): Jenkins, its job, the Git server
and the registry. The addresses come from ``bin/kind-state/proto/stack.env`` (``PROTO_JENKINS_URL``,
``PROTO_JENKINS_JOB``, ``PROTO_IMAGE_REGISTRY``, ``PROTO_GIT_URL_BASE``); the credentials from
``bin/kind-state/proto/jenkins.env`` (``JENKINS_USER``, ``JENKINS_API_TOKEN``), which is read here
and never printed. A missing file or key fails the run; it never skips (the ``jenkins`` suite of
tests/conftest.py).

Four builds: the sample site (succeeds, and ``result.json`` echoes the request), a commit that is
not in the repository (``commit_not_found``), the variant whose Dockerfile fails
(``build_failed``), and an image repository for another project (``invalid_parameters``). Three
more for ``cancel`` (task W6-L): a running build aborted, a just-queued one, and a finished one
plus unknown handles left alone.

Sample repositories come from ``scripts/prototype/sample-repo.sh <name> <variant>`` (variants
``site`` and ``broken-build``), served to Jenkins as ``<PROTO_GIT_URL_BASE>/<name>.git``. The image
repository is exactly ``<PROTO_IMAGE_REGISTRY>/webblock/sites/<project_id>``; anything else makes
the pipeline fail with ``invalid_parameters``.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildState,
    BuildStatus,
)
from provisioning.infrastructure.jenkins import ApiToken, JenkinsBuildSystem

REPO_ROOT = Path(__file__).resolve().parents[3]
STACK_ENV = REPO_ROOT / "bin" / "kind-state" / "proto" / "stack.env"
JENKINS_ENV = REPO_ROOT / "bin" / "kind-state" / "proto" / "jenkins.env"
SAMPLE_REPO = REPO_ROOT / "scripts" / "prototype" / "sample-repo.sh"

VARIANT_SITE = "site"
VARIANT_BROKEN = "broken-build"

BUILD_DEADLINE = 900.0  # seconds for one build, queue included
POLL_INTERVAL = 5.0
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REF = re.compile(r"jenkins/(build|queue)/[1-9][0-9]*")  # opaque log_ref, never a URL (revision 19)


def read_env(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """KEY=VALUE lines of a file written by proto-up. Values are never printed, even on failure."""
    if not path.is_file():
        pytest.fail(f"{path.relative_to(REPO_ROOT)} is missing: the prototype stack is not up")
    settings: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            settings[key.strip()] = value.strip().strip("'\"")
    missing = [k for k in keys if not settings.get(k)]
    if missing:
        pytest.fail(f"{path.name} lacks " + ", ".join(missing))
    return settings


def stack() -> dict[str, str]:
    return read_env(
        STACK_ENV,
        ("PROTO_JENKINS_URL", "PROTO_JENKINS_JOB", "PROTO_IMAGE_REGISTRY", "PROTO_GIT_URL_BASE"),
    )


def make_adapter() -> JenkinsBuildSystem:
    addresses = stack()
    credentials = read_env(JENKINS_ENV, ("JENKINS_USER", "JENKINS_API_TOKEN"))
    return JenkinsBuildSystem(
        base_url=addresses["PROTO_JENKINS_URL"],
        user=credentials["JENKINS_USER"],
        api_token=ApiToken(credentials["JENKINS_API_TOKEN"]),
        job=addresses["PROTO_JENKINS_JOB"],
        timeout=httpx2.Timeout(10.0),
        operation_timeout=60.0,
        queue_polls=20,
        queue_poll_interval=1.0,
        recent_builds=50,
    )


async def sample_commit(name: str, variant: str) -> str:
    """Create or update the sample repository ``name`` and return its new commit SHA."""
    process = await asyncio.create_subprocess_exec(
        str(SAMPLE_REPO),
        name,
        variant,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    async with asyncio.timeout(120):
        out, err = await process.communicate()
    assert process.returncode == 0, err.decode(errors="replace")[-2000:]
    sha = out.decode().strip().splitlines()[-1]
    assert SHA.fullmatch(sha), "sample-repo.sh did not print a full commit SHA"
    return sha


def build_request(name: str, commit_sha: str) -> BuildRequest:
    addresses = stack()
    project_id = uuid4()
    return BuildRequest(
        project_id=project_id,
        release_id=uuid4(),
        attempt=1,
        repository_url=f"{addresses['PROTO_GIT_URL_BASE']}/{name}.git",
        commit_sha=commit_sha,
        image_repository=f"{addresses['PROTO_IMAGE_REGISTRY']}/webblock/sites/{project_id}",
    )


async def wait_until_done(adapter: JenkinsBuildSystem, handle: BuildHandle) -> BuildStatus:
    async with asyncio.timeout(BUILD_DEADLINE):
        while True:
            status = await adapter.status(handle)
            if status.state not in (BuildState.QUEUED, BuildState.RUNNING):
                return status
            await asyncio.sleep(POLL_INTERVAL)


@pytest.mark.asyncio
async def test_live_sample_site_builds_and_echoes_the_request() -> None:
    adapter = make_adapter()
    name = f"live-c-{secrets.token_hex(4)}"
    request = build_request(name, await sample_commit(name, VARIANT_SITE))

    handle = await adapter.start(request)
    assert REF.fullmatch(handle.ref), handle.ref
    found = await adapter.find(request)
    assert found is not None
    assert REF.fullmatch(found.ref), found.ref
    assert (
        await adapter.find(
            BuildRequest(
                project_id=request.project_id,
                release_id=request.release_id,
                attempt=2,
                repository_url=request.repository_url,
                commit_sha=request.commit_sha,
                image_repository=request.image_repository,
            )
        )
        is None
    )
    status = await wait_until_done(adapter, handle)

    assert status.state is BuildState.SUCCEEDED, status.failure_code
    assert status.project_id == request.project_id
    assert status.release_id == request.release_id
    assert status.attempt == request.attempt
    assert status.commit_sha == request.commit_sha
    assert status.image_digest is not None
    assert DIGEST.fullmatch(status.image_digest)
    # A restart reads the stored ref again (0014 section 4): a new handle from the stored string.
    assert await make_adapter().status(BuildHandle(handle.ref)) == status


@pytest.mark.asyncio
async def test_live_a_commit_not_in_the_repository_fails_with_commit_not_found() -> None:
    adapter = make_adapter()
    name = f"live-c-{secrets.token_hex(4)}"
    await sample_commit(name, VARIANT_SITE)
    request = build_request(name, secrets.token_hex(20))

    status = await wait_until_done(adapter, await adapter.start(request))

    assert status == BuildStatus(BuildState.FAILED, failure_code="commit_not_found")


@pytest.mark.asyncio
async def test_live_a_broken_dockerfile_fails_with_build_failed() -> None:
    adapter = make_adapter()
    name = f"live-c-{secrets.token_hex(4)}"
    request = build_request(name, await sample_commit(name, VARIANT_BROKEN))

    status = await wait_until_done(adapter, await adapter.start(request))

    assert status == BuildStatus(BuildState.FAILED, failure_code="build_failed")


@pytest.mark.asyncio
async def test_live_a_wrong_image_repository_fails_with_invalid_parameters() -> None:
    adapter = make_adapter()
    name = f"live-c-{secrets.token_hex(4)}"
    good = build_request(name, await sample_commit(name, VARIANT_SITE))
    request = BuildRequest(
        project_id=good.project_id,
        release_id=good.release_id,
        attempt=good.attempt,
        repository_url=good.repository_url,
        commit_sha=good.commit_sha,
        image_repository=f"{stack()['PROTO_IMAGE_REGISTRY']}/webblock/sites/{uuid4()}",
    )

    status = await wait_until_done(adapter, await adapter.start(request))

    assert status == BuildStatus(BuildState.FAILED, failure_code="invalid_parameters")


# --- cancel (decision 0014 section 23, QA-10) ----------------------------------------------------


async def wait_until_running(adapter: JenkinsBuildSystem, handle: BuildHandle) -> BuildHandle:
    """The handle once Jenkins runs the build (a queue handle resolves to its build)."""
    async with asyncio.timeout(BUILD_DEADLINE):
        while True:
            status = await adapter.status(handle)
            if status.state is BuildState.RUNNING:
                return handle
            assert status.state is BuildState.QUEUED, status
            await asyncio.sleep(1.0)


async def jenkins_outcome(handle: BuildHandle) -> str:
    """What Jenkins itself recorded for the handle, read with the same credentials: the build's
    ``result`` (``ABORTED`` after a stop), or ``CANCELLED`` for a queue item cancelled before it
    started. The adapter reports an aborted build as failed with the pipeline's own code (the
    pipeline classifies the stage it was stopped in), so the abort is checked here."""
    addresses = stack()
    credentials = read_env(JENKINS_ENV, ("JENKINS_USER", "JENKINS_API_TOKEN"))
    base = addresses["PROTO_JENKINS_URL"].rstrip("/")
    job = "".join(f"/job/{s}" for s in addresses["PROTO_JENKINS_JOB"].split("/"))
    kind, _, number = handle.ref.removeprefix("jenkins/").partition("/")
    async with httpx2.AsyncClient(
        auth=(credentials["JENKINS_USER"], credentials["JENKINS_API_TOKEN"]),
        timeout=30,
        trust_env=False,
    ) as http:
        if kind == "queue":
            item = (await http.get(f"{base}/queue/item/{number}/api/json")).json()
            if item.get("cancelled") is True:
                return "CANCELLED"
            executable = item.get("executable") or {}
            number = str(executable.get("number"))
        build = (await http.get(f"{base}{job}/{number}/api/json?tree=result")).json()
    return str(build.get("result"))


@pytest.mark.asyncio
async def test_live_cancel_aborts_a_running_build_and_is_idempotent() -> None:
    adapter = make_adapter()
    name = f"live-l-{secrets.token_hex(4)}"
    request = build_request(name, await sample_commit(name, VARIANT_SITE))
    handle = await wait_until_running(adapter, await adapter.start(request))

    await adapter.cancel(handle)
    status = await wait_until_done(adapter, handle)
    await adapter.cancel(handle)  # already aborted: not an error
    await make_adapter().cancel(BuildHandle(handle.ref))  # a restart's handle from the stored ref

    assert status.state is BuildState.FAILED, status
    assert await jenkins_outcome(handle) == "ABORTED"


@pytest.mark.asyncio
async def test_live_cancel_of_a_just_queued_build_cancels_it_or_aborts_its_build() -> None:
    adapter = make_adapter()
    name = f"live-l-{secrets.token_hex(4)}"
    request = build_request(name, await sample_commit(name, VARIANT_SITE))
    handle = await adapter.start(request)

    await adapter.cancel(handle)  # still queued or already a build
    status = await wait_until_done(adapter, handle)

    assert status.state is BuildState.FAILED, status
    assert await jenkins_outcome(handle) in {"ABORTED", "CANCELLED"}


@pytest.mark.asyncio
async def test_live_cancel_of_a_finished_or_unknown_build_changes_nothing() -> None:
    adapter = make_adapter()
    name = f"live-l-{secrets.token_hex(4)}"
    request = build_request(name, await sample_commit(name, VARIANT_BROKEN))
    handle = await adapter.start(request)
    done = await wait_until_done(adapter, handle)

    await adapter.cancel(handle)
    await adapter.cancel(BuildHandle("jenkins/build/999999999"))
    await adapter.cancel(BuildHandle("jenkins/queue/999999999"))

    assert await adapter.status(handle) == done
