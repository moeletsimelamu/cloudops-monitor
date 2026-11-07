"""Serving the built Angular app from the API process (the container image).

Files from the build directory are served as they are. Any other path that is not an API path and
does not look like a file name gets ``index.html``, so client routes such as
``/applications/orders-api`` work on reload. The app itself is public: it holds no data, and every
API call it makes needs a session.
"""

import re
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from fastapi.responses import FileResponse

from cloudops.errors import NotFoundError

# Angular adds a content hash to bundle and font file names (main-35JR6M3P.js), so they never
# change and can be cached for good. Everything else must be revalidated.
_HASHED_NAME = re.compile(r"-[A-Z0-9]{8}\.[a-z0-9]+$")
_IMMUTABLE = "public, max-age=31536000, immutable"
# Not left to the operating system's table: Windows can map .js to text/plain, which browsers
# refuse for scripts when nosniff is set.
_MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}


def register_spa(app: FastAPI, static_dir: Path) -> None:
    """Register last: the route matches every GET path the API did not."""
    root = static_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"CLOUDOPS_STATIC_DIR has no index.html: {root}")

    @app.get("/{path:path}", include_in_schema=False)
    def single_page_app(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise NotFoundError("Not Found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                cache = _IMMUTABLE if _HASHED_NAME.search(candidate.name) else "no-cache"
                return FileResponse(
                    candidate,
                    media_type=_MEDIA_TYPES.get(candidate.suffix.lower()),
                    headers={"Cache-Control": cache},
                )
            if "." in PurePosixPath(path).name:
                # A missing asset is a 404, not the app shell served under a .js or .css name.
                raise NotFoundError("Not Found")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
