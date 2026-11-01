"""The per-site and tenant manifests: golden files, the intent digest, and refused input
(decision 0013 section 3, decision 0009 section 3, review-k8s-nocp.md (a) 1).

The goldens under ``golden/`` are compared byte for byte with ``yaml.safe_dump`` of the rendered
object. To regenerate them after an intended change, run the tests with
``WEBBLOCK_UPDATE_GOLDEN=1``; the run then fails once, naming the files it wrote.
"""

import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

from provisioning.core.application.ports.site_cluster import (
    OFFLINE_HEADER_VALUE,
    RELEASE_HEADER,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
)
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_COMMAND_NUMBER,
    ANNOTATION_INTENT_DIGEST,
    ANNOTATION_RESTART_COMMAND,
    INTENT_VERSION,
    LABEL_PROJECT_ID,
    LABEL_SUBDOMAIN,
    RUNTIME_SECRET_NAME,
    InvalidManifestInput,
    SiteSettings,
    TenantSettings,
    preview_token_for,
    release_intent_digest,
    render_deployment,
    render_limit_range,
    render_namespace,
    render_preview_route,
    render_reference_grant,
    render_resource_quota,
    render_route,
    render_runtime_secret,
    render_service,
    render_worker_role_binding,
)

GOLDEN = Path(__file__).parent / "golden"
PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
RELEASE = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")
DIGEST = "sha256:" + "ab" * 32
IMAGE = f"registry.webblock.test:5000/webblock/sites/{PROJECT}@{DIGEST}"
SETTINGS = SiteSettings(
    cpu_millicores=100,
    memory_mib=128,
    progress_deadline_seconds=60,
    readiness_deadline_seconds=120,
    offline_service_port=80,
)
TENANT = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)
WORKLOAD = ReleaseWorkload(project_id=PROJECT, release_id=RELEASE, image=IMAGE, command_number=7)
SITE_DOMAIN = "sites.webblock.test"
TOKEN = "preview-token-for-unit-tests-0123456789"  # noqa: S105 - a test value
# The site's DNS label, from project_hosting.subdomain (decision 0023 G18).
SUBDOMAIN = "golden-shop"
HOSTS = (f"{SUBDOMAIN}.sites.webblock.test",)
PREVIEW_HOST = f"{SUBDOMAIN}-preview.sites.webblock.test"
# The keywords that tie a preview route to its project's own preview host (review S3).
PREVIEW_OF = {"site_domain": SITE_DOMAIN, "subdomain": SUBDOMAIN}
SECRET_VALUES = {
    "PGHOST": "webblock-catalog-db",
    "PGPORT": "5432",
    "PGDATABASE": "catalog",
    "PGUSER": "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
    "PGPASSWORD": "golden-test-password-not-a-credential",
    "PGSSLMODE": "disable",
}


def check_golden(name: str, obj: dict[str, Any]) -> None:
    text = yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)
    path = GOLDEN / name
    if os.environ.get("WEBBLOCK_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        pytest.fail(f"golden {name} written; rerun without WEBBLOCK_UPDATE_GOLDEN")
    assert path.read_text() == text


@pytest.mark.parametrize(
    ("name", "rendered"),
    [
        ("deployment.yaml", lambda: render_deployment(WORKLOAD, SETTINGS)),
        ("service.yaml", lambda: render_service(WORKLOAD)),
        (
            "route-release.yaml",
            lambda: render_route(
                PROJECT,
                RouteTarget(RouteTargetKind.RELEASE, RELEASE),
                HOSTS,
                7,
                SETTINGS,
                site_domain=SITE_DOMAIN,
                subdomain=SUBDOMAIN,
            ),
        ),
        (
            "route-offline.yaml",
            lambda: render_route(
                PROJECT,
                RouteTarget(RouteTargetKind.OFFLINE),
                HOSTS,
                8,
                SETTINGS,
                site_domain=SITE_DOMAIN,
                subdomain=SUBDOMAIN,
            ),
        ),
        (
            "route-preview.yaml",
            lambda: render_preview_route(
                PROJECT, RELEASE, PREVIEW_HOST, 7, preview_secret=TOKEN, **PREVIEW_OF
            ),
        ),
        (
            "namespace.yaml",
            lambda: render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None),
        ),
        ("resourcequota.yaml", lambda: render_resource_quota(PROJECT, TENANT)),
        ("limitrange.yaml", lambda: render_limit_range(PROJECT, TENANT)),
        ("referencegrant.yaml", lambda: render_reference_grant(PROJECT)),
        ("rolebinding.yaml", lambda: render_worker_role_binding(PROJECT)),
        ("secret.yaml", lambda: render_runtime_secret(PROJECT, SECRET_VALUES)),
    ],
)
def test_rendered_manifests_match_the_goldens(name: str, rendered: Any) -> None:
    check_golden(name, rendered())


