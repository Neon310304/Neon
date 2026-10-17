import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from src.repositories.store import (
    AuthChallenge,
    CredentialHandover,
    LocalCredential,
    PasswordRecoveryRequest,
    Resident,
    now_utc,
)
from src.security.local_auth import _password_digest


class EmailStart(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    current_password: str = Field(default="", max_length=256, repr=False)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+", value):
            raise ValueError("email_invalid")
        return value


class EmailConfirm(BaseModel):
    challenge_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    code: str = Field(pattern=r"^\d{6}$", repr=False)
    new_password: str = Field(default="", max_length=256, repr=False)


class RecoveryIntake(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.@-]+$")
    full_name: str = Field(min_length=2, max_length=120)
    apartment: str = Field(min_length=1, max_length=40)
    building: str = Field(default="", max_length=80)
    contact: str = Field(min_length=5, max_length=160)
    note: str = Field(default="", max_length=500)
    confirmed: Literal[True]

    @field_validator("full_name", "apartment", "contact")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("field_required")
        return value.strip()


class ManagerPasswordReset(BaseModel):
    confirmed: Literal[True]
    identity_checked: Literal[True]
    reason: str = Field(min_length=5, max_length=500)
    recovery_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("reason_required")
        return value.strip()


class ManagerRecoveryReject(BaseModel):
    confirmed: Literal[True]
    reason: str = Field(min_length=5, max_length=500, pattern=r".*\S.*")


def mail_available(settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_code(settings, recipient, code):
    if not mail_available(settings):
        raise ValueError("email_delivery_unavailable")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = "MoveInMate — mã xác thực"
    message.set_content(f"Mã xác thực của bạn: {code}\nCó hiệu lực 15 phút, chỉ dùng một lần. Không gửi mã cho BQL hoặc người khác. Nếu không yêu cầu, hãy bỏ qua email này.")
    try:
        connection = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10, context=ssl.create_default_context()) if settings.smtp_port == 465 else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        with connection as server:
            if settings.smtp_port != 465:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        raise ValueError("email_delivery_unavailable") from None


def code_digest(settings, challenge_id, code):
    return hmac.new(settings.auth_jwt_secret.encode(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def start_email(app, payload: EmailStart, purpose: str, subject: str | None = None, challenge_id: str | None = None) -> dict:
    if not mail_available(app.settings):
        raise ValueError("email_delivery_unavailable")
    credential = app.store.get_local_credential(username=payload.username.strip().casefold()) if purpose == "reset_password" else app.store.get_local_credential(auth_subject=subject)
    username = credential["username"] if credential else payload.username.strip().casefold()
    challenge_id = challenge_id or uuid4().hex
    if purpose == "verify_email":
        if not credential or username in app.local_auth.accounts:
            raise ValueError("issued_account_required")
        if not hmac.compare_digest(_password_digest(payload.current_password, bytes.fromhex(credential["salt"])).hex(), credential["password_hash"]):
            raise ValueError("current_password_invalid")
    elif not credential or credential.get("recovery_email") != payload.email:
        return {"challenge_id": challenge_id, "message": "Nếu thông tin khớp với email đã xác thực, mã sẽ được gửi. Bạn cũng có thể gửi yêu cầu cho BQL."}
    code = f"{secrets.randbelow(1000000):06d}"
    with app.store.session() as session:
        session.query(AuthChallenge).filter(AuthChallenge.expires_at < now_utc()).delete(synchronize_session=False)
        session.add(AuthChallenge(id=challenge_id, username=username, purpose=purpose, email=payload.email, code_hash=code_digest(app.settings, challenge_id, code), credential_hash=credential["password_hash"], expires_at=now_utc() + timedelta(minutes=15), attempts=0, used=False))
    try:
        send_code(app.settings, payload.email, code)
    except ValueError:
        with app.store.session() as session:
            session.query(AuthChallenge).filter_by(id=challenge_id).delete()
        raise
    return {"challenge_id": challenge_id, "message": "Nếu thông tin khớp với email đã xác thực, mã sẽ được gửi. Bạn cũng có thể gửi yêu cầu cho BQL." if purpose == "reset_password" else "Đã gửi mã xác thực email. Mã có hiệu lực 15 phút."}


def deliver_recovery_email(app, payload, challenge_id):
    try:
        start_email(app, payload, "reset_password", challenge_id=challenge_id)
    except ValueError:
        return


def confirm_email(app, payload: EmailConfirm, purpose: str, subject: str | None = None) -> None:
    if purpose == "reset_password" and (not 8 <= len(payload.new_password) <= 256 or not payload.new_password.strip()):
        raise ValueError("new_password_invalid")
    failure = False
    with app.store.session() as session:
        candidate_username = session.query(AuthChallenge.username).filter_by(id=payload.challenge_id).scalar()
        credential = session.query(LocalCredential).filter_by(username=candidate_username).with_for_update().one_or_none() if candidate_username else None
        challenge = session.query(AuthChallenge).filter_by(id=payload.challenge_id).with_for_update().one_or_none()
        if not challenge or challenge.purpose != purpose or challenge.used or challenge.expires_at <= now_utc() or challenge.attempts >= 5:
            failure = True
        elif purpose == "verify_email" and (not credential or subject != credential.auth_subject):
            failure = True
        else:
            challenge.attempts += 1
            failure = not credential or credential.password_hash != challenge.credential_hash or not hmac.compare_digest(challenge.code_hash, code_digest(app.settings, challenge.id, payload.code))
            if not failure:
                if purpose == "verify_email":
                    credential.recovery_email = challenge.email
                    session.query(AuthChallenge).filter_by(username=credential.username, purpose="verify_email").update({"used": True}, synchronize_session=False)
                else:
                    failure = credential.recovery_email != challenge.email or hmac.compare_digest(_password_digest(payload.new_password, bytes.fromhex(credential.salt)).hex(), credential.password_hash)
                    if not failure:
                        salt = secrets.token_bytes(16)
                        credential.salt = salt.hex()
                        credential.password_hash = _password_digest(payload.new_password, salt).hex()
                        credential.password_change_required = False
                        session.query(CredentialHandover).filter_by(username=credential.username).delete(synchronize_session=False)
                challenge.used = not failure
    if failure:
        raise ValueError("verification_invalid_or_expired")


def recovery_requests(store, site_ids: set[str]) -> list[dict]:
    with store.session() as session:
        rows = session.query(PasswordRecoveryRequest, Resident).join(LocalCredential, LocalCredential.username == PasswordRecoveryRequest.username).join(Resident, Resident.id == LocalCredential.resident_id).filter(Resident.site_id.in_(site_ids), PasswordRecoveryRequest.status == "pending", PasswordRecoveryRequest.created_at >= now_utc() - timedelta(days=30)).order_by(PasswordRecoveryRequest.created_at.desc()).limit(100).all()
        return [{"request_id": row.id, "username": row.username, "full_name": row.full_name, "apartment": row.apartment, "building": row.building, "contact": row.contact, "note": row.note, "created_at": row.created_at.isoformat(), "registered_name": resident.full_name, "registered_address": " · ".join(filter(None, [resident.site_name, resident.building, resident.apartment])), "matches_profile": row.full_name.casefold() == resident.full_name.casefold() and row.apartment.casefold() == resident.apartment.casefold()} for row, resident in rows]


def intake_recovery(store, payload: RecoveryIntake) -> None:
    with store.session() as session:
        session.query(PasswordRecoveryRequest).filter(PasswordRecoveryRequest.created_at < now_utc() - timedelta(days=30)).delete(synchronize_session=False)
        session.add(PasswordRecoveryRequest(id=uuid4().hex, **payload.model_dump(exclude={"confirmed"}), status="pending", reason=""))


def reject_recovery(store, site_ids: set[str], request_id: str, reason: str) -> None:
    if len(reason.strip()) < 5:
        raise ValueError("reason_required")
    with store.session() as session:
        recovery = session.query(PasswordRecoveryRequest).filter_by(id=request_id, status="pending").with_for_update().one_or_none()
        credential = session.get(LocalCredential, recovery.username) if recovery else None
        resident = session.get(Resident, credential.resident_id) if credential else None
        if not resident or resident.site_id not in site_ids:
            raise LookupError("recovery_not_found")
        recovery.status = "rejected"
        recovery.reason = reason.strip()


def manager_reset(app, site_ids: set[str], username: str, payload: ManagerPasswordReset) -> dict:
    temporary = "Mm!" + secrets.token_urlsafe(12)
    with app.store.transaction(), app.store.session() as session:
        credential = session.query(LocalCredential).filter_by(username=username.casefold()).with_for_update().one_or_none()
        resident = session.get(Resident, credential.resident_id) if credential else None
        if not resident or resident.site_id not in site_ids:
            raise LookupError("account_not_found")
        if payload.recovery_id:
            recovery = session.query(PasswordRecoveryRequest).filter_by(id=payload.recovery_id, username=credential.username, status="pending").with_for_update().one_or_none()
            if not recovery or recovery.created_at < now_utc() - timedelta(days=30):
                raise ValueError("recovery_not_pending")
        salt = secrets.token_bytes(16)
        credential.salt = salt.hex()
        credential.password_hash = _password_digest(temporary, salt).hex()
        credential.password_change_required = True
        if app.settings.settings_encryption_key:
            app.store.save_handover(credential.username, temporary, app.settings.settings_encryption_key)
        else:
            session.query(CredentialHandover).filter_by(username=credential.username).delete(synchronize_session=False)
        session.query(AuthChallenge).filter_by(username=credential.username).delete(synchronize_session=False)
        session.query(PasswordRecoveryRequest).filter_by(username=credential.username, status="pending").update({"status": "resolved", "reason": payload.reason}, synchronize_session=False)
    return {"username": username.casefold(), "temporary_password": temporary, "password_change_required": True}
