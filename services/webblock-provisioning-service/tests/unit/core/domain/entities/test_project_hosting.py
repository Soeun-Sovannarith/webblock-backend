"""The project_hosting row."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    NIL_ID,
    HostingStatus,
    IllegalTransition,
    InvalidCommandNumber,
    InvalidId,
    InvalidSubdomain,
    OfflineReason,
    ProjectHosting,
    RoutingStatus,
    UnknownEnumValue,
    legal_hosting_transition,
    legal_routing_transition,
    new_project_hosting,
    validate_subdomain,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_PREV = UUID("0190a1b2-0000-7000-8000-0000000000b1")

# Routing and hosting transitions, written by hand from decision 0001 section 3.
_LEGAL_ROUTING = {
    (RoutingStatus.STABLE, RoutingStatus.CHANGING),
    (RoutingStatus.CHANGING, RoutingStatus.STABLE),
    (RoutingStatus.CHANGING, RoutingStatus.UNCERTAIN),
    (RoutingStatus.UNCERTAIN, RoutingStatus.CHANGING),
}
_LEGAL_HOSTING = {
    (HostingStatus.ONLINE, HostingStatus.OFFLINE),
    (HostingStatus.OFFLINE, HostingStatus.ONLINE),
}


def _base_hosting() -> ProjectHosting:
    return ProjectHosting(
        project_id=_ID_PROJECT,
        last_command_number=3,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=_T0,
        subdomain="shop",
    )


def test_route_state_matrix() -> None:
    n = 0
    for from_ in RoutingStatus:
        for to in RoutingStatus:
            n += 1
            want = (from_, to) in _LEGAL_ROUTING
            assert legal_routing_transition(from_, to) is want, f"{from_} -> {to}"
    assert n == 9
    # Named explicitly: the two pairs decision 0001 section 3 forbids.
    assert not legal_routing_transition(RoutingStatus.STABLE, RoutingStatus.UNCERTAIN)
    assert not legal_routing_transition(RoutingStatus.UNCERTAIN, RoutingStatus.STABLE)
    bogus = cast(RoutingStatus, "bogus")
    assert not legal_routing_transition(bogus, RoutingStatus.STABLE)
    assert not legal_routing_transition(RoutingStatus.STABLE, bogus)


def test_hosting_state_matrix() -> None:
    n = 0
    for from_ in HostingStatus:
        for to in HostingStatus:
            n += 1
            want = (from_, to) in _LEGAL_HOSTING
            assert legal_hosting_transition(from_, to) is want, f"{from_} -> {to}"
    assert n == 4
    bogus = cast(HostingStatus, "bogus")
    assert not legal_hosting_transition(bogus, HostingStatus.ONLINE)
    assert not legal_hosting_transition(HostingStatus.ONLINE, bogus)


def test_new_project_state() -> None:
    p = new_project_hosting(_ID_PROJECT, _T0, subdomain="shop")
    assert p == ProjectHosting(
        project_id=_ID_PROJECT,
        last_command_number=0,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=_T0,
        live_release_id=None,
        subdomain="shop",
    )
    p.validate()
    with pytest.raises(InvalidId):
        new_project_hosting(NIL_ID, _T0, subdomain="shop")
    with pytest.raises(InvalidSubdomain):
        new_project_hosting(_ID_PROJECT, _T0, subdomain="Shop")


def test_project_state_validate_good() -> None:
    _base_hosting().validate()
    replace(
        _base_hosting(), live_release_id=_ID_PREV, last_command_number=MAX_COMMAND_NUMBER
    ).validate()


_VALIDATE_CASES: dict[str, tuple[ProjectHosting, type[Exception]]] = {
    "zero project": (replace(_base_hosting(), project_id=NIL_ID), InvalidId),
    "zero active": (replace(_base_hosting(), live_release_id=NIL_ID), InvalidId),
    "negative generation": (replace(_base_hosting(), last_command_number=-1), InvalidCommandNumber),
    "generation 2^53": (
        replace(_base_hosting(), last_command_number=MAX_COMMAND_NUMBER + 1),
        InvalidCommandNumber,
    ),
    "unknown route": (
        replace(_base_hosting(), routing_status=cast(RoutingStatus, "moving")),
        UnknownEnumValue,
    ),
    "empty route": (
        replace(_base_hosting(), routing_status=cast(RoutingStatus, "")),
        UnknownEnumValue,
    ),
    "unknown hosting": (
        replace(_base_hosting(), hosting_status=cast(HostingStatus, "suspended")),
        UnknownEnumValue,
    ),
    # Python only: the hosting label before revision 19 is no longer a label.
    "serving hosting": (
        replace(_base_hosting(), hosting_status=cast(HostingStatus, "serving")),
        UnknownEnumValue,
    ),
}


@pytest.mark.parametrize(("row", "want"), list(_VALIDATE_CASES.values()), ids=list(_VALIDATE_CASES))
def test_project_state_validate(row: ProjectHosting, want: type[Exception]) -> None:
    with pytest.raises(want):
        row.validate()


def test_validate_refuses_offline_without_reasons() -> None:
    with pytest.raises(IllegalTransition):
        replace(_base_hosting(), hosting_status=HostingStatus.OFFLINE).validate()


def test_validate_refuses_online_with_a_reason() -> None:
    with pytest.raises(IllegalTransition):
        replace(_base_hosting(), offline_reasons=frozenset({OfflineReason.BILLING})).validate()


def test_validate_accepts_offline_with_reasons() -> None:
    replace(
        _base_hosting(),
        hosting_status=HostingStatus.OFFLINE,
        offline_reasons=frozenset({OfflineReason.BILLING, OfflineReason.USER_SUSPENDED}),
    ).validate()


def test_new_project_hosting_has_no_reason_and_version_zero() -> None:
    p = new_project_hosting(_ID_PROJECT, _T0, subdomain="shop")
    assert p.offline_reasons == frozenset()
    assert p.owner_status_version == 0


@pytest.mark.parametrize("version", [-1, 2**53])
def test_validate_refuses_an_owner_status_version_out_of_range(version: int) -> None:
    with pytest.raises(InvalidCommandNumber):
        replace(_base_hosting(), owner_status_version=version).validate()


def test_validate_accepts_the_owner_status_version_range() -> None:
    replace(_base_hosting(), owner_status_version=2**53 - 1).validate()


# --- the sub-domain (decision 0021; Notion: project_hosting.subdomain VARCHAR(63), a DNS label) ---


@pytest.mark.parametrize("value", ["a", "0", "shop-1", "a1-b2-c3", "a" * 63, "a" + "-" * 61 + "b"])
def test_validate_subdomain_accepts_a_dns_label(value: str) -> None:
    validate_subdomain(value)


@pytest.mark.parametrize(
    "value",
    ["", "a" * 64, "-a", "a-", "A", "a.b", "a_b", "a b", "shop\n", "\nshop", "ß", "a\x00"],
)
def test_validate_subdomain(value: str) -> None:
    with pytest.raises(InvalidSubdomain):
        validate_subdomain(value)


def test_validate_checks_a_loaded_subdomain() -> None:
    replace(_base_hosting(), subdomain="other").validate()
    for bad in ("-bad", None):
        with pytest.raises(InvalidSubdomain):
            replace(_base_hosting(), subdomain=cast(str, bad)).validate()


def test_the_subdomain_is_required() -> None:
    """Migration 00021 makes project_hosting.subdomain NOT NULL, so the row type needs it."""
    with pytest.raises(TypeError, match="subdomain"):
        ProjectHosting(  # type: ignore[call-arg]  # the missing argument is the point
            project_id=_ID_PROJECT,
            last_command_number=0,
            routing_status=RoutingStatus.STABLE,
            hosting_status=HostingStatus.ONLINE,
            updated_at=_T0,
        )