def test_the_intent_digest_is_the_sha256_of_the_versioned_intent_text() -> None:
    text = (
        "webblock.release-intent.v4\n"
        f"projectId={PROJECT}\n"
        f"releaseId={RELEASE}\n"
        f"image={IMAGE}\n"
        "runtimeAccessRef.name=catalog-runtime\n"
        "resources.cpuMillicores=100\n"
        "resources.memoryMiB=128\n"
        "readinessProbe.httpGet.path=/healthz\n"
        "readinessProbe.httpGet.port=http\n"
    )
    expected = "sha256:" + hashlib.sha256(text.encode()).hexdigest()

    assert release_intent_digest(PROJECT, RELEASE, IMAGE, SETTINGS) == expected


def test_the_digest_changes_with_every_intent_field_and_not_with_the_command_number() -> None:
    base = release_intent_digest(PROJECT, RELEASE, IMAGE, SETTINGS)
    other_image = IMAGE.replace("ab" * 32, "cd" * 32)
    more_cpu = SiteSettings(
        cpu_millicores=200,
        memory_mib=128,
        progress_deadline_seconds=60,
        readiness_deadline_seconds=120,
        offline_service_port=80,
    )
    assert release_intent_digest(PROJECT, RELEASE, other_image, SETTINGS) != base
    assert release_intent_digest(PROJECT, RELEASE, IMAGE, more_cpu) != base
    later = ReleaseWorkload(PROJECT, RELEASE, IMAGE, command_number=8)
    template = render_deployment(WORKLOAD, SETTINGS)["spec"]["template"]
    assert render_deployment(later, SETTINGS)["spec"]["template"] == template


def test_the_readiness_probe_is_an_http_get_of_healthz_at_the_site_port() -> None:
    """Decision 0014 section 21 ruling 12: Kubernetes asks /healthz over HTTP (section 15: 200
    while the process is up), never a bare TCP connect, which a hung server still accepts."""
    container = render_deployment(WORKLOAD, SETTINGS)["spec"]["template"]["spec"]["containers"][0]

    assert container["readinessProbe"] == {
        "httpGet": {"path": "/healthz", "port": "http", "scheme": "HTTP"},
        "periodSeconds": 5,
    }
    assert container["ports"][0] == {"name": "http", "containerPort": 8080, "protocol": "TCP"}
    assert "tcpSocket" not in str(container)


