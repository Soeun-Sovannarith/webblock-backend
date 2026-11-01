"""QA-03 live (decision 0014 section 23, lead's rulings; task W6-E): a suspend that failed is
retried, so Payment's "entitlement ended" is never forgotten.

A live site; its route is stamped with a command number far above the project's (an edit by hand,
QA's fault), so the suspend's route change is refused: the command fails and the route is
``uncertain``. The operator's rollback, sent with the ``ADMIN`` token (rollback is ``ADMIN``
only), takes the route back (QA-07) and restores the recorded state, so the site serves again.
The hosting retry sweep then issues ``<event_key>.retry-1``, which takes the site offline. Needs
``make proto-up proto-run``.

Cluster safety (CLAUDE.md section 0): ``kubectl`` is the project's pinned binary with the
project-local kubeconfig only, after checking that the context is ``<engine>-webblock-proto`` on
``https://127.0.0.1:<port>``; no other kubeconfig and no home directory is read.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any

import pytest
from tests.e2e._stack import ADMIN, PROTO, ROOT, Stack, until
from tests.e2e.faults.test_hosting_deletion import hosting_commands, live_site
from tests.integration.kind_safety import expected_context

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]

KUBECONFIG = PROTO / "kubeconfig"
KUBECTL = ROOT / "bin" / "kubectl"
CACHE = PROTO / "kubectl-cache"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(PROTO / "home")}


def kubectl(*args: str) -> str:
    def raw(argv: list[str]) -> tuple[int, str]:
        done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [str(KUBECTL), "--kubeconfig", str(KUBECONFIG), "--cache-dir", str(CACHE), *argv],
            capture_output=True,
            text=True,
            timeout=60,
            env=ENV,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr

    rc, out = raw(["config", "view", "--minify", "-o", "json"])
    assert rc == 0, out
    view = json.loads(out)
    assert view["current-context"] == expected_context(), view["current-context"]
    assert view["clusters"][0]["cluster"]["server"].startswith("https://127.0.0.1:")
    rc, out = raw(list(args))
    assert rc == 0, f"kubectl exited {rc}: {out[-1500:]}"
    return out


async def test_a_failed_suspend_is_retried_after_the_operator_recovers_the_route() -> None:
    stack = Stack()
    project, release = await live_site(stack, "e2e-qa03")
    kubectl(
        "-n",
        f"wb-{project}",
        "annotate",
        "httproute",
        f"route-{project}",
        "websites.webblock.internal/command-number=999999",
        "--overwrite",
    )
    key = await stack.simulate("entitlement-ended", "--project", str(project))

    def first_is(status: str) -> Any:
        return lambda rows: bool(rows) and rows[0][0] == key and rows[0][2] == status

    rows = await until(
        "the suspend failed", lambda: hosting_commands(stack, project), first_is("failed"), 180
    )
    assert rows[0][3] == "route_change_unconfirmed", rows
    deployment = await stack.deployment(project)
    assert (deployment["routingStatus"], deployment["hostingStatus"]) == ("uncertain", "online")

    # The operator's documented recovery: rollback naming the live release, with the ADMIN token
    # (rollback is ADMIN only, FR-50).
    rollback = await stack.post(
        project, release, "rollback", f"rollback-{uuid.uuid4().hex}", subject=ADMIN
    )
    assert rollback.status_code == 202, rollback.body
    done = await stack.wait_command(project, rollback.command_id, 300)
    assert done["status"] == "succeeded", done
    deployment = await stack.deployment(project)
    assert deployment["routingStatus"] == "stable", deployment

    # The retry sweep (RECONCILE_INTERVAL) re-issues the Payment intent.
    rows = await until(
        "the retried suspend succeeded",
        lambda: hosting_commands(stack, project),
        lambda r: len(r) >= 2 and r[1][0] == f"{key}.retry-1" and r[1][2] == "succeeded",
        240,
    )
    assert [r[1] for r in rows] == ["suspend_hosting", "suspend_hosting"]
    await until(
        "the offline page",
        lambda: stack.visit(project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )
    deployment = await stack.deployment(project)
    assert (deployment["hostingStatus"], deployment["routingStatus"]) == ("offline", "stable")


async def test_a_failed_resume_is_retried_and_brings_the_paying_site_back_online() -> None:
    """The mirror case: a suspended site whose resume fails stays offline after the operator's
    rollback (it restores the recorded state, offline), and the retry ``<event_key>.retry-1``
    brings it back online. The fault is the same hand edit of the route's command number, applied
    while the site is offline, so the resume's route change is refused deterministically."""
    stack = Stack()
    project, release = await live_site(stack, "e2e-qa03r")
    await stack.simulate("entitlement-ended", "--project", str(project))
    await until(
        "the offline page",
        lambda: stack.visit(project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )
    await until(
        "the suspend succeeded",
        lambda: hosting_commands(stack, project),
        lambda rows: len(rows) == 1 and rows[0][2] == "succeeded",
        60,
    )
    kubectl(
        "-n",
        f"wb-{project}",
        "annotate",
        "httproute",
        f"route-{project}",
        "websites.webblock.internal/command-number=999999",
        "--overwrite",
    )
    key = await stack.simulate("entitlement-restored", "--project", str(project))
    rows = await until(
        "the resume failed",
        lambda: hosting_commands(stack, project),
        lambda r: len(r) == 2 and r[1][0] == key and r[1][2] == "failed",
        180,
    )
    assert rows[1][3] == "route_change_unconfirmed", rows
    deployment = await stack.deployment(project)
    assert (deployment["routingStatus"], deployment["hostingStatus"]) == ("uncertain", "offline")

    rollback = await stack.post(
        project, release, "rollback", f"rollback-{uuid.uuid4().hex}", subject=ADMIN
    )
    assert rollback.status_code == 202, rollback.body
    done = await stack.wait_command(project, rollback.command_id, 300)
    assert done["status"] == "succeeded", done
    # The rollback restores the recorded state: the suspended site does not serve.
    status, body = await stack.visit(project)
    assert status == 503 and "offline" in body.lower()

    rows = await until(
        "the retried resume succeeded",
        lambda: hosting_commands(stack, project),
        lambda r: len(r) >= 3 and r[2][0] == f"{key}.retry-1" and r[2][2] == "succeeded",
        240,
    )
    assert rows[2][1] == "resume_hosting"
    await until(
        "v1 serving again",
        lambda: stack.visit(project),
        lambda r: r[0] == 200 and "sample-site version: v1" in r[1],
        120,
    )
    deployment = await stack.deployment(project)
    assert (deployment["hostingStatus"], deployment["routingStatus"]) == ("online", "stable")
    assert deployment["liveReleaseId"] == str(release)
