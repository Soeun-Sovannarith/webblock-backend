"""The process metrics (task H4): each instrument moves on the event it measures, and the whole
registry stays low-cardinality: no label name outside a fixed allow-list, and no label value that
is an identifier, whatever the callers pass."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import re
from datetime import UTC, datetime
from uuid import UUID

import pytest

from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    NullAuditLog,
)
from provisioning.core.domain import FAILURE_CODES, OTHER_CODES
from provisioning.infrastructure.telemetry import metrics as metrics_module
from provisioning.infrastructure.telemetry import new_logger
from provisioning.infrastructure.telemetry.metrics import (
    CONTENT_TYPE,
    MeteredAuditLog,
    Metrics,
)

PROJECT = UUID("0190f5a0-1111-7000-8000-00000000abcd")
RELEASE = UUID("0190f5a0-2222-7000-8000-00000000abcd")
COMMAND = UUID("0190f5a0-3333-7000-8000-00000000abcd")
AT = datetime(2026, 10, 10, tzinfo=UTC)
ROUTES = ("/v1/projects/{project_id}/deployment", "/mcp")

# The allow-list of the cardinality test: the only label names any series may carry. It is kept
# here, not imported, so that a label added in the code without review fails this test.
ALLOWED_LABELS = frozenset(
    {
        "kind",
        "status",
        "failure_code",
        "result",
        "outcome",
        "round",
        "route",
        "method",
        "status_class",
        "auth_cause",
        "sample",
        "generation",
        "le",  # a histogram bucket's upper bound
    }
)
_ID_LIKE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-|[0-9a-f]{16,}|eyJ", re.IGNORECASE)
_VALUE_FORM = re.compile(r"[a-z0-9_{}/]{1,80}|[1-5]xx|[A-Z]{3,7}|[0-9.]{1,8}|\+Inf")


def _value(metrics: Metrics, name: str, labels: dict[str, str] | None = None) -> float:
    value = metrics.registry.get_sample_value(name, labels or {})
    return 0.0 if value is None else value


def _event(
    action: AuditAction,
    result: AuditResult,
    *,
    code: str | None = None,
    actor: AuditActorKind = AuditActorKind.SYSTEM,
) -> AuditEvent:
    return AuditEvent(
        actor_kind=actor,
        actor="worker",
        action=action,
        project_id=PROJECT,
        release_id=RELEASE,
        command_id=COMMAND,
        result=result,
        failure_code=code,
        at=AT,
        correlation_id=None,
    )


def test_command_executions_and_http_requests_move_their_instruments() -> None:
    metrics = Metrics(routes=ROUTES, process_collectors=False)
    metrics.command_executed("deploy", "finished", 2.5)
    metrics.command_executed("promote", "transient_failure", 0.2)
    metrics.http_request(ROUTES[0], "GET", 200, 0.01)
    metrics.http_request(ROUTES[0], "GET", 503, 0.01)
    metrics.http_request("/mcp", "POST", None, 0.5)
    metrics.build_round_result("poll", "unavailable")

    assert (
        _value(
            metrics,
            "provisioning_command_execution_seconds_count",
            {"kind": "deploy", "result": "finished"},
        )
        == 1
    )
    assert (
        _value(
            metrics,
            "provisioning_command_execution_seconds_sum",
            {"kind": "deploy", "result": "finished"},
        )
        == 2.5
    )
    labels = {"route": ROUTES[0], "method": "GET"}
    assert (
        _value(metrics, "provisioning_http_requests_total", labels | {"status_class": "2xx"}) == 1
    )
    assert (
        _value(metrics, "provisioning_http_requests_total", labels | {"status_class": "5xx"}) == 1
    )
    assert (
        _value(
            metrics,
            "provisioning_http_requests_total",
            {"route": "/mcp", "method": "POST", "status_class": "none"},
        )
        == 1
    )
    assert _value(metrics, "provisioning_http_request_duration_seconds_count", labels) == 2
    assert (
        _value(
            metrics,
            "provisioning_build_round_results_total",
            {"round": "poll", "outcome": "unavailable"},
        )
        == 1
    )


def test_tr57_try_failed_is_a_bounded_execution_result() -> None:
    """TR-57 (G15): a failed go-live try is counted as ``try_failed``, never folded into
    ``other``."""
    metrics = Metrics(process_collectors=False)
    metrics.command_executed("promote", "try_failed", 0.3)
    name = "provisioning_command_execution_seconds_count"
    assert _value(metrics, name, {"kind": "promote", "result": "try_failed"}) == 1
    assert _value(metrics, name, {"kind": "promote", "result": "other"}) == 0


def test_the_audit_log_counts_finished_commands_and_build_attempts_with_their_duration() -> None:
    metrics = Metrics(process_collectors=False)
    now = [100.0]
    audit = MeteredAuditLog(NullAuditLog(), metrics, clock=lambda: now[0])

    audit.record(_event(AuditAction.DEPLOY, AuditResult.SUCCEEDED))
    audit.record(_event(AuditAction.PROMOTE, AuditResult.FAILED, code="route_change_unconfirmed"))
    audit.record(_event(AuditAction.SUSPEND_HOSTING, AuditResult.SKIPPED, code="hosting_offline"))
    # The API's acceptance of a command is not the end of one.
    audit.record(_event(AuditAction.DEPLOY, AuditResult.ACCEPTED, actor=AuditActorKind.USER))
    audit.record(_event(AuditAction.BUILD_ATTEMPT_STARTED, AuditResult.ACCEPTED))
    now[0] = 130.0
    audit.record(_event(AuditAction.BUILD_ATTEMPT_STARTED, AuditResult.SUCCEEDED))  # same attempt
    now[0] = 400.0
    audit.record(
        _event(AuditAction.BUILD_ATTEMPT_FINISHED, AuditResult.FAILED, code="build_failed")
    )
    audit.record(_event(AuditAction.BUILD_ATTEMPT_FINISHED, AuditResult.SUCCEEDED))  # start unseen
    audit.alert(_event(AuditAction.ROUTE_UNCERTAIN, AuditResult.FAILED, code="route_uncertain"))

    finished = "provisioning_commands_finished_total"
    assert (
        _value(metrics, finished, {"kind": "deploy", "status": "succeeded", "failure_code": "none"})
        == 1
    )
    assert (
        _value(
            metrics,
            finished,
            {"kind": "promote", "status": "failed", "failure_code": "route_change_unconfirmed"},
        )
        == 1
    )
    assert (
        _value(
            metrics,
            finished,
            {"kind": "suspend_hosting", "status": "skipped", "failure_code": "hosting_offline"},
        )
        == 1
    )
    builds = "provisioning_build_attempts_finished_total"
    assert _value(metrics, builds, {"outcome": "failed", "failure_code": "build_failed"}) == 1
    assert _value(metrics, builds, {"outcome": "succeeded", "failure_code": "none"}) == 1
    duration = "provisioning_build_attempt_duration_seconds"
    assert _value(metrics, duration + "_count", {"outcome": "failed"}) == 1
    assert _value(metrics, duration + "_sum", {"outcome": "failed"}) == 300.0
    assert _value(metrics, duration + "_count", {"outcome": "succeeded"}) == 0
    assert _value(metrics, "provisioning_route_uncertain_alerts_total") == 1


# The codes of the catalogue's second table (OTHER_CODES) that the worker's consumer writes on the
# dispatch row and that the failure-code label also carries (task H4-FU).
CONSUMER_CODES = ("transient_failure", "attempt_timeout", "worker_stopping")
_CODE_FORM = re.compile(r"[a-z][a-z0-9_]{0,99}")


def test_every_catalogued_failure_code_passes_through_as_itself() -> None:
    metrics = Metrics(process_collectors=False)
    audit = MeteredAuditLog(NullAuditLog(), metrics)
    codes = [entry.code for entry in FAILURE_CODES] + list(CONSUMER_CODES)
    for code in codes:
        metrics.command_finished("deploy", "failed", code)
        audit.record(_event(AuditAction.BUILD_ATTEMPT_FINISHED, AuditResult.FAILED, code=code))

    finished = "provisioning_commands_finished_total"
    builds = "provisioning_build_attempts_finished_total"
    for code in codes:
        labels = {"kind": "deploy", "status": "failed", "failure_code": code}
        assert _value(metrics, finished, labels) == 1, code
        assert _value(metrics, builds, {"outcome": "failed", "failure_code": code}) == 1, code
    other = {"kind": "deploy", "status": "failed", "failure_code": "other"}
    assert _value(metrics, finished, other) == 0
    assert _value(metrics, builds, {"outcome": "failed", "failure_code": "other"}) == 0


def test_an_uncatalogued_failure_code_counts_as_other() -> None:
    metrics = Metrics(process_collectors=False)
    # A build system's own code, a different spelling, a code of the catalogue's second table that
    # no command or build attempt ends with (the usage outbox's), and an identifier.
    for code in ("checkout_exploded", "Clone_Failed", "publish_failed", str(PROJECT)):
        metrics.command_finished("deploy", "failed", code)
        metrics.build_attempt_finished(RELEASE, "failed", code, 1.0)

    labels = {"kind": "deploy", "status": "failed", "failure_code": "other"}
    assert _value(metrics, "provisioning_commands_finished_total", labels) == 4
    assert (
        _value(
            metrics,
            "provisioning_build_attempts_finished_total",
            {"outcome": "failed", "failure_code": "other"},
        )
        == 4
    )


def test_the_failure_code_allow_list_is_the_catalogue_and_holds_no_identifier() -> None:
    allowed = metrics_module.FAILURE_CODE_LABELS
    # The catalogue still lists the consumer's codes in its second table.
    assert set(CONSUMER_CODES) <= {entry.code for entry in OTHER_CODES}
    assert allowed == {entry.code for entry in FAILURE_CODES} | set(CONSUMER_CODES)
    assert isinstance(allowed, frozenset)
    for code in allowed:
        assert _CODE_FORM.fullmatch(code), code
        assert _ID_LIKE.search(code) is None, code
    assert not {"other", "none"} & allowed


def test_the_identity_log_events_count_refusals_by_their_fixed_cause() -> None:
    metrics = Metrics(process_collectors=False)
    # The verifier logs refusals at info: the metric must not depend on LOG_LEVEL.
    logger = new_logger(io.StringIO(), logging.ERROR, observers=(metrics.observe_log_event,))
    logger.info("token refused", component="identity", auth_cause="expired")
    logger.info("token refused", component="identity", auth_cause="expired")
    logger.info("token refused", component="identity", auth_cause=str(PROJECT))
    logger.warning("jwks fetch failed", component="identity", auth_cause="jwks_timeout")
    logger.info("something else", auth_cause="expired")

    refusals = "provisioning_identity_refusals_total"
    assert _value(metrics, refusals, {"auth_cause": "expired"}) == 2
    assert _value(metrics, refusals, {"auth_cause": "other"}) == 1
    assert (
        _value(
            metrics,
            "provisioning_identity_key_fetch_failures_total",
            {"auth_cause": "jwks_timeout"},
        )
        == 1
    )


def test_the_registry_holds_no_identifier_whatever_the_callers_pass() -> None:
    metrics = Metrics(routes=ROUTES)
    audit = MeteredAuditLog(NullAuditLog(), metrics)
    hostile = str(PROJECT)
    metrics.command_executed(hostile, hostile, 1.0)
    metrics.build_round_result(hostile, hostile)
    metrics.http_request(f"/v1/projects/{PROJECT}/deployment", hostile, 999, 1.0)
    metrics.command_finished(hostile, hostile, hostile)
    metrics.build_attempt_started(RELEASE, 1.0)
    metrics.build_attempt_finished(RELEASE, hostile, "eyJhbGciOi.secret.sig", 2.0)
    metrics.bootstrap_round(not_ready=3)
    metrics.bootstrap_round(not_ready=None)
    metrics.observe_log_event("info", {"component": "identity", "auth_cause": hostile})
    audit.record(_event(AuditAction.DEPLOY, AuditResult.FAILED, code=hostile))
    audit.record(_event(AuditAction.BUILD_ATTEMPT_FINISHED, AuditResult.FAILED, code=hostile))
    gauge = metrics.gauge("provisioning_inbox_events", "test", ("status",))
    gauge.labels("pending").set(1)

    seen_labels: set[str] = set()
    for family in metrics.registry.collect():
        for sample in family.samples:
            for name, value in sample.labels.items():
                seen_labels.add(name)
                assert name in ALLOWED_LABELS, (family.name, name)
                assert _ID_LIKE.search(value) is None, (family.name, name, value)
                assert _VALUE_FORM.fullmatch(value), (family.name, name, value)
    assert {"kind", "failure_code", "route", "auth_cause"} <= seen_labels
    text = asyncio.run(metrics.render())[0].decode()
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text) is None


@pytest.mark.asyncio
async def test_render_reads_the_samples_and_marks_a_failed_one_unknown() -> None:
    metrics = Metrics(process_collectors=False, sample_timeout=0.05)
    gauge = metrics.gauge("provisioning_dispatch_lag_seconds", "test")
    reads: list[str] = []

    async def good() -> None:
        reads.append("good")
        gauge.set(12.0)

    async def hangs() -> None:
        reads.append("hangs")
        await asyncio.Event().wait()

    metrics.add_sample("dispatch", good, lambda: gauge.set(math.nan))
    body, content_type = await metrics.render()
    assert content_type == CONTENT_TYPE
    assert "provisioning_dispatch_lag_seconds 12.0" in body.decode()

    metrics.add_sample("inbox", hangs, lambda: gauge.set(math.nan))
    body, _ = await metrics.render()
    assert reads == ["good", "good", "hangs"]
    assert math.isnan(_value(metrics, "provisioning_dispatch_lag_seconds"))
    assert _value(metrics, "provisioning_metrics_sample_failures_total", {"sample": "inbox"}) == 1
    with pytest.raises(ValueError):
        metrics.add_sample(str(PROJECT), good, lambda: None)


def test_a_bootstrap_round_sets_the_tenants_not_ready_gauge_only_where_rounds_run() -> None:
    metrics = Metrics(process_collectors=False)
    assert metrics.registry.get_sample_value("provisioning_bootstrap_tenants_not_ready") is None
    metrics.bootstrap_round(not_ready=2)
    metrics.bootstrap_round(not_ready=None)
    assert _value(metrics, "provisioning_bootstrap_tenants_not_ready") == 2
    assert _value(metrics, "provisioning_bootstrap_rounds_total", {"result": "failed"}) == 1


def test_the_bootstrap_standby_gauge_is_one_while_standing_by_and_zero_once_the_lock_is_held() -> (
    None
):
    """Task H5c: provisioning_bootstrap_standby, registered only where a bootstrap reports it."""
    metrics = Metrics(process_collectors=False)
    assert metrics.registry.get_sample_value("provisioning_bootstrap_standby") is None
    metrics.bootstrap_standby(standby=True)
    assert _value(metrics, "provisioning_bootstrap_standby") == 1
    metrics.bootstrap_standby(standby=False)
    assert _value(metrics, "provisioning_bootstrap_standby") == 0
