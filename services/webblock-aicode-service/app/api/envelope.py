"""The platform's HTTP response formats, for the endpoints other WebBlock services call (Project's
service-communication.md, "HTTP conventions"): a successful JSON body is `{success, data, meta}`, an error is RFC 9457
Problem Details with `type` `urn:webblock:problem:<code in kebab-case>` and the extension `code`, never echoing input."""
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def _meta(request: Request, status: int) -> dict[str, Any]:
    return {"requestId": request.headers.get("x-request-id") or str(uuid.uuid4()), "traceId": None,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "path": request.url.path, "status": status, "page": None}


def ok(request: Request, data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"success": True, "data": jsonable_encoder(data), "meta": _meta(request, status)},
                        status_code=status)


def problem(request: Request, status: int, code: str, title: str) -> JSONResponse:
    media_type = "application/problem+json" if "application/problem+json" in request.headers.get("accept", "*/*") \
        or "*/*" in request.headers.get("accept", "*/*") else "application/json"
    meta = _meta(request, status)
    return JSONResponse({"type": f"urn:webblock:problem:{code.lower().replace('_', '-')}", "title": title,
                         "status": status, "instance": request.url.path, "code": code,
                         "requestId": meta["requestId"], "traceId": None},
                        status_code=status, media_type=media_type)
