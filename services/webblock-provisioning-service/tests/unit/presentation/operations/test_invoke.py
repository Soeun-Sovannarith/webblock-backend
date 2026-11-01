"""The shared operation path of REST and MCP (decision 0006 sections 1 and 3, and the prototype's
build_release)."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.unit.presentation.fakes import (
    COMMAND,
    OTHER,
    PRINCIPAL,
    PROJECT,
    RELEASE,
    FakeOperations,
    buses,
    release_view,
)

from provisioning.core.application import (
    Action,
    ActionDenied,
    BuildInProgress,
    BuildReleaseCommand,
    CommandAccepted,
    Contention,
    DependencyUnavailable,
    DeployReleaseCommand,
    LimitReached,
    NotFound,
    OperationInProgress,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    ReleaseNotBuildable,
    Unauthenticated,
)
from provisioning.core.domain import CommandStatus, IdempotencyConflict, RouteNotStable
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.presentation.operations import (
    OPERATIONS,
    Invoker,
    MalformedRequest,
    Outcome,
    random_correlation_id,
    request_id_scope,
)

CORR = "corr-fixed-1"
KEY = "key-visible-1"


def _invoker(
    service: FakeOperations, *, stream: io.StringIO | None = None, timeout: float = 5.0
) -> Invoker:
    return Invoker(
        **buses(service),
        request_timeout=timedelta(seconds=timeout),
        new_correlation_id=lambda: CORR,
        logger=new_logger(stream if stream is not None else io.StringIO(), logging.DEBUG),
        bind_correlation=correlation_scope,
        trace_id=lambda: None,
    )


def _inputs(action: Action) -> dict[str, str]:
    values = {
        "projectId": str(PROJECT),
        "releaseId": str(RELEASE),
        "commandId": str(COMMAND),
        "idempotencyKey": KEY,
    }
    op = next(o for o in OPERATIONS if o.action is action)
    return {name: values[name] for name in op.required()}


def _body(out: Outcome) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(out.body)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_success_bodies(action: Action) -> None:
    out = await _invoker(FakeOperations()).invoke(PRINCIPAL, action, _inputs(action))
    assert not out.is_error()
    assert out.code == ""
    assert out.media_type() == "application/json"
    assert out.media_type("text/html") == "application/json"  # a success ignores Accept
    body = _body(out)
    rest = json.loads(out.rest_body("/v1/p"))
    assert list(rest) == ["success", "data", "meta"]
    assert rest["success"] is True
    is_list = action in (Action.LIST_RELEASES, Action.LIST_COMMANDS, Action.ADMIN_LIST_COMMANDS)
    # MCP gets the REST data, or {items, page} for a list (decision 0023 G7).
    assert body == (
        {"items": rest["data"], "page": rest["meta"]["page"]} if is_list else rest["data"]
    )
    assert list(rest["meta"]) == ["requestId", "timestamp", "path", "status", "page"]
    assert rest["meta"]["requestId"] == CORR
    assert rest["meta"]["path"] == "/v1/p"
    assert rest["meta"]["status"] == out.status
    assert (rest["meta"]["page"] is None) == (not is_list)
    if action.command_kind() is not None:
        assert out.status == 202
        assert body == {
            "commandId": str(COMMAND),
            "status": "accepted",
            "statusUrl": f"/v1/projects/{PROJECT}/commands/{COMMAND}",
        }
    elif action is Action.BUILD_RELEASE:
        assert out.status == 202
        assert body["releaseId"] == str(RELEASE)
        assert body["projectId"] == str(PROJECT)
    else:
        assert out.status == 200


@pytest.mark.asyncio
async def test_the_application_receives_the_parsed_request_and_the_correlation_id() -> None:
    service = FakeOperations()
    await _invoker(service).invoke(PRINCIPAL, Action.DEPLOY_RELEASE, _inputs(Action.DEPLOY_RELEASE))
    assert service.calls == [
        (
            Action.DEPLOY_RELEASE,
            PRINCIPAL,
            DeployReleaseCommand(
                principal=PRINCIPAL,
                project_id=PROJECT,
                release_id=RELEASE,
                idempotency_key=KEY,
                correlation_id=CORR,
            ),
        )
    ]


@pytest.mark.asyncio
async def test_build_release_answers_with_the_release_the_service_returns() -> None:
    service = FakeOperations(full=False)
    out = await _invoker(service).invoke(
        PRINCIPAL, Action.BUILD_RELEASE, _inputs(Action.BUILD_RELEASE)
    )
    assert out.status == 202
    # One call: the service returns the release itself, so no get_release permission is asked
    # after the build started (E1 report, concern 2).
    assert service.calls == [
        (
            Action.BUILD_RELEASE,
            PRINCIPAL,
            BuildReleaseCommand(
                principal=PRINCIPAL, project_id=PROJECT, release_id=RELEASE, correlation_id=CORR
            ),
        ),
    ]
    assert _body(out)["status"] == "accepted"
    assert _body(out)["releaseId"] == str(RELEASE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NotFound(), 404, "RELEASE_NOT_FOUND"),
        (ProjectAccessDenied(), 404, "PROJECT_NOT_FOUND"),
        (ReleaseNotBuildable(), 422, "RELEASE_NOT_BUILDABLE"),
        (BuildInProgress(), 409, "BUILD_IN_PROGRESS"),
        (ActionDenied(), 403, "ACCESS_DENIED"),
        (Contention(), 409, "CONCURRENT_CHANGE"),
    ],
)
async def test_build_release_outcomes(error: Exception, status: int, code: str) -> None:
    service = FakeOperations(errors={Action.BUILD_RELEASE: error})
    out = await _invoker(service).invoke(
        PRINCIPAL, Action.BUILD_RELEASE, _inputs(Action.BUILD_RELEASE)
    )
    assert (out.status, out.code) == (status, code)
    assert service.actions() == [Action.BUILD_RELEASE]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_without_a_principal_the_call_is_unauthenticated(action: Action) -> None:
    service = FakeOperations()
    out = await _invoker(service).invoke(None, action, _inputs(action))
    assert (out.status, out.code) == (401, "UNAUTHENTICATED")
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "errors"),
    [
        ({"projectId": None}, [("projectId", "REQUIRED")]),
        ({"projectId": "not-a-uuid"}, [("projectId", "INVALID_FORMAT")]),
        ({"projectId": str(PROJECT).upper()}, [("projectId", "INVALID_FORMAT")]),
        ({"releaseId": "00000000-0000-0000-0000-000000000000"}, [("releaseId", "INVALID_FORMAT")]),
        ({"idempotencyKey": None}, [("Idempotency-Key", "REQUIRED")]),
        ({"idempotencyKey": ""}, [("Idempotency-Key", "REQUIRED")]),
        ({"idempotencyKey": " leading"}, [("Idempotency-Key", "INVALID_FORMAT")]),
        ({"idempotencyKey": "x" * 256}, [("Idempotency-Key", "TOO_LONG")]),
        ({"idempotencyKey": "\u00e9" * 128}, [("Idempotency-Key", "TOO_LONG")]),
        ({"idempotencyKey": "a\nb"}, [("Idempotency-Key", "INVALID_FORMAT")]),
        ({"extra": "x"}, []),
    ],
)
async def test_bad_input_is_refused_without_calling_the_application(
    change: dict[str, str | None], errors: list[tuple[str, str]]
) -> None:
    inputs: dict[str, str] = dict(_inputs(Action.DEPLOY_RELEASE))
    for key, value in change.items():
        if value is None:
            inputs.pop(key)
        else:
            inputs[key] = value
    service = FakeOperations()
    out = await _invoker(service).invoke(PRINCIPAL, Action.DEPLOY_RELEASE, inputs)
    assert (out.status, out.code) == (400, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in _body(out)["errors"]] == errors
    assert service.calls == []


@pytest.mark.asyncio
async def test_the_first_invalid_input_in_operation_order_decides_the_code() -> None:
    out = await _invoker(FakeOperations()).invoke(
        PRINCIPAL,
        Action.DEPLOY_RELEASE,
        {"projectId": "bad", "releaseId": str(RELEASE), "idempotencyKey": " bad"},
    )
    assert out.code == "VALIDATION_FAILED"
    assert [e["field"] for e in _body(out)["errors"]] == ["projectId"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize(
    "error",
    [IdempotencyConflict(), RouteNotStable(), LimitReached(), OperationInProgress(COMMAND)],
)
async def test_an_undeclared_status_is_internal(action: Action, error: Exception) -> None:
    service = FakeOperations(errors={action: error})
    out = await _invoker(service).invoke(PRINCIPAL, action, _inputs(action))
    op = next(o for o in OPERATIONS if o.action is action)
    if op.declares(out.status) and out.status != 500:
        assert out.status in (409, 422)
        assert op.is_mutation()
    else:
        assert (out.status, out.code) == (500, "INTERNAL_ERROR")
        assert not op.is_mutation()
    # The body follows the remap: its status and type are the outcome's, never the original
    # class's, and only COMMAND_IN_PROGRESS names a command.
    body = _body(out)
    assert body["status"] == out.status
    assert body["type"] == "urn:webblock:problem:" + out.code.lower().replace("_", "-")
    assert ("commandId" in body) == (out.code == "COMMAND_IN_PROGRESS")


@pytest.mark.asyncio
async def test_operation_in_progress_names_the_command() -> None:
    service = FakeOperations(errors={Action.DEPLOY_RELEASE: OperationInProgress(COMMAND)})
    out = await _invoker(service).invoke(
        PRINCIPAL, Action.DEPLOY_RELEASE, _inputs(Action.DEPLOY_RELEASE)
    )
    assert out.media_type() == "application/problem+json"
    assert out.media_type("application/json") == "application/json"  # Accept picks only the type
    both = "application/json, application/problem+json"
    assert out.media_type(both) == "application/problem+json"
    assert _body(out) == {
        "type": "urn:webblock:problem:command-in-progress",
        "title": "Another command of the project is in progress.",
        "status": 409,
        "code": "COMMAND_IN_PROGRESS",
        "requestId": CORR,
        "errors": [],
        "commandId": str(COMMAND),
    }
    assert json.loads(out.rest_body(f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy")) == {
        **_body(out),
        "instance": f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_the_access_not_found_cases_are_byte_identical(action: Action) -> None:
    """Whatever Project says, no access and an unknown project are one PROJECT_NOT_FOUND (G6);
    a missing release, command or deployment is told apart only after access passed."""
    outs = []
    for error in (
        ProjectAccessDenied("the project service denied access: NOT_FOUND"),
        ProjectAccessDenied("the project service denied access: NOT_MANAGER"),
    ):
        service = FakeOperations(errors={action: error})
        outs.append(await _invoker(service).invoke(PRINCIPAL, action, _inputs(action)))
    assert outs[0] == outs[1]
    assert (outs[0].status, outs[0].code) == (404, "PROJECT_NOT_FOUND")
    path = f"/v1/projects/{PROJECT}/deployment"
    assert outs[0].rest_body(path) == outs[1].rest_body(path)


@pytest.mark.asyncio
async def test_the_request_timeout_bounds_the_application_call() -> None:
    out = await _invoker(FakeOperations(delay=5), timeout=0.05).invoke(
        PRINCIPAL, Action.GET_DEPLOYMENT, _inputs(Action.GET_DEPLOYMENT)
    )
    assert (out.status, out.code) == (504, "REQUEST_TIMEOUT")


@pytest.mark.asyncio
async def test_an_unexpected_error_is_internal_with_the_detail_only_in_the_log() -> None:
    stream = io.StringIO()
    service = FakeOperations(
        errors={Action.GET_RELEASE: RuntimeError("boom-detail password=hunter2")}
    )
    out = await _invoker(service, stream=stream).invoke(
        PRINCIPAL, Action.GET_RELEASE, _inputs(Action.GET_RELEASE)
    )
    assert _body(out) == {
        "type": "urn:webblock:problem:internal-error",
        "title": "An unexpected failure occurred.",
        "status": 500,
        "code": "INTERNAL_ERROR",
        "requestId": CORR,
        "errors": [],
    }
    assert "boom-detail" in stream.getvalue()
    assert "hunter2" not in stream.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "result"),
    [
        (
            Action.DEPLOY_RELEASE,
            lambda _r: CommandAccepted(
                command_id=COMMAND, project_id=OTHER, status=CommandStatus.ACCEPTED, replayed=False
            ),
        ),
        (Action.GET_RELEASE, lambda _r: release_view(OTHER, RELEASE, full=True)),
        (Action.GET_RELEASE, lambda _r: release_view(PROJECT, OTHER, full=True)),
        (Action.BUILD_RELEASE, lambda _r: release_view(OTHER, RELEASE, full=True)),
        (Action.BUILD_RELEASE, lambda _r: release_view(PROJECT, OTHER, full=True)),
    ],
)
async def test_a_result_for_another_resource_is_internal(action: Action, result: Any) -> None:
    service = FakeOperations(results={action: result})
    out = await _invoker(service).invoke(PRINCIPAL, action, _inputs(action))
    assert (out.status, out.code) == (500, "INTERNAL_ERROR")
    assert str(OTHER) not in out.body.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_nothing_echoes_the_key_or_the_requester(action: Action) -> None:
    for errors in ({}, {action: IdempotencyConflict()}, {action: Unauthenticated()}):
        stream = io.StringIO()
        service = FakeOperations(errors=dict(errors))
        out = await _invoker(service, stream=stream).invoke(PRINCIPAL, action, _inputs(action))
        for text in (out.body.decode(), stream.getvalue()):
            assert KEY not in text
            assert PRINCIPAL.subject not in text


@pytest.mark.asyncio
async def test_each_call_logs_one_record_with_the_allowed_fields() -> None:
    stream = io.StringIO()
    service = FakeOperations(replayed=True)
    out = await _invoker(service, stream=stream).invoke(
        PRINCIPAL, Action.DEPLOY_RELEASE, _inputs(Action.DEPLOY_RELEASE)
    )
    (record,) = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert record["msg"] == "request finished"
    assert record["operation"] == "deploy_release"
    assert record["status"] == 202
    assert record["correlation_id"] == CORR
    assert isinstance(record["duration_ms"], int)
    assert record["replayed"] is True
    assert "replayed" not in out.body.decode()
    assert "[REDACTED]" not in stream.getvalue()


@pytest.mark.asyncio
async def test_an_invalid_correlation_id_function_fails_closed() -> None:
    service = FakeOperations()
    invoker = Invoker(
        **buses(service),
        request_timeout=timedelta(seconds=1),
        new_correlation_id=lambda: "bad id\n",
        logger=new_logger(io.StringIO(), logging.INFO),
        bind_correlation=correlation_scope,
        trace_id=lambda: None,
    )
    out = await invoker.invoke(PRINCIPAL, Action.GET_DEPLOYMENT, _inputs(Action.GET_DEPLOYMENT))
    assert (out.status, out.code) == (500, "INTERNAL_ERROR")
    # The refused value is never echoed; the Problem still carries a request ID of its own.
    for body in (_body(out), json.loads(out.rest_body("/x"))):
        assert UUID(body["requestId"]).version == 7
        assert "bad id" not in json.dumps(body)
    assert service.calls == []


def test_random_correlation_ids() -> None:
    ids = {random_correlation_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(UUID(i).version == 7 for i in ids)


def test_random_correlation_id_is_the_current_request_id() -> None:
    with request_id_scope("req-1"):
        assert random_correlation_id() == "req-1"


def test_reject_classifies_a_transport_error() -> None:
    invoker = _invoker(FakeOperations())
    out = invoker.reject(Action.DEPLOY_RELEASE, MalformedRequest("body"))
    assert (out.status, out.code) == (400, "VALIDATION_FAILED")
    assert _body(out) == {
        "type": "urn:webblock:problem:validation-failed",
        "title": "The request is invalid.",
        "status": 400,
        "code": "VALIDATION_FAILED",
        "requestId": CORR,
        "errors": [],
    }
    out = invoker.reject(None, Unauthenticated())
    assert (out.status, out.code) == (401, "UNAUTHENTICATED")


def test_construction_refuses_a_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="request_timeout"):
        Invoker(
            **buses(FakeOperations()),
            request_timeout=timedelta(0),
            new_correlation_id=random_correlation_id,
            logger=new_logger(io.StringIO(), logging.INFO),
            bind_correlation=correlation_scope,
            trace_id=lambda: None,
        )


def test_ids_are_uuid_objects() -> None:
    assert isinstance(PROJECT, UUID)


@pytest.mark.asyncio
async def test_the_trace_id_is_the_sources_and_only_a_valid_one() -> None:
    trace = "0af7651916cd43dd8448eb211c80319c"
    for given, want in ((trace, trace), (None, None), ("not-hex", None)):
        invoker = Invoker(
            **buses(FakeOperations(errors={Action.GET_DEPLOYMENT: NotFound()})),
            request_timeout=timedelta(seconds=1),
            new_correlation_id=lambda: CORR,
            logger=new_logger(io.StringIO(), logging.INFO),
            bind_correlation=correlation_scope,
            trace_id=lambda value=given: value,  # type: ignore[misc]
        )
        out = await invoker.invoke(PRINCIPAL, Action.GET_DEPLOYMENT, _inputs(Action.GET_DEPLOYMENT))
        assert _body(out).get("traceId") == want
        assert json.loads(out.rest_body("/x")).get("traceId") == want


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DependencyUnavailable("x", deadline=True), 504, "DEPENDENCY_TIMEOUT"),
        (DependencyUnavailable("x", upstream_reason="PAYMENT_PAUSED"), 503, "PAYMENT_PAUSED"),
        (
            DependencyUnavailable("x", upstream_reason="PROJECT_NOT_FOUND"),
            503,
            "DEPENDENCY_UNAVAILABLE",
        ),
    ],
)
async def test_a_dependency_failure_keeps_its_upstream_answer(
    error: Exception, status: int, code: str
) -> None:
    service = FakeOperations(errors={Action.DEPLOY_RELEASE: error})
    out = await _invoker(service).invoke(
        PRINCIPAL, Action.DEPLOY_RELEASE, _inputs(Action.DEPLOY_RELEASE)
    )
    body = _body(out)
    assert (out.status, out.code, body["status"], body["code"]) == (status, code, status, code)
    assert body["type"] == "urn:webblock:problem:" + code.lower().replace("_", "-")
    if code == "PAYMENT_PAUSED":
        assert body["title"] == "A required dependency is unavailable."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "error", "status", "code"),
    [
        # Our code from upstream: our status, when the operation declares it.
        (
            Action.DEPLOY_RELEASE,
            DependencyUnavailable("x", upstream_reason="PUBLISH_LIMIT_REACHED"),
            422,
            "PUBLISH_LIMIT_REACHED",
        ),
        # A read declares no 422: our own code stands in, never INTERNAL_ERROR.
        (
            Action.GET_DEPLOYMENT,
            DependencyUnavailable("x", upstream_reason="PUBLISH_LIMIT_REACHED"),
            503,
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            Action.DEPLOY_RELEASE,
            DependencyUnavailable("x", upstream_reason="PAYMENT_SLOW", deadline=True),
            504,
            "PAYMENT_SLOW",
        ),
        # A read answers no 409 but CONCURRENT_CHANGE: our own code, never INTERNAL_ERROR.
        (
            Action.GET_DEPLOYMENT,
            DependencyUnavailable("x", upstream_reason="COMMAND_IN_PROGRESS"),
            503,
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            Action.GET_RELEASE,
            DependencyUnavailable("x", upstream_reason="BUILD_IN_PROGRESS"),
            503,
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            Action.LIST_COMMANDS,
            DependencyUnavailable("x", upstream_reason="COMMAND_IN_PROGRESS", deadline=True),
            504,
            "DEPENDENCY_TIMEOUT",
        ),
        (
            Action.GET_DEPLOYMENT,
            DependencyUnavailable("x", upstream_reason="CONCURRENT_CHANGE"),
            409,
            "CONCURRENT_CHANGE",
        ),
        (
            Action.DEPLOY_RELEASE,
            DependencyUnavailable("x", upstream_reason="COMMAND_IN_PROGRESS"),
            409,
            "COMMAND_IN_PROGRESS",
        ),
        # Project access never passes anything on, a deadline included.
        (
            Action.GET_DEPLOYMENT,
            ProjectAccessUnavailable("x", upstream_reason="NOT_MANAGER", deadline=True),
            503,
            "PROJECT_ACCESS_UNAVAILABLE",
        ),
    ],
)
async def test_the_upstream_answer_follows_the_operations_statuses(
    action: Action, error: Exception, status: int, code: str
) -> None:
    out = await _invoker(FakeOperations(errors={action: error})).invoke(
        PRINCIPAL, action, _inputs(action)
    )
    assert (out.status, out.code) == (status, code)
    assert (_body(out)["status"], _body(out)["code"]) == (status, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [a for a in Action if not a.is_mutation()])
async def test_a_read_that_meets_contention_is_409_concurrent_change(action: Action) -> None:
    """Lead ruling (Task 2 review): reads run with a lock timeout; a read never answers another
    409, so a command in flight on a read is still an internal error."""
    out = await _invoker(FakeOperations(errors={action: Contention("lock")})).invoke(
        PRINCIPAL, action, _inputs(action)
    )
    assert (out.status, out.code) == (409, "CONCURRENT_CHANGE")
    busy = await _invoker(FakeOperations(errors={action: OperationInProgress(COMMAND)})).invoke(
        PRINCIPAL, action, _inputs(action)
    )
    assert (busy.status, busy.code) == (500, "INTERNAL_ERROR")


@pytest.mark.asyncio
async def test_a_transport_names_its_own_fields() -> None:
    """REST names the path parameter and the header; MCP passes its argument names."""
    inputs = {"projectId": "bad", "releaseId": str(RELEASE)}
    rest = await _invoker(FakeOperations()).invoke(PRINCIPAL, Action.DEPLOY_RELEASE, inputs)
    mcp = await _invoker(FakeOperations()).invoke(
        PRINCIPAL,
        Action.DEPLOY_RELEASE,
        {**inputs, "projectId": str(PROJECT)},
        field_of=lambda name: name,
    )
    assert [e["field"] for e in _body(rest)["errors"]] == ["projectId"]
    assert [(e["field"], e["code"]) for e in _body(mcp)["errors"]] == [
        ("idempotencyKey", "REQUIRED")
    ]


@pytest.mark.asyncio
async def test_the_success_meta_carries_the_trace_only_when_sampled_and_the_clock() -> None:
    """Notion 2.1 (decision 0023 G2): traceId only when sampled; timestamp in UTC with
    milliseconds and Z; path without the query, percent-encoded; status the HTTP status."""
    trace = "0af7651916cd43dd8448eb211c80319c"
    at = datetime(2026, 10, 25, 9, 8, 7, 654321, tzinfo=UTC)
    for given, want in ((trace, trace), (None, None), ("not-hex", None)):
        invoker = Invoker(
            **buses(FakeOperations()),
            request_timeout=timedelta(seconds=1),
            new_correlation_id=lambda: CORR,
            logger=new_logger(io.StringIO(), logging.INFO),
            bind_correlation=correlation_scope,
            trace_id=lambda value=given: value,  # type: ignore[misc]
            clock=lambda: at,
        )
        out = await invoker.invoke(PRINCIPAL, Action.GET_DEPLOYMENT, _inputs(Action.GET_DEPLOYMENT))
        meta = json.loads(out.rest_body('/v1/a b"c'))["meta"]
        assert meta.get("traceId") == want
        assert "traceId" not in _body(out)  # MCP has no envelope
        assert meta["timestamp"] == "2026-10-25T09:08:07.654Z"
        assert meta["path"] == "/v1/a%20b%22c"
        assert (meta["status"], meta["page"]) == (200, None)
