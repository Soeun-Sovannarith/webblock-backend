"""The in-cluster identities, live (task PK; decision 0016 section 9 ruling 2). Needs the service
in the cluster (make proto-up proto-run PROV_MODE=cluster); make proto-e2e runs this folder only in
that mode, and run on a host-mode stack it fails rather than skips.

- The API and the build worker hold no Kubernetes token: no projected token in their pod spec, no
  file at the default token path, and the API cannot even open a connection to the API server
  (the chart's CiliumNetworkPolicy).
- The worker and the bootstrap each run under their own ServiceAccount with exactly one projected
  token that expires after 600 s, and a SelfSubjectReview made with the mounted token from inside
  the pod names that ServiceAccount.
- Neither of the two ServiceAccounts may mint a token for the other.

kubectl runs through tests/e2e/faults/_recovery_support.kubectl, which checks the project-local
context first (CLAUDE.md section 0). No token is printed: the review runs inside the pod and only
the username comes back.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.e2e._stack import StackMissing, run_mode
from tests.e2e.faults._recovery_support import kube_json, kubectl

NAMESPACE = "webblock"
DEFAULT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105  # a path
PROJECTED = "/var/run/secrets/webblock/kube"
REVIEW = """
import json, os, ssl, urllib.request
host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ["KUBERNETES_SERVICE_PORT"]
base = "https://{}:{}".format(host, port)
with open("/var/run/secrets/webblock/kube/token") as f:
    token = f.read()
request = urllib.request.Request(
    base + "/apis/authentication.k8s.io/v1/selfsubjectreviews",
    data=json.dumps(
        {"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}
    ).encode(),
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    method="POST",
)
context = ssl.create_default_context(cafile="/var/run/secrets/webblock/kube/ca.crt")
with urllib.request.urlopen(request, context=context, timeout=10) as answer:
    print(json.load(answer)["status"]["userInfo"]["username"])
"""
API_SERVER = """
import os, socket
try:
    address = (os.environ["KUBERNETES_SERVICE_HOST"], int(os.environ["KUBERNETES_SERVICE_PORT"]))
    socket.create_connection(address, timeout=3).close()
    print("connected")
except OSError:
    print("refused")
"""


@pytest.fixture(autouse=True)
def service_in_the_cluster() -> None:
    if run_mode() != "cluster":
        raise StackMissing(
            "tests/e2e/incluster needs the service in the cluster: make proto-run PROV_MODE=cluster"
        )


def _pod(component: str) -> dict[str, Any]:
    pods = kube_json(
        "-n", NAMESPACE, "get", "pods", "--field-selector=status.phase=Running",
        "-l", f"app.kubernetes.io/instance=provisioning,app.kubernetes.io/component={component}",
    )["items"]  # fmt: skip
    pods = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    assert len(pods) == 1, f"{component}: {len(pods)} running pods"
    pod: dict[str, Any] = pods[0]
    return pod


def _in_pod(pod: dict[str, Any], container: str, program: str) -> str:
    return kubectl(
        "-n", NAMESPACE, "exec", pod["metadata"]["name"], "-c", container, "--",
        "/opt/venv/bin/python", "-c", program,
    ).strip()  # fmt: skip


def _projected_tokens(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        source["serviceAccountToken"]
        for volume in spec.get("volumes") or []
        for source in (volume.get("projected") or {}).get("sources") or []
        if "serviceAccountToken" in source
    ]


@pytest.mark.parametrize("component", ["api", "build-worker"])
def test_the_api_and_the_build_worker_hold_no_token(component: str) -> None:
    pod = _pod(component)
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert _projected_tokens(pod["spec"]) == []
    program = f"import os; print(os.path.exists({DEFAULT_TOKEN!r}), os.path.exists({PROJECTED!r}))"
    assert _in_pod(pod, component, program) == "False False"


def test_the_api_cannot_reach_the_api_server() -> None:
    assert _in_pod(_pod("api"), "api", API_SERVER) == "refused"


@pytest.mark.parametrize(
    ("component", "account"), [("worker", "provisioning-worker"), ("bootstrap", "tenant-bootstrap")]
)
def test_the_worker_and_the_bootstrap_are_their_own_service_accounts(
    component: str, account: str
) -> None:
    pod = _pod(component)
    assert pod["spec"]["serviceAccountName"] == account
    assert pod["spec"]["automountServiceAccountToken"] is False
    tokens = _projected_tokens(pod["spec"])
    assert [(t["path"], t["expirationSeconds"]) for t in tokens] == [("token", 600)]
    assert (
        _in_pod(pod, component, f"import os; print(os.path.exists({DEFAULT_TOKEN!r}))") == "False"
    )
    assert _in_pod(pod, component, REVIEW) == f"system:serviceaccount:{NAMESPACE}:{account}"


@pytest.mark.parametrize(
    ("account", "other"),
    [("provisioning-worker", "tenant-bootstrap"), ("tenant-bootstrap", "provisioning-worker")],
)
def test_neither_service_account_can_mint_the_others_token(account: str, other: str) -> None:
    answer = kubectl(
        "-n", NAMESPACE, "auth", "can-i", "create", f"serviceaccounts/{other}",
        "--subresource=token", f"--as=system:serviceaccount:{NAMESPACE}:{account}", ok=False,
    )  # fmt: skip
    assert answer.strip().splitlines()[0].startswith("no")
