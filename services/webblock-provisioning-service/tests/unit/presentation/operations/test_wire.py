"""The REST and MCP bodies (decision 0023 G2, G3): every name is camelCase, ``repositoryId``
replaces ``github_repository_id``, and enum values such as ``online`` go out unchanged. Optional
fields are omitted when absent, lists are never null, and no body carries a key, fingerprint or
requester. A REST success is the envelope of Notion 2.1."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from tests.unit.presentation.fakes import (
    COMMAND,
    DIGEST,
    GENERATION_REQUEST,
    PROJECT,
    RELEASE,
    SHA,
    admin_command_page,
    command_view,
    deployment_view,
    diagnostics_view,
    release_view,
)

from provisioning.core.application import CommandAccepted
from provisioning.core.domain import CommandStatus, HostingStatus
from provisioning.presentation.operations import (
    Page,
    accepted_body,
    admin_command_body,
    admin_command_list_body,
    command_body,
    deployment_body,
    diagnostics_body,
    envelope,
    list_result,
    meta_timestamp,
    release_body,
    timestamp,
)


def test_release_body_uses_camel_case_names() -> None:
    body = release_body(release_view(full=True))
    assert body == {
        "releaseId": str(RELEASE),
        "projectId": str(PROJECT),
        "generationRequestId": str(GENERATION_REQUEST),
        "repositoryId": 4242,
        "gitCommitSha": SHA,
        "containerImageDigest": DIGEST,
        "status": "rolled_back",
        "failureCode": "promotion_failed",
        "failureRetryable": False,  # not a catalogued code
        "failureSummary": "the candidate did not answer",
        "steps": [
            {
                "stepName": "build",
                "attempt": 1,
                "status": "failed",
                "startedAt": "2026-10-08T12:00:00.123456Z",
                "finishedAt": "2026-10-08T12:00:05Z",
                "deadlineAt": "2026-10-08T12:00:05Z",
                "failureCode": "build_failed",
                "failureRetryable": False,
            }
        ],
        "createdAt": "2026-10-08T12:00:00.123456Z",
        "updatedAt": "2026-10-08T12:00:05Z",
        "promotedAt": "2026-10-08T12:00:05Z",
    }


def test_optional_fields_are_omitted_and_lists_are_never_null() -> None:
    body = release_body(replace(release_view(full=False), steps=()))
    for key in (
        "containerImageDigest",
        "failureCode",
        "failureRetryable",
        "failureSummary",
        "promotedAt",
    ):
        assert key not in body
    assert body["steps"] == []
    step = release_body(release_view(full=False))["steps"][0]
    assert step == {"stepName": "build", "attempt": 1, "status": "pending"}
    assert diagnostics_body(diagnostics_view(full=False)) == {
        "releaseId": str(RELEASE),
        "projectId": str(PROJECT),
        "release": {
            "status": "accepted",
            "createdAt": "2026-10-08T12:00:00.123456Z",
            "updatedAt": "2026-10-08T12:00:05Z",
        },
        "steps": [],
        "stepsTruncated": False,
        "commands": [],
        "commandsTruncated": False,
    }


def test_command_body() -> None:
    assert command_body(command_view(full=True)) == {
        "commandId": str(COMMAND),
        "projectId": str(PROJECT),
        "releaseId": str(RELEASE),
        "kind": "deploy",
        "commandNumber": 7,
        "status": "failed",
        "failureCode": "candidate_not_ready",
        "failureRetryable": True,
        "createdAt": "2026-10-08T12:00:00.123456Z",
        "updatedAt": "2026-10-08T12:00:05Z",
    }
    bare = command_body(command_view(full=False))
    assert "releaseId" not in bare
    assert "failureCode" not in bare
    assert "failureRetryable" not in bare
    assert bare["kind"] == "suspend_hosting"


def test_the_admin_command_body_adds_only_the_requester() -> None:
    """Decision 0023 G9: the admin read is the Command schema plus requestedBySubject, present
    when the command has a requester (a command from an event has none). The client encoder never
    emits it, even when it is handed an admin view."""
    with_requester, from_event = admin_command_page(full=True).items
    assert admin_command_body(with_requester) == {
        **command_body(with_requester),
        "requestedBySubject": "admin-staff",
    }
    assert admin_command_body(from_event) == command_body(from_event)
    assert "requestedBySubject" not in command_body(with_requester)
    assert list(admin_command_body(with_requester))[-1] == "requestedBySubject"


def test_the_admin_command_list_body_pages_like_the_client_list() -> None:
    items, page = admin_command_list_body(admin_command_page(full=True), limit=2)
    assert [item.get("requestedBySubject") for item in items] == ["admin-staff", None]
    assert page == Page(limit=2, next_cursor="YwAAAAAAAAAG")
    empty, last = admin_command_list_body(admin_command_page(full=False), limit=20)
    assert (empty, last) == ([], Page(limit=20, next_cursor=None))


def test_deployment_body_uses_camel_case_names_and_online() -> None:
    assert deployment_body(deployment_view(full=True)) == {
        "projectId": str(PROJECT),
        "liveReleaseId": str(RELEASE),
        "lastCommandNumber": 7,
        "routingStatus": "uncertain",
        "hostingStatus": "online",
        "inFlightCommand": {
            "commandId": str(COMMAND),
            "kind": "promote",
            "status": "running",
        },
        "updatedAt": "2026-10-08T12:00:05Z",
    }
    bare = deployment_body(deployment_view(full=False))
    assert bare["hostingStatus"] == "offline"
    assert "liveReleaseId" not in bare
    assert "inFlightCommand" not in bare
    assert bare["lastCommandNumber"] == 0


@pytest.mark.parametrize("status", list(HostingStatus))
def test_every_hosting_status_goes_out_as_its_own_value(status: HostingStatus) -> None:
    body = deployment_body(replace(deployment_view(full=True), hosting_status=status))
    assert body["hostingStatus"] == status.value
    assert body["hostingStatus"] in ("online", "offline")


def test_accepted_body_carries_a_relative_status_url() -> None:
    body = accepted_body(
        CommandAccepted(
            command_id=COMMAND, project_id=PROJECT, status=CommandStatus.ACCEPTED, replayed=True
        )
    )
    assert body == {
        "commandId": str(COMMAND),
        "status": "accepted",
        "statusUrl": f"/v1/projects/{PROJECT}/commands/{COMMAND}",
    }


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "2026-01-02T03:04:05Z"),
        (datetime(2026, 1, 2, 3, 4, 5, 120000, tzinfo=UTC), "2026-01-02T03:04:05.12Z"),
        (datetime(2026, 1, 2, 3, 4, 5, 1, tzinfo=UTC), "2026-01-02T03:04:05.000001Z"),
        (
            datetime(2026, 1, 2, 5, 4, 5, tzinfo=timezone(timedelta(hours=2))),
            "2026-01-02T03:04:05Z",
        ),
    ],
)
def test_timestamps_are_rfc3339_in_utc_with_the_stored_fraction(value: datetime, text: str) -> None:
    assert timestamp(value) == text


def test_repository_id_replaces_github_repository_id() -> None:
    body = release_body(release_view(full=True))
    assert body["repositoryId"] == 4242
    assert "github_repository_id" not in body


def _camel(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert "_" not in key, key
            _camel(item)
    elif isinstance(value, list):
        for item in value:
            _camel(item)


@pytest.mark.parametrize("full", [True, False])
def test_every_body_name_is_camel_case(full: bool) -> None:
    for body in (
        release_body(release_view(full=full)),
        command_body(command_view(full=full)),
        deployment_body(deployment_view(full=full)),
        diagnostics_body(diagnostics_view(full=full)),
    ):
        _camel(body)


NOW = datetime(2026, 10, 25, 1, 2, 3, 45678, tzinfo=UTC)


def test_envelope_of_an_object() -> None:
    assert envelope(
        {"a": 1},
        request_id="r-1",
        trace_id=None,
        path="/v1/x",
        status=200,
        page=None,
        now=NOW,
    ) == {
        "success": True,
        "data": {"a": 1},
        "meta": {
            "requestId": "r-1",
            "timestamp": "2026-10-25T01:02:03.045Z",
            "path": "/v1/x",
            "status": 200,
            "page": None,
        },
    }


def test_envelope_of_a_list_with_a_trace() -> None:
    body = envelope(
        [{"a": 1}],
        request_id="r-1",
        trace_id="0" * 32,
        path="/v1/x",
        status=200,
        page=Page(limit=20, next_cursor=None),
        now=NOW,
    )
    assert list(body["meta"]) == ["requestId", "traceId", "timestamp", "path", "status", "page"]
    assert body["meta"]["page"] == {"limit": 20, "nextCursor": None}
    assert body["data"] == [{"a": 1}]
    page = Page(limit=5, next_cursor="Yw")
    assert list_result([{"a": 1}], page) == {
        "items": [{"a": 1}],
        "page": {"limit": 5, "nextCursor": "Yw"},
    }


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "2026-01-02T03:04:05.000Z"),
        (datetime(2026, 1, 2, 3, 4, 5, 999999, tzinfo=UTC), "2026-01-02T03:04:05.999Z"),
        (
            datetime(2026, 1, 2, 5, 4, 5, 1000, tzinfo=timezone(timedelta(hours=2))),
            "2026-01-02T03:04:05.001Z",
        ),
    ],
)
def test_meta_timestamps_are_utc_milliseconds(value: datetime, text: str) -> None:
    assert meta_timestamp(value) == text
