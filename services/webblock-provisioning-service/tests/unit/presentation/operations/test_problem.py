"""The one error body of REST and MCP: an RFC 9457 Problem Details object (decision 0018 as amended
by 0023 G5). One URN per code, the code's fixed message as the title, the members of Notion 2.2 in
their order (``requestId``, ``traceId`` only when sampled, ``errors`` always), no detail, and an
instance that is the request path as an ASCII URI reference."""

from __future__ import annotations

import json
import re
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON

from provisioning.infrastructure.telemetry import sampled_trace_id
from provisioning.presentation.operations import (
    PROBLEM_MEDIA_TYPE,
    Code,
    FieldCode,
    FieldError,
    field_message,
    message,
    problem_body,
    problem_instance,
    problem_media_type,
    problem_type,
    request_accept,
    status_of,
)

IN_FLIGHT = UUID("0190a1b2-0000-7000-8000-0000000000c9")
TRACE = "0af7651916cd43dd8448eb211c80319c"
TYPE_PATTERN = re.compile(r"^urn:webblock:problem:[a-z0-9-]+$")
CODES = list(Code)


def test_the_media_type_is_problem_json() -> None:
    assert PROBLEM_MEDIA_TYPE == "application/problem+json"


def test_every_code_has_a_urn_of_its_own() -> None:
    assert len(CODES) == 29
    assert len({problem_type(code) for code in CODES}) == len(CODES)
    for code in CODES:
        assert TYPE_PATTERN.fullmatch(problem_type(code)), code
    assert problem_type(Code.COMMAND_IN_PROGRESS) == "urn:webblock:problem:command-in-progress"


@pytest.mark.parametrize("code", CODES)
def test_the_problem_of_each_code(code: Code) -> None:
    body = json.loads(problem_body(code, status_of(code), request_id="r-1", trace_id=None))
    assert body == {
        "type": problem_type(code),
        "title": message(code),
        "status": status_of(code),
        "code": code.value,
        "requestId": "r-1",
        "errors": [],
    }
    assert not {"detail", "message", "instance", "traceId", "correlation_id"} & set(body)


def test_problem_members_in_order_and_errors_always_present() -> None:
    body = json.loads(
        problem_body(
            Code.ROUTE_NOT_STABLE, 422, request_id="r-1", trace_id=None, instance="/v1/projects/x"
        )
    )
    assert list(body) == ["type", "title", "status", "instance", "code", "requestId", "errors"]
    assert body["type"] == "urn:webblock:problem:route-not-stable" and body["errors"] == []


def test_the_members_are_in_the_decisions_order() -> None:
    raw = problem_body(
        Code.COMMAND_IN_PROGRESS,
        409,
        request_id="r-1",
        trace_id=TRACE,
        command_id=IN_FLIGHT,
        instance="/v1/projects/p/releases/r/deploy",
    )
    assert raw == (
        b'{"type":"urn:webblock:problem:command-in-progress",'
        b'"title":"Another command of the project is in progress.","status":409,'
        b'"instance":"/v1/projects/p/releases/r/deploy","code":"COMMAND_IN_PROGRESS",'
        b'"requestId":"r-1","traceId":"0af7651916cd43dd8448eb211c80319c","errors":[],'
        b'"commandId":"0190a1b2-0000-7000-8000-0000000000c9"}'
    )


def test_field_errors_are_listed_in_order_with_fixed_messages() -> None:
    errors = (
        FieldError("projectId", FieldCode.INVALID_FORMAT),
        FieldError("Idempotency-Key", FieldCode.REQUIRED),
    )
    body = json.loads(
        problem_body(Code.VALIDATION_FAILED, 400, request_id="r-1", trace_id=None, errors=errors)
    )
    assert body["errors"] == [
        {
            "field": "projectId",
            "code": "INVALID_FORMAT",
            "message": field_message(FieldCode.INVALID_FORMAT),
        },
        {
            "field": "Idempotency-Key",
            "code": "REQUIRED",
            "message": field_message(FieldCode.REQUIRED),
        },
    ]


def test_an_upstream_reason_keeps_the_title_it_is_given() -> None:
    body = json.loads(
        problem_body(
            "PROJECT_ARCHIVED",
            503,
            request_id="r-1",
            trace_id=None,
            title=message(Code.PROJECT_ACCESS_UNAVAILABLE),
        )
    )
    assert body["type"] == "urn:webblock:problem:project-archived"
    assert body["code"] == "PROJECT_ARCHIVED"
    assert body["title"] == message(Code.PROJECT_ACCESS_UNAVAILABLE)


def test_unknown_extensions_are_left_out() -> None:
    body = json.loads(problem_body(Code.INTERNAL_ERROR, 500, request_id="r-1", trace_id=None))
    assert set(body) == {"type", "title", "status", "code", "requestId", "errors"}


def test_trace_id_only_when_sampled() -> None:
    with_trace = json.loads(problem_body(Code.INTERNAL_ERROR, 500, request_id="r", trace_id=TRACE))
    assert with_trace["traceId"] == TRACE
    assert list(with_trace).index("traceId") == list(with_trace).index("requestId") + 1
    assert "traceId" not in json.loads(
        problem_body(Code.INTERNAL_ERROR, 500, request_id="r", trace_id=None)
    )
    assert sampled_trace_id() is None  # no span
    on, off = TracerProvider(sampler=ALWAYS_ON), TracerProvider(sampler=ALWAYS_OFF)
    with on.get_tracer("t").start_as_current_span("sampled") as span:
        trace_id = sampled_trace_id()
        assert trace_id is not None
        assert trace_id == format(span.get_span_context().trace_id, "032x")
    with off.get_tracer("t").start_as_current_span("not sampled") as span:
        assert span.get_span_context().is_valid
        assert sampled_trace_id() is None


