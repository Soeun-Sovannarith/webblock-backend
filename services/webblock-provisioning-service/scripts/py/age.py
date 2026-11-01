"""py-age: the 14-day version-age rule (decision 0013 section 9, S6; ruling 10.5; task H1).

Run by ``make py-age`` as ``python3.14 -I scripts/py/age.py`` from the module root; standard
library only. Every pin must be at least MIN_AGE_DAYS (14) days old:

- every package uv.lock takes from PyPI, dated by the newest of its locked files as PyPI's JSON API
  reports the upload (https://pypi.org/pypi/<name>/<version>/json). uv's exclude-newer works per
  file too. Every locked file (by SHA-256) must be listed there with the upload time the lock
  recorded; anything else fails;
- every tool version pinned for scripts/py/tools.sh (scripts/py/versions.sh: uv, osv-scanner,
  goose, grype, protoc) and for scripts/kind-tools.sh (deploy/kind/versions.sh: kind, kubectl, helm,
  crane) and docker-buildx, dated by its GitHub release
  (https://api.github.com/repos/<repo>/releases/tags/<tag>);
- the base image of the service's runtime image (``deploy/image/versions.sh``), dated by the
  publication recorded with its pin (decision 0016 section 9 ruling 4); no lookup.

A younger pin passes only with a waiver in scripts/py/age-waivers.toml that names it exactly
(kind, name, version) with a reason, who approved it and an expiry date (valid through that day).
An expired waiver fails, whether or not a pin still needs it, so the file never outlives its need;
a current waiver whose pin has aged past the limit is reported for removal.

Every lookup is HTTPS only (redirects too), bounded by LOOKUP_SECONDS and retried once; a failed
lookup fails the gate. Never reads .env. Exit status 0 when every pin is old enough or waived, 1
otherwise, 2 on a usage error.
"""

import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "uv.lock"
WAIVERS = ROOT / "scripts" / "py" / "age-waivers.toml"
MIN_AGE_DAYS = 14
LOOKUP_SECONDS = 20
WORKERS = 8
USER_AGENT = "webblock-provisioning-py-age"

# The version files and the tools whose pins they hold: (name, variable, GitHub repository).
TOOL_FILES: tuple[tuple[Path, tuple[tuple[str, str, str], ...]], ...] = (
    (
        ROOT / "scripts" / "py" / "versions.sh",
        (
            ("uv", "UV_VERSION", "astral-sh/uv"),
            ("osv-scanner", "OSV_SCANNER_VERSION", "google/osv-scanner"),
            ("goose", "GOOSE_VERSION", "pressly/goose"),
            ("grype", "GRYPE_VERSION", "anchore/grype"),
            ("protoc", "PROTOC_VERSION", "protocolbuffers/protobuf"),
        ),
    ),
    (
        ROOT / "deploy" / "kind" / "versions.sh",
        (
            ("kind", "KIND_VERSION", "kubernetes-sigs/kind"),
            ("k3d", "K3D_VERSION", "k3d-io/k3d"),
            ("kubectl", "KUBECTL_VERSION", "kubernetes/kubernetes"),
            ("helm", "HELM_VERSION", "helm/helm"),
            ("crane", "CRANE_VERSION", "google/go-containerregistry"),
            ("docker-buildx", "BUILDX_VERSION", "docker/buildx"),
        ),
    ),
)

# The base images, pinned by digest with their publication date: (name, reference variable, date
# variable). The date is recorded when the pin is chosen
# (.claude/docs/runbooks/prototype-in-cluster.md) and is not looked up here.
IMAGE_FILES: tuple[tuple[Path, tuple[tuple[str, str, str], ...]], ...] = (
    (
        ROOT / "deploy" / "image" / "versions.sh",
        (("python", "BASE_IMAGE", "BASE_IMAGE_PUBLISHED"),),
    ),
)
IMAGE_REFERENCE = re.compile(r"[^@\s]+:([^:@/\s]+)@(sha256:[0-9a-f]{64})")

Fetch = Callable[[str], dict[str, Any]]


class AgeError(Exception):
    pass


@dataclass(frozen=True)
class LockedPin:
    name: str
    version: str
    files: dict[str, datetime | None]  # SHA-256 of each locked file -> upload time in the lock


@dataclass(frozen=True)
class ToolPin:
    name: str
    version: str
    repo: str


@dataclass(frozen=True)
class Dated:
    kind: str  # "pypi", "tool" or "image"
    name: str
    version: str
    published: datetime


@dataclass(frozen=True)
class Waiver:
    kind: str
    name: str
    version: str
    reason: str
    approved_by: str
    expires: date


