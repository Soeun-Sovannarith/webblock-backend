import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import docker
from docker.errors import DockerException
from app.core.config import settings

MAX_LOG_CHARS = 20_000


class BuildError(Exception):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass
class BuildResult:
    status: str  # SUCCEEDED | SKIPPED
    log: str


def _tail(text: str) -> str:
    return text if len(text) <= MAX_LOG_CHARS else "...(truncated)...\n" + text[-MAX_LOG_CHARS:]


def write_workspace(files: dict[str, str], root: Path) -> None:
    base = root.resolve()
    for rel, content in files.items():
        if "\0" in rel or rel.startswith("/") or ".." in rel.split("/"):
            raise BuildError(f"Security: Refusing to write invalid path: {rel}")
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            raise BuildError(f"Refusing to write outside the workspace: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _get_docker_client() -> docker.DockerClient | None:
    """Initialize Docker Python SDK client from environment, or return None if daemon unreachable."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except (DockerException, Exception):
        return None


def _run_docker_sdk(client: docker.DockerClient, workspace: Path, network: str, command: list[str], log: list[str]) -> None:
    """Run container using Docker Python SDK with enterprise security controls."""
    log.append(f"$ [docker-sdk] ({network}) {' '.join(command)}\n")
    try:
        output = client.containers.run(
            settings.build_image,
            command=command,
            volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode=network,
            mem_limit=settings.build_memory,
            pids_limit=512,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            user=f"{os.getuid()}:{os.getgid()}",
            environment={
                "HOME": "/tmp",
                "npm_config_cache": "/workspace/.npm-cache",
                "NEXT_TELEMETRY_DISABLED": "1",
            },
            remove=True,
            stdout=True,
            stderr=True,
        )
        if output:
            log.append(output.decode("utf-8", errors="replace"))
    except DockerException as e:
        log.append(str(e) + "\n")
        raise BuildError(f"Docker SDK container execution failed: {e}", _tail("".join(log))) from e


def _docker_cmd(workspace: Path, network: str, command: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm",
        "--network", network,
        "--memory", settings.build_memory, "--cpus", settings.build_cpus, "--pids-limit", "512",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp", "-e", "npm_config_cache=/workspace/.npm-cache", "-e", "NEXT_TELEMETRY_DISABLED=1",
        "-v", f"{workspace}:/workspace", "-w", "/workspace",
        settings.build_image, *command,
    ]


def _run(cmd: list[str], cwd: Path, log: list[str], env: dict[str, str] | None = None) -> None:
    log.append(f"$ {' '.join(cmd)}\n")
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=settings.build_timeout_seconds)
    except subprocess.TimeoutExpired as e:
        log.append(str(e.stdout or "") + str(e.stderr or ""))
        raise BuildError(f"Build step timed out after {settings.build_timeout_seconds}s", _tail("".join(log))) from e
    except FileNotFoundError as e:
        raise BuildError(f"Build tool not found: {e.filename}", _tail("".join(log))) from e
    log.append(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise BuildError(f"Build step failed with exit code {proc.returncode}", _tail("".join(log)))


INSTALL = ["npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"]
BUILD = ["npm", "run", "build"]


def build(files: dict[str, str], on_phase: Callable[[str, str, str], None] | None = None) -> BuildResult:
    """Install and build the site in a throwaway workspace using Docker SDK or local runner.

    Raises BuildError on failure.
    """
    if settings.build_runner == "skip":
        return BuildResult("SKIPPED", "Build runner disabled (BUILD_RUNNER=skip).")
    report = on_phase or (lambda *_: None)
    workspace = Path(tempfile.mkdtemp(prefix="webblock-build-"))
    log: list[str] = []
    try:
        write_workspace(files, workspace)
        if settings.build_runner == "docker":
            docker_client = _get_docker_client()
            phases = [("INSTALL", "bridge", INSTALL), ("BUILD", "none", BUILD)]
            for phase, network, cmd in phases:
                start = len(log)
                report(phase, "RUNNING", "")
                try:
                    if docker_client:
                        _run_docker_sdk(docker_client, workspace, network, cmd, log)
                    else:
                        _run(_docker_cmd(workspace, network, cmd), workspace, log)
                except BuildError as e:
                    report(phase, "FAILED", _tail("".join(log[start:])))
                    raise e
                report(phase, "PASSED", _tail("".join(log[start:])))
        else:
            env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
            phases_local = [("INSTALL", INSTALL, env), ("BUILD", BUILD, env)]
            for phase, cmd, env in phases_local:
                start = len(log)
                report(phase, "RUNNING", "")
                try:
                    _run(cmd, workspace, log, env)
                except BuildError as e:
                    report(phase, "FAILED", _tail("".join(log[start:])))
                    raise e
                report(phase, "PASSED", _tail("".join(log[start:])))
        return BuildResult("SUCCEEDED", _tail("".join(log)))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