@pytest.mark.parametrize(
    ("path", "instance"),
    [
        ("/v1/projects/p/deployment", "/v1/projects/p/deployment"),
        ("/mcp", "/mcp"),
        ('/v1/x"<y>/é', "/v1/x%22%3Cy%3E/%C3%A9"),
        ("/a b\n", "/a%20b%0A"),
        ("/100%", "/100%25"),
        ("/a?b#c", "/a%3Fb%23c"),
        ("/\udcff", "/%3F"),
    ],
)
def test_the_instance_is_the_path_as_an_ascii_uri_reference(path: str, instance: str) -> None:
    assert problem_instance(path) == instance
    raw = problem_body(Code.PROJECT_NOT_FOUND, 404, request_id="r-1", trace_id=None, instance=path)
    assert raw.isascii()
    assert json.loads(raw)["instance"] == instance


PROBLEM = "application/problem+json"
PLAIN = "application/json"


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        # The Project service's ProblemDetailFactoryTest (spec 6.3), row for row.
        (None, PROBLEM),
        ("*/*", PROBLEM),
        ("application/*", PROBLEM),
        ("application/*+json", PROBLEM),
        ("application/problem+json", PROBLEM),
        ("application/json, application/problem+json", PROBLEM),
        ("application/json", PLAIN),
        ("text/html, application/json", PLAIN),
        ("application/xml", PROBLEM),
        ("text/html", PROBLEM),
        (";;;", PROBLEM),
        ("application/json;q=abc", PROBLEM),
        ("application/problem+json;q=0, application/json", PLAIN),
        # Absent, empty, a bare "*" and the wildcards that do not allow it.
        ("", PROBLEM),
        ("*", PROBLEM),
        ("text/*", PROBLEM),
        ("*/json", PROBLEM),
        # q-values: q=0 allows nothing; any q above 0 allows; a q outside 0..1 is unreadable.
        ("application/json;q=0", PROBLEM),
        ("application/json;q=0.5", PLAIN),
        ("application/json;q=1.5", PROBLEM),
        ("application/json;q=0.5;q=abc", PROBLEM),
        ("*/*;q=0", PROBLEM),
        ("*/*;q=0, application/json", PLAIN),
        ("application/*;q=0, application/json", PLAIN),
        ("application/json;q=0, text/html", PROBLEM),
        ("application/json;q=0, application/problem+json;q=0", PROBLEM),
        ("application/problem+json;q=0.1, application/json;q=0.9", PROBLEM),
        ("text/html, application/json;q=0.8, */*;q=0", PLAIN),
        # Case and parameters.
        ("APPLICATION/JSON", PLAIN),
        ("application/json; charset=utf-8", PLAIN),
        ("application/json;Q=abc", PROBLEM),
        ("application/json;Q=0.5", PLAIN),
        # Any number of decimals (Double.parseDouble parity), a trailing dot too; above 1 is not.
        ("application/json;q=1.", PLAIN),
        ("application/json;q=1.000", PLAIN),
        ("application/json;q=1.0001", PROBLEM),
        ("application/json;q=1.001", PROBLEM),
        ("application/json;q=0.1234", PLAIN),
        ("application/json;q=1.0000", PLAIN),
        ("application/json;q=2", PROBLEM),
        ("application/json;q=0.123", PLAIN),
        ("application/json, application/problem+json;q=0.", PLAIN),
        ("application/json;q=0.", PROBLEM),
        # Outside RFC 9110 (a qvalue starts with a digit): refused here; the Project service may
        # differ (ruling PD-R3).
        ("application/json;q=.5", PROBLEM),
        # A parameter value in quotes may hold a semicolon; it is not a parameter of its own.
        ('application/json;x="a;q=0"', PLAIN),
        ('application/json;x="a;q=0";q=0', PROBLEM),
        ('application/json;x="a\\";q=0"', PLAIN),
        # The MCP transport's own Accept (review M2): JSON allowed, problem+json not named.
        ("application/json, text/event-stream", PLAIN),
        ("text/event-stream", PROBLEM),
    ],
)
def test_the_content_type_follows_the_accept_header(accept: str | None, expected: str) -> None:
    """Spec 6.3 of the Project service, the same input giving the same answer in both services."""
    assert problem_media_type(accept) == expected


def test_the_accept_lines_of_a_request_are_one_list() -> None:
    lines = [(b"accept", b"text/html"), (b"host", b"x"), (b"accept", b"application/json")]
    scope = {"headers": lines}
    assert request_accept(scope) == "text/html, application/json"
    assert problem_media_type(request_accept(scope)) == PLAIN
    assert request_accept({"headers": [(b"host", b"x")]}) is None
    assert request_accept({}) is None


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "accept",
    [
        "," * (1 << 20),
        ',"' * (1 << 19),
        'a/b;x="," ' * (1 << 17),
        "a/b," * (1 << 18),
        ";" * (1 << 20),
        "\\" * (1 << 20),
        "a/b" + ';"' * (1 << 19),
        "a/b" + ';x="a;"' * (1 << 17),
        "a/b" + ";q=0" * (1 << 18),
    ],
    ids=[
        "commas",
        "quotes-and-commas",
        "quoted-commas-in-ranges",
        "ranges",
        "semicolons",
        "backslashes",
        "semicolons-and-quotes",
        "quoted-semicolons",
        "many-parameters",
    ],
)
def test_a_long_accept_header_is_answered_in_linear_time(accept: str) -> None:
    """Review I1: the split is one pass, so a megabyte of commas or quotes cannot hold the event
    loop. The result only is asserted; the test timeout catches a super-linear regression."""
    assert problem_media_type(accept) == PROBLEM
