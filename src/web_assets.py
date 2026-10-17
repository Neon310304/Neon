"""Let browsers cache web assets, but revalidate them on the next request."""

from pathlib import Path

from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def web_asset_response(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