def test_the_intent_digest_covers_the_readiness_probe() -> None:
    """The digest text names the probe the template carries (version 4), so the digest changes
    when the probe does."""
    probe = render_deployment(WORKLOAD, SETTINGS)["spec"]["template"]["spec"]["containers"][0][
        "readinessProbe"
    ]["httpGet"]
    text = (
        "webblock.release-intent.v4\n"
        f"projectId={PROJECT}\n"
        f"releaseId={RELEASE}\n"
        f"image={IMAGE}\n"
        "runtimeAccessRef.name=catalog-runtime\n"
        "resources.cpuMillicores=100\n"
        "resources.memoryMiB=128\n"
        f"readinessProbe.httpGet.path={probe['path']}\n"
        f"readinessProbe.httpGet.port={probe['port']}\n"
    )

    assert INTENT_VERSION == "webblock.release-intent.v4"
    assert release_intent_digest(PROJECT, RELEASE, IMAGE, SETTINGS) == (
        "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    )


def test_the_command_number_is_on_metadata_only_and_the_digest_on_the_pod_template() -> None:
    deployment = render_deployment(WORKLOAD, SETTINGS)
    template = deployment["spec"]["template"]

    assert deployment["metadata"]["annotations"] == {ANNOTATION_COMMAND_NUMBER: "7"}
    assert template["metadata"]["annotations"] == {
        ANNOTATION_INTENT_DIGEST: release_intent_digest(PROJECT, RELEASE, IMAGE, SETTINGS)
    }
    assert ANNOTATION_COMMAND_NUMBER not in str(template)
    assert deployment["spec"]["progressDeadlineSeconds"] == 60


def test_render_without_restart_is_unchanged() -> None:
    """Decision 0023 G12: without a restart command the Deployment is byte for byte what it was
    (the golden file), and passing None is the same as passing nothing."""
    check_golden("deployment.yaml", render_deployment(WORKLOAD, SETTINGS, restart_command=None))
    assert render_deployment(WORKLOAD, SETTINGS, restart_command=None) == render_deployment(
        WORKLOAD, SETTINGS
    )
    assert ANNOTATION_RESTART_COMMAND not in str(render_deployment(WORKLOAD, SETTINGS))


def test_a_restart_adds_only_the_restart_annotation_to_the_pod_template() -> None:
    """A redeploy (decision 0023 G12) sets ``restart-command`` on the pod template to its command
    number, which starts a rolling restart; nothing else of the Deployment changes."""
    plain = render_deployment(WORKLOAD, SETTINGS)
    restarted = render_deployment(WORKLOAD, SETTINGS, restart_command=9)

    assert ANNOTATION_RESTART_COMMAND == "websites.webblock.internal/restart-command"
    annotations = restarted["spec"]["template"]["metadata"]["annotations"]
    assert annotations == {
        ANNOTATION_INTENT_DIGEST: release_intent_digest(PROJECT, RELEASE, IMAGE, SETTINGS),
        ANNOTATION_RESTART_COMMAND: "9",
    }
    del annotations[ANNOTATION_RESTART_COMMAND]
    assert restarted == plain


@pytest.mark.parametrize("number", [0, -1, 2**53])
def test_a_restart_command_outside_one_to_two_to_the_53_minus_one_is_refused(number: int) -> None:
    with pytest.raises(InvalidManifestInput):
        render_deployment(WORKLOAD, SETTINGS, restart_command=number)


def test_the_site_container_takes_its_catalog_credential_from_the_runtime_secret() -> None:
    container = render_deployment(WORKLOAD, SETTINGS)["spec"]["template"]["spec"]["containers"][0]

    assert container["envFrom"] == [{"secretRef": {"name": "catalog-runtime"}}]
    assert RUNTIME_SECRET_NAME == "catalog-runtime"  # noqa: S105 - a name, not a credential
    assert "env" not in container


def test_the_runtime_secret_is_opaque_with_the_six_keys_base64_encoded() -> None:
    secret = render_runtime_secret(PROJECT, SECRET_VALUES)

    assert secret["type"] == "Opaque"
    assert secret["metadata"]["name"] == "catalog-runtime"
    assert secret["metadata"]["namespace"] == f"wb-{PROJECT}"
    assert "stringData" not in secret
    decoded = {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}
    assert decoded == SECRET_VALUES


@pytest.mark.parametrize(
    "change",
    [
        {"PGPASSWORD": None},
        {"PGSSLMODE": None},
        {"PGOPTIONS": "-c role=catalog_owner"},
        {"PGUSER": ""},
        {"PGHOST": 5432},
    ],
    ids=["no-password", "no-sslmode", "extra-key", "empty-value", "not-a-string"],
)
def test_a_runtime_secret_without_exactly_the_six_keys_as_text_is_refused(
    change: dict[str, object],
) -> None:
    values: dict[str, object] = dict(SECRET_VALUES)
    for key, value in change.items():
        if value is None:
            del values[key]
        else:
            values[key] = value

    with pytest.raises(InvalidManifestInput) as caught:
        render_runtime_secret(PROJECT, values)  # type: ignore[arg-type]  # deliberately wrong types

    assert "golden-test-password" not in str(caught.value)


@pytest.mark.parametrize(
    "image",
    [
        f"registry.webblock.test:5000/webblock/sites/{PROJECT}:latest",
        f"registry.webblock.test:5000/webblock/sites/{PROJECT}",
        f"registry.webblock.test:5000/webblock/sites/{RELEASE}@{DIGEST}",
        f"registry.webblock.test:5000/other/{PROJECT}@{DIGEST}",
        f"registry.webblock.test:5000/webblock/sites/{PROJECT}@sha256:abc",
        f"Registry.Test/webblock/sites/{PROJECT}@{DIGEST}",
        f"registry.webblock.test:5000/webblock/sites/{PROJECT}@{DIGEST}\n",
    ],
)
def test_an_image_that_is_not_this_projects_repository_by_digest_is_refused(image: str) -> None:
    with pytest.raises(InvalidManifestInput):
        render_deployment(ReleaseWorkload(PROJECT, RELEASE, image, 7), SETTINGS)


@pytest.mark.parametrize("number", [0, -1, 2**53])
def test_a_command_number_outside_one_to_two_to_the_53_minus_one_is_refused(number: int) -> None:
    with pytest.raises(InvalidManifestInput):
        render_deployment(ReleaseWorkload(PROJECT, RELEASE, IMAGE, number), SETTINGS)
    with pytest.raises(InvalidManifestInput):
        render_route(
            PROJECT,
            RouteTarget(RouteTargetKind.OFFLINE),
            HOSTS,
            number,
            SETTINGS,
            site_domain=SITE_DOMAIN,
            subdomain=SUBDOMAIN,
        )


@pytest.mark.parametrize(
    "hostnames",
    [
        (),
        ("*.sites.webblock.test",),
        ("Upper.sites.webblock.test",),
        ("a..b",),
        ("-a.sites.webblock.test",),
        ("a" * 64 + ".test",),
        tuple(f"h{i}.sites.webblock.test" for i in range(17)),
        ("a.sites.webblock.test", "a.sites.webblock.test"),
    ],
)
def test_route_hostnames_must_be_one_to_sixteen_distinct_dns_names_without_wildcards(
    hostnames: tuple[str, ...],
) -> None:
    with pytest.raises(InvalidManifestInput):
        render_route(
            PROJECT,
            RouteTarget(RouteTargetKind.OFFLINE),
            hostnames,
            7,
            SETTINGS,
            site_domain=SITE_DOMAIN,
            subdomain=SUBDOMAIN,
        )


@pytest.mark.parametrize(
    "target",
    [RouteTarget(RouteTargetKind.RELEASE), RouteTarget(RouteTargetKind.OFFLINE, RELEASE)],
)
def test_a_route_target_with_a_release_id_exactly_when_it_is_a_release(
    target: RouteTarget,
) -> None:
    with pytest.raises(InvalidManifestInput):
        render_route(
            PROJECT, target, HOSTS, 7, SETTINGS, site_domain=SITE_DOMAIN, subdomain=SUBDOMAIN
        )


@pytest.mark.parametrize(
    ("progress", "readiness"),
    [(120, 120), (121, 120), (0, 120)],
)
def test_the_progress_deadline_must_be_positive_and_below_the_readiness_deadline(
    progress: int, readiness: int
) -> None:
    with pytest.raises(ValueError, match="deadline"):
        SiteSettings(
            cpu_millicores=100,
            memory_mib=128,
            progress_deadline_seconds=progress,
            readiness_deadline_seconds=readiness,
            offline_service_port=80,
        )


@pytest.mark.parametrize(("cpu", "memory"), [(49, 128), (2001, 128), (100, 63), (100, 4097)])
def test_release_resources_outside_the_bounds_are_refused(cpu: int, memory: int) -> None:
    with pytest.raises(ValueError, match=r"cpu|memory"):
        SiteSettings(
            cpu_millicores=cpu,
            memory_mib=memory,
            progress_deadline_seconds=60,
            readiness_deadline_seconds=120,
            offline_service_port=80,
        )


# --- the release header (decision 0014 section 17) ----------------------------------------------


@pytest.mark.parametrize(
    ("route", "value"),
    [
        (
            lambda: render_route(
                PROJECT,
                RouteTarget(RouteTargetKind.RELEASE, RELEASE),
                HOSTS,
                7,
                SETTINGS,
                site_domain=SITE_DOMAIN,
                subdomain=SUBDOMAIN,
            ),
            str(RELEASE),
        ),
        (
            lambda: render_route(
                PROJECT,
                RouteTarget(RouteTargetKind.OFFLINE),
                HOSTS,
                7,
                SETTINGS,
                site_domain=SITE_DOMAIN,
                subdomain=SUBDOMAIN,
            ),
            OFFLINE_HEADER_VALUE,
        ),
        (
            lambda: render_preview_route(
                PROJECT, RELEASE, PREVIEW_HOST, 7, preview_secret=TOKEN, **PREVIEW_OF
            ),
            str(RELEASE),
        ),
    ],
    ids=["site-release", "site-offline", "preview"],
)
def test_every_rule_sets_the_release_header_to_its_backend(route: Any, value: str) -> None:
    """FR-52.2: only the preview rule has a second filter, which removes the probe's header
    before the site (review S1); FR-52.1: only the preview's answers are ``private, no-store``."""
    rendered = route()
    rules = rendered["spec"]["rules"]
    assert rules
    preview = rendered["metadata"]["name"].startswith("preview-")
    rest = [STRIP_PREVIEW] if preview else []
    headers = [{"name": RELEASE_HEADER, "value": value}]
    if preview:
        headers.append(NO_STORE)
    for rule in rules:
        assert rule["filters"] == [
            {"type": "ResponseHeaderModifier", "responseHeaderModifier": {"set": headers}},
            *rest,
        ]


def test_the_preview_route_is_fenced_on_the_gateway_with_its_one_hostname() -> None:
    """FR-20.1: the candidate's preview route on its one host ``<subdomain>-preview.<site
    domain>``, matched only with the project's token; FR-52.2: its rule removes
    ``X-Webblock-Preview`` before the site."""
    preview = render_preview_route(
        PROJECT, RELEASE, PREVIEW_HOST, 9, preview_secret=TOKEN, **PREVIEW_OF
    )
    site = render_route(
        PROJECT,
        RouteTarget(RouteTargetKind.RELEASE, RELEASE),
        HOSTS,
        9,
        SETTINGS,
        site_domain=SITE_DOMAIN,
        subdomain=SUBDOMAIN,
    )
    assert preview["metadata"]["name"] == f"preview-{PROJECT}"
    assert preview["metadata"]["namespace"] == f"wb-{PROJECT}"
    assert preview["metadata"]["annotations"] == {ANNOTATION_COMMAND_NUMBER: "9"}
    assert preview["spec"]["parentRefs"] == site["spec"]["parentRefs"]
    assert preview["spec"]["hostnames"] == [PREVIEW_HOST]
    # The same backend as the site's route and its release header, with no-store (security
    # review M1), then the removal of the probe's header (security review S1), matched only with
    # the project's own token (recheck N1, S1).
    (rule,) = preview["spec"]["rules"]
    (site_rule,) = site["spec"]["rules"]
    (site_headers,) = site_rule["filters"]
    assert rule["filters"] == [
        {
            "type": "ResponseHeaderModifier",
            "responseHeaderModifier": {
                "set": [*site_headers["responseHeaderModifier"]["set"], NO_STORE]
            },
        },
        STRIP_PREVIEW,
    ]
    assert rule["backendRefs"] == site_rule["backendRefs"]
    assert rule["matches"] == [
        {
            "path": {"type": "PathPrefix", "value": "/"},
            "headers": [
                {
                    "type": "Exact",
                    "name": "X-Webblock-Preview",
                    "value": preview_token_for(TOKEN, PROJECT),
                }
            ],
        }
    ]


STRIP_PREVIEW = {
    "type": "RequestHeaderModifier",
    "requestHeaderModifier": {"remove": ["X-Webblock-Preview"]},
}
NO_STORE = {"name": "Cache-Control", "value": "private, no-store"}


def test_fr52_1_only_the_preview_answers_private_no_store() -> None:
    """FR-52.1 (security review M1): a public preview passes through Cloudflare, which caches
    static files by host and path, not by the token header. Every answer of the preview route
    says ``Cache-Control: private, no-store``, so no shared cache serves the candidate to a
    visitor without the token. The site's own route keeps its caching."""
    preview = render_preview_route(
        PROJECT, RELEASE, PREVIEW_HOST, 7, preview_secret=TOKEN, **PREVIEW_OF
    )
    site = render_route(
        PROJECT,
        RouteTarget(RouteTargetKind.RELEASE, RELEASE),
        HOSTS,
        7,
        SETTINGS,
        site_domain=SITE_DOMAIN,
        subdomain=SUBDOMAIN,
    )
    (rule,) = preview["spec"]["rules"]
    responses = [f for f in rule["filters"] if f["type"] == "ResponseHeaderModifier"]
    assert len(responses) == 1  # the Gateway API allows one filter of a type per rule
    assert NO_STORE in responses[0]["responseHeaderModifier"]["set"]
    assert "Cache-Control" not in json.dumps(site)


OTHER_PROJECT = UUID("9c8b7a69-5847-4362-9150-0a1b2c3d4e5f")


def test_s1_the_preview_token_is_an_hmac_of_the_project_and_differs_per_project() -> None:
    """Security review S1 (decision 0023 G18c): the route and the probe carry
    ``base64url(HMAC-SHA256(secret, projectId))``, never the secret, so a tenant that reads the
    header learns only its own preview's token."""
    mine = preview_token_for(TOKEN, PROJECT)
    other = preview_token_for(TOKEN, OTHER_PROJECT)
    digest = hmac.new(TOKEN.encode(), str(PROJECT).encode(), hashlib.sha256).digest()
    assert mine == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert mine != other
    assert TOKEN not in mine and TOKEN not in other
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", mine)
    assert preview_token_for("another-secret-of-32-characters-xx", PROJECT) != mine


def test_s1_two_projects_preview_routes_match_different_values() -> None:
    """FR-52.1: another project's token does not reach this candidate: each preview route
    matches only its own project's token, never the worker's secret."""

    def value(project: UUID) -> str:
        route = render_preview_route(
            project, RELEASE, PREVIEW_HOST, 7, preview_secret=TOKEN, **PREVIEW_OF
        )
        header: str = route["spec"]["rules"][0]["matches"][0]["headers"][0]["value"]
        return header

    assert value(PROJECT) != value(OTHER_PROJECT)
    assert TOKEN not in json.dumps(
        render_preview_route(PROJECT, RELEASE, PREVIEW_HOST, 7, preview_secret=TOKEN, **PREVIEW_OF)
    )


@pytest.mark.parametrize("hostname", ["", "*.sites.webblock.test", "Preview.sites.webblock.test"])
def test_a_preview_hostname_must_be_a_dns_name(hostname: str) -> None:
    with pytest.raises(InvalidManifestInput):
        render_preview_route(PROJECT, RELEASE, hostname, 7, preview_secret=TOKEN, **PREVIEW_OF)


@pytest.mark.parametrize(
    ("hostname", "subdomain"),
    [
        (f"{SUBDOMAIN}.{SITE_DOMAIN}", SUBDOMAIN),
        (f"preview-{SUBDOMAIN}.{SITE_DOMAIN}", SUBDOMAIN),
        (f"{SUBDOMAIN}.preview.{SITE_DOMAIN}", SUBDOMAIN),
        (f"other-shop-preview.{SITE_DOMAIN}", SUBDOMAIN),
        (PREVIEW_HOST, "other-shop"),
        (f"{SUBDOMAIN}-preview.evil.test", SUBDOMAIN),
        (f"x.{SUBDOMAIN}-preview.{SITE_DOMAIN}", SUBDOMAIN),
    ],
    ids=[
        "site-host",
        "old-preview-dash-host",
        "old-dotted-preview-host",
        "another-projects-preview",
        "another-subdomain",
        "outside-the-site-domain",
        "one-label-too-many",
    ],
)
def test_fr20_s3_a_preview_route_names_only_its_projects_preview_host(
    hostname: str, subdomain: str
) -> None:
    """FR-20, security review S3: the renderer refuses any host but
    ``<subdomain>-preview.<site domain>``, the older ``<subdomain>.preview.<site domain>``
    included, without naming it."""
    with pytest.raises(InvalidManifestInput, match="preview hostname") as raised:
        render_preview_route(
            PROJECT,
            RELEASE,
            hostname,
            7,
            preview_secret=TOKEN,
            site_domain=SITE_DOMAIN,
            subdomain=subdomain,
        )
    assert hostname not in str(raised.value)


@pytest.mark.parametrize(
    ("site_domain", "subdomain"), [("Sites.test", SUBDOMAIN), (SITE_DOMAIN, "Shop")]
)
def test_s3_a_preview_route_checks_its_site_domain_and_subdomain(
    site_domain: str, subdomain: str
) -> None:
    with pytest.raises(InvalidManifestInput):
        render_preview_route(
            PROJECT,
            RELEASE,
            PREVIEW_HOST,
            7,
            preview_secret=TOKEN,
            site_domain=site_domain,
            subdomain=subdomain,
        )


def test_fr55_4_a_subdomain_over_55_characters_has_no_preview_route() -> None:
    """FR-55.4: ``<56 characters>-preview`` is no DNS label, so the renderer refuses the route; a
    55-character sub-domain's preview route renders."""
    ok = "a" * 55
    route = render_preview_route(
        PROJECT,
        RELEASE,
        f"{ok}-preview.{SITE_DOMAIN}",
        7,
        preview_secret=TOKEN,
        site_domain=SITE_DOMAIN,
        subdomain=ok,
    )
    assert route["spec"]["hostnames"] == [f"{ok}-preview.{SITE_DOMAIN}"]
    too_long = "a" * 56
    with pytest.raises(InvalidManifestInput):
        render_preview_route(
            PROJECT,
            RELEASE,
            f"{too_long}-preview.{SITE_DOMAIN}",
            7,
            preview_secret=TOKEN,
            site_domain=SITE_DOMAIN,
            subdomain=too_long,
        )


def test_a_preview_route_command_number_is_checked() -> None:
    with pytest.raises(InvalidManifestInput):
        render_preview_route(PROJECT, RELEASE, PREVIEW_HOST, 0, preview_secret=TOKEN, **PREVIEW_OF)


@pytest.mark.parametrize("token", ["", "short", "x" * 257, "has space" + "x" * 30, "ü" * 40])
def test_a_preview_token_that_is_not_32_to_256_url_safe_characters_is_refused(token: str) -> None:
    with pytest.raises(InvalidManifestInput, match="preview secret") as raised:
        render_preview_route(PROJECT, RELEASE, PREVIEW_HOST, 5, preview_secret=token, **PREVIEW_OF)
    assert token not in str(raised.value) or token == ""


OTHER_SUB = f"other-shop.{SITE_DOMAIN}"


@pytest.mark.parametrize(
    "hostnames",
    [
        (OTHER_SUB,),
        (HOSTS[0], OTHER_SUB),
        (f"a.{HOSTS[0]}",),
        (SITE_DOMAIN,),
        (f"{PROJECT}.{SITE_DOMAIN}",),
    ],
    ids=["other-project", "own-and-other", "under-own", "the-site-domain", "the-project-id"],
)
def test_a_route_names_no_other_projects_platform_hostname(hostnames: tuple[str, ...]) -> None:
    """Hostname takeover (task T2, the lead's ruling): under the platform suffix a route names
    only the project's own host ``<subdomain>.<site domain>`` (decision 0023 G18); the old
    ``<projectId>.<site domain>`` is refused too."""
    with pytest.raises(InvalidManifestInput, match="own subdomain"):
        render_route(
            PROJECT,
            RouteTarget(RouteTargetKind.OFFLINE),
            hostnames,
            5,
            SETTINGS,
            site_domain=SITE_DOMAIN,
            subdomain=SUBDOMAIN,
        )


def test_the_site_route_names_only_the_shared_gateway() -> None:
    route = render_route(
        PROJECT,
        RouteTarget(RouteTargetKind.RELEASE, RELEASE),
        HOSTS,
        7,
        SETTINGS,
        site_domain=SITE_DOMAIN,
        subdomain=SUBDOMAIN,
    )
    assert route["spec"]["parentRefs"] == [
        {
            "group": "gateway.networking.k8s.io",
            "kind": "Gateway",
            "namespace": "webblock-gateway",
            "name": "webblock-public",
        }
    ]
    assert route["spec"]["hostnames"] == list(HOSTS)


def test_render_namespace_requires_the_catalog_secret_argument() -> None:
    """Leaving the mark out is a type error (and a TypeError at run time), not a silent no-mark."""
    with pytest.raises(TypeError, match="catalog_secret"):
        render_namespace(PROJECT, subdomain=SUBDOMAIN)  # type: ignore[call-arg]


def test_render_namespace_requires_the_subdomain_argument() -> None:
    """Leaving the label out is a type error, not a silent namespace without it (decision 0023
    G18a): an apply without it would drop it from the bootstrap's field set."""
    with pytest.raises(TypeError, match="subdomain"):
        render_namespace(PROJECT, catalog_secret=None)  # type: ignore[call-arg]


def test_the_namespace_carries_the_subdomain_label() -> None:
    labels = render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)["metadata"][
        "labels"
    ]
    assert labels[LABEL_SUBDOMAIN] == SUBDOMAIN
    assert labels[LABEL_PROJECT_ID] == str(PROJECT)
    bare = render_namespace(PROJECT, subdomain=None, catalog_secret=None)["metadata"]["labels"]
    assert LABEL_SUBDOMAIN not in bare


