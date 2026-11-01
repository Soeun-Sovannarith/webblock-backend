"""The bootstrap admission policy ``webblock-bootstrap-scope`` as written, without a cluster
(decision 0014 section 17 item 3; review finding M3). ``tests/integration/policy`` proves it live.

These checks hold the file to what the decision and the runbook promise: it matches exactly the
bootstrap identity named by the prototype RBAC, it denies (not warns or audits), every refusal has
a fixed message, and the names it allows are the ones the bootstrap renderer writes."""

import re
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

from provisioning.core.domain import MAX_HOSTED_SUBDOMAIN_LEN, PREVIEW_LABEL_SUFFIX
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_CATALOG_SECRET,
    CATALOG_SECRET_DELIVERED,
    CATALOG_SECRET_PENDING,
    LABEL_PROJECT_ID,
    LABEL_SUBDOMAIN,
    LABEL_TENANT,
    OFFLINE_NAMESPACE,
    OFFLINE_SERVICE,
    POD_SECURITY_LEVEL,
    RUNTIME_SECRET_NAME,
    WORKER_CLUSTER_ROLE,
    WORKER_SERVICE_ACCOUNT,
    WORKER_SERVICE_ACCOUNT_NAMESPACE,
    render_namespace,
    render_reference_grant,
    tenant_namespace,
)

REPO = Path(__file__).resolve().parents[3]
POLICY_FILE = REPO / "deploy" / "prototype" / "policy" / "bootstrap-scope.yaml"
RBAC = REPO / "deploy" / "prototype" / "rbac"
NAME = "webblock-bootstrap-scope"
PROJECT = UUID("0b6f3a52-4c1e-4f7a-9d2b-5e8c1a7f3d90")


