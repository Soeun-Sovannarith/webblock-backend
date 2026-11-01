"""Swagger UI and the hand-written contract for team testing (task SW; decision 0006 section 7,
"Amended by task SW"). Only the prototype profile registers these two routes: ``GET``/``HEAD``
``/docs`` (a page that loads a pinned Swagger UI) and ``/openapi.yaml`` (the contract file, byte
for byte). They are the one exception to "every route requires a token", by exact path and with
no query string; the calls the page makes still carry the bearer token the Authorize button holds.
No CORS header is set: the page and the API share one origin."""

from __future__ import annotations

from typing import Final

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

DOCS_PATH: Final = "/docs"
OPENAPI_PATH: Final = "/openapi.yaml"
OPEN_READ_PATHS: Final = frozenset({DOCS_PATH, OPENAPI_PATH})

SWAGGER_UI_VERSION: Final = "5.33.0"
"""swagger-ui-dist 5.33.0, published to npm on 2026-09-16."""
SWAGGER_UI_CSS_SRI: Final = (
    "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW"
)
SWAGGER_UI_BUNDLE_SRI: Final = (
    "sha384-YDALVcy8kj8yltLBVi1vBiBAUqdxvus673gM8XKwiy6aDUJFXivF/KCufekjYbVf"
)

_CDN: Final = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"

_PAGE: Final = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Provisioning API (prototype)</title>
<link rel="stylesheet" href="{_CDN}/swagger-ui.css"
  integrity="{SWAGGER_UI_CSS_SRI}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_CDN}/swagger-ui-bundle.js"
  integrity="{SWAGGER_UI_BUNDLE_SRI}" crossorigin="anonymous"></script>
<script>
window.ui = SwaggerUIBundle({{
  url: "{OPENAPI_PATH}",
  dom_id: "#swagger-ui",
  persistAuthorization: false,
  tryItOutEnabled: true
}});
</script>
</body>
</html>
""".encode()


def docs_routes(openapi_yaml: bytes) -> list[Route]:
    """The two routes, serving ``openapi_yaml`` (read once by the composition root)."""

    async def page(request: Request) -> Response:
        _exact(request)
        return Response(_PAGE, media_type="text/html")

    async def contract(request: Request) -> Response:
        _exact(request)
        return Response(openapi_yaml, headers={"Content-Type": "application/yaml; charset=utf-8"})

    return [
        Route(DOCS_PATH, endpoint=page, methods=["GET", "HEAD"]),
        Route(OPENAPI_PATH, endpoint=contract, methods=["GET", "HEAD"]),
    ]


def _exact(request: Request) -> None:
    if request.scope.get("query_string"):
        raise StarletteHTTPException(404)
