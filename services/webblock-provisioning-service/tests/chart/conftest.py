"""Fixtures of the chart suite (make chart-check): the pinned helm must be installed (make
kind-tools); a missing helm fails the suite, never skips it."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.chart._render import HELM, VALUES, Doc, documents, helm_template


@pytest.fixture(scope="session")
def helm_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HELM.is_file():
        pytest.fail("bin/helm is missing: run make kind-tools")
    return tmp_path_factory.mktemp("helm")


@pytest.fixture(scope="session")
def rendered(helm_home: Path) -> list[Doc]:
    done = helm_template(helm_home, VALUES)
    assert done.returncode == 0, done.stderr
    return documents(done.stdout)
