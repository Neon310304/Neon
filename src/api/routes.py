import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import date
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from openai import APIError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from src.agent_core import AgentResult, RuntimeNotConfigured
from src.agent_core.policy import evaluate_request
from src.container import AppContainer
from src.models.schemas import (
    ChatRequest,
    ChatResponse,
    ChecklistNoNeed,
    CorrectionChatRequest,
    FirstPasswordChange,
    HealthResponse,
    HouseholdRemoval,
    LoginRequest,
    ManagementDecision,
    NotificationsReadAll,
    OwnershipConfirm,
    PasswordChange,
    ProfileUpdate,
    RegulationAcknowledge,
    RegulationBatchAcknowledge,
    RequestCreate,
    RequestDraftChat,
    RequestUpdate,
    ResidentAccountCreate,
    TextToSpeechRequest,
    UsernameAvailability,
)
from src.repositories.store import LocalCredential, ResetInvalidatedError
from src.security import (
    AuthContext,
    AuthenticationError,
    AuthorizationError,
    build_auth_context,
    can_access_resident,
    public_auth_config,
    require_role,
)
from src.services import (
    MAX_FILES,
    MAX_OMICX_AUDIO_BYTES,
    MAX_TOTAL_BYTES,
    AttachmentError,
    GeminiSpeechError,
    OmicxTranscriptionError,
    PtitSpeechError,
    build_human_content,
    login_reset,
    prepare_attachment,
)
from src.services.a2ui import build_answer_surface
from src.services.ai_limits import AiLimitError, ai_category
from src.services.attachments import attachment_request
from src.services.demo_accounts import sample_login_hints
from src.services.form_extraction import FORM_FIELDS, extract_form_fields
from src.services.manager_assistant import (
    ManagerAssistantRequest,
    ManagerBatchDecision,
    ManagerBatchPreview,
    apply_batch_decision,
    manager_answer,
    preview_batch,
)
from src.services.manager_conversation import (
    ConversationConfirmation,
    ConversationPreview,
    conversation_chat,
    execute_conversation,
    preview_conversation,
)
from src.services.password_recovery import (
    EmailConfirm,
    EmailStart,
    ManagerPasswordReset,
    ManagerRecoveryReject,
    RecoveryIntake,
    confirm_email,
    deliver_recovery_email,
    intake_recovery,
    mail_available,
    manager_reset,
    recovery_requests,
    reject_recovery,
    start_email,
)
from src.services.procedure_knowledge import knowledge_partitions, wants_guidance
from src.services.request_drafts import (
    DRAFT_TYPES,
    needs_intent_resolution,
    prepare_request_draft,
    registration_choices,
)
from src.services.resident_portal import reading_documents
from src.services.ticket_delivery import (
    HideSelection,
    ReviewConfirmation,
    account_preview,
    handover_flags,
    hide_confirm,
    hide_preview,
    issue_account,
    ticket_handover_username,
)
from src.services.usage_limits import reserve_usage
from src.services.vehicle_documents import extract_vehicle_fields, normalize_vehicle_document, reserve_extraction
from src.tools import TOOL_NAMES
from src.tools.agent_tools import REQUEST_STATUSES, REQUEST_TYPES


async def reset_request_scope(request: Request):
    store = request.app.state.container.store
    if request.url.path in {"/api/v1/auth/reset", "/api/v1/demo/reset", "/api/v1/management/reset", "/api/v1/health"}:
        yield
        return
    try:
        with store.reset_scope():
            yield
    except ResetInvalidatedError:
        raise HTTPException(status_code=401, detail={
            "code": "session_reset", "message": "Hệ thống đã Reset. Vui lòng đăng nhập lại.",
        }) from None


router = APIRouter(prefix="/api/v1", dependencies=[Depends(reset_request_scope)])


def container(request: Request) -> AppContainer:
    return request.app.state.container


def _reserve_ai_request(app, context, operation, maximum):
    if not reserve_usage(app.store, context.auth_subject, operation, maximum):
        raise HTTPException(status_code=429, detail={"code": "usage_limit_reached", "message": "Đã đạt giới hạn thao tác này trong 10 phút. Bạn vẫn có thể điền form và theo dõi ticket; thử lại sau."}, headers={"Retry-After": "600"})


async def _prepare_files(files):
    try:
        if not 1 <= len(files) <= MAX_FILES:
            raise AttachmentError("attachment_count_invalid", f"Chọn từ 1 đến {MAX_FILES} tệp.")
        prepared = []
        total = 0
        for upload in files:
            data = await upload.read(MAX_TOTAL_BYTES + 1)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise AttachmentError("attachments_too_large", "Tổng dung lượng tệp vượt quá 16 MB.", 413)
            prepared.append(await asyncio.to_thread(prepare_attachment, upload.filename, data))
        return prepared
    except AttachmentError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message}) from error
    finally:
        for upload in files:
            await upload.close()


def _auth_context(request: Request) -> AuthContext:
    """Build the server-side identity for this request.

    Identity and scope never come from a route parameter or a request body.  The
    security module owns token verification and the explicit development/test
    demo mode; this adapter only translates its domain errors to the public API
    contract.
    """
    app = container(request)
    try:
        context = build_auth_context(request, app.settings, app.store)
        if context.password_change_required and request.url.path not in {
            "/api/v1/me/context", "/api/v1/auth/password", "/api/v1/auth/password/first-login", "/api/v1/auth/username-availability", "/api/v1/auth/password/status", "/api/v1/auth/logout",
        }:
            raise AuthorizationError("password_change_required", "Bạn cần đổi mật khẩu được cấp trước khi sử dụng ứng dụng.")
        if context.resident_id and "resident" in _roles(context):
            profile = app.store.get_resident(context.resident_id)
            path = request.url.path
            base = f"/api/v1/residents/{context.resident_id}"
            allowed = path.startswith("/api/v1/auth/") or path == "/api/v1/me/context" or (request.method == "GET" and path in {base, base + "/progress", "/api/v1/locations"}) or (request.method == "POST" and path == base + "/ownership/confirm")
            if profile and not profile.get("ownership_confirmed_at") and not allowed:
                raise AuthorizationError("ownership_confirmation_required", "Hãy xác nhận thông tin cư trú")
        return context
    except AuthenticationError as exc:
        detail = getattr(exc, "detail", None) or {
            "code": getattr(exc, "code", "authentication_required"),
            "message": str(exc) or "Authentication required.",
        }
        raise HTTPException(
            status_code=503 if getattr(exc, "code", "") in {"authentication_not_configured", "authentication_unavailable"} else 401,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AuthorizationError as exc:
        detail = getattr(exc, "detail", None) or {
            "code": getattr(exc, "code", "forbidden"),
            "message": str(exc) or "Forbidden.",
        }
        raise HTTPException(status_code=403, detail=detail) from exc


def _roles(context: AuthContext) -> set[str]:
    return {str(role).strip().casefold() for role in (getattr(context, "roles", ()) or ())}


def _require_moveinmate_project(project_id: str) -> None:
    if project_id != "moveinmate":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "project_scope_required",
                "message": "Trợ lý hiện chỉ hỗ trợ dự án MoveInMate.",
            },
        )


def _actor_id(context: AuthContext) -> str:
    """Return the canonical server-side actor identifier.

    A demo principal may expose a resident id separately; in production the
    authenticated user id is the stable actor id.  The client-provided actor_id
    is deliberately ignored by all agent calls.
    """
    for attr in ("resident_id", "user_id", "auth_subject"):
        value = getattr(context, attr, None)
        if value:
            return str(value)
    principal = getattr(context, "principal", None)
    for attr in ("resident_id", "user_id", "auth_subject"):
        value = getattr(principal, attr, None) if principal else None
        if value:
            return str(value)
    raise HTTPException(status_code=401, detail={"code": "identity_missing", "message": "Authenticated identity is missing."})


def _resident_profile(request: Request, resident_id: str) -> dict:
    profile = container(request).store.get_resident(resident_id)
    if not profile:
        # Do not reveal whether an arbitrary id exists to an out-of-scope user.
        raise HTTPException(status_code=404, detail="resident_not_found")
    return profile


def _authorize_resident(request: Request, resident_id: str, *, allow_manager: bool = True) -> tuple[AuthContext, dict]:
    context = _auth_context(request)
    profile = _resident_profile(request, resident_id)
    try:
        allowed = bool(can_access_resident(context, resident_id))
    except (AuthenticationError, AuthorizationError):
        allowed = False
    if not allowed and allow_manager and "manager" in _roles(context):
        site_ids = set(context.manager_site_ids)
        allowed = bool(site_ids) and str(profile.get("site_id")) in site_ids
    if not allowed:
        # 404 avoids leaking the existence of another resident/resource.
        raise HTTPException(status_code=404, detail="resident_not_found")
    return context, profile


