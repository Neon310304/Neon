from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


class RequestGuard:
    def __init__(self, app):
        self.app = app

    @staticmethod
    def body_limit(path: str) -> int:
        if path == "/api/v1/speech/transcribe":
            return 26 * 1024 * 1024
        if path in {"/api/v1/agent/form-from-files", "/api/v1/agent/chat-with-files"}:
            return 17 * 1024 * 1024
        if path == "/api/v1/vehicles/extract" or path.endswith("/vehicle-document") or path == "/api/v1/requests/vehicle" or path.endswith("/vehicle"):
            return 6 * 1024 * 1024
        return 128 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/")
        draft_chat = path.startswith("/api/v1/residents/") and path.endswith("/request-drafts/chat")
        request = Request(scope)
        try:
            if path in {"/api/v1/auth/reset", "/api/v1/demo/reset"}:
                raise HTTPException(410, "reset_endpoint_retired")
            if path.startswith("/api/v1/management/") or path == "/api/v1/management":
                from src.api.routes import _require_manager

                await run_in_threadpool(_require_manager, request)
            elif draft_chat or path.startswith("/api/v1/agent/") or path.startswith("/api/v1/speech/") or path.startswith("/api/v1/vehicles/") or path.endswith("/vehicle-document") or path == "/api/v1/requests/vehicle" or path.endswith("/vehicle"):
                from src.api.routes import _auth_context

                await run_in_threadpool(_auth_context, request)
            limit = self.body_limit(path)
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError:
                    raise HTTPException(400, "invalid_content_length") from None
                if declared < 0:
                    raise HTTPException(400, "invalid_content_length")
                if declared > limit:
                    raise HTTPException(413, "request_body_too_large")
            if scope["method"] == "POST" and (draft_chat or path.startswith(("/api/v1/agent/", "/api/v1/speech/", "/api/v1/vehicles/")) or path == "/api/v1/management/assistant/chat"):
                from src.services.usage_limits import reserve_ai_ingress

                container = request.app.state.container
                retry = await run_in_threadpool(reserve_ai_ingress, container.store,
                                               request.client.host if request.client else "unknown", container.settings)
                if retry:
                    raise HTTPException(429, "ai_request_rate_limited", headers={"Retry-After": str(retry)})
        except HTTPException as error:
            return await JSONResponse({"detail": error.detail}, error.status_code, headers=error.headers)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "request_body_too_large")
            return message

        await self.app(scope, bounded_receive, send)
