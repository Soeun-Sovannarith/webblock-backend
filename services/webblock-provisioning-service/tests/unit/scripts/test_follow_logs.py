"""scripts/prototype/follow_logs.py (task PK): one log file per process for the whole run, across
pods and restarts. A fake kubectl answers ``get pods`` from a JSON file and ``logs`` from one file
per pod and container (``-previous`` for the earlier instance); no cluster is contacted."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

FOLLOWER = Path(__file__).resolve().parents[3] / "scripts" / "prototype" / "follow_logs.py"
FAKE_KUBECTL = """#!/usr/bin/env python3
import sys
from pathlib import Path
state = Path(sys.argv[0]).resolve().parent
args = sys.argv[1:]
if args[:2] == ["get", "pods"]:
    sys.stdout.write((state / "pods.json").read_text())
elif args[0] == "logs":
    pod, container = args[3], args[args.index("-c") + 1]
    suffix = "-previous" if "--previous" in args else ""
    path = state / f"{pod}-{container}{suffix}.log"
    sys.stdout.write(path.read_text() if path.exists() else "")
"""


def pod(name: str, uid: str, component: str, restarts: int, state: str) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "uid": uid,
            "labels": {"app.kubernetes.io/component": component},
        },
        "status": {
            "containerStatuses": [
                {"name": component, "restartCount": restarts, "state": {state: {}}}
            ]
        },
    }


def until(what: str, check: Any, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.1)


def test_one_file_per_process_across_pods_and_restarts(tmp_path: Path) -> None:
    state, run = tmp_path / "state", tmp_path / "run"
    state.mkdir()
    run.mkdir()
    kubectl = state / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(0o755)
    pods = state / "pods.json"
    (state / "w1-worker.log").write_text("w1 line 1\nw1 line 2\n")
    (state / "b1-build-worker.log").write_text("b1 done\n")
    pods.write_text(
        json.dumps(
            {
                "items": [
                    pod("w1", "u1", "worker", 0, "running"),
                    pod("b1", "u2", "build-worker", 0, "terminated"),
                ]
            }
        )
    )
    worker_log, build_log = run / "worker.log", run / "build_worker.log"

    def text(path: Path) -> str:
        return path.read_text() if path.exists() else ""

    follower = subprocess.Popen(  # noqa: S603  # fixed argument list: this interpreter and the script
        [sys.executable, "-I", str(FOLLOWER), str(run), str(kubectl)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        until(
            "the first lines",
            lambda: text(worker_log) == "w1 line 1\nw1 line 2\n" and text(build_log) == "b1 done\n",
        )
        # The worker's container restarts: its new instance is appended to the same file.
        (state / "w1-worker.log").write_text("w1 second\n")
        pods.write_text(json.dumps({"items": [pod("w1", "u1", "worker", 1, "running")]}))
        until("the restarted instance", lambda: text(worker_log).endswith("w1 second\n"))
        # A new pod that already restarted twice: its earlier instance is read once (--previous).
        (state / "w2-worker-previous.log").write_text("w2 crashed\n")
        pods.write_text(json.dumps({"items": [pod("w2", "u3", "worker", 2, "waiting")]}))
        until("the earlier instance", lambda: text(worker_log).endswith("w2 crashed\n"))
    finally:
        follower.send_signal(signal.SIGTERM)
        _, errors = follower.communicate(timeout=10)
    assert follower.returncode == 0, errors
    assert text(worker_log) == "w1 line 1\nw1 line 2\nw1 second\nw2 crashed\n"
    assert text(build_log) == "b1 done\n"
    assert sorted(p.name for p in run.iterdir()) == ["build_worker.log", "worker.log"]


def test_a_missing_run_folder_is_a_usage_error(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and the script
        [sys.executable, "-I", str(FOLLOWER), str(tmp_path / "absent"), "/bin/true"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 2
    assert "usage: follow_logs.py RUN_DIR KUBECTL" in done.stderr