def _require_manager(request: Request) -> AuthContext:
    context = _auth_context(request)
    try:
        require_role(context, "manager")
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail={"code": getattr(exc, "code", "authentication_required"), "message": str(exc)}) from exc
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail={"code": getattr(exc, "code", "forbidden"), "message": str(exc)}) from exc
    if not _context_site_ids(context):
        raise HTTPException(status_code=403, detail={"code": "scope_required", "message": "A site scope is required."})
    return context


def _context_site_ids(context: AuthContext) -> set[str]:
    return set(context.manager_site_ids)


def _audit(
    request: Request,
    context: AuthContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str = "success",
    site_id: str | None = None,
    role: str | None = None,
) -> None:
    recorder = getattr(container(request).store, "record_security_event", None)
    if not callable(recorder):
        return
    role = role.casefold() if role else None
    if role is None:
        if action.startswith("request.decide") or context.roles == frozenset({"manager"}):
            role = "manager"
        elif context.roles == frozenset({"resident"}):
            role = "resident"
    scoped_sites = (
        context.manager_site_ids if role == "manager" else context.resident_site_ids if role == "resident" else frozenset()
    )
    recorder(
        actor_subject=context.auth_subject,
        user_id=context.user_id,
        role=role,
        site_id=site_id or (sorted(scoped_sites)[0] if scoped_sites else None),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        correlation_id=request.headers.get("X-Correlation-ID", uuid4().hex),
    )


def _request_in_manager_scope(request: Request, context: AuthContext, request_id: str) -> tuple[dict, dict] | None:
    """Find a request and resident profile within the manager's site scope."""
    store = container(request).store
    site_ids = _context_site_ids(context)
    for item in store.list_requests():
        if str(item.get("request_id")) != str(request_id):
            continue
        profile = store.get_resident(item.get("resident_id"))
        if not profile:
            return None
        if not site_ids or str(profile.get("site_id")) not in site_ids:
            return None
        return item, profile
    return None


def llm_error_detail(exc: APIError) -> tuple[str, str]:
    status = getattr(exc, "status_code", None)
    # Log only classifications: provider bodies can contain resident data or keys.
    signature_error = "thought_signature" in str(exc).lower() or "thought signature" in str(exc).lower()
    logging.getLogger(__name__).warning(
        "LLM request failed: status=%s exception=%s thought_signature_error=%s",
        status, type(exc).__name__, signature_error,
    )
    if status == 400:
        return (
            "llm_request_rejected",
            "Mô hình AI từ chối yêu cầu (HTTP 400). Vui lòng bắt đầu hội thoại mới; nếu vẫn lỗi, liên hệ quản trị viên.",
        )
    if status == 401:
        return (
            "llm_authentication_failed",
            "API key của mô hình AI không hợp lệ hoặc đã hết hiệu lực.",
        )
    return (
        "llm_provider_error",
        "Không thể kết nối tới mô hình AI. Vui lòng thử lại sau.",
    )


@router.get("/auth/config")
async def auth_config(request: Request) -> dict:
    """Expose only the local provider status, never server credentials."""
    app = container(request)
    return public_auth_config(app.settings)


class ManagementResetRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256, repr=False)
    confirm: bool = False


@router.post("/auth/reset")
def login_page_reset() -> None:
    raise HTTPException(status_code=410, detail="reset_endpoint_retired")


@router.post("/management/reset")
def management_application_reset(payload: ManagementResetRequest, request: Request) -> JSONResponse:
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    app = container(request)
    if not login_reset.available(app) or app.settings.local_auth_site_id not in context.manager_site_ids:
        raise HTTPException(status_code=403, detail="reset_disabled")
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="reset_confirmation_required")
    try:
        login_reset.record_attempt(app.store, request.client.host if request.client else "unknown", context.auth_subject)
        if not hmac.compare_digest(payload.code.encode(), app.settings.login_reset_code.encode()):
            _audit(request, context, action="application.reset", resource_type="application", resource_id="moveinmate", outcome="denied")
            raise HTTPException(status_code=401, detail="reset_code_invalid")
        result = login_reset.reset_application(app, context.auth_subject)
    except login_reset.ResetRateLimitedError:
        raise HTTPException(status_code=429, detail="reset_rate_limited", headers={"Retry-After": "600"}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail="reset_unavailable") from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/auth/login-hints", include_in_schema=False)
