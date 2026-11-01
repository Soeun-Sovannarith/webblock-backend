"""The structured logger: JSON lines, an allow-list of fields, credential scrubbing and the
correlation ID of the context (decision 0006 section 7)."""

from __future__ import annotations

import ast
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from provisioning.infrastructure.config import Secret
from provisioning.infrastructure.telemetry import (
    ALLOWED_FIELDS,
    MAX_FIELD_LEN,
    REDACTED,
    correlation_scope,
    new_logger,
    scrub,
    stdlib_handler,
)

FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLW5vdC1yZWFs"
ID = UUID("0190a1b2-0000-7000-8000-000000000001")


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _log(**fields: object) -> dict[str, Any]:
    stream = io.StringIO()
    new_logger(stream, logging.DEBUG).info("request finished", **fields)
    (record,) = _lines(stream)
    return record


def test_allow_listed_fields_are_logged_with_their_values() -> None:
    record = _log(
        operation="deploy_release",
        status=202,
        duration_ms=12,
        project_id=str(ID),
        release_id=ID,
        replayed=True,
        attempt=2,
        error_code="not_found",
    )
    assert record["msg"] == "request finished"
    assert record["level"] == "info"
    assert datetime.fromisoformat(record["time"]).tzinfo is not None
    assert record["operation"] == "deploy_release"
    assert record["status"] == 202
    assert record["duration_ms"] == 12
    assert record["project_id"] == str(ID)
    assert record["release_id"] == str(ID)
    assert record["replayed"] is True
    assert record["attempt"] == 2
    assert record["error_code"] == "not_found"


@pytest.mark.parametrize(
    "key",
    ["token", "authorization", "password", "body", "subject", "idempotency_key", "requested_by"],
)
def test_fields_outside_the_allow_list_are_redacted(key: str) -> None:
    record = _log(**{key: "visible-value"})
    assert record[key] == REDACTED


def test_a_field_set_to_none_is_omitted() -> None:
    record = _log(error_code=None, token=None, status=200)
    assert "error_code" not in record
    assert "token" not in record
    assert record["status"] == 200


def test_a_key_of_another_form_is_logged_as_invalid_field() -> None:
    record = _log(**{"Bad-Key": "x"})
    assert record["invalid_field"] == REDACTED
    assert "Bad-Key" not in record


def test_values_of_other_kinds_are_redacted_on_allowed_keys() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "opaque-visible"

    record = _log(operation=Opaque(), status=[1, 2], error_code={"a": 1})
    assert record["operation"] == REDACTED
    assert record["status"] == REDACTED
    assert record["error_code"] == REDACTED


def test_durations_print_milliseconds_and_enums_their_value() -> None:
    class Kind(StrEnum):
        DEPLOY = "deploy"

    record = _log(retry_in=timedelta(seconds=1.5), kind=Kind.DEPLOY)
    assert record["retry_in"] == 1500
    assert record["kind"] == "deploy"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("connect postgresql://user:hunter2@db:5432/x failed", "hunter2"),
        (f"token {FAKE_JWT} refused", FAKE_JWT),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
            "abcdefghijklmnopqrstuvwxyz012345",
        ),
        ("password=hunter2 user=x", "hunter2"),
        ('{"api_key": "k-1234567"}', "k-1234567"),
        ("cookie: session=abc123def", "session=abc123def"),
    ],
)
def test_credentials_inside_allowed_text_are_scrubbed(text: str, secret: str) -> None:
    record = _log(error=text)
    assert secret not in record["error"]
    assert REDACTED in record["error"]
    assert secret not in scrub(text)


def test_the_message_is_scrubbed_too() -> None:
    stream = io.StringIO()
    new_logger(stream, logging.INFO).warning(f"bad token {FAKE_JWT}")
    (record,) = _lines(stream)
    assert FAKE_JWT not in record["msg"]


def test_scrub_bounds_length_at_a_character_boundary() -> None:
    out = scrub("é" * (MAX_FIELD_LEN + 10))
    assert out.endswith("…[truncated]")
    assert len(out.removesuffix("…[truncated]").encode("utf-8")) <= MAX_FIELD_LEN


def test_errors_are_logged_scrubbed_under_error() -> None:
    record = _log(error=RuntimeError("dial postgresql://u:pw-visible@h/d"))
    assert "pw-visible" not in record["error"]
    assert "dial" in record["error"]


def test_built_in_field_names_cannot_carry_user_values() -> None:
    record = _log(time="1999", level="fake", msg="forged")
    assert record["level"] == "info"
    assert record["msg"] == "request finished"
    assert record["time"] != "1999"
    assert record["field_time"] == REDACTED
    assert record["field_level"] == REDACTED
    assert record["field_msg"] == REDACTED


def test_secrets_stay_redacted_under_allowed_keys() -> None:
    record = _log(error=Secret("secret-visible"), reason=Secret("secret-visible"))
    assert "secret-visible" not in json.dumps(record)


