from app.core.config import settings
from app.services import artifacts

def test_source_archive_round_trip_is_deterministic():
    files = {"b.txt": "two", "app/[[...slug]]/page.tsx": "ក្រម", "a.txt": "one"}
    packed = artifacts.pack_source(files)
    assert artifacts.unpack_source(packed) == files
    assert artifacts.pack_source(dict(reversed(list(files.items())))) == packed

def test_storage_refs_stay_inside_artifact_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))
    ref = artifacts.ref("p", "r", artifacts.RUN_LOG)
    artifacts.append_text(ref, "[PREPARE]\n")
    assert artifacts.read_text(ref) == "[PREPARE]\n"
    try:
        artifacts.read_text("../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
