from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from src.web_assets import RevalidatingStaticFiles, web_asset_response


def test_web_assets_revalidate_cached_html_and_static_files(tmp_path: Path):
    html = tmp_path / "index.html"
    html.write_text("<html>current UI</html>", encoding="utf-8")
    asset = tmp_path / "agent.svg"
    asset.write_text("<svg>blue mascot</svg>", encoding="utf-8")

    async def home(request):
        return web_asset_response(html)

    app = Starlette(routes=[
        Route("/", home),
        Mount("/static", app=RevalidatingStaticFiles(directory=tmp_path)),
    ])
    with TestClient(app) as client:
        for url in ["/", "/static/agent.svg?v=motion-v3"]:
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            assert "etag" in response.headers

        cached = client.get("/static/agent.svg?v=motion-v3")
        unchanged = client.get("/static/agent.svg?v=motion-v3", headers={"If-None-Match": cached.headers["etag"]})
        assert unchanged.status_code == 304
        assert unchanged.headers["cache-control"] == "no-cache"

        asset.write_text("<svg>updated blue mascot</svg>", encoding="utf-8")
        changed = client.get("/static/agent.svg?v=motion-v3", headers={"If-None-Match": cached.headers["etag"]})
        assert changed.status_code == 200
        assert changed.text == "<svg>updated blue mascot</svg>"
        assert changed.headers["etag"] != cached.headers["etag"]