def parse_time(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(lock: Path) -> list[LockedPin]:
    """Each package uv.lock takes from a registry, with the SHA-256 and upload time of its files."""
    pins = []
    for package in tomllib.loads(lock.read_text()).get("package", []):
        source = package.get("source")
        if not isinstance(source, dict) or "registry" not in source:
            continue
        entries = list(package.get("wheels", []))
        if "sdist" in package:
            entries.append(package["sdist"])
        files: dict[str, datetime | None] = {}
        for entry in entries:
            digest = str(entry["hash"]).removeprefix("sha256:")
            uploaded = entry.get("upload-time")
            files[digest] = parse_time(uploaded) if isinstance(uploaded, str) else None
        if not files:
            raise AgeError(f"{package['name']} {package['version']}: uv.lock lists no file")
        pins.append(LockedPin(package["name"], package["version"], files))
    return pins


def date_pypi_pin(pin: LockedPin, fetch: Fetch) -> Dated:
    document = fetch(f"https://pypi.org/pypi/{pin.name}/{pin.version}/json")
    uploads: dict[str, datetime] = {}
    for entry in document.get("urls") or []:
        uploads[str(entry["digests"]["sha256"])] = parse_time(str(entry["upload_time_iso_8601"]))
    unknown = [d for d in pin.files if d not in uploads]
    if unknown:
        raise AgeError(f"{pin.name} {pin.version}: {len(unknown)} locked files are not on PyPI")
    for digest, recorded in pin.files.items():
        if recorded is not None and abs(uploads[digest] - recorded) > timedelta(seconds=1):
            raise AgeError(
                f"{pin.name} {pin.version}: PyPI dates a locked file "
                f"{uploads[digest].isoformat()}, uv.lock {recorded.isoformat()}"
            )
    return Dated("pypi", pin.name, pin.version, max(uploads[d] for d in pin.files))


def read_shell_values(path: Path) -> dict[str, str]:
    """Plain NAME=value and NAME="value" assignments of a version file, without running it."""
    values = {}
    for line in path.read_text().splitlines():
        match = re.match(r'^([A-Z][A-Z0-9_]*)=(?:"([^"$`]*)"|([^\s"$`]*))\s*(?:#.*)?$', line)
        if match:
            values[match.group(1)] = (
                match.group(2) if match.group(2) is not None else match.group(3)
            )
    return values


def tool_pins(files: Iterable[tuple[Path, tuple[tuple[str, str, str], ...]]]) -> list[ToolPin]:
    pins = []
    for path, tools in files:
        values = read_shell_values(path)
        for name, variable, repo in tools:
            if not values.get(variable):
                raise AgeError(f"{variable} is not set in {path.name}")
            pins.append(ToolPin(name, values[variable], repo))
    return pins


def date_tool_pin(pin: ToolPin, fetch: Fetch) -> Dated:
    document = fetch(f"https://api.github.com/repos/{pin.repo}/releases/tags/{pin.version}")
    published = document.get("published_at")
    if document.get("tag_name") != pin.version or not isinstance(published, str):
        raise AgeError(f"{pin.name} {pin.version}: GitHub lists no published release of that tag")
    return Dated("tool", pin.name, pin.version, parse_time(published))


def image_pins(files: Iterable[tuple[Path, tuple[tuple[str, str, str], ...]]]) -> list[Dated]:
    """Each base image as ``<tag>@sha256:<digest>``, dated by its recorded publication."""
    pins = []
    for path, images in files:
        values = read_shell_values(path)
        for name, ref_variable, date_variable in images:
            for variable in (ref_variable, date_variable):
                if not values.get(variable):
                    raise AgeError(f"{variable} is not set in {path.name}")
            match = IMAGE_REFERENCE.fullmatch(values[ref_variable])
            if match is None:
                raise AgeError(f"{ref_variable} in {path.name} is not <name>:<tag>@sha256:<digest>")
            try:
                published = parse_time(values[date_variable])
            except ValueError:
                raise AgeError(f"{date_variable} in {path.name} is not a timestamp") from None
            pins.append(Dated("image", name, f"{match.group(1)}@{match.group(2)}", published))
    return pins


def load_waivers(path: Path) -> list[Waiver]:
    if not path.is_file():
        return []
    try:
        entries = tomllib.loads(path.read_text()).get("waiver", [])
    except tomllib.TOMLDecodeError as error:
        raise AgeError(f"{path.name} is not valid TOML: {error}") from None
    waivers = []
    for n, entry in enumerate(entries, start=1):
        where = f"{path.name} waiver {n}"
        if entry.get("kind") not in ("pypi", "tool", "image"):
            raise AgeError(f"{where}: kind must be pypi, tool or image")
        for field in ("name", "version", "reason", "approved_by"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise AgeError(f"{where}: {field} is missing or empty")
        if not isinstance(entry.get("expires"), date) or isinstance(entry["expires"], datetime):
            raise AgeError(f"{where}: expires must be a date (YYYY-MM-DD, unquoted)")
        waivers.append(
            Waiver(
                entry["kind"],
                entry["name"],
                entry["version"],
                entry["reason"],
                entry["approved_by"],
                entry["expires"],
            )
        )
    return waivers


def evaluate(
    pins: list[Dated], waivers: list[Waiver], now: datetime, min_days: int
) -> tuple[list[str], list[str]]:
    """The failures and the waived pins, as lines to print."""
    failures: list[str] = []
    notes: list[str] = []
    by_key = {(w.kind, normalize(w.name), w.version): w for w in waivers}
    used: set[tuple[str, str, str]] = set()
    for pin in pins:
        days = (now - pin.published) / timedelta(days=1)
        if days >= min_days:
            continue
        label = f"{pin.kind} {pin.name} {pin.version}"
        key = (pin.kind, normalize(pin.name), pin.version)
        waiver = by_key.get(key)
        if waiver is None:
            failures.append(
                f"{label}: published {pin.published.date().isoformat()}, {days:.1f} days ago, "
                f"younger than {min_days} days and no waiver"
            )
            continue
        used.add(key)
        if waiver.expires < now.date():
            failures.append(f"{label}: its waiver expired on {waiver.expires.isoformat()}")
        else:
            notes.append(
                f"{label}: {days:.1f} days old, waived until {waiver.expires.isoformat()}: "
                f"{waiver.reason} ({waiver.approved_by})"
            )
    for key, waiver in by_key.items():
        if key not in used:
            label = f"{waiver.kind} {waiver.name} {waiver.version}"
            if waiver.expires < now.date():
                failures.append(
                    f"{label}: its waiver expired on {waiver.expires.isoformat()}; remove it"
                )
            else:
                notes.append(f"{label}: the waiver is no longer needed (no young pin); remove it")
    return failures, notes


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            raise AgeError(f"refused a redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url: str) -> dict[str, Any]:
    """GET url (https only) as JSON, bounded by LOOKUP_SECONDS, one retry; AgeError on failure."""
    if not url.startswith("https://"):
        raise AgeError(f"refused a lookup over plain HTTP: {url}")
    opener = urllib.request.build_opener(HttpsOnlyRedirects)
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    reason = ""
    for attempt in range(2):
        try:
            with opener.open(request, timeout=LOOKUP_SECONDS) as response:
                document = json.loads(response.read())
        except urllib.error.HTTPError as error:
            reason = f"HTTP {error.code}"
            if error.code in (403, 404, 429):
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            reason = type(error).__name__
        else:
            if not isinstance(document, dict):
                raise AgeError(f"lookup failed: {url}: not a JSON object")
            return document
        if attempt == 0:
            time.sleep(2)
    raise AgeError(f"lookup failed: {url}: {reason}")


def main() -> int:
    if Path.cwd().resolve() != ROOT:
        print(f"py-age: run from the module root {ROOT}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    try:
        locked = read_lock(LOCK)
        tools = tool_pins(TOOL_FILES)
        images = image_pins(IMAGE_FILES)
        waivers = load_waivers(WAIVERS)
    except AgeError as error:
        print(f"py-age: FAILED: {error}", file=sys.stderr)
        return 1
    errors: list[str] = []
    dated: list[Dated] = list(images)

    def run(job: Callable[[], Dated]) -> None:
        try:
            dated.append(job())
        except AgeError as lookup_error:
            errors.append(str(lookup_error))

    jobs: list[Callable[[], Dated]] = [
        *[lambda p=p: date_pypi_pin(p, fetch_json) for p in locked],
        *[lambda t=t: date_tool_pin(t, fetch_json) for t in tools],
    ]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, jobs))
    for reason in sorted(errors):
        print(f"py-age: FAILED: {reason}")
    failures, notes = evaluate(dated, waivers, now, MIN_AGE_DAYS)
    for pin in sorted(dated, key=lambda d: d.published, reverse=True):
        days = (now - pin.published) / timedelta(days=1)
        print(
            f"py-age: {pin.kind:4} {pin.name} {pin.version}: {pin.published.date()} ({days:.1f} d)"
        )
    for note in notes:
        print(f"py-age: waived: {note}")
    for failure in failures:
        print(f"py-age: FAILED: {failure}")
    print(
        f"py-age: {len(locked)} uv.lock pins, {len(tools)} tool pins and {len(images)} image pins, "
        f"minimum {MIN_AGE_DAYS} days, {len(notes)} waived, {len(failures) + len(errors)} failures"
    )
    return 1 if failures or errors else 0


if __name__ == "__main__":
    sys.exit(main())
