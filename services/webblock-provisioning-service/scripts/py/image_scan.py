"""image-scan: Grype on the runtime images against one verified offline database per run (task H1;
CLAUDE.md section 4: "runtime image has no unresolved high or critical findings without a
reviewed, time-bounded exception").

Run by ``make image-scan`` as ``python3.14 -I scripts/py/image_scan.py`` from the module root, after
``scripts/py/tools.sh grype`` has verified bin/grype against its pinned checksum. Standard library
only. The scanner is Grype, not Trivy: Trivy's release binaries and GitHub actions were compromised
in a supply-chain attack in March 2026.

1. The database. One download per run into a fresh temporary directory under bin/, HTTPS only,
   bounded in time; an earlier copy is never reused:
   - the listing https://grype.anchore.io/databases/v6/latest.json must be active, schema v6, name
     its archive by a plain file name, give its SHA-256, and have been built at most MAX_AGE_DAYS
     (14) days ago and not in the future;
   - the archive https://grype.anchore.io/databases/v6/<name> must match that SHA-256 (Anchore
     publishes no signature for it; authenticity rests on TLS to grype.anchore.io);
   - ``grype db import`` loads it into the temporary directory (Grype checks the database file
     against the digest inside the archive), and ``grype db status`` must then report it valid,
     with the schema and build time of the listing.
   Grype runs with HOME and XDG_* inside the temporary directory, automatic database updates and
   the update check off, and its own age validation set to the same 14 days.
2. The images. TARGETS maps each scanned image to its build folder. ``docker build`` builds each one
   named in IMAGE_SCAN_TARGETS (default: sample-site), ``docker save`` writes it into the temporary
   directory, Grype scans the archive (``docker-archive:``), and the image is removed afterwards.
   The Provisioning service's image (``provisioning``) is scanned by reference only: the image the
   cluster run pushed, ``PROV_IMAGE_ON_HOST`` in bin/kind-state/proto/provisioning/image.env
   (decision 0016 section 9 ruling 4). With the pinned buildx plugin installed (make kind-tools),
   ``docker build`` runs with the project-local DOCKER_CONFIG bin/kind-state/docker-cli on the
   daemon of the caller's current Docker context, so it uses BuildKit (decision 0016 section 9
   ruling 5; finding F-10).
   Instead of a local build, IMAGE_SCAN_REFS can name an image already built, pinned by digest,
   for example the one the stack's Jenkins and BuildKit pushed to the local registry
   (``sample-site=localhost:5001/webblock/sites/<projectId>@sha256:...``); Grype then reads it from
   the registry, trusting IMAGE_SCAN_REGISTRY_CA when given. That is the image the platform
   deploys, and it needs no BuildKit on the host (the sample site's ``COPY --chmod`` does).
3. The gate. Every High or Critical match fails unless scripts/py/image-scan-exceptions.toml holds
   an exception naming it (image, vulnerability, package) with a reason, a reviewer, an expiry date
   (valid through that day, at most MAX_EXCEPTION_DAYS ahead) and a follow-up. An expired exception
   fails whether or not it still matches; one that matches nothing is reported for removal. Lower
   severities are listed by count only.

Environment: IMAGE_SCAN_TARGETS (space-separated names from TARGETS); IMAGE_SCAN_REFS
(space-separated name=reference, each reference pinned by digest); IMAGE_SCAN_REGISTRY_CA (a CA
file for those references); IMAGE_SCAN_MAX_AGE_DAYS
(default 14, for testing the failure path only). Never reads .env. Exit status 0 when nothing
unaccepted was found, 1 on a finding, a refused database or a failed build, 2 on a usage error.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GRYPE = ROOT / "bin" / "grype"
EXCEPTIONS = ROOT / "scripts" / "py" / "image-scan-exceptions.toml"
DB_BASE_URL = "https://grype.anchore.io/databases/v6"
MAX_AGE_DAYS = 14
MAX_EXCEPTION_DAYS = 30
CLOCK_SKEW = timedelta(days=1)
FAILING = ("Critical", "High")
DOWNLOAD_SECONDS = 900
BUILD_SECONDS = 1800
SCAN_SECONDS = 900
ARCHIVE = re.compile(r"^vulnerability-db_v6[A-Za-z0-9._:-]*\.tar\.zst$")
REFERENCE = re.compile(r"^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$")
TARGETS = {
    # The sample site the prototype deploys (nginx-unprivileged and two static files).
    "sample-site": ROOT / "deploy" / "prototype" / "sample-site" / "site",
    # The generated clothing shop of the demo (Next.js standalone on Node.js 24); its build needs
    # registry.npmjs.org.
    "clothing-shop": ROOT / "deploy" / "prototype" / "demo-sites" / "clothing-shop",
    # The Provisioning service (deploy/image/Dockerfile, built by deploy/image/build.sh from an
    # allow-list context); scanned by reference only (REF_ONLY).
    "provisioning": ROOT / "deploy" / "image",
}
DEFAULT_TARGETS = ("sample-site",)
REF_ONLY = frozenset({"provisioning"})
DOCKER_CLI = ROOT / "bin" / "kind-state" / "docker-cli"


class ScanError(Exception):
    pass


@dataclass(frozen=True)
class Listing:
    archive: str
    sha256: str
    schema: str
    built: datetime


@dataclass(frozen=True)
class Match:
    ident: str
    severity: str
    package: str
    version: str
    fixed_in: str


@dataclass(frozen=True)
class Accepted:
    image: str
    vulnerability: str
    package: str
    reason: str
    reviewer: str
    expires: date
    follow_up: str


def parse_time(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def validate_listing(document: dict[str, Any], now: datetime, max_age_days: int) -> Listing:
    if document.get("status") != "active":
        raise ScanError(f"the database listing is not active (status {document.get('status')!r})")
    schema = str(document.get("schemaVersion", ""))
    if not schema.startswith("v6."):
        raise ScanError(f"the database schema {schema!r} is not v6, which grype 0.119 reads")
    archive = str(document.get("path", ""))
    if not ARCHIVE.match(archive):
        raise ScanError(f"unexpected archive name in the listing: {archive!r}")
    checksum = str(document.get("checksum", ""))
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", checksum)
    if not match:
        raise ScanError(f"the listing gives no SHA-256 for the archive ({checksum!r})")
    try:
        built = parse_time(str(document.get("built", "")))
    except ValueError:
        raise ScanError(
            f"the listing's built date {document.get('built')!r} is not a timestamp"
        ) from None
    if built > now + CLOCK_SKEW:
        raise ScanError(f"the database was built {built.isoformat()}, in the future")
    if now - built > timedelta(days=max_age_days):
        raise ScanError(
            f"the database was built {built.isoformat()}, older than the limit of "
            f"{max_age_days} days"
        )
    return Listing(archive, match.group(1), schema, built)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_archive(archive: Path, listing: Listing) -> None:
    got = sha256(archive)
    if got != listing.sha256:
        raise ScanError(
            f"SHA-256 mismatch for {listing.archive}: got {got}, listed {listing.sha256}"
        )


def check_status(status: dict[str, Any], listing: Listing) -> None:
    """Grype's view of the imported database must be valid and be the listed one."""
    if status.get("valid") is not True:
        raise ScanError("grype reports the imported database as not valid")
    if status.get("schemaVersion") != listing.schema:
        raise ScanError(
            f"grype reports schema {status.get('schemaVersion')!r}, the listing {listing.schema}"
        )
    try:
        built = parse_time(str(status.get("built", "")))
    except ValueError:
        built = None
    if built != listing.built:
        raise ScanError(
            f"grype reports the database built {status.get('built')!r}, the listing "
            f"{listing.built.isoformat()}"
        )