def documents(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [d for d in yaml.safe_load_all(f) if d is not None]


def policy_and_binding() -> tuple[dict[str, Any], dict[str, Any]]:
    docs = documents(POLICY_FILE)
    kinds = sorted(d["kind"] for d in docs)
    assert kinds == ["ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"]
    policy = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicy")
    binding = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicyBinding")
    return policy, binding


def bootstrap_username() -> str:
    """The bootstrap ServiceAccount's username, from the RBAC files rather than from the policy."""
    accounts = [
        d
        for d in documents(RBAC / "10-identities.yaml")
        if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == "tenant-bootstrap"
    ]
    assert len(accounts) == 1
    namespace = accounts[0]["metadata"]["namespace"]
    bound = [
        s
        for d in documents(RBAC / "30-tenant-bootstrap.yaml")
        if d["kind"] == "ClusterRoleBinding"
        for s in d["subjects"]
    ]
    assert bound == [{"kind": "ServiceAccount", "name": "tenant-bootstrap", "namespace": namespace}]
    return f"system:serviceaccount:{namespace}:tenant-bootstrap"


def all_expressions(policy: dict[str, Any]) -> str:
    spec = policy["spec"]
    parts = [c["expression"] for c in spec["matchConditions"]]
    parts += [v["expression"] for v in spec["variables"]]
    parts += [v["expression"] for v in spec["validations"]]
    return "\n".join(parts)


def tenant_pattern(policy: dict[str, Any]) -> re.Pattern[str]:
    (variable,) = [v for v in policy["spec"]["variables"] if v["name"] == "tenantName"]
    literal = variable["expression"]
    # CEL's matches() finds the pattern anywhere (RE2), so the pattern must anchor both ends.
    assert literal.startswith("'^") and literal.endswith("$'")
    return re.compile(literal[1:-1])


def test_the_policy_matches_exactly_the_bootstrap_identity() -> None:
    policy, _ = policy_and_binding()
    conditions = policy["spec"]["matchConditions"]
    assert conditions == [
        {
            "name": "tenant-bootstrap-identity",
            "expression": f"request.userInfo.username == '{bootstrap_username()}'",
        }
    ]
    usernames = set(re.findall(r"system:serviceaccount:[^'\"\s]*", all_expressions(policy)))
    assert usernames == {bootstrap_username()}
    assert "userInfo.groups" not in all_expressions(policy)


def test_the_policy_covers_every_write_of_that_identity() -> None:
    policy, _ = policy_and_binding()
    spec = policy["spec"]
    assert policy["metadata"]["name"] == NAME
    assert spec["failurePolicy"] == "Fail"
    assert spec["matchConstraints"] == {
        "resourceRules": [
            {
                "apiGroups": ["*"],
                "apiVersions": ["*"],
                "operations": ["*"],
                "resources": ["*/*"],
                "scope": "*",
            }
        ]
    }
    assert "excludeResourceRules" not in spec["matchConstraints"]


def test_the_binding_denies_everywhere() -> None:
    _, binding = policy_and_binding()
    assert binding["metadata"]["name"] == NAME
    assert binding["spec"] == {"policyName": NAME, "validationActions": ["Deny"]}


def test_every_refusal_has_a_fixed_message() -> None:
    policy, _ = policy_and_binding()
    validations = policy["spec"]["validations"]
    assert len(validations) >= 9
    for v in validations:
        assert set(v) == {"expression", "reason", "message"}
        assert v["reason"] == "Forbidden"
        assert v["message"].startswith(f"{NAME}: ")
        assert len(v["message"]) <= 160
    assert "messageExpression" not in POLICY_FILE.read_text()


def test_the_allowed_names_are_the_ones_bootstrap_renders() -> None:
    policy, _ = policy_and_binding()
    expressions = all_expressions(policy)
    for name in (
        RUNTIME_SECRET_NAME,
        WORKER_CLUSTER_ROLE,
        WORKER_SERVICE_ACCOUNT,
        WORKER_SERVICE_ACCOUNT_NAMESPACE,
        OFFLINE_NAMESPACE,
        OFFLINE_SERVICE,
        LABEL_TENANT,
        LABEL_PROJECT_ID,
    ):
        assert f"'{name}'" in expressions, name
    assert f"'pod-security.kubernetes.io/enforce'] == '{POD_SECURITY_LEVEL}'" in expressions
    assert "== 'Opaque'" in expressions


def test_the_tenant_name_pattern_admits_rendered_names_only() -> None:
    pattern = tenant_pattern(policy_and_binding()[0])
    rendered = render_namespace(PROJECT, subdomain="shop", catalog_secret=None)["metadata"]["name"]
    assert rendered == tenant_namespace(PROJECT)
    assert pattern.search(rendered)
    assert pattern.search(render_reference_grant(PROJECT)["metadata"]["name"])
    for refused in (
        "wb-foo",
        "kube-system",
        "webblock",
        f"wb-{str(PROJECT).upper()}",
        f"wb-{PROJECT.hex}",
        f"x-wb-{PROJECT}",
        f"wb-{PROJECT}-x",
    ):
        assert not pattern.search(refused), refused


def test_the_catalog_secret_mark_is_the_one_bootstrap_renders() -> None:
    """Task F2B: both policies name the renderer's annotation, and this one its two values (the
    moves themselves are proven live in tests/integration/policy/test_catalog_secret_mark_live.py)."""
    expressions = all_expressions(policy_and_binding()[0])
    assert f"'{ANNOTATION_CATALOG_SECRET}'" in expressions
    assert f"variables.newMark == '{CATALOG_SECRET_PENDING}'" in expressions
    assert f"variables.newMark == '{CATALOG_SECRET_DELIVERED}'" in expressions
    writer = next(
        d
        for d in documents(POLICY_FILE.parent / "tenant-writer.yaml")
        if d["kind"] == "ValidatingAdmissionPolicy"
    )
    assert f"'{ANNOTATION_CATALOG_SECRET}'" in all_expressions_of(writer)


def all_expressions_of(policy: dict[str, Any]) -> str:
    spec = policy["spec"]
    return "\n".join(v["expression"] for v in spec["variables"] + spec["validations"])


def test_the_subdomain_label_is_the_one_bootstrap_renders_set_once() -> None:
    """Decision 0023 G18a: the policy names the renderer's label, admits a DNS label only, and
    allows it from none to a value but never a change (proven live in
    tests/integration/policy/test_bootstrap_scope_live.py)."""
    expressions = all_expressions(policy_and_binding()[0])
    assert f"'{LABEL_SUBDOMAIN}'" in expressions
    assert "variables.newSubdomain == variables.oldSubdomain" in expressions
    assert "variables.oldSubdomain == '<none>'" in expressions
    assert "matches('^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')" in expressions
    labels = render_namespace(PROJECT, subdomain="shop", catalog_secret=None)["metadata"]["labels"]
    assert labels[LABEL_SUBDOMAIN] == "shop"


def test_fr55_a_new_subdomain_label_never_ends_in_dash_preview_nor_passes_55_characters() -> None:
    """FR-55 (security review L2): the admission policy holds a new sub-domain label to the rule
    the bootstrap applies (``subdomain_refusal``), so a bootstrap bug cannot label a namespace
    whose site host is another project's preview host. A label left unchanged is not checked
    (proven live in tests/integration/policy/test_bootstrap_scope_live.py)."""
    (rule,) = [
        v["expression"]
        for v in policy_and_binding()[0]["spec"]["validations"]
        if "variables.newSubdomain == variables.oldSubdomain" in v["expression"]
    ]
    text = " ".join(rule.split())
    assert f"!variables.newSubdomain.endsWith('{PREVIEW_LABEL_SUFFIX}')" in text
    assert f"size(variables.newSubdomain) <= {MAX_HOSTED_SUBDOMAIN_LEN}" in text
    # Both sit inside the branch of a new label, after the unchanged-label branch.
    assert text.index("variables.newSubdomain == variables.oldSubdomain ||") < text.index(
        "endsWith("
    )


def test_s6_tenant_writer_names_the_subdomain_label() -> None:
    """Task 12 security review S6 (decision 0023 G18a): the route-shape policy trusts the label
    ``websites.webblock.internal/subdomain``, so ``webblock-tenant-writer`` names it, as it names
    the catalog-secret mark: only the bootstrap identity sets, changes or removes it, even if the
    general rule is widened (proven live in tests/integration/policy/
    test_catalog_secret_mark_live.py)."""
    writer = next(
        d
        for d in documents(POLICY_FILE.parent / "tenant-writer.yaml")
        if d["kind"] == "ValidatingAdmissionPolicy"
    )
    spec = writer["spec"]
    variables = {v["name"]: v["expression"] for v in spec["variables"]}
    assert variables["subdomainKey"] == f"'{LABEL_SUBDOMAIN}'"
    assert "object.metadata.labels[variables.subdomainKey]" in variables["newSubdomain"]
    assert "oldObject.metadata.labels[variables.subdomainKey]" in variables["oldSubdomain"]
    named = [
        v
        for v in spec["validations"]
        if "variables.newSubdomain == variables.oldSubdomain" in v["expression"]
    ]
    assert len(named) == 1
    (rule,) = named
    assert "variables.user == variables.bootstrap" in rule["expression"]
    assert "!variables.isNamespace" in rule["expression"]
    assert "sub-domain label" in rule["message"]
    # It comes before the general rule, so its refusal says what it protects.
    messages = [v["message"] for v in spec["validations"]]
    general = next(i for i, m in enumerate(messages) if "writes tenant namespaces and" in m)
    assert messages.index(rule["message"]) < general
