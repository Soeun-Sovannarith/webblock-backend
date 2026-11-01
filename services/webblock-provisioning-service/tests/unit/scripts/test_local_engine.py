"""The local engine switch of the prototype stack (TR-34, TR-44; spike design
``k3s-site-cluster-spike`` G2, G3 and G5), checked without a cluster: the context the live test
harness expects (``tests/integration/kind_safety.expected_context``) and the context, node prefix
and kubeconfig checks of ``deploy/kind/proto-lib.sh``. The shell runs in a clean bash with a stub
``kubectl`` that only prints what a kubeconfig file holds; no cluster, no Docker."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from tests.integration.kind_safety import engine, expected_context

ROOT = Path(__file__).resolve().parents[3]
PROTO_LIB = ROOT / "deploy" / "kind" / "proto-lib.sh"
LIB_TEXT = PROTO_LIB.read_text()


# --- the harness ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recorded", "context"),
    [
        ("kind\n", "kind-webblock-proto"),
        ("k3d\n", "k3d-webblock-proto"),
        ("k3d", "k3d-webblock-proto"),
    ],
)
def test_tr34_fr17_the_harness_expects_the_context_of_the_recorded_engine(
    tmp_path: Path, recorded: str, context: str
) -> None:
    """TR-34, FR-17 (G3): the six harness files compare the current context with the one proto-up
    recorded in ``bin/kind-state/proto/engine``."""
    path = tmp_path / "engine"
    path.write_text(recorded)
    assert expected_context(path) == context


def test_tr34_fr17_a_stack_without_an_engine_record_is_kind(tmp_path: Path) -> None:
    """TR-34, FR-17 (G3): a stack started before the record existed is the Kind stack."""
    assert engine(tmp_path / "engine") == "kind"
    assert expected_context(tmp_path / "engine") == "kind-webblock-proto"


@pytest.mark.parametrize("recorded", ["k3s\n", "", "KIND\n", "kind k3d\n", "doks\n"])
def test_tr34_fr17_an_unknown_engine_record_fails_the_harness(
    tmp_path: Path, recorded: str
) -> None:
    """TR-34, FR-17 (G3): only ``kind`` and ``k3d`` name a local engine; anything else stops the
    live tests before any kubectl call."""
    path = tmp_path / "engine"
    path.write_text(recorded)
    with pytest.raises(pytest.fail.Exception, match="neither kind nor k3d"):
        expected_context(path)


# --- proto-lib.sh ---------------------------------------------------------------------------------


def _function(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", LIB_TEXT, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return match.group(0)


def _bash(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )


_PRELUDE = """
set -euo pipefail
die() { printf 'ERROR: %s\\n' "$*" >&2; exit 1; }
log() { :; }
"""


def _choose(proto: Path, requested: str | None) -> subprocess.CompletedProcess[str]:
    request = f"PROTO_ENGINE={requested}" if requested is not None else "unset PROTO_ENGINE"
    return _bash(
        _PRELUDE
        + f'CLUSTER=webblock-proto; PROTO="{proto}"; {request}\n'
        + _function("proto_engine")
        + 'proto_engine; printf "%s %s %s" "$ENGINE" "$CONTEXT" "$NODE_PREFIX"\n'
    )


@pytest.mark.parametrize(
    ("recorded", "requested", "expected"),
    [
        (None, None, "kind kind-webblock-proto webblock-proto"),
        (None, "kind", "kind kind-webblock-proto webblock-proto"),
        (None, "k3d", "k3d k3d-webblock-proto k3d-webblock-proto"),
        ("k3d", None, "k3d k3d-webblock-proto k3d-webblock-proto"),
        ("k3d", "k3d", "k3d k3d-webblock-proto k3d-webblock-proto"),
        ("kind", None, "kind kind-webblock-proto webblock-proto"),
    ],
)
def test_tr44_one_engine_switch_sets_the_context_and_the_node_prefix(
    tmp_path: Path, recorded: str | None, requested: str | None, expected: str
) -> None:
    """TR-44 (G5): ``PROTO_ENGINE`` picks the engine on proto-up, every later script reads the one
    proto-up recorded; the context is ``<engine>-webblock-proto``; k3d names its nodes
    ``k3d-webblock-proto-*``, Kind ``webblock-proto-*``."""
    if recorded is not None:
        (tmp_path / "engine").write_text(recorded + "\n")
    done = _choose(tmp_path, requested)
    assert done.returncode == 0, done.stderr
    assert done.stdout == expected


@pytest.mark.parametrize("requested", ["k3s", "KIND", "kind ", "doks", ""])
def test_tr44_an_unknown_engine_is_refused(tmp_path: Path, requested: str) -> None:
    """TR-44 (G5): only kind and k3d."""
    done = _choose(tmp_path, f"'{requested}'")
    assert done.returncode != 0
    assert "PROTO_ENGINE must be kind or k3d" in done.stderr


def test_tr44_a_running_stack_of_the_other_engine_is_refused(tmp_path: Path) -> None:
    """TR-44 (G2: one cluster at a time): a k3d stack is recorded, so a Kind proto-up stops."""
    (tmp_path / "engine").write_text("k3d\n")
    done = _choose(tmp_path, "kind")
    assert done.returncode != 0
    assert "a k3d stack is recorded" in done.stderr


def _kubeconfig(path: Path, context: str, server: str) -> Path:
    path.write_text(json.dumps({"current-context": context, "server": server}))
    path.chmod(0o600)
    return path


# The stub answers only the two questions require_local_kubeconfig asks, from the JSON file above.
_STUB_KUBECTL = """
stub_kubectl() {
  local f="" a
  while [ "$#" -gt 0 ]; do a="$1"; shift; [ "$a" = --kubeconfig ] && f="$1"; done
  python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]], end="")' \
    "$f" "$Q"
}
"""


def _require(kubeconfig: Path, engine_name: str) -> subprocess.CompletedProcess[str]:
    function = _function("require_local_kubeconfig")
    # The stub stands in for bin/kubectl: current-context first, then the server.
    function = function.replace(
        '"$(HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$f" config current-context 2>/dev/null)"',
        '"$(Q=current-context stub_kubectl --kubeconfig "$f")"',
    ).replace(
        '"$(HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$f" config view --minify -o '
        "jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)\"",
        '"$(Q=server stub_kubectl --kubeconfig "$f")"',
    )
    assert "stub_kubectl" in function and "$KUBECTL" not in function
    return _bash(
        _PRELUDE
        + _STUB_KUBECTL
        + f"CONTEXT={engine_name}-webblock-proto\n"
        + function
        + f'require_local_kubeconfig "{kubeconfig}" && echo accepted\n'
    )


@pytest.mark.parametrize("engine_name", ["kind", "k3d"])
def test_tr34_fr17_the_scripts_accept_the_local_context_of_the_recorded_engine(
    tmp_path: Path, engine_name: str
) -> None:
    """TR-34, FR-17 (G3): proto-lib's check passes the recorded engine's own context on
    ``https://127.0.0.1:<port>``."""
    path = _kubeconfig(tmp_path / "kc", f"{engine_name}-webblock-proto", "https://127.0.0.1:41234")
    done = _require(path, engine_name)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "accepted"


