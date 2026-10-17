import hashlib
import json
import secrets
from datetime import date, timedelta
from typing import Literal

import jwt
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.repositories.store import CredentialHandover, LocalCredential, ManagementRequest, Resident, now_utc

TERMINAL_STATUSES = {"completed", "rejected", "cancelled_by_resident"}


class ReviewConfirmation(BaseModel):
    model_config = {"extra": "forbid"}
    review_token: str = Field(min_length=1, max_length=4000)
    confirmed: Literal[True]


class HideSelection(BaseModel):
    model_config = {"extra": "forbid"}
    list_name: Literal["tickets", "notifications", "management"]
    request_id: str | None = Field(default=None, pattern=r"^REQ-[A-Z0-9]{8}$")
    query: str = Field(default="", max_length=120)
    request_type: str | None = None
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    terminal_statuses: list[Literal["completed", "rejected", "cancelled_by_resident"]] = Field(
        default_factory=lambda: ["completed", "rejected", "cancelled_by_resident"], min_length=1, max_length=3,
    )


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sign_review(app, subject: str, purpose: str, data: dict) -> str:
    return jwt.encode({"sub": subject, "purpose": purpose, "data": data,
                       "generation": app.store.login_reset_generation(), "exp": now_utc() + timedelta(minutes=10)},
                      app.settings.auth_jwt_secret or app.local_auth.dummy_hash.hex(), algorithm="HS256")


def verify_review(app, subject: str, purpose: str, token: str) -> dict:
    try:
        result = jwt.decode(token, app.settings.auth_jwt_secret or app.local_auth.dummy_hash.hex(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ValueError("review_expired") from None
    if result.get("sub") != subject or result.get("purpose") != purpose or result.get("generation") != app.store.login_reset_generation():
        raise ValueError("review_expired")
    return result["data"]


def account_snapshot(app, item, session) -> tuple[dict, str]:
    owner = session.query(Resident).filter_by(id=item.resident_id).with_for_update().one()
    source = session.get(ManagementRequest, item.payload_json.get("household_member_id", ""), with_for_update=True)
    member = app.store.account_registration_member(item.resident_id, item.payload_json.get("household_member_id", ""))
    snapshot = {"ticket": app.store._request_dict(item), "source": app.store._request_dict(source),
                "owner": app.store._resident_dict(owner), "member": member}
    return member, digest(snapshot)


def account_preview(app, site_ids: set[str], subject: str, request_id: str) -> dict:
    with app.store.transaction(), app.store.session() as session:
        item = session.query(ManagementRequest).filter_by(id=request_id).with_for_update().one_or_none()
        owner = session.get(Resident, item.resident_id) if item else None
        if not item or not owner or owner.site_id not in site_ids or item.request_type != "account_registration":
            raise LookupError("request_not_found")
        if item.status not in {"submitted", "in_review", "approved"} or item.payload_json.get("app_account_resident_id"):
            raise ValueError("account_request_not_eligible")
        member, fingerprint = account_snapshot(app, item, session)
        try:
            Fernet(app.settings.settings_encryption_key.encode("ascii"))
        except (ValueError, UnicodeError):
            raise ValueError("settings_encryption_key_required") from None
        return {"member": member, "review_token": sign_review(app, subject, "issue_account", {"request_id": request_id, "fingerprint": fingerprint})}


def issue_account(app, site_ids: set[str], subject: str, request_id: str, token: str) -> dict:
    review = verify_review(app, subject, "issue_account", token)
    if review.get("request_id") != request_id:
        raise ValueError("review_changed")
    with app.store.transaction(), app.store.session() as session:
        item = session.query(ManagementRequest).filter_by(id=request_id).with_for_update().one_or_none()
        owner = session.get(Resident, item.resident_id) if item else None
        if not item or not owner or owner.site_id not in site_ids or item.request_type != "account_registration":
            raise LookupError("request_not_found")
        if item.payload_json.get("app_account_resident_id"):
            credential = session.query(LocalCredential).filter_by(resident_id=item.payload_json["app_account_resident_id"]).one()
            handover = app.store.read_handover(credential.username, app.settings.settings_encryption_key)
            return {"username": credential.username, "temporary_password": handover.get("temporary_password"),
                    "password_change_required": handover["password_change_required"], "duplicate": True, "request_id": request_id}
        member, fingerprint = account_snapshot(app, item, session)
        if fingerprint != review["fingerprint"] or item.status not in {"submitted", "in_review", "approved"}:
            raise ValueError("review_changed")
        source = session.get(ManagementRequest, member["household_member_id"])
        identity = digest({"site_id": member["site_id"], "person": source.payload_json.get("citizen_id") or member["person_name"].strip().casefold()})
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(identity[:15], 16)})
        member = app.store.account_registration_member(item.resident_id, member["household_member_id"])
        username = "cd." + secrets.token_hex(8)
        while username in app.local_auth.accounts or app.store.get_local_credential(username=username):
            username = "cd." + secrets.token_hex(8)
        password = "Mm!" + secrets.token_urlsafe(14)
        values = {"username": username, "password": password, "full_name": member["person_name"],
                  **{key: member[key] for key in ("resident_type", "site_id", "site_name", "building", "apartment")},
                  "idempotency_key": "dependent:" + request_id}
        if item.status != "approved":
            app.store.decide_request(request_id, "approved", "BQL xác nhận hồ sơ cấp tài khoản cư dân phụ thuộc hợp lệ.")
        result = app.store.issue_requested_account(request_id, values, lambda data: app.local_auth.create_resident_account(data, subject),
                                                  temporary_password=password, encryption_key=app.settings.settings_encryption_key)
        app.tool_service.build_checklist(result["profile"]["resident_id"])
        return {"username": result["username"], "temporary_password": password, "request_id": request_id, "duplicate": False,
                "password_change_required": True}