async def login_hints(request: Request) -> JSONResponse:
    app = container(request)
    hints = app.local_auth.login_hints()
    if hints.get("visible"):
        hints["accounts"].extend(sample_login_hints(app))
    return JSONResponse(
        hints,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/me/context")
async def me_context(request: Request) -> dict:
    context = _auth_context(request)
    return {
        "user_id": context.user_id,
        "username": context.username or context.auth_subject.removeprefix("local:"),
        "username_change_required": context.username_change_required,
        "roles": sorted(context.roles),
        "resident_id": context.resident_id,
        "resident_ids": sorted(context.resident_ids),
        "site_ids": sorted(context.site_ids),
        "manager_site_ids": sorted(context.manager_site_ids),
        "mode": context.mode,
        "password_change_required": context.password_change_required,
        "demo_reset_enabled": False,
        "reset_available": "manager" in context.roles and context.mode == "required" and container(request).settings.local_auth_site_id in context.manager_site_ids and login_reset.available(container(request)),
    }


@router.post("/auth/login")
def auth_login(payload: LoginRequest, request: Request) -> JSONResponse:
    try:
        session = container(request).local_auth.login(payload.username, payload.password)
    except AuthenticationError as error:
        raise HTTPException(
            status_code=503 if error.code == "authentication_not_configured" else 401,
            detail=error.detail,
        ) from error
    except AuthorizationError as error:
        raise HTTPException(status_code=403, detail=error.detail) from error
    return JSONResponse(session, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/auth/logout", status_code=204)
async def auth_logout(request: Request) -> Response:
    """Revoke the current local session before the browser clears its token."""
    context = _auth_context(request)
    if context.session_id:
        revoke = getattr(container(request).store, "revoke_auth_session", None)
        if not callable(revoke):
            raise HTTPException(status_code=503, detail={"code": "session_store_unavailable", "message": "Session revocation is not configured."})
        revoke(context.session_id, context.auth_subject)
    return Response(status_code=204)


@router.get("/auth/password/status")
async def password_status(request: Request) -> dict:
    context = _auth_context(request)
    username = context.username or context.auth_subject.removeprefix("local:")
    app = container(request)
    return {"username": username, "change_allowed": context.mode == "required" and username not in app.local_auth.accounts and bool(app.store.get_local_credential(username=username))}


@router.post("/auth/username-availability")
def username_availability(payload: UsernameAvailability, request: Request) -> JSONResponse:
    context = _auth_context(request)
    if context.mode != "required" or not ("manager" in _roles(context) or context.password_change_required):
        raise HTTPException(status_code=403, detail="username_check_not_allowed")
    app = container(request)
    address = request.client.host if request.client else "unknown"
    allowed = reserve_usage(app.store, "global", "username-check", 3000)
    allowed = reserve_usage(app.store, address, "username-check:ip", 300) and allowed
    allowed = reserve_usage(app.store, context.auth_subject, "username-check:account", 150) and allowed
    if not allowed:
        raise HTTPException(status_code=429, detail="username_check_rate_limited", headers={"Retry-After": "600"})
    username = payload.username.strip().casefold()
    if not re.fullmatch(r"[a-z0-9_.@-]{1,80}", username):
        raise HTTPException(status_code=422, detail="invalid_username")
    current = context.password_change_required and not context.username_change_required and username == context.username
    available = username not in app.local_auth.accounts and (current or app.store.username_available(username))
    return JSONResponse({"available": bool(available)}, headers={"Cache-Control": "no-store, private"})


@router.post("/auth/password/first-login", status_code=204)
def set_initial_password(payload: FirstPasswordChange, request: Request) -> Response:
    context = _auth_context(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_account_required")
    try:
        container(request).local_auth.set_initial_password(context.auth_subject, payload.username, payload.new_password)
    except AuthorizationError as error:
        raise HTTPException(status_code=403, detail=error.detail) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="username_unavailable") from error
    except ValueError as error:
        code = str(error)
        raise HTTPException(status_code=403 if code in {"configured_account_password_unchanged", "username_change_not_allowed", "initial_password_not_required"} else 409 if code in {"password_changed_concurrently", "username_unavailable"} else 422, detail=code) from error
    _audit(request, context, action="account.initial_password_set", resource_type="account", resource_id=context.auth_subject)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/password", status_code=204)
def change_password(payload: PasswordChange, request: Request) -> Response:
    context = _auth_context(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_account_required")
    try:
        container(request).local_auth.change_password(context.auth_subject, payload.current_password, payload.new_password)
    except AuthenticationError as error:
        raise HTTPException(status_code=400, detail=error.detail) from error
    except ValueError as error:
        raise HTTPException(status_code=403 if str(error) == "configured_account_password_unchanged" else 409 if str(error) == "password_changed_concurrently" else 422, detail=str(error)) from error
    _audit(request, context, action="account.password_changed", resource_type="account", resource_id=context.auth_subject)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def _recovery_limit(request: Request, operation: str, username: str = "") -> None:
    store = container(request).store
    address = request.client.host if request.client else "unknown"
    allowed = reserve_usage(store, "global", operation, 100)
    allowed = reserve_usage(store, address, operation + ":ip", 10) and allowed
    if username:
        allowed = reserve_usage(store, username.strip().casefold(), operation + ":account", 5) and allowed
    if not allowed:
        raise HTTPException(status_code=429, detail="recovery_rate_limited", headers={"Retry-After": "600"})


def _recovery_error(error):
    code = str(error)
    return HTTPException(status_code=503 if code == "email_delivery_unavailable" else 400, detail=code)


@router.get("/auth/recovery/config")
def recovery_config(request: Request) -> dict:
    return {"email_enabled": mail_available(container(request).settings), "manager_enabled": True}


@router.post("/auth/recovery/email")
def recovery_email_start(payload: EmailStart, request: Request, background: BackgroundTasks) -> JSONResponse:
    _recovery_limit(request, "recovery_email", payload.username)
    app = container(request)
    if not mail_available(app.settings):
        raise HTTPException(status_code=503, detail="email_delivery_unavailable")
    challenge_id = uuid4().hex
    background.add_task(deliver_recovery_email, app, payload, challenge_id)
    result = {"challenge_id": challenge_id, "message": "Nếu thông tin khớp với email đã xác thực, mã sẽ được gửi. Nếu không nhận được email, bạn có thể gửi yêu cầu cho BQL."}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/auth/recovery/email/confirm", status_code=204)
def recovery_email_confirm(payload: EmailConfirm, request: Request) -> Response:
    _recovery_limit(request, "recovery_confirm")
    try:
        confirm_email(container(request), payload, "reset_password")
    except ValueError as error:
        raise _recovery_error(error) from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/recovery/requests", status_code=202)
def recovery_intake(payload: RecoveryIntake, request: Request) -> JSONResponse:
    _recovery_limit(request, "recovery_intake", payload.username)
    payload.username = payload.username.casefold()
    intake_recovery(container(request).store, payload)
    return JSONResponse({"message": "Đã tiếp nhận yêu cầu. BQL sẽ đối chiếu hồ sơ và liên hệ qua thông tin bạn cung cấp; chưa có mật khẩu nào được thay đổi."}, status_code=202, headers={"Cache-Control": "no-store"})


@router.get("/auth/recovery/email/status")
def recovery_email_status(request: Request) -> JSONResponse:
    context = _auth_context(request)
    credential = container(request).store.get_local_credential(auth_subject=context.auth_subject)
    return JSONResponse({"email": credential.get("recovery_email") if credential else None, "allowed": bool(credential), "email_enabled": mail_available(container(request).settings)}, headers={"Cache-Control": "no-store"})


@router.post("/auth/recovery/email/register")
def recovery_email_register(payload: EmailStart, request: Request) -> JSONResponse:
    context = _auth_context(request)
    _recovery_limit(request, "email_register", context.auth_subject)
    try:
        result = start_email(container(request), payload, "verify_email", context.auth_subject)
    except ValueError as error:
        raise _recovery_error(error) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/auth/recovery/email/register/confirm", status_code=204)
def recovery_email_register_confirm(payload: EmailConfirm, request: Request) -> Response:
    context = _auth_context(request)
    _recovery_limit(request, "email_register_confirm", context.auth_subject)
    try:
        confirm_email(container(request), payload, "verify_email", context.auth_subject)
    except ValueError as error:
        raise _recovery_error(error) from None
    _audit(request, context, action="account.email_verified", resource_type="account", resource_id=context.auth_subject)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/management/password-recovery")
def management_recovery_requests(request: Request) -> JSONResponse:
    context = _require_manager(request)
    return JSONResponse({"items": recovery_requests(container(request).store, _context_site_ids(context))}, headers={"Cache-Control": "no-store"})


@router.post("/management/resident-accounts/{username}/reset-password")
def management_reset_password(username: str, payload: ManagerPasswordReset, request: Request) -> JSONResponse:
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    app = container(request)
    if username.casefold() in app.local_auth.accounts:
        raise HTTPException(status_code=404, detail="account_not_found")
    try:
        with app.store.transaction():
            result = manager_reset(app, _context_site_ids(context), username, payload)
            _audit(request, context, action="account.password_reset", resource_type="account", resource_id="local:" + username.casefold())
    except LookupError:
        raise HTTPException(status_code=404, detail="account_not_found") from None
    except ValueError as error:
        raise _recovery_error(error) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/management/password-recovery/{request_id}/reject", status_code=204)
def management_recovery_reject(request_id: str, payload: ManagerRecoveryReject, request: Request) -> Response:
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    try:
        with container(request).store.transaction():
            reject_recovery(container(request).store, _context_site_ids(context), request_id, payload.reason)
            _audit(request, context, action="account.recovery_rejected", resource_type="recovery", resource_id=request_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="recovery_not_found") from None
    except ValueError as error:
        raise _recovery_error(error) from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    app = container(request)
    connection = app.settings.resolve_llm()
    provider, model = app.settings.llm_summary()
    return HealthResponse(
        status="ready" if connection else "configuration_required",
        provider=provider,
        model=model,
        agent="langgraph_reusable_runtime",
        rag="bm25_phrase_filtered_chroma",
        database=app.store.engine.url.get_backend_name(),
        tools=TOOL_NAMES,
        speech_to_text=app.settings.stt_provider if app.speech_to_text.configured else "configuration_required",
        text_to_speech=(
            app.settings.tts_provider if app.text_to_speech.configured else "configuration_required"
        ),
    )


@router.get("/agent-packs")
async def agent_packs(request: Request) -> dict:
    # The runtime is reusable internally, but this public product exposes only
    # the registered MoveInMate pack.
    return {
        "items": [
            item for item in container(request).packs.describe() if item.get("project_id") == "moveinmate"
        ]
    }


async def run_agent_chat(
    payload: ChatRequest, request: Request, message_content: str | list[dict] | None = None
) -> ChatResponse:
    app = container(request)
    _require_moveinmate_project(payload.project_id)
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail={"code": "resident_role_required", "message": "A resident scope is required."})
    actor_id = context.resident_id
    try:
        content = message_content if message_content is not None else payload.message
        decision = evaluate_request(payload.project_id, content, payload.language)
        if not decision.allowed:
            result = AgentResult(answer=decision.message, policy_code=decision.code)
        else:
            _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)
            result = await app.agent.run(
                payload.project_id, actor_id, payload.conversation_id, content, payload.language,
            )
    except RuntimeNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "agent_not_configured", "message": str(exc)},
        ) from exc
    except APIError as exc:
        code, message = llm_error_detail(exc)
        raise HTTPException(
            status_code=502,
            detail={"code": code, "message": message},
        ) from exc
    except ResetInvalidatedError:
        raise HTTPException(status_code=401, detail={
            "code": "session_reset", "message": "Hệ thống đã Reset. Vui lòng đăng nhập lại.",
        }) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatResponse(
        answer=result.answer,
        project_id=payload.project_id,
        actor_id=actor_id,
        conversation_id=payload.conversation_id,
        tools_used=result.tools_used,
        citations=result.citations,
        requires_human=result.requires_human,
        policy_code=result.policy_code,
        a2ui=build_answer_surface(
            result.answer,
            conversation_id=payload.conversation_id,
            requires_human=result.requires_human,
            question=payload.message if not result.policy_code else "",
            procedures=app.tool_service.get_onboarding_progress(actor_id)["all_tasks"]
            if any(trace["tool"] in {"build_checklist", "get_onboarding_progress"} for trace in result.tools_used)
            or (not result.policy_code and wants_guidance(payload.message))
            else [],
        ),
    )


