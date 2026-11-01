"""Sentry error reports are default-deny (task H4; CLAUDE.md section 3).

Every event goes through a client built by ``sentry_client`` and a capturing transport, so nothing
leaves the process. Seven kinds of secret are planted into an exception's message, a local
variable, a log breadcrumb, a request header and an extra; none may appear anywhere in the
serialized envelope, while the exception's type and frames must still be there."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event

from provisioning.infrastructure.config import Secret
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.infrastructure.telemetry.sentry import (
    ErrorReporter,
    scrub_event,
    sentry_client,
)

DSN = "https://0123456789abcdef0123456789abcdef@sentry.example.invalid/4242"
DSN_KEY = "0123456789abcdef0123456789abcdef"
SECRETS = {
    "bearer": "Bearer s3cr3t-bearer-token-ABCDEFGHIJKLMNOP",
    "jwt": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlLXNlY3JldA",
    "dsn": DSN,
    "pg_password": "pg-password-Qx7Lw2Zr",
    "jenkins_token": "11aa22bb33cc44dd55ee66ff77gg88hh99",
    "catalog_dsn": "postgresql://site_0190:cat-pass-Hj3k@catalog.internal:5432/catalog",
    "customer": "Customer page text: Maria's bakery, 12 Rue Secret",
}
PROJECT_ID = "0190f5a0-1111-7000-8000-00000000abcd"


class CapturingTransport(Transport):
    """Keeps every envelope in memory; sends nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def serialized(self) -> str:
        return "".join(e.serialize().decode("utf-8", "replace") for e in self.envelopes)

    def events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for envelope in self.envelopes:
            event = envelope.get_event()
            if event is not None:
                out.append(cast("dict[str, Any]", event))
        return out


@pytest.fixture
def transport() -> CapturingTransport:
    return CapturingTransport()


@pytest.fixture
def client(transport: CapturingTransport) -> Iterator[sentry_sdk.Client]:
    built = sentry_client(
        dsn=Secret(DSN),
        environment="prototype",
        traces_sample_rate=0.0,
        process="provisioning-worker",
        transport=transport,
    )
    yield built
    built.close(timeout=0)


@pytest.fixture
def reporter(client: sentry_sdk.Client) -> ErrorReporter:
    return ErrorReporter(client, process="provisioning-worker", profile="prototype")


class DriverError(Exception):
    """Stands in for a driver's error whose text carries values."""


def _raise_with_secrets() -> None:
    bearer = SECRETS["bearer"]  # a local variable holding a secret
    password = SECRETS["pg_password"]
    del bearer, password
    raise DriverError(" | ".join(SECRETS.values()))


def _assert_no_secret(text: str) -> None:
    for name, secret in SECRETS.items():
        assert secret not in text, name
    for fragment in ("s3cr3t", "Qx7Lw2Zr", "cat-pass", "Maria", "11aa22bb", DSN_KEY, PROJECT_ID):
        assert fragment not in text, fragment


def test_a_reported_exception_keeps_its_type_and_frames_and_nothing_else(
    reporter: ErrorReporter, transport: CapturingTransport
) -> None:
    try:
        try:
            _raise_with_secrets()
        except DriverError as inner:
            raise RuntimeError(SECRETS["customer"]) from inner
    except RuntimeError as exc:
        with correlation_scope("corr-123"):
            reporter.report(exc)

    _assert_no_secret(transport.serialized())
    [event] = transport.events()
    types = [v["type"] for v in event["exception"]["values"]]
    assert types == ["DriverError", "RuntimeError"]
    for value in event["exception"]["values"]:
        assert "value" not in value
        frames = value["stacktrace"]["frames"]
        assert frames, value
        for frame in frames:
            assert set(frame) <= {"filename", "function", "module", "lineno", "in_app"}
    functions = [f.get("function") for f in event["exception"]["values"][0]["stacktrace"]["frames"]]
    assert "_raise_with_secrets" in functions
    assert event["tags"] == {
        "process": "provisioning-worker",
        "profile": "prototype",
        "correlation_id": "corr-123",
    }
    assert set(event) <= {
        "event_id",
        "timestamp",
        "level",
        "platform",
        "release",
        "environment",
        "server_name",
        "exception",
        "tags",
        "sdk",
    }


def test_secrets_in_breadcrumbs_headers_extras_and_tags_never_leave(
    client: sentry_sdk.Client, transport: CapturingTransport
) -> None:
    scope = sentry_sdk.Scope(client=client)
    for name, secret in SECRETS.items():
        scope.add_breadcrumb(category="log", message=secret, data={name: secret})
        scope.set_extra(name, secret)
        scope.set_tag(name, secret)
    scope.set_tag("project_id", PROJECT_ID)
    scope.set_user({"id": "alice", "email": "alice@example.invalid"})
    scope.set_context("catalog", {"dsn": SECRETS["catalog_dsn"]})
    try:
        _raise_with_secrets()
    except DriverError as exc:
        event, hint = sentry_sdk.utils.event_from_exception(exc, client_options=client.options)
    event["request"] = {
        "headers": {
            "Authorization": SECRETS["bearer"],
            "X-Jenkins-Token": SECRETS["jenkins_token"],
            "X-Correlation-Id": "corr-1",
        },
        "cookies": {"session": SECRETS["jwt"]},
        "data": SECRETS["customer"],
        "env": {"PG_PASS": SECRETS["pg_password"], "SENTRY_DSN": DSN},
        "query_string": "token=" + SECRETS["jwt"],
    }
    event["extra"] = {"dsn": DSN}
    event["logentry"] = {"message": SECRETS["customer"]}
    client.capture_event(event=event, hint=hint, scope=scope)

    _assert_no_secret(transport.serialized())
    [sent] = transport.events()
    assert "breadcrumbs" not in sent and "request" not in sent and "extra" not in sent
    assert set(sent.get("tags", {})) <= {"process", "profile", "correlation_id"}
    assert [v["type"] for v in sent["exception"]["values"]] == ["DriverError"]


