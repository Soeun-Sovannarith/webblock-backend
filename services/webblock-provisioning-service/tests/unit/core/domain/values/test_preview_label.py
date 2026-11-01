"""Owner ruling PREVIEW1: a project's preview host is ``<subdomain>-preview.<site domain>``, one
DNS label under the site domain. So a sub-domain the platform hosts has at most 55 characters and
never ends in ``-preview``: that site host would be another project's preview host."""

import re

import pytest

from provisioning.core.domain import MAX_HOSTED_SUBDOMAIN_LEN, preview_label, subdomain_refusal

_ONE_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def test_fr20_1_the_preview_label_is_the_subdomain_and_dash_preview() -> None:
    """FR-20.1: ``shop`` previews on ``shop-preview``."""
    assert preview_label("shop") == "shop-preview"


def test_fr20_a_55_character_subdomain_gives_a_one_label_preview_host() -> None:
    """FR-20: 55 + 8 = 63 characters, the longest DNS label; 56 would not be a label."""
    assert MAX_HOSTED_SUBDOMAIN_LEN == 55
    longest = preview_label("a" * 54 + "z")
    assert len(longest) == 63
    assert _ONE_DNS_LABEL.fullmatch(longest)
    assert not _ONE_DNS_LABEL.fullmatch(preview_label("a" * 56))


@pytest.mark.parametrize(
    "subdomain", ["preview", "preview-shop", "shop-previews", "shop", "a" * 55]
)
def test_fr55_1_these_subdomains_are_hosted(subdomain: str) -> None:
    """FR-55.1: ``preview`` alone, ``preview-`` first and ``-previews`` last are allowed."""
    assert subdomain_refusal(subdomain) is None


@pytest.mark.parametrize("subdomain", ["shop-preview", "a-preview", "x" * 47 + "-preview"])
def test_fr55_2_a_subdomain_ending_in_dash_preview_is_refused(subdomain: str) -> None:
    """FR-55.2: ``shop-preview`` is ``shop``'s preview label, so it is never a site."""
    assert subdomain_refusal(subdomain) == "preview_suffix"


def test_fr55_2_the_refused_form_is_exactly_another_projects_preview_label() -> None:
    """FR-55.2: the refusal closes the one collision of a site host and a preview host."""
    assert subdomain_refusal(preview_label("shop")) is not None


@pytest.mark.parametrize("length", range(56, 64))
def test_fr55_4_a_subdomain_of_56_to_63_characters_is_refused(length: int) -> None:
    """FR-55.4: the database allows 63 characters; the preview label of 56 or more is no label."""
    assert subdomain_refusal("a" * length) == "subdomain_too_long"