def stream_agent_chat(
    payload: ChatRequest, request: Request, message_content: str | list[dict] | None = None
) -> StreamingResponse:
    app = container(request)
    _require_moveinmate_project(payload.project_id)
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail={"code": "resident_role_required", "message": "A resident scope is required."})
    actor_id = context.resident_id
    _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)

    async def events():
        try:
            async for event in app.agent.stream(
                payload.project_id,
                actor_id,
                payload.conversation_id,
                message_content if message_content is not None else payload.message,
                payload.language,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except RuntimeNotConfigured as exc:
            yield (
                json.dumps(
                    {"type": "error", "code": "agent_not_configured", "message": str(exc)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        except AiLimitError as exc:
            yield json.dumps({"type": "error", "code": exc.code, "message": exc.message, "retry_after": exc.retry_after}, ensure_ascii=False) + "\n"
        except APIError as exc:
            code, message = llm_error_detail(exc)
            yield (
                json.dumps(
                    {"type": "error", "code": code, "message": message},
                    ensure_ascii=False,
                )
                + "\n"
            )
        except ValueError as exc:
            yield (
                json.dumps(
                    {"type": "error", "code": str(exc), "message": str(exc)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "code": "stream_failed",
                        "message": "Kết nối tạo câu trả lời bị gián đoạn. Vui lòng thử lại.",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(payload: ChatRequest, request: Request) -> ChatResponse:
    return await run_agent_chat(payload, request)


@router.post("/agent/chat/stream")
async def agent_chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    return stream_agent_chat(payload, request)


@router.post("/agent/chat/ag-ui")
async def agent_chat_ag_ui(payload: ChatRequest, request: Request) -> StreamingResponse:
    """AG-UI SSE transport with an A2UI v0.9.1 answer surface.

    The existing NDJSON endpoint remains available for older clients. This
    endpoint emits standard AG-UI run/text/state events and carries the fixed,
    allow-listed A2UI catalog as a CUSTOM event.
    """
    app = container(request)
    _require_moveinmate_project(payload.project_id)
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "resident_role_required", "message": "A resident scope is required."},
        )
    actor_id = context.resident_id
    run_id = uuid4().hex
    _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)
    message_id = uuid4().hex

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def events():
        result: dict = {
            "answer": "",
            "tools_used": [],
            "citations": [],
            "requires_human": False,
            "policy_code": None,
        }
        yield sse({"type": "RUN_STARTED", "threadId": payload.conversation_id, "runId": run_id})
        yield sse({"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"})
        try:
            async for item in app.agent.stream(
                payload.project_id,
                actor_id,
                payload.conversation_id,
                payload.message,
                payload.language,
            ):
                if item.get("type") == "token":
                    yield sse(
                        {
                            "type": "TEXT_MESSAGE_CONTENT",
                            "messageId": message_id,
                            "delta": item.get("content", ""),
                        }
                    )
                elif item.get("type") == "speech_segment":
                    yield sse({"type": "CUSTOM", "name": "speech_segment", "value": item})
                elif item.get("type") == "warning":
                    yield sse({"type": "CUSTOM", "name": "stream_warning", "value": item})
                elif item.get("type") == "done":
                    result = {
                        key: item.get(key)
                        for key in ("answer", "tools_used", "citations", "requires_human", "policy_code")
                    }
            yield sse({"type": "TEXT_MESSAGE_END", "messageId": message_id})
            surface = build_answer_surface(
                result["answer"],
                conversation_id=payload.conversation_id,
                requires_human=bool(result.get("requires_human")),
                question=payload.message if not result.get("policy_code") else "",
                procedures=app.tool_service.get_onboarding_progress(actor_id)["all_tasks"]
                if any(trace["tool"] in {"build_checklist", "get_onboarding_progress"} for trace in result.get("tools_used") or [])
                or (not result.get("policy_code") and wants_guidance(payload.message))
                else [],
            )
            yield sse({"type": "CUSTOM", "name": "a2ui", "value": surface})
            yield sse(
                {
                    "type": "STATE_SNAPSHOT",
                    "snapshot": {
                        "project_id": payload.project_id,
                        "conversation_id": payload.conversation_id,
                        "context_policy": {
                            "scope": "moveinmate",
                            "history": "bounded_and_compressed",
                            "tool_access": "resident_scoped",
                        },
                    },
                }
            )
            yield sse({"type": "RUN_FINISHED", "threadId": payload.conversation_id, "runId": run_id, "result": result | {"a2ui": surface}})
        except AiLimitError as exc:
            yield sse({"type": "RUN_ERROR", "threadId": payload.conversation_id, "runId": run_id,
                       "code": exc.code, "message": exc.message, "retry_after": exc.retry_after})
        except RuntimeNotConfigured as exc:
            yield sse(
                {
                    "type": "RUN_ERROR",
                    "threadId": payload.conversation_id,
                    "runId": run_id,
                    "code": "agent_not_configured",
                    "message": str(exc),
                }
            )
        except APIError as exc:
            code, message = llm_error_detail(exc)
            yield sse(
                {
                    "type": "RUN_ERROR",
                    "threadId": payload.conversation_id,
                    "runId": run_id,
                    "code": code,
                    "message": message,
                }
            )
        except ValueError as exc:
            yield sse(
                {
                    "type": "RUN_ERROR",
                    "threadId": payload.conversation_id,
                    "runId": run_id,
                    "code": str(exc),
                    "message": str(exc),
                }
            )
        except Exception:
            yield sse(
                {
                    "type": "RUN_ERROR",
                    "threadId": payload.conversation_id,
                    "runId": run_id,
                    "code": "stream_failed",
                    "message": "Kết nối tạo câu trả lời bị gián đoạn. Vui lòng thử lại.",
                }
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/speech/transcribe")
async def transcribe_speech(
    request: Request,
    file: Annotated[UploadFile, File()],
    language_code: str = Form(default="vi"),
) -> dict:
    context = _auth_context(request)
    app = container(request)
    _reserve_ai_request(app, context, "stt", app.settings.transcription_requests_per_10_minutes)
    content_type = file.content_type or "application/octet-stream"
    if not content_type.startswith("audio/") and content_type != "video/webm":
        await file.close()
        raise HTTPException(
            status_code=415,
            detail={
                "code": "unsupported_audio_type",
                "message": "Định dạng ghi âm không được hỗ trợ.",
            },
        )

    try:
        content = await file.read(MAX_OMICX_AUDIO_BYTES + 1)
        if not content or len(content) > MAX_OMICX_AUDIO_BYTES:
            raise HTTPException(status_code=413 if content else 422, detail="audio_too_large" if content else "audio_required")
        async with app.ai_usage.call("stt"):
            async with asyncio.timeout(90):
                return await app.speech_to_text.transcribe(file.filename or "resident-recording.webm", content, content_type, language_code)
    except (OmicxTranscriptionError, PtitSpeechError) as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers=headers,
        ) from exc
    finally:
        await file.close()


@router.post("/speech/synthesize")
async def synthesize_speech(
    payload: TextToSpeechRequest, request: Request
) -> Response:
    context = _auth_context(request)
    app = container(request)
    identity = hashlib.sha256(f"{app.settings.tts_provider}:{app.settings.ptit_tts_url}:{app.settings.gemini_tts_model}:{app.settings.gemini_tts_voice}".encode()).hexdigest()
    cache_key = (context.auth_subject, context.session_id or f"demo:{app.store.login_reset_generation()}", "tts", identity, hashlib.sha256(payload.text.encode()).hexdigest())

    async def generate():
        _reserve_ai_request(app, context, "tts", app.settings.speech_requests_per_10_minutes)
        async with app.ai_usage.call("tts", len(payload.text)):
            async with asyncio.timeout(app.settings.ptit_tts_timeout_seconds if app.settings.tts_provider == "ptit" else 60):
                return await app.text_to_speech.synthesize(payload.text)

    try:
        audio = await app.private_results.load(cache_key, generate, request.is_disconnected)
    except TimeoutError:
        raise HTTPException(status_code=504, detail={"code": "tts_timeout", "message": "PTIT chưa tạo giọng đọc kịp. Nội dung vẫn có trên màn hình; bấm Nghe để thử lại."}) from None
    except (GeminiSpeechError, PtitSpeechError) as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers=headers,
        ) from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="moveinmate-vi.wav"',
            "X-TTS-Provider": app.settings.tts_provider,
        },
    )


@router.post("/residents/{resident_id}/request-drafts/chat")
async def request_draft_chat(resident_id: str, payload: RequestDraftChat, request: Request, response: Response) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    response.headers["Cache-Control"] = "no-store, private"
    app = container(request)
    if payload.request_type or registration_choices(payload.message) or needs_intent_resolution(payload.message):
        _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)
    try:
        return await prepare_request_draft(app, resident_id, payload)
    except AiLimitError as error:
        raise HTTPException(status_code=429, detail={"code": error.code, "message": error.message}, headers={"Retry-After": str(error.retry_after)}) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except APIError:
        raise HTTPException(status_code=502, detail="form_extraction_failed") from None