def test_a_log_record_with_secrets_reports_only_the_exception(
    reporter: ErrorReporter, transport: CapturingTransport
) -> None:
    log = logging.getLogger("provisioning.test.sentry")
    log.propagate = False
    handler = reporter.log_handler()
    log.addHandler(handler)
    try:
        try:
            _raise_with_secrets()
        except DriverError:
            log.error("call failed %s", SECRETS["pg_password"], extra={"dsn": DSN})
        log.error("no exception here %s", SECRETS["customer"])
    finally:
        log.removeHandler(handler)

    _assert_no_secret(transport.serialized())
    [event] = transport.events()
    assert event["exception"]["values"][0]["type"] == "DriverError"


def test_the_structured_logger_reports_an_error_written_while_handling_an_exception(
    reporter: ErrorReporter, transport: CapturingTransport
) -> None:
    logger = new_logger(io.StringIO(), logging.INFO, observers=(reporter.log_observer,))
    try:
        _raise_with_secrets()
    except DriverError:
        logger.warning("retrying", error=SECRETS["jenkins_token"])  # not error level
        logger.error("request finished", error_code="timeout")  # an expected 5xx
        logger.error("request finished", error_code="internal", token=SECRETS["jwt"])
    logger.error("an error without an exception", error=SECRETS["customer"])

    _assert_no_secret(transport.serialized())
    assert [e["exception"]["values"][0]["type"] for e in transport.events()] == ["DriverError"]


def test_a_message_event_is_dropped(
    client: sentry_sdk.Client, transport: CapturingTransport
) -> None:
    client.capture_event(event=cast("Event", {"message": SECRETS["customer"], "level": "error"}))
    assert transport.envelopes == []


def test_transactions_and_breadcrumbs_are_dropped_by_the_hooks(client: sentry_sdk.Client) -> None:
    options = client.options
    assert options["before_breadcrumb"]({"message": "x"}, {}) is None
    assert options["before_send_transaction"]({"type": "transaction"}, {}) is None


def test_scrub_event_drops_an_event_whose_shapes_are_unexpected() -> None:
    hostile: dict[str, Any] = {
        "exception": {"values": [{"type": "Bad Type With Spaces " + SECRETS["jwt"]}]},
        "tags": {"process": SECRETS["bearer"], "correlation_id": "bad value with spaces"},
    }
    assert scrub_event(cast("Event", hostile), {}) is None
    kept = scrub_event(
        cast(
            "Event",
            {
                "exception": {
                    "values": [
                        {
                            "type": "ValueError",
                            "value": SECRETS["pg_password"],
                            "stacktrace": {
                                "frames": [
                                    {
                                        "filename": "provisioning/x.py",
                                        "abs_path": "/home/roa/x.py",
                                        "function": "f",
                                        "lineno": 3,
                                        "vars": {"password": SECRETS["pg_password"]},
                                        "context_line": "password = " + SECRETS["pg_password"],
                                    }
                                ]
                            },
                        }
                    ]
                },
                "tags": {"process": SECRETS["bearer"], "profile": "prototype"},
                "server_name": "host.example.invalid",
            },
        ),
        {},
    )
    assert kept is not None
    _assert_no_secret(json.dumps(kept))
    assert kept["exception"]["values"][0]["stacktrace"]["frames"] == [
        {"filename": "provisioning/x.py", "function": "f", "lineno": 3}
    ]
    assert kept["tags"] == {"profile": "prototype"}
    assert "server_name" not in kept


def test_every_automatic_collection_is_off(client: sentry_sdk.Client) -> None:
    options = client.options
    assert options["default_integrations"] is False
    assert options["auto_enabling_integrations"] is False
    assert client.integrations == {}
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["include_source_context"] is False
    assert options["max_request_body_size"] == "never"
    assert options["max_breadcrumbs"] == 0
    assert options["attach_stacktrace"] is False
    assert options["before_send"] is scrub_event
    assert options["http_proxy"] == "" and options["https_proxy"] == ""
    assert options["server_name"] == "provisioning-worker"
    assert options["auto_session_tracking"] is False
    assert options["enable_logs"] is False


def test_the_client_refuses_settings_without_naming_the_dsn() -> None:
    with pytest.raises(ValueError) as caught:
        sentry_client(
            dsn=Secret("ftp://" + DSN_KEY + "@x/1"),
            environment="prototype",
            traces_sample_rate=0.0,
            process="provisioning-api",
            transport=CapturingTransport(),
        )
    assert DSN_KEY not in str(caught.value)
    with pytest.raises(ValueError, match="sample rate"):
        sentry_client(
            dsn=Secret(DSN),
            environment="prototype",
            traces_sample_rate=1.5,
            process="provisioning-api",
        )


def test_a_report_never_raises(client: sentry_sdk.Client) -> None:
    class Broken(Transport):
        def capture_envelope(self, envelope: Envelope) -> None:
            raise RuntimeError("transport down")

    broken = sentry_client(
        dsn=Secret(DSN),
        environment="prototype",
        traces_sample_rate=0.0,
        process="provisioning-api",
        transport=Broken(),
    )
    ErrorReporter(broken, process="provisioning-api", profile="production").report(ValueError("x"))
