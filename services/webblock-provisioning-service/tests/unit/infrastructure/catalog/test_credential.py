"""The site role name and the runtime credential (Notion 6 · Shared catalog; decision 0014
section 14)."""

import re
from uuid import UUID

import pytest

from provisioning.infrastructure.catalog import (
    InvalidRuntimeCredential,
    InvalidSiteRoleName,
    RuntimeCredential,
    check_site_role_name,
    new_runtime_credential,
    site_role_name,
)

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")


def test_the_role_name_is_site_and_the_project_id_without_hyphens() -> None:
    assert site_role_name(PROJECT) == "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11"


@pytest.mark.parametrize(
    "name",
    [
        "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
        "site_" + "0" * 32,
        "site_" + "f" * 32,
    ],
)
def test_a_well_formed_role_name_is_accepted(name: str) -> None:
    assert check_site_role_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "postgres",
        "catalog_site",
        "site_",
        "site_0B7C2F435D5E4F1E9A430E1D5C1C7A11",
        "site_0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11",
        "site_0b7c2f435d5e4f1e9a430e1d5c1c7a1",
        "site_0b7c2f435d5e4f1e9a430e1d5c1c7a111",
        "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11\n",
        " site_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
        'site_0b7c2f435d5e4f1e9a430e1d5c1c7a1"',
        "site_0b7c2f435d5e4f1e9a430e1d5c1c7a1;",
        "site_" + chr(0x661) * 32,  # Arabic-Indic digits: \d would match them, [0-9] does not
        "SITE_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
    ],
)
def test_any_other_role_name_is_refused(name: str) -> None:
    with pytest.raises(InvalidSiteRoleName):
        check_site_role_name(name)


def test_a_new_credential_is_the_projects_role_with_a_strong_random_password() -> None:
    passwords = set()
    for _ in range(50):
        credential = new_runtime_credential(PROJECT)
        assert credential.project_id == PROJECT
        assert credential.role == site_role_name(PROJECT)
        password = credential.reveal_password()
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", password)
        passwords.add(password)
    assert len(passwords) == 50


def test_a_credential_never_shows_its_password() -> None:
    credential = RuntimeCredential(PROJECT, site_role_name(PROJECT), "CANARY-password-1234567890")

    for text in (repr(credential), str(credential), f"{credential}", f"{credential!r}"):
        assert "CANARY" not in text
    assert credential.reveal_password() == "CANARY-password-1234567890"


def test_a_credential_for_another_projects_role_is_refused() -> None:
    with pytest.raises(InvalidSiteRoleName):
        RuntimeCredential(PROJECT, "site_" + "0" * 32, "x" * 43)


@pytest.mark.parametrize("password", ["", "pässword-CANARY-password", "CANARY\npassword-password"])
def test_a_password_the_verifier_would_refuse_is_refused_without_the_password(
    password: str,
) -> None:
    with pytest.raises(InvalidRuntimeCredential) as caught:
        RuntimeCredential(PROJECT, site_role_name(PROJECT), password)

    assert "CANARY" not in str(caught.value)