@router.post("/agent/form-from-files")
async def form_from_files(request: Request, response: Response, files: Annotated[list[UploadFile], File()], message: str = Form(default="", max_length=2000), form_type: str = Form(default=""), active_form_type: str = Form(default="", max_length=40)) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail="resident_role_required")
    if form_type and form_type not in FORM_FIELDS:
        raise HTTPException(status_code=422, detail="unsupported_form_type")
    if active_form_type and active_form_type not in DRAFT_TYPES:
        raise HTTPException(status_code=422, detail="unsupported_form_type")
    purpose = attachment_request(message)
    policy = evaluate_request("moveinmate", purpose, "vi")
    if not policy.allowed:
        for upload in files:
            await upload.close()
        return {"answer": policy.message, "policy_code": policy.code}
    app = container(request)
    prepared = await _prepare_files(files)
    policy = evaluate_request("moveinmate", build_human_content(purpose, prepared), "vi")
    if not policy.allowed:
        return {"answer": policy.message, "policy_code": policy.code}
    choices = registration_choices(message) if not form_type else []
    if len(choices) > 1:
        return {"answer": "Bạn nhắc nhiều thủ tục. Hãy chọn một thủ tục muốn làm trước để tôi điền đúng hồ sơ; chưa gửi BQL.", "form_draft": {"form_type": "", "values": {}, "unreadable_fields": []}, "requires_confirmation": True}
    if len(choices) == 1:
        form_type = choices[0]
    digest = hashlib.sha256((purpose + form_type + active_form_type + repr(prepared)).encode()).hexdigest()
    cache_key = (context.auth_subject, context.session_id or f"demo:{app.store.login_reset_generation()}", "ocr", digest)
    cached = app.private_results.get(cache_key)
    if cached is not None:
        return cached
    _reserve_ai_request(app, context, "ocr", app.settings.document_extractions_per_10_minutes)
    try:
        with ai_category("ocr"):
            result = await extract_form_fields(app, prepared, purpose, form_type, active_form_type=active_form_type)
    except RuntimeNotConfigured:
        raise HTTPException(status_code=503, detail="agent_not_configured") from None
    except TimeoutError:
        raise HTTPException(status_code=504, detail="form_extraction_timeout") from None
    except (APIError, ValueError):
        raise HTTPException(status_code=502, detail="form_extraction_failed") from None
    app.private_results.put(cache_key, result)
    return result


@router.post("/agent/chat-with-files", response_model=ChatResponse)
async def agent_chat_with_files(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    project_id: str = Form(default="moveinmate"),
    actor_id: str = Form(default="R001"),
    conversation_id: str = Form(...),
    message: str = Form(default=""),
    language: str = Form(default="auto"),
) -> ChatResponse:
    context = _auth_context(request)
    _require_moveinmate_project(project_id)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail={"code": "resident_role_required", "message": "A resident scope is required."})
    if not files:
        raise HTTPException(status_code=422, detail="attachment_required")
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Chỉ được đính kèm tối đa {MAX_FILES} tệp mỗi lần.",
        )
    try:
        payload = ChatRequest(
            project_id=project_id,
            actor_id=context.resident_id,
            conversation_id=conversation_id,
            message=attachment_request(message, language),
            language=language,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_input=False)) from exc

    prepared = []
    total_bytes = 0
    try:
        if not evaluate_request(project_id, payload.message, payload.language).allowed:
            return await run_agent_chat(payload, request)
        for upload in files:
            data = await upload.read(MAX_TOTAL_BYTES + 1)
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise AttachmentError(
                    "attachments_too_large",
                    "Tổng dung lượng tệp đính kèm vượt quá giới hạn 16 MB.",
                    413,
                )
            prepared.append(prepare_attachment(upload.filename, data))
    except AttachmentError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        for upload in files:
            await upload.close()

    content = build_human_content(payload.message, prepared)
    return await run_agent_chat(payload, request, content)


@router.get("/residents/{resident_id}")
async def resident(resident_id: str, request: Request) -> dict:
    _, profile = _authorize_resident(request, resident_id)
    return profile