@pytest.mark.parametrize("subdomain", ["", "Shop", "-shop", "a.b", "a" * 64, "shop_1"])
def test_a_subdomain_that_is_not_one_dns_label_is_refused(subdomain: str) -> None:
    with pytest.raises(InvalidManifestInput, match="sub-domain"):
        render_namespace(PROJECT, subdomain=subdomain, catalog_secret=None)
    with pytest.raises(InvalidManifestInput, match="sub-domain"):
        render_route(
            PROJECT,
            RouteTarget(RouteTargetKind.OFFLINE),
            HOSTS,
            7,
            SETTINGS,
            site_domain=SITE_DOMAIN,
            subdomain=subdomain,
        )


def test_the_site_route_names_the_subdomain_host() -> None:
    """Decision 0023 G18: the site's route serves ``<subdomain>.<site domain>``."""
    route = render_route(
        PROJECT,
        RouteTarget(RouteTargetKind.RELEASE, RELEASE),
        ("golden-shop.sites.webblock.test",),
        7,
        SETTINGS,
        site_domain=SITE_DOMAIN,
        subdomain="golden-shop",
    )
    assert route["spec"]["hostnames"] == ["golden-shop.sites.webblock.test"]
    # Resource names stay on the project ID.
    assert route["metadata"]["name"] == f"route-{PROJECT}"
    assert route["metadata"]["namespace"] == f"wb-{PROJECT}"