def read_matches(report: dict[str, Any]) -> list[Match]:
    found = []
    for entry in report.get("matches") or []:
        vulnerability, artifact = entry["vulnerability"], entry["artifact"]
        fixed = (vulnerability.get("fix") or {}).get("versions") or []
        found.append(
            Match(
                str(vulnerability["id"]),
                str(vulnerability.get("severity") or "Unknown"),
                str(artifact["name"]),
                str(artifact.get("version", "")),
                ", ".join(map(str, fixed)),
            )
        )
    return found


def load_exceptions(path: Path) -> list[Accepted]:
    if not path.is_file():
        return []
    try:
        entries = tomllib.loads(path.read_text()).get("exception", [])
    except tomllib.TOMLDecodeError as error:
        raise ScanError(f"{path.name} is not valid TOML: {error}") from None
    accepted = []
    for n, entry in enumerate(entries, start=1):
        where = f"{path.name} exception {n}"
        for field in ("image", "vulnerability", "package", "reason", "reviewer", "follow_up"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ScanError(f"{where}: {field} is missing or empty")
        expires = entry.get("expires")
        if not isinstance(expires, date) or isinstance(expires, datetime):
            raise ScanError(f"{where}: expires must be a date (YYYY-MM-DD, unquoted)")
        accepted.append(
            Accepted(
                entry["image"],
                entry["vulnerability"],
                entry["package"],
                entry["reason"],
                entry["reviewer"],
                expires,
                entry["follow_up"],
            )
        )
    return accepted


def parse_refs(value: str) -> dict[str, str]:
    """IMAGE_SCAN_REFS: name=reference pairs, each a known image pinned by digest."""
    refs: dict[str, str] = {}
    for item in value.split():
        name, sep, ref = item.partition("=")
        if not sep or not ref:
            raise ScanError(f"IMAGE_SCAN_REFS entries are name=reference, not {item!r}")
        if name not in TARGETS:
            raise ScanError(f"IMAGE_SCAN_REFS names the unknown image {name!r}")
        if not REFERENCE.match(ref):
            raise ScanError(f"IMAGE_SCAN_REFS: {name} must be pinned by digest (got {ref!r})")
        if name in refs:
            raise ScanError(f"IMAGE_SCAN_REFS names {name} twice")
        refs[name] = ref
    return refs


def check_exception_images(accepted: Iterable[Accepted], known: set[str]) -> None:
    for item in accepted:
        if item.image not in known:
            raise ScanError(f"an exception names the unknown image {item.image!r}")


def evaluate(
    found: dict[str, list[Match]], accepted: list[Accepted], today: date
) -> tuple[list[str], list[str]]:
    """The failures and the accepted or obsolete items, as lines to print."""
    failures: list[str] = []
    notes: list[str] = []
    by_key = {(a.image, a.vulnerability, a.package): a for a in accepted}
    used: set[tuple[str, str, str]] = set()
    for item in accepted:
        label = f"{item.image} {item.vulnerability} {item.package}"
        if item.expires < today:
            failures.append(f"{label}: the exception expired on {item.expires.isoformat()}")
        elif item.expires > today + timedelta(days=MAX_EXCEPTION_DAYS):
            failures.append(
                f"{label}: the exception expires {item.expires.isoformat()}, more than "
                f"{MAX_EXCEPTION_DAYS} days ahead"
            )
    for image, matches in found.items():
        seen: set[tuple[str, str, str]] = set()
        for m in matches:
            if m.severity not in FAILING or (m.ident, m.package, m.version) in seen:
                continue
            seen.add((m.ident, m.package, m.version))
            label = f"{image}: {m.severity} {m.ident} in {m.package} {m.version}"
            key = (image, m.ident, m.package)
            granted = by_key.get(key)
            if granted is None:
                fix = f" (fixed in {m.fixed_in})" if m.fixed_in else ""
                failures.append(f"{label}: no exception{fix}")
                continue
            used.add(key)
            if today <= granted.expires <= today + timedelta(days=MAX_EXCEPTION_DAYS):
                notes.append(
                    f"{label}: accepted until {granted.expires.isoformat()} by {granted.reviewer}: "
                    f"{granted.reason} (follow-up: {granted.follow_up})"
                )
    for key, item in by_key.items():
        if key not in used and item.expires >= today:
            notes.append(
                f"{item.image} {item.vulnerability} {item.package}: the exception matches no "
                "finding; remove it"
            )
    return failures, notes


def run_command(command: list[str], timeout: int, what: str, **kwargs: Any) -> None:
    result = subprocess.run(command, check=False, timeout=timeout, **kwargs)  # noqa: S603 - fixed argv
    if result.returncode != 0:
        raise ScanError(f"{what} failed (exit {result.returncode})")


def curl(url: str, out: Path, seconds: int) -> None:
    command = [
        "curl", "-sS", "--fail", "--proto", "=https", "--proto-redir", "=https", "--tlsv1.2",
        "--connect-timeout", "20", "--max-time", str(seconds), "--retry", "3", "--retry-delay",
        "5", "--retry-all-errors", "-o", str(out), url,
    ]  # fmt: skip
    run_command(command, seconds * 4 + 60, f"download of {url}")


def grype_env(work: Path, registry_ca: Path | None = None) -> dict[str, str]:
    home = work / "home"
    ca = {"GRYPE_REGISTRY_CA_CERT": str(registry_ca)} if registry_ca else {}
    return ca | {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(work / "tmp"),
        "GRYPE_DB_CACHE_DIR": str(work / "db"),
        "GRYPE_DB_AUTO_UPDATE": "false",
        "GRYPE_DB_VALIDATE_AGE": "true",
        "GRYPE_DB_MAX_ALLOWED_BUILT_AGE": f"{MAX_AGE_DAYS * 24}h",
        "GRYPE_DB_REQUIRE_UPDATE_CHECK": "false",
        "GRYPE_CHECK_FOR_APP_UPDATE": "false",
    }


def prepare_database(work: Path, max_age_days: int) -> Listing:
    listing_file = work / "latest.json"
    print(f"image-scan: downloading {DB_BASE_URL}/latest.json", flush=True)
    curl(f"{DB_BASE_URL}/latest.json", listing_file, 60)
    try:
        document = json.loads(listing_file.read_text())
    except ValueError:
        raise ScanError("the database listing is not JSON") from None
    if not isinstance(document, dict):
        raise ScanError("the database listing is not a JSON object")
    listing = validate_listing(document, datetime.now(UTC), max_age_days)
    archive = work / listing.archive
    print(f"image-scan: downloading {DB_BASE_URL}/{listing.archive}", flush=True)
    curl(f"{DB_BASE_URL}/{listing.archive}", archive, DOWNLOAD_SECONDS)
    check_archive(archive, listing)
    env = grype_env(work)
    run_command([str(GRYPE), "db", "import", str(archive)], 600, "grype db import", env=env)
    archive.unlink()
    status = subprocess.run(  # noqa: S603 - fixed argv
        [str(GRYPE), "db", "status", "-o", "json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if status.returncode != 0:
        raise ScanError(f"grype db status failed (exit {status.returncode})")
    try:
        check_status(json.loads(status.stdout), listing)
    except ValueError:
        raise ScanError("grype db status wrote no JSON") from None
    print(
        f"image-scan: verified database {listing.archive}: schema {listing.schema}, built "
        f"{listing.built.isoformat()} (limit {max_age_days} days), sha256 {listing.sha256}"
    )
    return listing


def docker_env(docker: str) -> dict[str, str] | None:
    """The environment of the local builds: with the pinned buildx plugin installed (make
    kind-tools), the project-local DOCKER_CONFIG that holds it and the daemon of the caller's
    current Docker context, so that ``docker build`` uses BuildKit; without it, None (the caller's
    own environment)."""
    if not (DOCKER_CLI / "cli-plugins" / "docker-buildx").is_file():
        return None
    host = (
        os.environ.get("DOCKER_HOST")
        or subprocess.run(  # noqa: S603 - fixed argv
            [docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    )
    return {**os.environ, "DOCKER_CONFIG": str(DOCKER_CLI), "DOCKER_HOST": host}


def build_and_scan(name: str, work: Path, docker: str) -> list[Match]:
    env = docker_env(docker)
    tag = f"webblock-image-scan/{name}:{uuid.uuid4().hex[:12]}"
    archive = work / f"{name}.tar"
    report = work / f"{name}.json"
    print(f"image-scan: {name}: docker build {TARGETS[name].relative_to(ROOT)}", flush=True)
    try:
        run_command(
            [docker, "build", "--quiet", "--tag", tag, str(TARGETS[name])],
            BUILD_SECONDS,
            f"{name}: docker build",
            stdout=subprocess.DEVNULL,
            env=env,
        )
        run_command(
            [docker, "save", "--output", str(archive), tag], 600, f"{name}: docker save", env=env
        )
    finally:
        subprocess.run(  # noqa: S603 - fixed argv; removing an image that was never built is harmless
            [docker, "image", "rm", "--force", tag],
            check=False,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    run_command(
        [str(GRYPE), f"docker-archive:{archive}", "--output", "json", "--file", str(report)],
        SCAN_SECONDS,
        f"{name}: grype",
        env=grype_env(work),
    )
    archive.unlink()
    try:
        document = json.loads(report.read_text())
    except OSError, ValueError:
        raise ScanError(f"{name}: grype wrote no readable report") from None
    if document.get("source", {}).get("type") != "image":
        raise ScanError(f"{name}: grype did not scan an image")
    return read_matches(document)


def scan_reference(name: str, ref: str, work: Path, registry_ca: Path | None) -> list[Match]:
    report = work / f"{name}.json"
    print(f"image-scan: {name}: scanning {ref} from its registry (no local build)", flush=True)
    run_command(
        [str(GRYPE), f"registry:{ref}", "--platform", "linux/amd64", "--output", "json",
         "--file", str(report)],
        SCAN_SECONDS,
        f"{name}: grype",
        env=grype_env(work, registry_ca),
    )  # fmt: skip
    try:
        document = json.loads(report.read_text())
    except OSError, ValueError:
        raise ScanError(f"{name}: grype wrote no readable report") from None
    if document.get("source", {}).get("type") != "image":
        raise ScanError(f"{name}: grype did not scan an image")
    return read_matches(document)


def run(
    work: Path,
    names: list[str],
    max_age_days: int,
    docker: str | None,
    refs: dict[str, str],
    registry_ca: Path | None,
) -> int:
    accepted = load_exceptions(EXCEPTIONS)
    check_exception_images(accepted, set(TARGETS))
    for name in names:
        if name in REF_ONLY and name not in refs:
            raise ScanError(
                f"{name}: scan the image the cluster run pushed: IMAGE_SCAN_REFS={name}=<the "
                "PROV_IMAGE_ON_HOST of bin/kind-state/proto/provisioning/image.env>"
            )
    for folder in ("home", "tmp"):
        (work / folder).mkdir()
    prepare_database(work, max_age_days)
    found: dict[str, list[Match]] = {}
    for name in names:
        if name in refs:
            found[name] = scan_reference(name, refs[name], work, registry_ca)
        elif docker is None:
            raise ScanError(f"{name}: no IMAGE_SCAN_REFS entry and no docker to build it")
        else:
            found[name] = build_and_scan(name, work, docker)
    for name, matches in found.items():
        counts = Counter(
            m.severity for m in {(m.ident, m.package, m.version): m for m in matches}.values()
        )
        summary = ", ".join(
            f"{counts[s]} {s}"
            for s in ("Critical", "High", "Medium", "Low", "Negligible", "Unknown")
            if counts[s]
        )
        print(f"image-scan: {name}: {summary or 'no finding'}")
    failures, notes = evaluate(found, accepted, datetime.now(UTC).date())
    for note in notes:
        print(f"image-scan: {note}")
    for failure in failures:
        print(f"image-scan: FAILED: {failure}")
    if failures:
        print(
            f"image-scan: FAILED: {len(failures)} unaccepted findings or exceptions",
            file=sys.stderr,
        )
        return 1
    print("image-scan: no unaccepted High or Critical finding")
    return 0


def main() -> int:
    if Path.cwd().resolve() != ROOT:
        print(f"image-scan: run from the module root {ROOT}", file=sys.stderr)
        return 2
    names = os.environ.get("IMAGE_SCAN_TARGETS", " ".join(DEFAULT_TARGETS)).split()
    unknown = [n for n in names if n not in TARGETS]
    if not names or unknown:
        print(
            f"image-scan: IMAGE_SCAN_TARGETS must name images from {', '.join(TARGETS)}",
            file=sys.stderr,
        )
        return 2
    try:
        max_age_days = int(os.environ.get("IMAGE_SCAN_MAX_AGE_DAYS", str(MAX_AGE_DAYS)))
    except ValueError:
        print("image-scan: IMAGE_SCAN_MAX_AGE_DAYS is not a whole number", file=sys.stderr)
        return 2
    try:
        refs = parse_refs(os.environ.get("IMAGE_SCAN_REFS", ""))
    except ScanError as error:
        print(f"image-scan: {error}", file=sys.stderr)
        return 2
    ca_value = os.environ.get("IMAGE_SCAN_REGISTRY_CA", "")
    registry_ca = Path(ca_value).resolve() if ca_value else None
    if registry_ca is not None and not registry_ca.is_file():
        print("image-scan: IMAGE_SCAN_REGISTRY_CA is not a file", file=sys.stderr)
        return 2
    docker = shutil.which("docker")
    if not GRYPE.is_file() or (docker is None and set(names) - set(refs)):
        print("image-scan: needs bin/grype (make py-tools), and docker to build", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix=".image-scan.", dir=ROOT / "bin"))
    try:
        return run(work, names, max_age_days, docker, refs, registry_ca)
    except ScanError as error:
        print(f"image-scan: FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
