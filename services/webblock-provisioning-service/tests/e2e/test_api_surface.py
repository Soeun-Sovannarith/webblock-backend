"""The API surface live (task W6-D; decision 0014 section 21 rulings 3, 6, 7 and 10, section 23
item 5): refusals from the file-based project-access and entitlement stand-ins, diagnostics and
both listings over REST and MCP, and the audit lines of the API in Loki.

Needs ``make proto-up proto-run`` with the API on PROJECT_ACCESS_MODE=prototype-file and both the
API and the worker on ENTITLEMENT_MODE=prototype-file, the files ``access.json`` and
``entitlement.json`` in the run script's folder, and the tokens of the subjects
``prototype-tenant`` (every scope, the projects the access file gives it) and ``prototype-reader``
(no action's scope). Every rewrite of a stand-in file is atomic and restored afterwards. Tokens are
read from the run script's file and never printed. The tests share one project and run in file
order.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.e2e._stack import (
    PROV,
    ROOT,
    Stack,
    StackMissing,
    new_project,
    new_repository_id,
    run,
    sync_standins,
    until,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

ACCESS_FILE = PROV / "access.json"
ENTITLEMENT_FILE = PROV / "entitlement.json"
TOKENS_FILE = PROV / "tokens.json"
OPERATOR, TENANT, READER = "prototype-operator", "prototype-tenant", "prototype-reader"
OPERATOR_ACCESS: dict[str, Any] = {"subjects": [{"subject": OPERATOR, "projects": "*"}]}
ALL_ENTITLED: dict[str, Any] = {"default_entitled": True, "projects": {}}
BUILD_TIMEOUT = 300.0
DEPLOY_TIMEOUT = 300.0
PROMOTE_TIMEOUT = 180.0


def _write(path: Path, doc: dict[str, Any]) -> None:
    """Replace a stand-in file atomically (write, then rename), as the stand-ins require, and sync
    it into the cluster in cluster mode."""
    tmp = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(doc))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    sync_standins()


class Surface:
    """The project the tests share and the tokens by subject."""

    def __init__(self) -> None:
        for path in (ACCESS_FILE, ENTITLEMENT_FILE, TOKENS_FILE):
            if not path.is_file():
                raise StackMissing(f"{path.name} is missing; the run script predates task W6-D")
        tokens = json.loads(TOKENS_FILE.read_text())["tokens"]
        self.tokens = {t["subject"]: t["token"] for t in tokens}
        for subject in (OPERATOR, TENANT, READER):
            if subject not in self.tokens:
                raise StackMissing(f"tokens.json has no token of {subject}")
        self.project = new_project()
        self.other = new_project()
        self.repository_id = new_repository_id()
        self.name = f"surface-{str(self.project)[-12:]}"
        self.release: uuid.UUID | None = None
        self.deploy_command = ""
        self.promote_command = ""

    def auth(self, subject: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[subject]}"}


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


@pytest.fixture(scope="module")
def surface() -> Iterator[Surface]:
    surface = Surface()
    yield surface
    # Whatever happened, the stack is left with the operator's access and everyone entitled.
    _write(ACCESS_FILE, OPERATOR_ACCESS)
    _write(ENTITLEMENT_FILE, ALL_ENTITLED)


async def _request(
    stack: Stack, method: str, path: str, headers: dict[str, str]
) -> httpx2.Response:
    async with stack.http() as http:
        return await http.request(method, stack.api + path, headers=headers)


async def _commands_of(stack: Stack, project: uuid.UUID) -> int:
    rows = await stack.query(
        "SELECT count(*) FROM provisioning.commands WHERE project_id = %s", project
    )
    return int(rows[0][0])


# --- refusals --------------------------------------------------------------------------------


async def test_1_a_project_the_caller_cannot_access_is_404_and_writes_nothing(
    stack: Stack, surface: Surface
) -> None:
    _write(
        ACCESS_FILE,
        {
            "subjects": [
                *OPERATOR_ACCESS["subjects"],
                {"subject": TENANT, "projects": [str(surface.other)]},
            ]
        },
    )
    release = uuid.uuid7()
    tenant = surface.auth(TENANT)
    deploy = await _request(
        stack,
        "POST",
        f"/v1/projects/{surface.project}/releases/{release}/deploy",
        {**tenant, "Idempotency-Key": "surface-denied"},
    )
    listing = await _request(stack, "GET", f"/v1/projects/{surface.project}/releases", tenant)
    assert (deploy.status_code, deploy.json()["code"]) == (404, "PROJECT_NOT_FOUND")
    assert (listing.status_code, listing.json()["code"]) == (404, "PROJECT_NOT_FOUND")
    # Decision 0018 on the live API: a Problem whose instance is the request path.
    for response, path in (
        (deploy, f"/v1/projects/{surface.project}/releases/{release}/deploy"),
        (listing, f"/v1/projects/{surface.project}/releases"),
    ):
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["cache-control"] == "no-store"
        problem = response.json()
        assert (problem["type"], problem["status"], problem["instance"]) == (
            "urn:webblock:problem:project-not-found",
            404,
            path,
        )
        assert "detail" not in problem
    # The content type follows Accept as in the Project service (spec 6.3); the body is the same.
    as_json = await _request(
        stack,
        "GET",
        f"/v1/projects/{surface.project}/releases",
        {**tenant, "Accept": "application/json"},
    )
    assert as_json.status_code == 404
    assert as_json.headers["content-type"] == "application/json"
    drop = {"requestId", "traceId"}
    assert {k: v for k, v in as_json.json().items() if k not in drop} == {
        k: v for k, v in listing.json().items() if k not in drop
    }
    # The tenant's own project answers (an empty page): the refusal is the file's.
    own = await _request(stack, "GET", f"/v1/projects/{surface.other}/commands", tenant)
    assert (own.status_code, own.json()["data"]) == (200, [])
    assert own.json()["meta"]["page"] == {"limit": 20, "nextCursor": None}
    assert await _commands_of(stack, surface.project) == 0
    # Reloaded: granting the project makes the same call an ordinary read.
    _write(
        ACCESS_FILE,
        {
            "subjects": [
                *OPERATOR_ACCESS["subjects"],
                {"subject": TENANT, "projects": [str(surface.other), str(surface.project)]},
            ]
        },
    )
    granted = await until(
        "the access file reloaded",
        lambda: _request(stack, "GET", f"/v1/projects/{surface.project}/releases", tenant),
        lambda r: r.status_code == 200,
        30,
    )
    assert granted.json()["data"] == []


async def test_2_a_missing_scope_is_403(stack: Stack, surface: Surface) -> None:
    reader = surface.auth(READER)
    read = await _request(stack, "GET", f"/v1/projects/{surface.project}/deployment", reader)
    deploy = await _request(
        stack,
        "POST",
        f"/v1/projects/{surface.project}/releases/{uuid.uuid7()}/deploy",
        {**reader, "Idempotency-Key": "surface-reader"},
    )
    for response in (read, deploy):
        assert (response.status_code, response.json()["code"]) == (403, "ACCESS_DENIED")
    assert await _commands_of(stack, surface.project) == 0


async def test_3_an_entitlement_refusal_at_acceptance_is_limit_reached(
    stack: Stack, surface: Surface
) -> None:
    _write(ENTITLEMENT_FILE, {"default_entitled": True, "projects": {str(surface.project): False}})
    try:
        refused = await until(
            "the entitlement file reloaded",
            lambda: stack.post(surface.project, uuid.uuid7(), "deploy", "surface-limit"),
            lambda c: c.status_code != 404 or c.body.get("code") != "RELEASE_NOT_FOUND",
            30,
        )
    finally:
        _write(ENTITLEMENT_FILE, ALL_ENTITLED)
    assert (refused.status_code, refused.body["code"]) == (422, "PUBLISH_LIMIT_REACHED")
    assert await _commands_of(stack, surface.project) == 0


async def test_4_entitlement_withdrawn_before_the_run_ends_the_promote_refused(
    stack: Stack, surface: Surface
) -> None:
    stack.register(surface.project, surface.repository_id, surface.name)
    sha = await stack.commit(surface.name, "site", "v1")
    request = await stack.publish(surface.project, surface.repository_id, sha)
    release = await stack.release_of(request)
    surface.release = release
    built = await stack.wait_release(surface.project, release, {"built", "failed"}, BUILD_TIMEOUT)
    assert built["status"] == "built", built
    deploy = await stack.post(surface.project, release, "deploy", f"deploy-{release}")
    assert deploy.status_code == 202, deploy.body
    surface.deploy_command = deploy.command_id
    deployed = await stack.wait_command(surface.project, deploy.command_id, DEPLOY_TIMEOUT)
    assert deployed["status"] == "succeeded", deployed

    # The worker is stopped, so the promote is accepted while entitled and runs after the file
    # withdrew the entitlement.
    await stack.process("stop-process", "worker")
    try:
        promote = await stack.post(surface.project, release, "promote", f"promote-{release}")
        assert promote.status_code == 202, promote.body
        surface.promote_command = promote.command_id
        _write(
            ENTITLEMENT_FILE, {"default_entitled": True, "projects": {str(surface.project): False}}
        )
    finally:
        await stack.process("start-process", "worker")
    try:
        done = await stack.wait_command(surface.project, promote.command_id, PROMOTE_TIMEOUT)
    finally:
        _write(ENTITLEMENT_FILE, ALL_ENTITLED)
    assert (done["status"], done.get("failureCode")) == ("failed", "entitlement_lost"), done
    assert (await stack.release(surface.project, release))["status"] == "candidate_ready"
    deployment = await stack.deployment(surface.project)
    assert (deployment["routingStatus"], deployment.get("liveReleaseId")) == ("stable", None)


# --- diagnostics and listings, REST and MCP --------------------------------------------------


async def test_5_listings_over_rest_and_mcp(stack: Stack, surface: Surface) -> None:
    assert surface.release is not None, "test_4 did not create the release"
    project, release = surface.project, surface.release
    # stack.page gives {items: data, page: meta.page}, the MCP form (decision 0023 G4, G7).
    releases = await stack.page(f"/v1/projects/{project}/releases?limit=1")
    assert [r["releaseId"] for r in releases["items"]] == [str(release)]
    assert releases["items"][0]["status"] == "candidate_ready"
    assert releases["page"] == {"limit": 1, "nextCursor": None}
    commands = await stack.page(f"/v1/projects/{project}/commands?limit=1")
    assert [c["commandId"] for c in commands["items"]] == [surface.promote_command]
    assert commands["items"][0]["failureCode"] == "entitlement_lost"
    rest_page_2 = await stack.page(
        f"/v1/projects/{project}/commands?limit=1&cursor={commands['page']['nextCursor']}"
    )
    assert [c["commandId"] for c in rest_page_2["items"]] == [surface.deploy_command]
    assert rest_page_2["page"]["nextCursor"] is None

    async with (
        httpx2.AsyncClient(headers=stack.auth(), timeout=30, trust_env=False) as http,
        Client(streamable_http_client(f"{stack.api}/mcp", http_client=http), mode="legacy") as sdk,
    ):
        tools = [t.name for t in (await sdk.list_tools()).tools]
        assert len(tools) == 10 and {"list_releases", "list_commands"} <= set(tools)
        via_mcp = {}
        for tool in ("list_releases", "list_commands"):
            result = await sdk.call_tool(tool, {"projectId": str(project), "limit": 1})
            assert not result.is_error, (tool, result)
            via_mcp[tool] = result.structured_content
    assert via_mcp["list_releases"] == releases
    assert via_mcp["list_commands"] == commands


async def test_6_diagnostics_over_rest_and_mcp(stack: Stack, surface: Surface) -> None:
    """Needs migration 00016's grant on the source-published event columns (task W6-E)."""
    assert surface.release is not None, "test_4 did not create the release"
    project, release = surface.project, surface.release
    diagnostics = await stack.get(f"/v1/projects/{project}/releases/{release}/diagnostics")
    assert diagnostics["release"]["status"] == "candidate_ready"
    steps = {(s["stepName"], s["status"]) for s in diagnostics["steps"]}
    assert ("build", "succeeded") in steps and ("candidate", "succeeded") in steps
    assert [(c["kind"], c["status"], c.get("failureCode")) for c in diagnostics["commands"]] == [
        ("promote", "failed", "entitlement_lost"),
        ("deploy", "succeeded", None),
    ]
    assert diagnostics["sourceEvent"]["status"] == "accepted"
    assert diagnostics["stepsTruncated"] is False and diagnostics["commandsTruncated"] is False
    text = json.dumps(diagnostics)
    for hidden in ("logref", "failuresummary", "idempotency", "requestedby", "jenkins"):
        assert hidden not in text.lower()
    async with (
        httpx2.AsyncClient(headers=stack.auth(), timeout=30, trust_env=False) as http,
        Client(streamable_http_client(f"{stack.api}/mcp", http_client=http), mode="legacy") as sdk,
    ):
        result = await sdk.call_tool(
            "get_release_diagnostics", {"projectId": str(project), "releaseId": str(release)}
        )
    assert not result.is_error, result
    assert result.structured_content == diagnostics