def ticket_handover_username(app, request_id: str, *, resident_id: str | None = None, site_ids: set[str] | None = None) -> str:
    with app.store.session() as session:
        item = session.get(ManagementRequest, request_id, with_for_update=True)
        owner = session.get(Resident, item.resident_id, with_for_update=True) if item else None
        if not item or not owner or (site_ids is not None and owner.site_id not in site_ids) or (resident_id is not None and (owner.id != resident_id or owner.resident_type != "owner")):
            raise LookupError("request_not_found")
        account_id = item.payload_json.get("app_account_resident_id") if item.request_type == "account_registration" else None
        credential = session.query(LocalCredential).filter_by(resident_id=account_id).one_or_none() if account_id else None
        if not credential:
            raise LookupError("account_not_found")
        return credential.username


def handover_flags(app, items: list[dict]) -> None:
    with app.store.session() as session:
        account_ids = [item.get("payload", {}).get("app_account_resident_id") for item in items]
        credentials = {row.resident_id: row for row in session.query(LocalCredential).filter(LocalCredential.resident_id.in_(account_ids))}
        available = {row.username for row in session.query(CredentialHandover.username)}
        for item in items:
            credential = credentials.get(item.get("payload", {}).get("app_account_resident_id"))
            item["account_handover_available"] = bool(credential and item["request_type"] == "account_registration")
            item["temporary_password_available"] = bool(credential and credential.password_change_required and credential.username in available)


def hide_items(app, subject: str, selection: HideSelection, *, resident_id: str | None, site_ids: set[str] | None) -> list[dict]:
    from src.tools.agent_tools import REQUEST_STATUSES, REQUEST_TYPES
    if selection.request_type and selection.request_type not in REQUEST_TYPES:
        raise ValueError("unsupported_request_type")
    if selection.status and selection.status not in REQUEST_STATUSES | {"active", "finished", "closed"}:
        raise ValueError("invalid_request_status")
    if (site_ids is not None and selection.list_name != "management") or (resident_id is not None and selection.list_name not in {"tickets", "notifications"}):
        raise ValueError("list_scope_required")
    filters = selection.model_dump(exclude={"list_name", "terminal_statuses"})
    result = app.store.search_requests(**filters, subject=subject, list_name=selection.list_name, resident_id=resident_id,
                                       site_ids=site_ids, management_updates_only=selection.list_name == "notifications", limit=1000001)
    if result["total"] > 1000000:
        raise ValueError("hide_selection_too_large")
    if selection.request_id and (not result["items"] or result["items"][0]["status"] not in TERMINAL_STATUSES):
        raise ValueError("request_not_terminal")
    return [item for item in result["items"] if item["status"] in set(selection.terminal_statuses)]


def hide_preview(app, subject: str, selection: HideSelection, *, resident_id: str | None, site_ids: set[str] | None) -> dict:
    items = hide_items(app, subject, selection, resident_id=resident_id, site_ids=site_ids)
    snapshot = [{key: item[key] for key in ("request_id", "updated_at", "status")} for item in items]
    data = {"selection": selection.model_dump(mode="json"), "fingerprint": digest(snapshot)}
    return {"count": len(items), "review_token": sign_review(app, subject, "hide_requests", data)}


def hide_confirm(app, subject: str, token: str, *, resident_id: str | None, site_ids: set[str] | None, list_name: str) -> dict:
    review = verify_review(app, subject, "hide_requests", token)
    selection = HideSelection.model_validate(review["selection"])
    if selection.list_name != list_name:
        raise ValueError("review_changed")
    with app.store.transaction(), app.store.session() as session:
        lock_key = int(app.store.visibility_key(subject, list_name)[:15], 16)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        items = hide_items(app, subject, selection, resident_id=resident_id, site_ids=site_ids)
        ids = [item["request_id"] for item in items]
        session.query(ManagementRequest).filter(ManagementRequest.id.in_(ids)).order_by(ManagementRequest.id).with_for_update().all()
        items = hide_items(app, subject, selection, resident_id=resident_id, site_ids=site_ids)
        snapshot = [{key: item[key] for key in ("request_id", "updated_at", "status")} for item in items]
        if digest(snapshot) != review["fingerprint"]:
            raise ValueError("review_changed")
        return app.store.hide_requests(ids, subject, list_name)
