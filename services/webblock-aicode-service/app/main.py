import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import errors
from app.api.routes import assistant, internal, projects, runs
from app.services import event_bus
from app.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = Worker() if settings.run_worker else None
    if worker:
        worker.start()
    if settings.run_consumer:
        event_bus.start_consumer()
    yield
    if settings.run_consumer:
        event_bus.stop_consumer()
    if worker:
        worker.stop()

app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
errors.register(app)  # before CORS, see errors.register
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(projects.router, prefix="/v1/projects", tags=["projects"])
app.include_router(projects.onboarding_router, prefix="/v1", tags=["projects"])
app.include_router(runs.router, prefix="/v1/generation-runs", tags=["runs"])
app.include_router(assistant.router, prefix="/v1", tags=["assistant"])
app.include_router(internal.router, prefix="/internal/v1", tags=["internal"])


@app.get("/health")
def health():
    return {"status":"ok","service":settings.app_name}
