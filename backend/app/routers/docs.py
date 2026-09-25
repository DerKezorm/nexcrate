"""The API documentation page, served from local files only.

⚠️ FastAPI's own docs page loads Swagger UI from a CDN and starts it with an inline
script. The Content-Security-Policy forbids both; the page would be white. Here the
files lie in ``static/swagger`` and the start script is a file of its own.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from ..meldungen import error

router = APIRouter(tags=["docs"])

SWAGGER_DIR = Path(__file__).resolve().parent.parent / "static" / "swagger"

#: The only files served under /api/docs/. A fixed list: no path from outside reaches the disk.
ASSETS = {
    "swagger-ui.css": "text/css; charset=utf-8",
    "swagger-ui-bundle.js": "text/javascript; charset=utf-8",
    "start.js": "text/javascript; charset=utf-8",
}

# Relative addresses on purpose: they resolve against /api/docs, and keep working when
# nexcrate runs under a sub path one day.
DOCS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nexcrate API</title>
<link rel="stylesheet" href="docs/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="docs/swagger-ui-bundle.js"></script>
<script src="docs/start.js"></script>
</body>
</html>
"""


@router.get("/api/docs", include_in_schema=False)
def docs_page() -> HTMLResponse:
    return HTMLResponse(DOCS_PAGE, headers={"Cache-Control": "no-cache"})


@router.get("/api/docs/{name}", include_in_schema=False, response_model=None)
def docs_asset(name: str) -> FileResponse:
    media_type = ASSETS.get(name)
    if media_type is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return FileResponse(SWAGGER_DIR / name, media_type=media_type, headers={"Cache-Control": "no-cache"})
