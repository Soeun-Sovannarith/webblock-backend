"""QA-10 live (task W6-L): Jenkins is stopped while a build runs and started again 25 s later.

The release must end ``built``, or ``failed`` with its attempts used up and no pending step left:
never ``building`` for good. Before the fix, the attempt that the restart broke failed only after
a long time, and the next attempt then stayed ``pending`` (nothing started it). Now the build
worker starts a pending attempt after ``BUILD_PENDING_GRACE``, and an attempt whose deadline
passes has its build aborted in Jenkins (``BuildSystem.cancel``), so a pipeline that never resumes
does not hold the executor.

The build is the stock ``site`` sample with ``RUN sleep 75`` added (QA's ``qa-slow-build``
variant, made here from the stock files so that no sample directory is added), so that the restart
falls in the middle of it. QA's reproduction is
``tests/qa/test_qa_live_jenkins_restarted_mid_build.py`` at ``d487207``. The deadline covers the
prototype's ``BUILD_DURATION`` (20 minutes) for an attempt that never ends by itself.

Needs ``make proto-up proto-run``. Touches only the stack's container ``webblock-jenkins``.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from tests.e2e._stack import PROTO, ROOT, Stack, new_project, new_repository_id, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(2400)]

SITE = ROOT / "deploy" / "prototype" / "sample-site" / "site"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(PROTO / "home")}
JENKINS = "webblock-jenkins"


def docker(*args: str) -> None:
    done = subprocess.run(  # noqa: S603  # fixed argument list, no shell
        ["/usr/bin/docker", *args], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {
        **ENV,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    config = (
        "-c",
        "user.name=W6-L",
        "-c",
        "user.email=w6l@webblock.test",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "init.defaultBranch=main",
    )
    done = subprocess.run(  # noqa: S603  # fixed argument list, no shell
        ["/usr/bin/git", *config, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return done.stdout.strip()


def _commit_slow_site(name: str) -> str:
    """A commit in the stack's bare repository ``name``: the ``site`` sample whose build sleeps
    75 seconds. Returns the commit SHA."""
    bare = PROTO / "git" / f"{name}.git"
    bare.parent.mkdir(exist_ok=True)
    if not bare.is_dir():
        _git("init", "--quiet", "--bare", str(bare))
    work = Path(tempfile.mkdtemp(prefix=".w6l-work.", dir=PROTO))
    try:
        repo = work / "repo"
        _git("clone", "--quiet", str(bare), str(repo))
        shutil.copytree(SITE, repo, dirs_exist_ok=True)
        dockerfile = repo / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "RUN sleep 75\n")
        index = repo / "index.html"
        index.write_text(index.read_text().replace("@VERSION@", "slow"))
        _git("add", "-A", cwd=repo)
        _git("commit", "--quiet", "-m", "w6-l slow build", cwd=repo)
        _git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=repo)
        _git("--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")
        _git("--git-dir", str(bare), "update-server-info")
        return _git("rev-parse", "HEAD", cwd=repo)
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def test_jenkins_restarted_during_a_build_never_leaves_the_release_building() -> None:
    stack = Stack()
    project, repository_id = new_project(), new_repository_id()
    name = f"w6l-restart-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await asyncio.to_thread(_commit_slow_site, name)
    request = await stack.publish(project, repository_id, sha)
    release: uuid.UUID = await stack.release_of(request)
    await until(
        "the build to run",
        lambda: stack.release(project, release),
        lambda b: any(s["status"] == "running" for s in b["steps"]),
        180,
        0.5,
    )
    await asyncio.sleep(15)
    docker("stop", JENKINS)
    try:
        await asyncio.sleep(25)
    finally:
        docker("start", JENKINS)
    body = await until(
        "the release to settle",
        lambda: stack.release(project, release),
        lambda b: b["status"] in {"built", "failed"},
        2200,
        5,
    )
    builds = [s for s in body["steps"] if s["stepName"] == "build"]
    print("release", body["status"], body.get("failureCode"), builds)
    assert body["status"] in {"built", "failed"}
    assert all(s["status"] != "pending" for s in builds), builds
    if body["status"] == "failed":
        assert body["failureCode"] == "build_attempts_exhausted", body
        assert len(builds) == 3, builds
