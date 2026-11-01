"""The example manifests in deploy/prototype/manifests/ are exactly the renderer's output
(decision 0014 section 20): scripts/prototype/render-examples.py renders them, and any difference,
a missing file or an extra one fails here. Regenerate with
``.venv/bin/python scripts/prototype/render-examples.py``; never edit the files by hand.

The script runs in a subprocess of this interpreter with a fixed argument list and a poisoned
``KUBECONFIG`` and ``HOME``; it contacts no cluster."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prototype" / "render-examples.py"
MANIFESTS = ROOT / "deploy" / "prototype" / "manifests"
NAMES = ("tenant.yaml", "release.yaml", "route-release.yaml", "route-offline.yaml", "preview.yaml")


def run_script(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "poisoned-home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "KUBECONFIG": str(home / "never-a-kubeconfig"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(  # noqa: S603 - this interpreter and a fixed script path, no shell
        [sys.executable, "-X", "dev", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("rendered")
    done = run_script(out, "--out", str(out))
    assert done.returncode == 0, done.stderr
    return out


@pytest.mark.parametrize("name", NAMES)
def test_each_example_file_is_the_renderers_output(rendered: Path, name: str) -> None:
    committed = MANIFESTS / name
    assert committed.is_file(), f"{committed} is missing: run scripts/prototype/render-examples.py"
    assert committed.read_text() == (rendered / name).read_text(), (
        f"{committed} differs from the renderer's output: regenerate it, never edit it"
    )


def test_the_example_folder_holds_exactly_the_generated_files(rendered: Path) -> None:
    generated = {p.name for p in rendered.glob("*.yaml")}
    committed = {p.name for p in MANIFESTS.glob("*.yaml")}

    assert generated == set(NAMES)
    assert committed == generated


def test_check_mode_passes_on_the_committed_files_and_fails_on_an_edited_copy(
    tmp_path: Path,
) -> None:
    assert run_script(tmp_path, "--check", "--out", str(MANIFESTS)).returncode == 0
    edited = tmp_path / "edited"
    edited.mkdir()
    for name in NAMES:
        (edited / name).write_text((MANIFESTS / name).read_text())
    release = edited / "release.yaml"
    release.write_text(release.read_text().replace("periodSeconds: 5", "periodSeconds: 3"))

    done = run_script(tmp_path, "--check", "--out", str(edited))

    assert done.returncode == 1
    assert "release.yaml differs" in done.stderr


def test_the_examples_carry_the_documented_values(rendered: Path) -> None:
    """The files are the prototype's shapes: the /healthz readiness probe, the fence on every
    per-site object, the stack's registry and site domain."""
    release = list(yaml.safe_load_all((rendered / "release.yaml").read_text()))
    assert [d["kind"] for d in release] == ["Deployment", "Service"]
    container = release[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["image"].startswith(
        "webblock-proto-registry.test:5000/webblock/sites/11111111-1111-4111-8111-111111111111@"
    )
    tenant = list(yaml.safe_load_all((rendered / "tenant.yaml").read_text()))
    assert [d["kind"] for d in tenant] == [
        "Namespace",
        "ResourceQuota",
        "LimitRange",
        "ReferenceGrant",
        "RoleBinding",
        "Secret",
    ]
    for name, number in (
        ("release.yaml", "1"),
        ("route-release.yaml", "1"),
        ("route-offline.yaml", "2"),
        ("preview.yaml", "1"),
    ):
        for doc in yaml.safe_load_all((rendered / name).read_text()):
            annotations = doc["metadata"]["annotations"]
            assert annotations["websites.webblock.internal/command-number"] == number
    # The site's host is <subdomain>.<site domain> (decision 0023 G18), the preview's
    # <subdomain>-preview.<site domain> (FR-20), the namespace carries the sub-domain label the
    # route policy checks (decision 0023 G18a).
    route = yaml.safe_load((rendered / "route-release.yaml").read_text())
    assert route["spec"]["hostnames"] == ["example-shop.sites.webblock.test"]
    preview = yaml.safe_load((rendered / "preview.yaml").read_text())
    assert preview["spec"]["hostnames"] == ["example-shop-preview.sites.webblock.test"]
    assert tenant[0]["metadata"]["labels"]["websites.webblock.internal/subdomain"] == "example-shop"


def test_other_ids_render_the_same_shapes_for_a_smoke_test(tmp_path: Path) -> None:
    project = "0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11"
    image = f"webblock-proto-registry.test:5000/webblock/sites/{project}@sha256:" + "ab" * 32
    out = tmp_path / "smoke"
    done = run_script(
        tmp_path,
        "--out",
        str(out),
        "--project-id",
        project,
        "--subdomain",
        "smoke-shop",
        "--image",
        image,
    )

    assert done.returncode == 0, done.stderr
    text = (out / "release.yaml").read_text()
    assert image in text
    assert "11111111-1111-4111-8111-111111111111" not in text
    route = yaml.safe_load((out / "route-release.yaml").read_text())
    assert route["spec"]["hostnames"] == ["smoke-shop.sites.webblock.test"]


def test_an_image_of_another_project_is_refused(tmp_path: Path) -> None:
    image = (
        "webblock-proto-registry.test:5000/webblock/sites/"
        "0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11@sha256:" + "ab" * 32
    )
    done = run_script(tmp_path, "--out", str(tmp_path / "x"), "--image", image)

    assert done.returncode != 0
    assert "InvalidManifestInput" in done.stderr