@pytest.mark.parametrize(
    ("engine_name", "context", "server", "reason"),
    [
        ("k3d", "k3d-webblock-proto", "https://0.0.0.0:41234", "not https://127.0.0.1:<port>"),
        ("k3d", "k3d-webblock-proto", "https://localhost:41234", "not https://127.0.0.1:<port>"),
        ("k3d", "k3d-webblock-proto", "https://203.0.113.10:6443", "not https://127.0.0.1:<port>"),
        ("k3d", "k3d-other", "https://127.0.0.1:41234", "not kind-webblock-* or k3d-webblock-*"),
        (
            "k3d",
            "k3s-webblock-proto",
            "https://127.0.0.1:41234",
            "not kind-webblock-* or k3d-webblock-*",
        ),
        ("k3d", "do-ams3-ksga", "https://127.0.0.1:41234", "not kind-webblock-* or k3d-webblock-*"),
        ("k3d", "kind-webblock-proto", "https://127.0.0.1:41234", "is not k3d-webblock-proto"),
        ("kind", "k3d-webblock-proto", "https://127.0.0.1:41234", "is not kind-webblock-proto"),
    ],
)
def test_tr34_fr17_the_scripts_refuse_any_other_context_or_server(
    tmp_path: Path, engine_name: str, context: str, server: str, reason: str
) -> None:
    """TR-34, FR-17 (G3): a context of the other engine, another context, or a server that is not
    the loopback address (k3d's default ``0.0.0.0`` included) stops the script."""
    done = _require(_kubeconfig(tmp_path / "kc", context, server), engine_name)
    assert done.returncode != 0
    assert "accepted" not in done.stdout
    assert reason in done.stderr


