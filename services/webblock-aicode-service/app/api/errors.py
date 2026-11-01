"""JSON answers for errors no route handled: 503 when the database is unreachable, 500 for anything else.

/internal/v1 answers in Problem Details (envelope.problem), as the Project Service expects; /v1 answers {"detail": …}
like HTTPException. Neither echoes the exception, which may carry connection strings or SQL; the log has the traceback.
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import InterfaceError, OperationalError
from app.api import envelope

log = logging.getLogger(__name__)
RETRY_AFTER_SECONDS = "5"


def _is_internal(request: Request) -> bool:
    return request.url.path.startswith("/internal/")


def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc.__class__.__name__)
    if _is_internal(request):
        response = envelope.problem(request, 503, "SERVICE_UNAVAILABLE", "The database is unavailable; retry shortly.")
    else:
        response = JSONResponse({"detail": "The database is unavailable; retry shortly."}, status_code=503)
    response.headers["Retry-After"] = RETRY_AFTER_SECONDS
    return response


def internal_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    if _is_internal(request):
        return envelope.problem(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")
    return JSONResponse({"detail": "An unexpected error occurred."}, status_code=500)


async def catch_unhandled(request: Request, call_next) -> Response:
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 - the last line of defence; logged with its traceback
        return internal_error(request, exc)


def register(app: FastAPI) -> None:
    """Call before adding CORSMiddleware: the catch-all then sits inside it, so a 500 carries the CORS headers the
    editor needs to read it (Starlette's own 500 handler is outside every middleware)."""
    app.add_exception_handler(OperationalError, database_unavailable)
    app.add_exception_handler(InterfaceError, database_unavailable)
    app.middleware("http")(catch_unhandled)
