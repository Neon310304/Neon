from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import router
from src.config import get_settings
from src.container import AppContainer
from src.logging_config import configure_logging
from src.security.request_guard import RequestGuard
from src.services.ai_limits import AiLimitError
from src.web_assets import RevalidatingStaticFiles, web_asset_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not getattr(app.state, "container", None):
        app.state.container = AppContainer()
    try:
        app.state.container.initialize()
        yield
    finally:
        await app.state.container.aclose()


settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(
    title="MoveInMate AI",
    version="1.0.0",
    description="Reusable LangGraph agent runtime with a MoveInMate project pack.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.allowed_origins.split(",") if item.strip()],
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Demo-Role"],
)
app.include_router(router)
app.add_middleware(RequestGuard)


@app.exception_handler(AiLimitError)
async def ai_limit_error(request, error):
    return JSONResponse({"detail": {"code": error.code, "message": error.message}},
                        status_code=429, headers={"Retry-After": str(error.retry_after)})

web_dir = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", RevalidatingStaticFiles(directory=web_dir), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return web_asset_response(web_dir / "index.html")