def test_exc_info_is_not_a_way_around_the_filter() -> None:
    stream = io.StringIO()
    try:
        raise ValueError("driver said password=hunter2")
    except ValueError:
        new_logger(stream, logging.INFO).error("failed", exc_info=True)
    assert "hunter2" not in stream.getvalue()


def test_level_filter_applies() -> None:
    stream = io.StringIO()
    log = new_logger(stream, logging.WARNING)
    log.info("dropped")
    log.debug("dropped")
    log.warning("kept")
    log.error("kept")
    assert [r["level"] for r in _lines(stream)] == ["warning", "error"]


def test_counter_fields_print_integers_only() -> None:
    record = _log(claim_number="tok-visible", publish_attempts=3, command_number=True)
    assert record["claim_number"] == REDACTED
    assert record["publish_attempts"] == 3
    assert record["command_number"] == REDACTED


def test_replayed_prints_a_boolean_only() -> None:
    assert _log(replayed="yes")["replayed"] == REDACTED
    assert _log(replayed=False)["replayed"] is False


def test_a_newline_in_a_value_cannot_forge_a_log_line() -> None:
    stream = io.StringIO()
    new_logger(stream, logging.INFO).info("x", reason='a\n{"level":"error","msg":"forged"}')
    assert len(stream.getvalue().splitlines()) == 1


def test_the_correlation_id_of_the_context_is_logged() -> None:
    stream = io.StringIO()
    log = new_logger(stream, logging.INFO)
    with correlation_scope("corr-123"):
        log.info("inside")
    log.info("outside")
    inside, outside = _lines(stream)
    assert inside["correlation_id"] == "corr-123"
    assert "correlation_id" not in outside


def test_a_supplied_correlation_attribute_must_be_valid() -> None:
    assert _log(correlation_id="ok-1")["correlation_id"] == "ok-1"
    assert _log(correlation_id="bad id\n")["correlation_id"] == "[INVALID]"


def test_allowed_fields_have_no_secret_names() -> None:
    for name in ALLOWED_FIELDS:
        for word in ("token", "secret", "password", "authorization", "cookie", "key", "subject"):
            assert word not in name, name


def test_the_stdlib_bridge_writes_the_same_records() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("test_telemetry_bridge")
    logger.propagate = False
    handler = stdlib_handler(stream, logging.INFO)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug("dropped")
        logger.warning("attempt %d of release %s: token %s", 2, ID, FAKE_JWT)
    finally:
        logger.removeHandler(handler)
    (record,) = _lines(stream)
    assert record["level"] == "warning"
    assert record["component"] == "test_telemetry_bridge"
    assert f"attempt 2 of release {ID}" in record["msg"]
    assert FAKE_JWT not in record["msg"]
    assert datetime.fromisoformat(record["time"]).tzinfo == UTC


# Every keyword a module of the package logs with is on the allow-list. Log calls are found by the
# method name on a receiver named like a logger; the telemetry package itself is the redaction and
# is skipped. Standard-library keywords that our handlers never print are allowed.
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
_STDLIB_KWARGS = frozenset({"exc_info", "stack_info", "stacklevel"})


def test_every_log_key_in_the_package_is_allow_listed() -> None:
    src = Path(__file__).resolve().parents[4] / "src" / "provisioning"
    offending: list[str] = []
    calls = 0
    for path in sorted(src.rglob("*.py")):
        if "telemetry" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
            ):
                continue
            receiver = ast.unparse(node.func.value).lower()
            if "log" not in receiver:
                continue
            calls += 1
            for kw in node.keywords:
                if kw.arg is None:
                    offending.append(f"{path.relative_to(src)}:{node.lineno}: **kwargs")
                elif kw.arg not in ALLOWED_FIELDS and kw.arg not in _STDLIB_KWARGS:
                    offending.append(f"{path.relative_to(src)}:{node.lineno}: {kw.arg}")
    assert calls > 0, "no log call found: the scan is not looking at the package"
    assert offending == []


def test_observers_see_every_record_before_the_level_filter_and_never_change_it() -> None:
    """Task H4: a metric must not depend on LOG_LEVEL, and an observer that raises is dropped."""
    seen: list[tuple[str, dict[str, object]]] = []

    def broken(level: str, event: object) -> None:
        raise RuntimeError("observer down")

    out = io.StringIO()
    log = new_logger(
        out, logging.WARNING, observers=(lambda lv, ev: seen.append((lv, dict(ev))), broken)
    )
    log.info("below the level", component="identity", auth_cause="expired")
    log.error("kept", token="t" * 40)
    lines = out.getvalue().splitlines()
    assert [level for level, _ in seen] == ["info", "error"]
    assert seen[0][1]["auth_cause"] == "expired"
    assert len(lines) == 1 and '"msg":"kept"' in lines[0] and "t" * 40 not in lines[0]