def test_tr34_fr17_the_scripts_refuse_a_kubeconfig_that_is_not_private(tmp_path: Path) -> None:
    """TR-34, FR-17 (G3): mode 0600 stays required."""
    path = _kubeconfig(tmp_path / "kc", "k3d-webblock-proto", "https://127.0.0.1:41234")
    path.chmod(0o644)
    done = _require(path, "k3d")
    assert done.returncode != 0
    assert "expected 600" in done.stderr


def _others(
    engine_name: str, kind_labels: str, k3d_labels: str
) -> subprocess.CompletedProcess[str]:
    """require_no_other_cluster with a stub ``docker ps`` that lists the node containers' labels."""
    stub = f"""
docker() {{
  case "$*" in
    *io.x-k8s.kind.cluster*) printf '{kind_labels}' ;;
    *k3d.cluster*) printf '{k3d_labels}' ;;
    *) return 1 ;;
  esac
}}
"""
    return _bash(
        _PRELUDE
        + stub
        + f"CLUSTER=webblock-proto; ENGINE={engine_name}\n"
        + _function("require_no_other_cluster")
        + "require_no_other_cluster && echo alone\n"
    )


@pytest.mark.parametrize(
    ("engine_name", "kind_labels", "k3d_labels"),
    [
        ("kind", "", ""),
        ("k3d", "", ""),
        ("kind", "webblock-proto\\nwebblock-proto\\n", ""),
        ("k3d", "", "webblock-proto\\nwebblock-proto\\nwebblock-proto\\n"),
    ],
)
def test_tr44_the_stack_may_start_when_only_its_own_cluster_runs(
    engine_name: str, kind_labels: str, k3d_labels: str
) -> None:
    """TR-44 (G2): no other local cluster, or only this engine's own nodes."""
    done = _others(engine_name, kind_labels, k3d_labels)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "alone"


@pytest.mark.parametrize(
    ("engine_name", "kind_labels", "k3d_labels", "named"),
    [
        ("k3d", "webblock-proto\\n", "", "kind:webblock-proto"),
        ("kind", "", "webblock-proto\\n", "k3d:webblock-proto"),
        ("kind", "webblock-p4\\n", "", "kind:webblock-p4"),
        ("k3d", "", "other\\n", "k3d:other"),
    ],
)
def test_tr44_another_local_cluster_of_either_engine_stops_the_stack(
    engine_name: str, kind_labels: str, k3d_labels: str, named: str
) -> None:
    """TR-44 (G2): one cluster at a time across both engines."""
    done = _others(engine_name, kind_labels, k3d_labels)
    assert done.returncode != 0
    assert f"another local cluster is running ({named})" in done.stderr