@router.put("/residents/{resident_id}")
async def update_resident(resident_id: str, payload: ProfileUpdate, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    values = payload.model_dump(exclude_none=True)
    app = container(request)
    try:
        with app.store.transaction():
            profile = app.store.update_resident(resident_id, values, lock_assigned_identity=True)
            checklist = app.tool_service.build_checklist(resident_id)
    except ValueError as exc:
        raise HTTPException(status_code=403 if str(exc) == "owner_role_required" else 409 if str(exc) in {"apartment_owner_exists", "ownership_information_locked"} else 404, detail=str(exc)) from exc
    _audit(request, context, action="profile.update", resource_type="resident", resource_id=resident_id)
    return {"profile": profile, "checklist": checklist}


@router.post("/residents/{resident_id}/ownership/confirm")
async def confirm_ownership(resident_id: str, payload: OwnershipConfirm, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    app = container(request)
    try:
        with app.store.transaction():
            profile = app.store.confirm_ownership(resident_id, payload.model_dump())
            checklist = app.tool_service.build_checklist(resident_id)
    except ValueError as error:
        raise HTTPException(status_code=403 if str(error) == "owner_role_required" else 409, detail=str(error)) from error
    _audit(request, context, action="ownership.confirm", resource_type="resident", resource_id=resident_id)
    return {"profile": profile, "checklist": checklist}


@router.post("/residents/{resident_id}/checklist/rebuild")
async def rebuild_checklist(resident_id: str, request: Request) -> dict:
    _authorize_resident(request, resident_id, allow_manager=False)
    try:
        return container(request).tool_service.build_checklist(resident_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/residents/{resident_id}/progress")
async def progress(resident_id: str, request: Request) -> dict:
    _authorize_resident(request, resident_id)
    try:
        data = container(request).tool_service.get_onboarding_progress(resident_id)
        data["a2ui"] = build_answer_surface(
            "", conversation_id=f"directory_{resident_id}",
            procedures=data.get("all_tasks", []), include_actions=False,
        )
        return data
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/residents/{resident_id}/checklist/{task_code}/no-need")
async def checklist_no_need(resident_id: str, task_code: str, payload: ChecklistNoNeed, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    app = container(request)
    try:
        with app.store.transaction():
            task = app.store.mark_task_not_needed(resident_id, task_code, payload.expected_updated_at, payload.expected_profile_version)
            if not task["already_recorded"]:
                _audit(request, context, action="checklist.no_need", resource_type="checklist_task", resource_id=f"{resident_id}:{task_code}")
            data = app.tool_service.get_onboarding_progress(resident_id)
    except ValueError as error:
        code = str(error)
        status = 403 if code in {"owner_role_required", "ownership_confirmation_required"} else 404 if code in {"resident_not_found", "task_not_found"} else 400 if code == "task_no_need_not_allowed" else 409
        raise HTTPException(status_code=status, detail=code) from error
    return {"task": task, "progress": data, "ticket_created": False}


@router.get("/knowledge/sources")
async def knowledge_sources(request: Request) -> dict:
    _auth_context(request)
    return {"items": container(request).knowledge_store.list_documents()}

@router.get("/knowledge/sources/{source_id}")
async def knowledge_source(source_id: str, request: Request) -> dict:
    _auth_context(request)
    for document in container(request).knowledge_store.list_documents(include_body=True):
        if document["source_id"] == source_id:
            return document
    raise HTTPException(status_code=404, detail="source_not_found")

@router.get("/residents/{resident_id}/regulations")
async def resident_regulations(resident_id: str, request: Request) -> dict:
    app = container(request)
    _, profile = _authorize_resident(request, resident_id)
    return {
        "items": reading_documents(app.store, resident_id, app.retriever.list_documents(profile["site_id"])),
        **knowledge_partitions(),
    }


@router.post("/residents/{resident_id}/regulations/acknowledge")
async def acknowledge_regulations(resident_id: str, payload: RegulationAcknowledge, request: Request) -> dict:
    context, profile = _authorize_resident(request, resident_id, allow_manager=False)
    app = container(request)
    documents = reading_documents(app.store, resident_id, app.retriever.list_documents(profile["site_id"]))
    document = next((item for item in documents if item["source_id"] == payload.source_id), None)
    if not document:
        raise HTTPException(status_code=404, detail="document_not_found")
    if document["revision"] != payload.revision:
        raise HTTPException(status_code=409, detail="document_revision_changed")
    with app.store.transaction():
        app.store.acknowledge_document(resident_id, document)
        _audit(request, context, action="regulations.acknowledge", resource_type="document", resource_id=payload.source_id)
    return {"acknowledged": True, "progress": app.tool_service.get_onboarding_progress(resident_id)}


@router.post("/residents/{resident_id}/regulations/acknowledge-batch")
async def acknowledge_regulations_batch(resident_id: str, payload: RegulationBatchAcknowledge, request: Request) -> dict:
    app = container(request)
    context, profile = _authorize_resident(request, resident_id, allow_manager=False)
    current = {item["source_id"]: item for item in reading_documents(app.store, resident_id, app.retriever.list_documents(profile["site_id"]))}
    if {item.source_id for item in payload.documents} != set(current) or len(payload.documents) != len(current):
        raise HTTPException(status_code=409, detail="document_revision_changed")
    selected = []
    for item in payload.documents:
        document = current.get(item.source_id)
        if not document:
            raise HTTPException(status_code=404, detail="document_not_found")
        if document["revision"] != item.revision:
            raise HTTPException(status_code=409, detail="document_revision_changed")
        selected.append(document)
    with app.store.transaction():
        for document in selected:
            app.store.acknowledge_document(resident_id, document)
        _audit(request, context, action="regulations.acknowledge_batch", resource_type="document", resource_id=resident_id)
    return {"acknowledged": True, "count": len(selected), "progress": app.tool_service.get_onboarding_progress(resident_id)}


@router.get("/locations")
async def available_locations(request: Request) -> dict:
    context = _auth_context(request)
    sites = set(context.manager_site_ids | context.resident_site_ids)
    app = container(request)
    return {"items": app.store.list_locations(sites, exclude_resident_id=context.resident_id), "sites": app.store.list_sites(sites)}


@router.get("/management/resident-accounts")
async def resident_accounts(request: Request) -> dict:
    context = _require_manager(request)
    return {"items": container(request).store.list_resident_accounts(_context_site_ids(context))}


@router.get("/management/requests/{request_id}/dependent-account-preview")
async def dependent_account_preview(request_id: str, request: Request) -> dict:
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    try:
        return account_preview(container(request), _context_site_ids(context), context.auth_subject, request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/management/requests/{request_id}/issue-dependent-account")
async def issue_dependent_account(request_id: str, payload: ReviewConfirmation, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    token = payload.review_token
    try:
        result = issue_account(container(request), _context_site_ids(context), context.auth_subject, request_id, token)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/management/requests/{request_id}/account-handover")
async def management_ticket_handover(request_id: str, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    try:
        with container(request).store.transaction():
            username = ticket_handover_username(container(request), request_id, site_ids=_context_site_ids(context))
            return container(request).store.read_handover(username, container(request).settings.settings_encryption_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/management/resident-accounts/{username}/handover")
async def management_account_handover(username: str, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    try:
        with container(request).store.transaction(), container(request).store.session() as session:
            credential = session.get(LocalCredential, username.casefold(), with_for_update=True)
            if not credential:
                raise LookupError("account_not_found")
            resident = container(request).store.get_resident(credential.resident_id)
            if not resident or resident["site_id"] not in _context_site_ids(context):
                raise LookupError("account_not_found")
            return container(request).store.read_handover(username.casefold(), container(request).settings.settings_encryption_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/management/resident-accounts")
async def create_resident_account(payload: ResidentAccountCreate, request: Request) -> dict:
    context = _require_manager(request)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_manager_required")
    if payload.account_request_id:
        raise HTTPException(status_code=409, detail="dependent_account_endpoint_required")
    app = container(request)
    locations = app.store.list_sites(_context_site_ids(context))
    location = next((item for item in locations if item["site_id"] == payload.site_id), None)
    if not location:
        raise HTTPException(status_code=403, detail="site_scope_required")
    units = app.store.list_locations({payload.site_id})
    catalog = [item for item in units if item.get("floor") is not None]
    unit = next((item for item in catalog if app.store._location_code(item["apartment"]) == app.store._location_code(payload.apartment) and (
        not payload.building.strip() or app.store._location_code(item["building"]) == app.store._location_code(payload.building)
    )), None)
    if catalog and not unit:
        raise HTTPException(status_code=422, detail="apartment_not_available")
    assigned_location = {"building": unit["building"], "apartment": unit["apartment"]} if unit else {}
    try:
        with app.store.transaction():
            values = {**payload.model_dump(exclude={"account_request_id"}), **assigned_location, "site_name": location["site_name"]}
            def create(data):
                return app.local_auth.create_resident_account(data, context.auth_subject)
            result = create(values)
            if not result.get("duplicate"):
                app.tool_service.build_checklist(result["profile"]["resident_id"])
                _audit(request, context, action="account.create", resource_type="resident", resource_id=result["profile"]["resident_id"], site_id=payload.site_id)
    except (ValueError, IntegrityError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else "username_unavailable"
        raise HTTPException(status_code=409, detail=code) from exc
    return result


@router.get("/residents/{resident_id}/household")
async def household_dashboard(resident_id: str, request: Request) -> dict:
    _authorize_resident(request, resident_id)
    try:
        return container(request).tool_service.get_household_dashboard(resident_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/requests")
async def create_request(payload: RequestCreate, request: Request) -> dict:
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail={"code": "resident_role_required", "message": "A resident role is required."})
    resident_id = context.resident_id
    if payload.resident_id not in {None, "", resident_id}:
        raise HTTPException(status_code=403, detail={"code": "resident_scope_mismatch", "message": "Resident scope mismatch."})
    if payload.request_type == "vehicle_registration":
        raise HTTPException(status_code=422, detail={"code": "vehicle_document_required", "message": "Đăng ký xe cần đính kèm ảnh giấy đăng ký. Hãy mở form Đăng ký thẻ xe."})
    if payload.request_type == "verify_residency" and payload.residency_purpose:
        profile = _resident_profile(request, resident_id)
        locations = container(request).store.list_locations({profile["site_id"]})
        target = next((item for item in locations if item["apartment"] == payload.target_apartment and (
            not payload.target_building or item["building"] == payload.target_building
        )), None)
        if not target:
            raise HTTPException(status_code=422, detail="apartment_not_available")
        purposes = {"current": "Xác nhận thông tin cư trú", "supplement": "Bổ sung hồ sơ cư trú", "correction": "Kiểm tra thông tin căn hộ"}
        note = (payload.details or "").strip()
        payload.details = f"{purposes[payload.residency_purpose]}. Cư dân: {profile['full_name']}. Khu: {profile['site_name']}. Căn hộ đề nghị kiểm tra: {target['building']} {target['apartment']}. Căn hộ đang được cấp: {profile['apartment']}. {note}"[:1000]
    if payload.request_type == "account_registration" and _resident_profile(request, resident_id)["resident_type"] != "owner":
        raise HTTPException(status_code=403, detail={"code": "owner_role_required", "message": "Chỉ chủ sở hữu mới được quyền đăng ký tính năng này"})
    try:
        result = container(request).tool_service.create_management_request(
            resident_id,
            payload.request_type,
            payload.vehicle_plate,
            payload.idempotency_key,
            payload.vehicle_type,
            payload.visitor_name,
            payload.visit_date,
            payload.details,
            payload.person_name,
            payload.citizen_id,
            payload.move_time,
            household_member_id=payload.household_member_id,
            resident_type=payload.resident_type,
            pickup_date=payload.pickup_date,
            pickup_time=payload.pickup_time,
        )
    except ValueError as error:
        raise HTTPException(status_code=403 if str(error) == "owner_role_required" else 409, detail=str(error)) from error
    if result.get("conflict"):
        raise HTTPException(status_code=409, detail=result)
    if not result.get("created") and not result.get("duplicate"):
        raise HTTPException(status_code=422, detail=result)
    _audit(request, context, action="request.create", resource_type="management_request", resource_id=result.get("request_id"))
    return result


@router.get("/requests/{request_id}")
async def request_status(request_id: str, resident_id: str, request: Request) -> dict:
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail={"code": "resident_role_required", "message": "A resident role is required."})
    if resident_id and resident_id != context.resident_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    result = container(request).tool_service.get_request_status(context.resident_id, request_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="request_not_found")
    return result


@router.get("/residents/{resident_id}/requests")
async def resident_requests(
    resident_id: str,
    request: Request,
    request_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=1000000),
    q: str = Query(default="", max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    context, _ = _authorize_resident(request, resident_id)
    result = _search_requests(request, resident_id=resident_id, subject=context.auth_subject, list_name="tickets", query=q, request_type=request_type, status=status, date_from=date_from, date_to=date_to, offset=offset, limit=limit)
    result["requests"] = [{**item, "timeline": container(request).store.request_timeline(item["request_id"])} for item in result.pop("items")]
    handover_flags(container(request), result["requests"])
    result["resident_id"] = resident_id
    result["truncated"] = result["has_more"]
    return result


@router.get("/residents/{resident_id}/requests/{request_id}/account-handover")
async def resident_ticket_handover(resident_id: str, request_id: str, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    if context.mode != "required":
        raise HTTPException(status_code=403, detail="verified_resident_required")
    try:
        with container(request).store.transaction():
            username = ticket_handover_username(container(request), request_id, resident_id=resident_id)
            return container(request).store.read_handover(username, container(request).settings.settings_encryption_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _search_requests(request: Request, **filters) -> dict:
    if filters.get("request_type") and filters["request_type"] not in REQUEST_TYPES:
        raise HTTPException(status_code=422, detail="unsupported_request_type")
    if filters.get("status") and filters["status"] not in REQUEST_STATUSES | {"active", "finished", "closed"}:
        raise HTTPException(status_code=422, detail="invalid_request_status")
    try:
        result = container(request).store.search_requests(**filters)
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail="invalid_date_range") from error
    documents = container(request).store.vehicle_document_ids([item["request_id"] for item in result["items"]])
    for item in result["items"]:
        item["vehicle_document_available"] = item["request_id"] in documents
    return result


async def _vehicle_upload(upload: UploadFile) -> tuple[bytes, str]:
    try:
        content = await upload.read(5 * 1024 * 1024 + 1)
        return normalize_vehicle_document(upload.filename or "registration.jpg", content)
    except AttachmentError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message}) from error
    finally:
        await upload.close()


@router.post("/vehicles/extract")
async def extract_vehicle_registration(request: Request, document: UploadFile = File(), consent: bool = Form(default=False)) -> dict:
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail="resident_role_required")
    if not consent:
        raise HTTPException(status_code=400, detail="image_processing_consent_required")
    content, _ = await _vehicle_upload(document)
    if not reserve_extraction(container(request).store, context.auth_subject):
        raise HTTPException(status_code=429, detail={"code": "vehicle_extraction_rate_limited", "message": "Đã đạt giới hạn đọc ảnh. Chờ 10 phút hoặc nhập tay và đính kèm ảnh."})
    return await extract_vehicle_fields(container(request), content)


@router.post("/requests/vehicle")
async def create_vehicle_request(request: Request, document: UploadFile = File(), payload: str = Form(max_length=4000), confirmed: bool = Form(default=False)) -> dict:
    context = _auth_context(request)
    if "resident" not in _roles(context) or not context.resident_id:
        raise HTTPException(status_code=403, detail="resident_role_required")
    if not confirmed:
        raise HTTPException(status_code=400, detail="request_confirmation_required")
    try:
        values = RequestCreate.model_validate_json(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid_vehicle_request") from error
    if values.request_type != "vehicle_registration":
        raise HTTPException(status_code=422, detail="invalid_vehicle_request")
    if values.resident_id not in {None, "", context.resident_id}:
        raise HTTPException(status_code=403, detail="resident_scope_mismatch")
    content, media_type = await _vehicle_upload(document)
    app = container(request)
    try:
        with app.store.transaction():
            result = app.tool_service.create_management_request(context.resident_id, values.request_type, values.vehicle_plate, values.idempotency_key, values.vehicle_type, details=values.details)
            if not result.get("created") and not result.get("duplicate"):
                raise HTTPException(status_code=409 if result.get("conflict") else 422, detail=result)
            app.store.save_vehicle_document(result["request_id"], content, media_type, "vehicle-registration.jpg")
            _audit(request, context, action="vehicle.submit", resource_type="management_request", resource_id=result["request_id"])
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {**result, "vehicle_document_available": True}


@router.get("/requests/{request_id}/vehicle-document")
async def view_vehicle_document(request_id: str, request: Request) -> Response:
    _auth_context(request)
    app = container(request)
    item = app.store.get_request(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="request_not_found")
    _authorize_resident(request, item["resident_id"])
    document = app.store.get_vehicle_document(request_id)
    if not document:
        raise HTTPException(status_code=404, detail="vehicle_document_not_found")
    return Response(content=document["content"], media_type=document["media_type"], headers={"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="vehicle-registration.jpg"'})


@router.get("/residents/{resident_id}/notifications")
async def resident_notifications(resident_id: str, request: Request, after_id: int = 0) -> dict:
    app = container(request)
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    items = app.store.list_management_notifications(resident_id, max(0, after_id), subject=context.auth_subject)
    return {
        "items": items,
        "last_event_id": items[-1]["event_id"] if items else max(0, after_id),
        "latest_event_id": app.store.latest_management_event_id(resident_id),
    }


@router.get("/residents/{resident_id}/notifications/feed")
async def notification_feed(resident_id: str, request: Request, before_id: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=50)) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    return container(request).store.management_notification_feed(resident_id, context.auth_subject, before_id, limit)


@router.post("/residents/{resident_id}/notifications/read-all")
async def notifications_read_all(resident_id: str, payload: NotificationsReadAll, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    count = container(request).store.read_management_notifications(resident_id, context.auth_subject, through_event_id=payload.through_event_id)
    return {"read_count": count}


@router.get("/residents/{resident_id}/notifications/groups")
async def notification_groups(
    resident_id: str, request: Request,
    query: str = Query(default="", alias="q", max_length=120),
    request_type: str | None = None, status: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    if request_type and request_type not in REQUEST_TYPES:
        raise HTTPException(status_code=422, detail="unsupported_request_type")
    if status and status not in REQUEST_STATUSES | {"active", "finished", "closed"}:
        raise HTTPException(status_code=422, detail="invalid_request_status")
    try:
        result = container(request).store.management_notification_groups(
            resident_id, context.auth_subject, query=query, request_type=request_type, status=status,
            date_from=date_from, date_to=date_to, offset=offset, limit=limit,
        )
        return result
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail="invalid_date_range") from error


@router.post("/residents/{resident_id}/notifications/tickets/{request_id}/read")
async def notification_ticket_read(resident_id: str, request_id: str, payload: NotificationsReadAll, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        count = container(request).store.read_management_notifications(
            resident_id, context.auth_subject, through_event_id=payload.through_event_id, request_id=request_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"read_count": count}


@router.post("/residents/{resident_id}/notifications/{event_id}/read")
async def notification_read(resident_id: str, event_id: int, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        container(request).store.read_management_notifications(resident_id, context.auth_subject, event_id=event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"event_id": event_id, "read": True}


@router.post("/residents/{resident_id}/requests/{request_id}/cancel")
async def cancel_resident_request(resident_id: str, request_id: str, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        result = container(request).tool_service.cancel_management_request(resident_id, request_id)
        _audit(request, context, action="request.cancel", resource_type="management_request", resource_id=request_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/residents/{resident_id}/requests/{request_id}")
async def update_resident_request(resident_id: str, request_id: str, payload: RequestUpdate, request: Request) -> dict:
    _authorize_resident(request, resident_id, allow_manager=False)
    try:
        return container(request).tool_service.update_management_request(
            resident_id, request_id, **payload.model_dump(),
        )
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(status_code=404 if code == "request_not_found" else 409 if code in {"request_not_editable", "request_version_conflict", "request_duplicate"} else 422, detail=code) from exc


@router.post("/residents/{resident_id}/request-corrections/chat")
async def ticket_correction_chat(resident_id: str, payload: CorrectionChatRequest, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    app = container(request)
    _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)
    try:
        return await container(request).correction_assistant.reply(resident_id, payload)
    except RuntimeNotConfigured as error:
        raise HTTPException(status_code=503, detail="agent_not_configured") from error
    except APIError as error:
        code, message = llm_error_detail(error)
        raise HTTPException(status_code=502, detail={"code": code, "message": message}) from error
    except ValueError as error:
        code = str(error)
        raise HTTPException(status_code=404 if code == "request_not_found" else 409 if code in {"request_not_editable", "request_version_conflict"} else 422, detail=code) from error


@router.patch("/residents/{resident_id}/requests/{request_id}/vehicle")
async def update_resident_vehicle_request(
    resident_id: str, request_id: str, request: Request,
    document: UploadFile = File(), payload: str = Form(max_length=4000), confirmed: bool = Form(default=False),
) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    if not confirmed:
        raise HTTPException(status_code=400, detail="request_confirmation_required")
    try:
        values = RequestUpdate.model_validate_json(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid_vehicle_request") from error
    app = container(request)
    current = app.tool_service.get_request_status(resident_id, request_id)
    if not current["found"] or current["request_type"] != "vehicle_registration":
        raise HTTPException(status_code=404, detail="request_not_found")
    content, media_type = await _vehicle_upload(document)
    try:
        with app.store.transaction():
            app.store.replace_vehicle_document(resident_id, current["request_id"], content, media_type)
            result = app.tool_service.update_management_request(resident_id, request_id, **values.model_dump())
            _audit(request, context, action="vehicle.correct", resource_type="management_request", resource_id=result["request_id"])
    except ValueError as error:
        code = str(error)
        raise HTTPException(status_code=404 if code == "request_not_found" else 409 if code in {"request_not_editable", "request_version_conflict", "request_duplicate"} else 422, detail=code) from error
    return {**result, "vehicle_document_available": True}


@router.post("/residents/{resident_id}/requests/{request_id}/remove-person")
async def remove_person_from_household(resident_id: str, request_id: str, payload: HouseholdRemoval, request: Request) -> dict:
    return _remove_household_entry(request, resident_id, request_id, "temporary_residency", payload)


@router.post("/residents/{resident_id}/requests/{request_id}/remove-vehicle")
async def remove_vehicle_from_household(resident_id: str, request_id: str, payload: HouseholdRemoval, request: Request) -> dict:
    return _remove_household_entry(request, resident_id, request_id, "vehicle_registration", payload)


@router.post("/residents/{resident_id}/requests/{request_id}/cancel-internet")
async def cancel_household_internet(resident_id: str, request_id: str, payload: HouseholdRemoval, request: Request) -> dict:
    return _remove_household_entry(request, resident_id, request_id, "internet_registration", payload)


def _remove_household_entry(request: Request, resident_id: str, request_id: str, request_type: str, payload: HouseholdRemoval) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    app = container(request)
    try:
        with app.store.transaction():
            result = app.store.remove_household_request(resident_id, request_id.strip().upper(), request_type, expected_updated_at=payload.expected_updated_at.isoformat())
            _audit(request, context, action="household.remove", resource_type="management_request", resource_id=result["request_id"])
            return result
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(status_code=404 if code == "request_not_found" else 409, detail=code) from exc


@router.post("/demo/reset")
async def reset_demo(request: Request) -> dict:
    raise HTTPException(status_code=410, detail="reset_endpoint_retired")


@router.get("/management/requests")
async def management_requests(request: Request, q: str = Query(default="", max_length=120), request_type: str | None = None, status: str | None = None, date_from: date | None = None, date_to: date | None = None, offset: int = Query(default=0, ge=0, le=1000000), limit: int = Query(default=20, ge=1, le=50)) -> dict:
    context = _require_manager(request)
    store = container(request).store
    site_ids = _context_site_ids(context)
    result = _search_requests(request, site_ids=site_ids, subject=context.auth_subject, list_name="management", query=q, request_type=request_type, status=status, date_from=date_from, date_to=date_to, offset=offset, limit=limit)
    result["items"] = [{**item, "resident_profile": store.get_resident(item["resident_id"]), "timeline": store.request_timeline(item["request_id"])} for item in result["items"]]
    handover_flags(container(request), result["items"])
    return result


@router.post("/residents/{resident_id}/requests/hide/preview")
async def resident_hide_preview(resident_id: str, payload: HideSelection, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        return hide_preview(container(request), context.auth_subject, payload, resident_id=resident_id, site_ids=None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/residents/{resident_id}/requests/hide")
async def resident_hide_confirm(resident_id: str, payload: ReviewConfirmation, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        return hide_confirm(container(request), context.auth_subject, payload.review_token, resident_id=resident_id, site_ids=None, list_name="tickets")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/management/requests/hide/preview")
async def management_hide_preview(payload: HideSelection, request: Request) -> dict:
    context = _require_manager(request)
    try:
        return hide_preview(container(request), context.auth_subject, payload, resident_id=None, site_ids=_context_site_ids(context))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/management/requests/hide")
async def management_hide_confirm(payload: ReviewConfirmation, request: Request) -> dict:
    context = _require_manager(request)
    try:
        return hide_confirm(container(request), context.auth_subject, payload.review_token, resident_id=None, site_ids=_context_site_ids(context), list_name="management")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/residents/{resident_id}/notifications/hide")
async def notification_hide_confirm(resident_id: str, payload: ReviewConfirmation, request: Request) -> dict:
    context, _ = _authorize_resident(request, resident_id, allow_manager=False)
    try:
        return hide_confirm(container(request), context.auth_subject, payload.review_token, resident_id=resident_id, site_ids=None, list_name="notifications")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/management/assistant/chat")
async def management_assistant(payload: ManagerAssistantRequest, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    app = container(request)
    _reserve_ai_request(app, context, "chat", app.settings.chat_requests_per_10_minutes)
    try:
        if payload.conversation_id:
            return await conversation_chat(app, context, payload, lambda item, action: _audit(
                request, context, action="request.hide" if action == "hide" else "request.decide",
                resource_type="management_request", resource_id=item["request_id"],
            ))
        return await manager_answer(app, _context_site_ids(context), payload.message, payload.request_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="request_not_found") from None
    except ValueError as exc:
        if payload.conversation_id:
            raise HTTPException(status_code=422 if str(exc) == "invalid_date_range" else 409, detail=str(exc)) from None
        raise HTTPException(status_code=422, detail="invalid_date_range") from None


@router.post("/management/assistant/proposal/preview")
async def management_conversation_preview(payload: ConversationPreview, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    try:
        return preview_conversation(container(request), context, payload)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="request_not_found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/management/assistant/conversations/{conversation_id}")
async def management_conversation_context(conversation_id: str, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    try:
        with container(request).store.transaction():
            from src.services.manager_conversation import conversation_row
            row = conversation_row(container(request), context, conversation_id)
            return {"conversation": {"id": row.id, "revision": row.revision}, "read_only": True}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="conversation_not_found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/management/assistant/proposal/confirm")
async def management_conversation_confirm(payload: ConversationConfirmation, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    try:
        return execute_conversation(container(request), context, payload, lambda item, action: _audit(
            request, context, action="request.hide" if action == "hide" else "request.decide",
            resource_type="management_request", resource_id=item["request_id"],
        ))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="request_not_found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/management/assistant/batch-preview")
async def management_batch_preview(payload: ManagerBatchPreview, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    try:
        return {"items": preview_batch(container(request), _context_site_ids(context), payload), "read_only": True}
    except LookupError:
        raise HTTPException(status_code=404, detail="request_not_found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/management/assistant/batch-decision")
async def management_batch_decision(payload: ManagerBatchDecision, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    context = _require_manager(request)
    app = container(request)
    try:
        with app.store.transaction():
            items = apply_batch_decision(app, _context_site_ids(context), payload)
            for item in items:
                _audit(request, context, action="request.decide", resource_type="management_request", resource_id=item["request_id"])
        return {"items": items, "updated": len(items)}
    except LookupError:
        raise HTTPException(status_code=404, detail="request_not_found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.patch("/management/requests/{request_id}")
async def management_decision(
    request_id: str,
    payload: ManagementDecision,
    request: Request,
) -> dict:
    app = container(request)
    context = _require_manager(request)
    if not _request_in_manager_scope(request, context, request_id):
        raise HTTPException(status_code=404, detail="request_not_found")
    try:
        result = app.store.decide_request(request_id, payload.status, payload.reason)
        _audit(request, context, action="request.decide", resource_type="management_request", resource_id=request_id)
        return result
    except ValueError as exc:
        status_code = 409 if str(exc) in {"invalid_status_transition", "account_issuance_required"} else 404
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
