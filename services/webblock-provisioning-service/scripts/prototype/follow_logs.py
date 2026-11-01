"""follow_logs.py: the log files of the cluster mode (task PK;
.claude/docs/runbooks/prototype-in-cluster.md).

    python3 -I follow_logs.py RUN_DIR KUBECTL [KUBECTL ARGUMENTS...]

Started on the host by scripts/prototype/run-provisioning.sh when the service runs in the Kind stack
(PROV_MODE=cluster); KUBECTL and its arguments are the project's kubectl with the checked admin
kubeconfig, context and cache (deploy/kind/proto-lib.sh). Every second it lists the pods of the Helm
release ``provisioning`` in webblock. For each container instance it has not seen before (pod
UID, container, restart count) it copies that instance's log from its first line into
RUN_DIR/<process>.log (api.log, worker.log, build_worker.log, bootstrap.log, migrate.log): a running
instance is followed until it ends, an ended one is read once, and an earlier instance that ended
between two listings is read with --previous. There is one file per process for the whole run,
across restarts and new pods, so a byte offset taken before a kill stays valid after the next pod
starts (tests/e2e/faults/_recovery_support.py). The stack's log shipper sends these files to Loki
(deploy/prototype/loki/fluent-bit.yaml) as it does for the processes on the host.

The lines are the processes' JSON logs, which carry no secret. SIGTERM ends the follower and its
kubectl children (they share its process group). Standard library only. Exit 2 on a usage error.
"""

import asyncio
import json
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

NAMESPACE = "webblock"
SELECTOR = "app.kubernetes.io/instance=provisioning"
POLL_SECONDS = 1.0
LINE_LIMIT = 1 << 20
FILES = {
    "api": "api",
    "worker": "worker",
    "build-worker": "build_worker",
    "bootstrap": "bootstrap",
    "migrate": "migrate",
}

Instance = tuple[str, str, int]


class Follower:
    def __init__(self, run_dir: Path, kubectl: list[str]) -> None:
        self.run_dir = run_dir
        self.kubectl = kubectl
        self.seen: set[Instance] = set()
        self.tasks: set[asyncio.Task[None]] = set()
        self.files: dict[str, IO[bytes]] = {}

    async def pods(self) -> list[dict[str, Any]]:
        process = await asyncio.create_subprocess_exec(
            *self.kubectl, "get", "pods", "-n", NAMESPACE, "-l", SELECTOR, "-o", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        out, _ = await process.communicate()
        if process.returncode != 0:
            return []
        try:
            items = json.loads(out).get("items")
        except ValueError:
            return []
        return [item for item in items or [] if isinstance(item, dict)]

    def instances(self, pod: dict[str, Any]) -> Iterator[tuple[Instance, str, list[str]]]:
        meta = pod.get("metadata") or {}
        component = (meta.get("labels") or {}).get("app.kubernetes.io/component")
        if component not in FILES:
            return
        name, uid = meta.get("name"), meta.get("uid")
        for status in (pod.get("status") or {}).get("containerStatuses") or []:
            container, restarts = status.get("name"), int(status.get("restartCount") or 0)
            state = status.get("state") or {}
            logs = ["logs", "-n", NAMESPACE, name, "-c", container]
            if restarts > 0:
                yield (uid, container, restarts - 1), component, [*logs, "--previous"]
            if "running" in state:
                yield (uid, container, restarts), component, [*logs, "--follow"]
            elif "terminated" in state:
                yield (uid, container, restarts), component, logs

    def file(self, component: str) -> IO[bytes]:
        if component not in self.files:
            self.files[component] = (self.run_dir / f"{FILES[component]}.log").open("ab")
        return self.files[component]

    async def copy(self, component: str, arguments: list[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *self.kubectl, *arguments,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=LINE_LIMIT,
        )  # fmt: skip
        if process.stdout is None:  # never: stdout is a pipe
            return
        out = self.file(component)
        try:
            while line := await process.stdout.readline():
                out.write(line if line.endswith(b"\n") else line + b"\n")
                out.flush()
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for pod in await self.pods():
                for key, component, arguments in self.instances(pod):
                    if key in self.seen:
                        continue
                    self.seen.add(key)
                    task = asyncio.create_task(self.copy(component, arguments))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
            try:
                await asyncio.wait_for(stop.wait(), POLL_SECONDS)
            except TimeoutError:
                pass
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for out in self.files.values():
            out.close()


async def follow(run_dir: Path, kubectl: list[str]) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await Follower(run_dir, kubectl).run(stop)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3 or not Path(argv[1]).is_dir():
        print("usage: follow_logs.py RUN_DIR KUBECTL [KUBECTL ARGUMENTS...]", file=sys.stderr)
        return 2
    return asyncio.run(follow(Path(argv[1]), argv[2:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
