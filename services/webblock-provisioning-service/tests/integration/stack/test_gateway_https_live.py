"""The shared Gateway (tasks T1 and W6-K): the platform subdomain answers over HTTPS with the
wildcard certificate of the local CA, plain HTTP still answers, and the rule-level
``ResponseHeaderModifier`` the renderer writes sets ``X-Webblock-Release`` (decision 0014 section
17) on both listeners. The route is the renderer's ``route-<projectId>``, applied by the worker's
token with every admission policy and the tenant network policy in force; the Gateway reaching the
site is the ingress the network policy allows."""

from pathlib import Path

from tests.integration.stack._stack import (
    ADMIN,
    GATEWAY_NAME,
    GATEWAY_NAMESPACE,
    RELEASE_HEADER,
    SITE_SUFFIX,
    WORKER,
    Kubectl,
    Response,
    Stack,
    Tenant,
    command_number,
    curl,
    curl_exit_code,
    is_true,
    poll,
    site_route,
    wait_route_accepted,
)

CURL_UNTRUSTED_CERTIFICATE = 60
OFFLINE = "offline"


def _serves_release(response: Response | None, release: str) -> bool:
    return (
        response is not None
        and response.status == 200
        and "Welcome to nginx" in response.body
        and response.header_values(RELEASE_HEADER) == [release]
    )


def _serves_offline(response: Response | None) -> bool:
    return (
        response is not None
        and response.status == 503
        and response.header_values(RELEASE_HEADER) == [OFFLINE]
    )


def test_gateway_has_programmed_http_and_https_listeners(kubectl: Kubectl) -> None:
    gateway = kubectl.get(ADMIN, "gateway", GATEWAY_NAME, GATEWAY_NAMESPACE)
    generation = gateway["metadata"]["generation"]
    listeners = {item["name"]: item for item in gateway["spec"]["listeners"]}
    assert set(listeners) == {"http-sites", "https-sites"}
    assert listeners["http-sites"]["protocol"] == "HTTP"
    assert listeners["https-sites"]["protocol"] == "HTTPS"
    assert listeners["https-sites"]["hostname"] == f"*.{SITE_SUFFIX}"
    for status in gateway["status"]["listeners"]:
        conditions = status["conditions"]
        assert is_true(conditions, "Programmed", generation), status
        assert is_true(conditions, "ResolvedRefs", generation), status


def test_gateway_accepts_no_listenerset(kubectl: Kubectl) -> None:
    """No custom domains (decision 0014 section 23): the Gateway allows no ListenerSet at all."""
    gateway = kubectl.get(ADMIN, "gateway", GATEWAY_NAME, GATEWAY_NAMESPACE)
    allowed = gateway["spec"].get("allowedListeners", {}).get("namespaces", {}).get("from", "None")
    assert allowed == "None", gateway["spec"].get("allowedListeners")


def test_platform_subdomain_over_https_and_http_with_the_release_header(
    kubectl: Kubectl, stack: Stack, tenant: Tenant, tmp_path: Path
) -> None:
    host = tenant.host
    route = f"route-{tenant.project}"
    kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))
    wait_route_accepted(kubectl, tenant.namespace, route)
    https_url = f"https://{host}:{stack.https_port}/"
    resolve = f"{host}:{stack.https_port}:{stack.gateway_address}"

    def over_https() -> Response | None:
        answer = curl(https_url, resolve=resolve, cacert=stack.local_ca, work=tmp_path)
        return answer if _serves_release(answer, str(tenant.release)) else None

    poll(f"{https_url} through the Gateway, verified by the local CA", 90, over_https)

    def over_http() -> Response | None:
        answer = curl(
            f"http://{host}/",
            resolve=f"{host}:80:{stack.gateway_address}",
            cacert=None,
            work=tmp_path,
        )
        return answer if _serves_release(answer, str(tenant.release)) else None

    poll(f"http://{host}/ through the Gateway", 90, over_http)

    # The header follows the route: to the offline page, then back to the release.
    kubectl.apply(WORKER, site_route(tenant.project, None, command_number()))
    wait_route_accepted(kubectl, tenant.namespace, route)

    def offline() -> Response | None:
        answer = curl(https_url, resolve=resolve, cacert=stack.local_ca, work=tmp_path)
        return answer if _serves_offline(answer) else None

    poll(f"{RELEASE_HEADER}: {OFFLINE} on {https_url}", 90, offline)

    kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))
    wait_route_accepted(kubectl, tenant.namespace, route)
    poll(f"{https_url} back on the release", 90, over_https)


def test_wildcard_certificate_is_not_publicly_trusted(
    stack: Stack, tenant: Tenant, tmp_path: Path
) -> None:
    """The HTTPS listener presents the local CA's certificate: with the system roots alone curl
    refuses it (exit 60), with the local CA it is accepted."""
    host = tenant.host
    resolve = f"{host}:{stack.https_port}:{stack.gateway_address}"
    url = f"https://{host}:{stack.https_port}/"
    assert curl_exit_code(url, resolve=resolve, cacert=None, work=tmp_path) == (
        CURL_UNTRUSTED_CERTIFICATE
    )
    assert curl_exit_code(url, resolve=resolve, cacert=stack.local_ca, work=tmp_path) == 0
