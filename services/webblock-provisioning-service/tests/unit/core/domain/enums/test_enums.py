"""The domain enums (revision 19 names: routing and hosting status, ``online`` instead of
``serving``)."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

import pytest

from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    HostingStatus,
    OfflineReason,
    ReleaseStatus,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
    UnknownEnumValue,
    parse_command_kind,
    parse_command_status,
    parse_hosting_status,
    parse_offline_reason,
    parse_release_status,
    parse_release_step_name,
    parse_release_step_status,
    parse_routing_status,
)

# Expected values, written by hand from decision 0001 section 1 (revision 19 labels).
_VALUES: list[tuple[type[StrEnum], list[str]]] = [
    (
        ReleaseStatus,
        [
            "accepted",
            "building",
            "built",
            "candidate_ready",
            "promoting",
            "live",
            "superseded",
            "failed",
            "rolled_back",
        ],
    ),
    (
        ReleaseStepName,
        ["build", "candidate", "promotion", "rollback", "access_setup", "observation"],
    ),
    (ReleaseStepStatus, ["pending", "running", "succeeded", "failed"]),
    (RoutingStatus, ["stable", "changing", "uncertain"]),
    (HostingStatus, ["online", "offline"]),
    (
        CommandKind,
        # Notion "5 · Provisioning" lists redeploy before sync_domains; that list is the enum order
        # (decision 0021, lead ruling on Task 8).
        [
            "deploy",
            "promote",
            "rollback",
            "suspend_hosting",
            "resume_hosting",
            "redeploy",
            "sync_domains",
        ],
    ),
    (CommandStatus, ["accepted", "running", "succeeded", "failed", "skipped"]),
    (OfflineReason, ["billing", "user_suspended", "project_deleted", "admin_suspended"]),
]
_VALUE_IDS = [
    "release status",
    "step name",
    "step status",
    "routing status",
    "hosting status",
    "command kind",
    "command status",
    "offline reason",
]

_PARSERS: list[tuple[str, type[StrEnum], Callable[[str], StrEnum]]] = [
    ("release status", ReleaseStatus, parse_release_status),
    ("step name", ReleaseStepName, parse_release_step_name),
    ("step status", ReleaseStepStatus, parse_release_step_status),
    ("routing status", RoutingStatus, parse_routing_status),
    ("hosting status", HostingStatus, parse_hosting_status),
    ("command kind", CommandKind, parse_command_kind),
    ("command status", CommandStatus, parse_command_status),
    ("offline reason", OfflineReason, parse_offline_reason),
]


@pytest.mark.parametrize(("enum", "want"), _VALUES, ids=_VALUE_IDS)
def test_enum_values_and_order(enum: type[StrEnum], want: list[str]) -> None:
    assert [str(v) for v in enum] == want
    assert [v.value for v in enum] == want


def test_enum_constant_labels() -> None:
    cases: list[tuple[str, str]] = [
        (ReleaseStatus.ACCEPTED, "accepted"),
        (ReleaseStatus.BUILDING, "building"),
        (RoutingStatus.STABLE, "stable"),
        (RoutingStatus.CHANGING, "changing"),
        (RoutingStatus.UNCERTAIN, "uncertain"),
        (HostingStatus.ONLINE, "online"),
        (HostingStatus.OFFLINE, "offline"),
        (CommandKind.SUSPEND_HOSTING, "suspend_hosting"),
        (CommandKind.RESUME_HOSTING, "resume_hosting"),
        (CommandStatus.SKIPPED, "skipped"),
    ]
    for got, want in cases:
        assert got == want
        assert str(got) == want


@pytest.mark.parametrize(("name", "enum", "parse"), _PARSERS, ids=[name for name, _, _ in _PARSERS])
def test_enum_parsers(name: str, enum: type[StrEnum], parse: Callable[[str], StrEnum]) -> None:
    for v in enum:
        got = parse(str(v))
        assert got == v, f"{name}: parse({v!r}) = {got!r}"
        assert type(got) is enum


# Includes the labels of the earlier models, which must no longer parse anywhere; "serving" is the
# hosting label before revision 19.
_UNKNOWN = [
    "",
    "LIVE",
    "Live",
    " live",
    "unknown",
    "live\n",
    "Stable",
    "OFFLINE",
    "frozen",
    "generating",
    "present",
    "absent",
    "missing",
    "ready",
    "serving",
]


@pytest.mark.parametrize("text", _UNKNOWN, ids=[repr(t) for t in _UNKNOWN])
@pytest.mark.parametrize("parse", [p for _, _, p in _PARSERS], ids=[n for n, _, _ in _PARSERS])
def test_enum_parsers_reject_unknown(parse: Callable[[str], StrEnum], text: str) -> None:
    with pytest.raises(UnknownEnumValue):
        parse(text)


def test_enum_parser_errors_name_the_enum_not_the_input() -> None:
    with pytest.raises(UnknownEnumValue) as routing:
        parse_routing_status("secret-input")
    assert "secret-input" not in str(routing.value)
    assert "routing" in str(routing.value)
    with pytest.raises(UnknownEnumValue) as hosting:
        parse_hosting_status("secret-input")
    assert "secret-input" not in str(hosting.value)
    assert "hosting" in str(hosting.value)


def test_unfinished() -> None:
    want_release = {
        ReleaseStatus.ACCEPTED: True,
        ReleaseStatus.BUILDING: True,
        ReleaseStatus.BUILT: True,
        ReleaseStatus.CANDIDATE_READY: True,
        ReleaseStatus.PROMOTING: True,
        ReleaseStatus.LIVE: False,
        ReleaseStatus.SUPERSEDED: False,
        ReleaseStatus.FAILED: False,
        ReleaseStatus.ROLLED_BACK: False,
    }
    assert set(want_release) == set(ReleaseStatus), "table does not cover every release status"
    for s, want in want_release.items():
        assert s.unfinished() is want, f"ReleaseStatus({s}).unfinished()"
    want_cmd = {
        CommandStatus.ACCEPTED: True,
        CommandStatus.RUNNING: True,
        CommandStatus.SUCCEEDED: False,
        CommandStatus.FAILED: False,
        CommandStatus.SKIPPED: False,
    }
    assert set(want_cmd) == set(CommandStatus), "table does not cover every command status"
    for c, want in want_cmd.items():
        assert c.unfinished() is want, f"CommandStatus({c}).unfinished()"


def test_command_kind_is_hosting() -> None:
    want = {
        CommandKind.DEPLOY: False,
        CommandKind.PROMOTE: False,
        CommandKind.ROLLBACK: False,
        CommandKind.SUSPEND_HOSTING: True,
        CommandKind.RESUME_HOSTING: True,
        CommandKind.REDEPLOY: False,
        CommandKind.SYNC_DOMAINS: False,
    }
    assert set(want) == set(CommandKind), "table does not cover every command kind"
    for k, w in want.items():
        assert k.is_hosting() is w, f"CommandKind({k}).is_hosting()"


def test_command_kind_names_no_release() -> None:
    """The hosting kinds and sync_domains name no release (decision 0014 section 16)."""
    want = {
        CommandKind.DEPLOY: False,
        CommandKind.PROMOTE: False,
        CommandKind.ROLLBACK: False,
        CommandKind.SUSPEND_HOSTING: True,
        CommandKind.RESUME_HOSTING: True,
        CommandKind.REDEPLOY: False,
        CommandKind.SYNC_DOMAINS: True,
    }
    assert set(want) == set(CommandKind), "table does not cover every command kind"
    for k, w in want.items():
        assert k.names_no_release() is w, f"CommandKind({k}).names_no_release()"


def test_redeploy_names_a_release_and_is_no_hosting_kind() -> None:
    assert not CommandKind.REDEPLOY.names_no_release()
    assert not CommandKind.REDEPLOY.is_hosting()
