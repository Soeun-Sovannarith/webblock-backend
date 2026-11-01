"""The route admission policy ``webblock-site-route-shape`` as written, without a cluster (decision
0023 G18): a route's host is ``<subdomain>.<site domain>``, with the sub-domain from the tenant
namespace's label that the bootstrap writes, never from the project ID. ``tests/integration/
kubernetes/test_dry_run_live.py`` proves the refusals live (marker ``kind``). The preview host is
``<subdomain>-preview.<site domain>`` (owner ruling PREVIEW1)."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

from provisioning.infrastructure.kubernetes.render import LABEL_SUBDOMAIN

REPO = Path(__file__).resolve().parents[3]
POLICY_FILE = REPO / "deploy" / "prototype" / "policy" / "site-route-shape.yaml"


def policy() -> dict[str, Any]:
    with POLICY_FILE.open() as f:
        docs = [d for d in yaml.safe_load_all(f) if d is not None]
    found: dict[str, Any] = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicy")
    return found


def variables() -> dict[str, str]:
    return {v["name"]: v["expression"] for v in policy()["spec"]["variables"]}


def test_the_subdomain_comes_from_the_namespace_label_bootstrap_renders() -> None:
    expression = variables()["subdomain"]
    assert "namespaceObject.metadata.labels" in expression
    assert f"'{LABEL_SUBDOMAIN}'" in expression


def test_both_hosts_are_built_from_the_subdomain_not_the_project() -> None:
    found = variables()
    assert found["platformHost"] == "variables.subdomain + '.' + variables.siteDomain"
    assert "variables.subdomain + '-preview.' + variables.siteDomain" in found["allowedHost"]
    assert "variables.project" not in found["platformHost"]
    assert "variables.project" not in found["allowedHost"]


def test_a_namespace_without_the_label_admits_no_attached_route() -> None:
    expressions = [v["expression"] for v in policy()["spec"]["validations"]]
    assert "size(variables.parents) == 0 || variables.subdomain != ''" in expressions
    assert "variables.hostnames.all(h, h == variables.allowedHost)" in expressions


def test_fr20_the_preview_host_is_the_subdomain_dash_preview_and_the_old_forms_are_gone() -> None:
    """FR-20 (PREVIEW1): the preview route admits only ``<subdomain>-preview.<siteDomain>``;
    the older ``<subdomain>.preview.<siteDomain>`` and ``preview-<subdomain>`` hosts are refused,
    since every attached route's hostnames must equal ``allowedHost`` (and a sub-domain label has
    no dot)."""
    allowed = variables()["allowedHost"]
    assert allowed.split()[0:3] == ["variables.isPreview", "?", "variables.subdomain"]
    assert "'.preview.'" not in allowed
    assert "'preview-' + variables.subdomain" not in allowed
    assert "'preview-'" not in allowed
    assert "variables.hostnames.all(h, h == variables.allowedHost)" in validations()


def _covers(wildcard: str, host: str) -> bool:
    """RFC 6125 section 6.4.3 as TLS clients apply it: ``*`` stands for exactly one whole label."""
    star, _, suffix = wildcard.partition(".")
    label, _, rest = host.partition(".")
    return star == "*" and bool(label) and rest == suffix


def certificate_names() -> list[str]:
    path = REPO / "deploy" / "prototype" / "gateway" / "10-wildcard-certificate.yaml"
    with path.open() as f:
        certificate: dict[str, Any] = yaml.safe_load(f)
    names: list[str] = certificate["spec"]["dnsNames"]
    return names


def test_fr20_the_one_wildcard_certificate_covers_the_preview_host() -> None:
    """FR-20 (PREVIEW1; TR-45): the preview host is one label under the site domain, so the
    Gateway's one wildcard ``*.<site domain>`` covers it, as the free Cloudflare certificate covers
    ``*.roaa15.xyz``; the certificate names nothing else, so the older two-label preview host has
    no certificate."""
    names = certificate_names()
    assert names == ["*.sites.webblock.test"]
    assert any(_covers(n, "example-shop-preview.sites.webblock.test") for n in names)
    assert any(_covers(n, "example-shop.sites.webblock.test") for n in names)
    assert not any(_covers(n, "example-shop.preview.sites.webblock.test") for n in names)


def validations() -> list[str]:
    return [v["expression"] for v in policy()["spec"]["validations"]]


def test_s1_only_the_preview_route_may_remove_exactly_the_preview_header() -> None:
    """Security review S1 (decision 0023 G18c): the preview rule removes ``X-Webblock-Preview``
    before the site with a ``RequestHeaderModifier``; the policy admits exactly that one filter,
    only on the preview route, with nothing set or added."""
    (filters,) = [e for e in validations() if "r.filters.all(" in e]
    assert "variables.isPreview && f.type == 'RequestHeaderModifier'" in filters
    assert "!has(f.requestHeaderModifier.set)" in filters
    assert "!has(f.requestHeaderModifier.add)" in filters
    assert "f.requestHeaderModifier.remove == ['X-Webblock-Preview']" in filters
    # The two filters of before keep their own branch, without RequestHeaderModifier, and only
    # on the site and redirect routes (security review M1).
    assert "!variables.isPreview && f.type in ['ResponseHeaderModifier', 'RequestRedirect']" in filters


def test_fr52_1_a_preview_route_sets_only_its_release_header_and_no_store() -> None:
    """FR-52.1 (security review M1): on ``preview-<projectId>`` the one response filter sets
    exactly two headers, ``X-Webblock-Release`` and ``Cache-Control: private, no-store`` (the
    renderer's value), and adds or removes none; a preview route has no redirect."""
    (filters,) = [e for e in validations() if "r.filters.all(" in e]
    preview_branch = filters.split("(variables.isPreview && f.type == 'ResponseHeaderModifier'", 1)
    assert len(preview_branch) == 2
    branch = preview_branch[1]
    assert "!has(f.responseHeaderModifier.add)" in branch
    assert "!has(f.responseHeaderModifier.remove)" in branch
    assert "size(f.responseHeaderModifier.set) == 2" in branch
    assert "f.responseHeaderModifier.set.exists_one(h, h.name == 'X-Webblock-Release')" in branch
    assert (
        "f.responseHeaderModifier.set.exists_one(h, h.name == 'Cache-Control' &&"
        " h.value == 'private, no-store')"
    ) in " ".join(branch.split())


def test_fr52_1_every_preview_rule_must_carry_the_no_store_header() -> None:
    """FR-52.1 (security review M1): the policy requires, not only allows, the response filter
    on every rule of the preview route; the filter rule above makes it the no-store one. A site
    route is not touched by this rule."""
    (required,) = [
        e for e in validations() if "exists_one(f, f.type == 'ResponseHeaderModifier')" in e
    ]
    assert required.startswith("!variables.isPreview ||")
    assert "size(variables.rules) > 0" in required
    assert "has(r.filters)" in required
    messages = [v["message"] for v in policy()["spec"]["validations"]]
    assert any("Cache-Control: private, no-store" in m for m in messages)


def test_s4_every_preview_rule_matches_exactly_one_exact_preview_header() -> None:
    """Security review S4: on ``preview-<projectId>`` every rule has matches, and each match has
    exactly one header match, ``Exact`` on ``X-Webblock-Preview`` with a token-shaped value, so a
    preview route without the header (public) is refused."""
    (matches,) = [e for e in validations() if "r.matches" in e]
    assert matches.startswith("!variables.isPreview ||")
    assert "size(variables.rules) > 0" in matches
    assert "has(r.matches) && size(r.matches) > 0" in matches
    assert "has(m.headers) && size(m.headers) == 1" in matches
    assert "(!has(h.type) || h.type == 'Exact')" in matches
    assert "h.name == 'X-Webblock-Preview'" in matches
    assert "h.value.matches('^[A-Za-z0-9_-]{32,256}$')" in matches


def test_l1_every_preview_rule_must_carry_exactly_one_header_removal() -> None:
    """Task 12 security re-review L1 (decision 0023 G18c): the policy requires, not only allows,
    the removal of ``X-Webblock-Preview`` on the preview route. Every rule has filters and
    exactly one ``RequestHeaderModifier``; the filter rule (S1) makes that one remove exactly the
    preview header. A site route is not touched by this rule."""
    (removal,) = [
        e for e in validations() if "exists_one(f, f.type == 'RequestHeaderModifier')" in e
    ]
    assert removal.startswith("!variables.isPreview ||")
    assert "size(variables.rules) > 0" in removal
    assert "has(r.filters)" in removal
    assert "r.filters.exists_one(f, f.type == 'RequestHeaderModifier')" in removal
    messages = [v["message"] for v in policy()["spec"]["validations"]]
    assert any("removes the X-Webblock-Preview header" in m for m in messages)
