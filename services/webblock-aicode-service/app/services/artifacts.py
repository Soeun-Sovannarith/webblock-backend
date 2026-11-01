import gzip
import hashlib
import hmac
import time
import io
import json
import tarfile
from pathlib import Path
from typing import Any
from app.core.config import settings

# Local artifact storage. storage_ref is relative to ARTIFACT_DIR: <project_id>/<harness_run_id>/<file>.
SOURCE_ARCHIVE = "source.tar.gz"
CHECK_REPORT = "check-report.json"
RUN_LOG = "run.log"

MEDIA_TYPES = {SOURCE_ARCHIVE: "application/gzip", CHECK_REPORT: "application/json", RUN_LOG: "text/plain; charset=utf-8"}

def ref(project_id, run_id, name: str) -> str:
    return f"{project_id}/{run_id}/{name}"

def _path(storage_ref: str) -> Path:
    base = Path(settings.artifact_dir).resolve()
    path = (base / storage_ref).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"storage_ref escapes the artifact directory: {storage_ref}")
    return path

def file_path(storage_ref: str) -> Path:
    """The file behind a storage_ref, refused when it would escape ARTIFACT_DIR."""
    return _path(storage_ref)

def sha256_file(storage_ref: str) -> str:
    path = _path(storage_ref)
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()

def write_bytes(storage_ref: str, data: bytes) -> str:
    path = _path(storage_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()

def append_text(storage_ref: str, text: str) -> None:
    path = _path(storage_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)

def read_text(storage_ref: str) -> str:
    path = _path(storage_ref)
    return path.read_text(encoding="utf-8") if path.exists() else ""

def write_json(storage_ref: str, data: dict[str, Any]) -> str:
    return write_bytes(storage_ref, json.dumps(data, indent=2, default=str).encode())

def read_json(storage_ref: str) -> dict[str, Any] | None:
    text = read_text(storage_ref)
    return json.loads(text) if text else None

def pack_source(files: dict[str, str]) -> bytes:
    """Deterministic tar.gz of the source tree (sorted, zero mtimes), so equal trees give equal hashes."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for path in sorted(files):
            data = files[path].encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()

def unpack_source(data: bytes) -> dict[str, str]:
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name] = tar.extractfile(member).read().decode("utf-8")
    return files

def read_source(storage_ref: str) -> dict[str, str]:
    path = _path(storage_ref)
    return unpack_source(path.read_bytes()) if path.exists() else {}


def byte_size(storage_ref: str) -> int:
    path = _path(storage_ref)
    return path.stat().st_size if path.exists() else 0

def _signature(artifact_id, expires: int) -> str:
    return hmac.new(settings.artifact_url_secret.encode(), f"{artifact_id}:{expires}".encode(), hashlib.sha256).hexdigest()

def download_url(artifact_id) -> str | None:
    """A presigned URL for one artifact, valid for ARTIFACT_URL_TTL_SECONDS (Project downloads the source and the
    check report through it and checks the SHA-256). None without ARTIFACT_URL_SECRET."""
    if not settings.artifact_url_secret:
        return None
    expires = int(time.time()) + settings.artifact_url_ttl_seconds
    return (f"{settings.artifact_base_url.rstrip('/')}/internal/v1/artifacts/{artifact_id}"
            f"?expires={expires}&signature={_signature(artifact_id, expires)}")

def verify_download(artifact_id, expires: int, signature: str) -> bool:
    if not settings.artifact_url_secret or expires < time.time():
        return False
    return hmac.compare_digest(_signature(artifact_id, expires), signature)