# --- audit lines in Loki ---------------------------------------------------------------------


async def _loki(project: uuid.UUID) -> list[dict[str, Any]]:
    query = (
        '{job="webblock-provisioning", process="api"} | json | audit="true" '
        f'| project_id="{project}"'
    )
    out = await run(
        ["bash", str(ROOT / "deploy" / "kind" / "proto-loki-query.sh"), "--since", "1h", query]
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def test_7_the_api_audit_lines_are_in_loki_without_a_token(
    stack: Stack, surface: Surface
) -> None:
    want = {
        (TENANT, "deploy", "refused", "project_access_denied"),
        (READER, "deploy", "refused", "action_denied"),
        (OPERATOR, "deploy", "refused", "limit_reached"),
        (OPERATOR, "deploy", "accepted", None),
        (OPERATOR, "promote", "accepted", None),
    }

    def seen(lines: list[dict[str, Any]]) -> set[tuple[str, str, str, str | None]]:
        return {(r["actor"], r["action"], r["result"], r.get("failure_code")) for r in lines}

    lines = await until(
        "the API's audit lines in Loki",
        lambda: _loki(surface.project),
        lambda found: want <= seen(found),
        120,
    )
    assert all(r["actor_kind"] == "user" for r in lines)
    accepted = {r["action"]: r["command_id"] for r in lines if r["result"] == "accepted"}
    assert accepted == {"deploy": surface.deploy_command, "promote": surface.promote_command}
    raw = json.dumps(lines)
    for token in surface.tokens.values():
        assert token not in raw
