from uuid import UUID
from fastapi import APIRouter, HTTPException
from app.db import SessionLocal
from app.services import artifacts, store
router = APIRouter()

@router.get("/{run_id}", summary="Get Generation Run")
def get_run(run_id: UUID):
    with SessionLocal() as db:
        row = store.get_run_view(db, run_id)
        if not row: raise HTTPException(status_code=404,detail="Run not found")
        return row

@router.get("/{run_id}/files", summary="Get Generation Run Files")
def get_run_files(run_id: UUID):
    """Full source snapshot produced by the run (path -> content), from its SOURCE_ARCHIVE artifact."""
    with SessionLocal() as db:
        if not store.get_run(db, run_id): raise HTTPException(status_code=404,detail="Run not found")
        ref = store.artifact_ref(db, run_id, "SOURCE_ARCHIVE")
        return artifacts.read_source(ref) if ref else {}

@router.get("/{run_id}/check-report", summary="Get Generation Run Check Report")
def get_check_report(run_id: UUID):
    with SessionLocal() as db:
        if not store.get_run(db, run_id): raise HTTPException(status_code=404,detail="Run not found")
        ref = store.artifact_ref(db, run_id, "CHECK_REPORT")
        return (artifacts.read_json(ref) if ref else None) or {}
