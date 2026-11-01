"""Loki and its log shipper on the stack (task W6-K; decision 0014 section 23 item 5): a JSON line
that a Provisioning process would write into its run folder
(``bin/kind-state/proto/provisioning/logs/<run>/<process>.log``) reaches Loki, labelled
with its process and run, and an audit event
(``audit=true``) is found by a LogQL JSON filter, through Loki's API and through
``deploy/kind/proto-loki-query.sh`` (``make proto-logs``), which later tasks use for their audit
assertions. A line with ``audit=false`` and the same marker is not returned by the audit filter."""

import json
import os
import secrets
import shutil
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.integration.stack._stack import REPO, STATE, poll, stack_env

LOGS = STATE / "provisioning" / "logs"
QUERY_SCRIPT = REPO / "deploy" / "kind" / "proto-loki-query.sh"


def bash() -> str:
    found = shutil.which("bash")
    if found is None:
        pytest.fail("bash is required")
    return found


@pytest.fixture(scope="module")
def loki_url() -> str:
    url = stack_env().get("PROTO_LOKI_URL", "")
    if not url.startswith("http://127.0.0.1:"):
        pytest.fail("stack.env lacks PROTO_LOKI_URL (http://127.0.0.1:<port>): run make proto-up")
    return url


@pytest.fixture
def run_folder() -> Iterator[Path]:
    """A run folder of its own, as run-provisioning.sh makes one per start; removed afterwards."""
    if not LOGS.is_dir():
        pytest.fail(f"{LOGS} is missing: run make proto-up on this branch")
    folder = LOGS / f"stack-test-{secrets.token_hex(4)}"
    folder.mkdir()
    try:
        yield folder
    finally:
        shutil.rmtree(folder)


def query(loki_url: str, logql: str) -> list[dict[str, object]]:
    """Every entry Loki returns for ``logql`` over the last 15 minutes, with its stream labels
    under ``_labels``."""
    params = urllib.parse.urlencode({"query": logql, "since": "15m", "limit": "100"})
    with urllib.request.urlopen(  # noqa: S310 - a fixed http://127.0.0.1 URL from stack.env
        f"{loki_url}/loki/api/v1/query_range?{params}", timeout=10
    ) as answer:
        body = json.load(answer)
    assert body["status"] == "success", body
    found: list[dict[str, object]] = []
    for stream in body["data"]["result"]:
        for _, line in stream["values"]:
            entry = json.loads(line)
            entry["_labels"] = stream["stream"]
            found.append(entry)
    return found


def test_loki_is_ready(loki_url: str) -> None:
    with urllib.request.urlopen(f"{loki_url}/ready", timeout=5) as answer:  # noqa: S310 - as above
        assert answer.status == 200


def test_an_audit_line_written_to_a_run_folder_is_found_in_loki(
    loki_url: str, run_folder: Path
) -> None:
    marker = secrets.token_hex(8)
    lines = [
        {"event": "stack.audit_probe", "audit": True, "marker": marker, "result": "accepted"},
        {"event": "stack.plain_probe", "audit": False, "marker": marker},
    ]
    with (run_folder / "stacktest.log").open("a") as log:
        for line in lines:
            log.write(json.dumps(line) + "\n")

    logql = f'{{job="webblock-provisioning"}} | json | audit="true" | marker="{marker}"'

    def shipped() -> list[dict[str, object]] | None:
        return query(loki_url, logql) or None

    found = poll("the audit line in Loki", 60, shipped)
    assert len(found) == 1, found
    assert found[0]["event"] == "stack.audit_probe"
    # The stream's labels: the shipper's three, plus what "| json" extracted from the line.
    labels = found[0]["_labels"]
    assert isinstance(labels, dict)
    assert {k: labels.get(k) for k in ("job", "process", "run")} == {
        "job": "webblock-provisioning",
        "process": "stacktest",
        "run": run_folder.name,
    }
    # Both lines arrived; the audit filter returned only the audit one.
    every = query(loki_url, f'{{run="{run_folder.name}"}} | json | marker="{marker}"')
    assert sorted(str(e["event"]) for e in every) == ["stack.audit_probe", "stack.plain_probe"]

    done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [bash(), str(QUERY_SCRIPT), "--since", "15m", logql],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(run_folder)},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    printed = [json.loads(line) for line in done.stdout.splitlines()]
    assert [p["event"] for p in printed] == ["stack.audit_probe"], done.stdout


def test_the_query_script_fails_on_a_bad_query(loki_url: str, tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [bash(), str(QUERY_SCRIPT), "{job="],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(tmp_path)},
        check=False,
    )
    assert done.returncode != 0
    assert "Loki" in done.stderr
