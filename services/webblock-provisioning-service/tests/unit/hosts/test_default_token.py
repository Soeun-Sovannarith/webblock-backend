"""Every host refuses a mounted default ServiceAccount token before anything else (decision 0016
section 9 ruling 2): the service's pods set automountServiceAccountToken: false, so a token at the
default path means a pod spec that must not run. One line, exit 2, nothing read before."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from provisioning.hosts.shared import runtime

# Each host's main(), imported when the test runs, not at collection: importing the worker or the
# bootstrap loads kubernetes_asyncio, and test_client.py compares the library's default kubeconfig
# path with the folder's poison, which only holds if the folder's conftest ran first. (Safety does
# not depend on it: tests/conftest.py already poisons KUBECONFIG.)
HOSTS: Final = ("api", "worker", "build_worker", "bootstrap")


@pytest.mark.parametrize("host", HOSTS)
def test_a_mounted_default_token_stops_every_host_first(
    host: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = tmp_path / "token"
    token.write_text("a token the process must never use")
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", token)
    assert _main_of(host)() == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1, err
    assert "a default ServiceAccount token is mounted" in err


def _main_of(host: str) -> Callable[[], int]:
    if host == "api":
        from provisioning.hosts.api.__main__ import main
    elif host == "worker":
        from provisioning.hosts.worker.__main__ import main
    elif host == "build_worker":
        from provisioning.hosts.build_worker.__main__ import main
    else:
        from provisioning.hosts.bootstrap.__main__ import main
    return main


def test_the_default_token_folder_alone_stops_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable folder or a dangling link hides the token file from exists(); the automount
    folder itself is the sign that the pod spec has the automount on."""
    folder = tmp_path / "serviceaccount"
    folder.mkdir()
    (folder / "token").symlink_to(tmp_path / "dangling")
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", folder / "token")
    out = io.StringIO()
    assert runtime.default_token_refused(out, "provisioning-api") is True
    assert "a default ServiceAccount token is mounted" in out.getvalue()
