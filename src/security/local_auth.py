import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import jwt

from src.security.errors import AuthenticationError, AuthorizationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalAccount:
    username: str
    password: str = field(repr=False)
    role: str
    site_id: str
    resident_id: str | None
    additional_site_ids: tuple[str, ...] = ()

    @property
    def subject(self) -> str:
        return f"local:{self.username}"


def configured_accounts(settings: Any) -> tuple[LocalAccount, ...]:
    site_id = str(getattr(settings, "local_auth_site_id", "")).strip()
    admin_sites = tuple(sorted({value.strip() for value in str(getattr(settings, "local_auth_admin_site_ids", "")).split(",") if value.strip()} - {site_id}))
    return (
        LocalAccount(
            str(getattr(settings, "local_auth_admin_username", "")).strip().casefold(),
            str(getattr(settings, "local_auth_admin_password", "")),
            "manager", site_id, None, admin_sites,
        ),
        LocalAccount(
            str(getattr(settings, "local_auth_user_username", "")).strip().casefold(),
            str(getattr(settings, "local_auth_user_password", "")),
            "resident", site_id,
            str(getattr(settings, "local_auth_user_resident_id", "")).strip(),
        ),
    )


def local_auth_enabled(settings: Any) -> bool:
    accounts = configured_accounts(settings)
    secret = str(getattr(settings, "auth_jwt_secret", ""))
    minimum_password_length = 6 if getattr(settings, "app_env", "production") in {"development", "test"} else 8
    return bool(
        getattr(settings, "auth_mode", "required") == "required"
        and getattr(settings, "auth_provider", "local") == "local"
        and len(secret.encode()) >= 32
        and getattr(settings, "auth_jwt_issuer", "")
        and getattr(settings, "auth_jwt_audience", "")
        and len({account.username for account in accounts}) == len(accounts)
        and all(
            re.fullmatch(r"[a-z0-9_.@-]{1,80}", account.username)
            and minimum_password_length <= len(account.password) <= 256
            and account.site_id
            and all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", site) for site in account.additional_site_ids)
            and (account.role != "resident" or account.resident_id)
            for account in accounts
        )
    )


def _jwt_error(error: BaseException) -> AuthenticationError:
    if isinstance(error, jwt.ExpiredSignatureError):
        return AuthenticationError("token_expired", "Authentication token has expired.")
    if isinstance(error, jwt.InvalidAudienceError):
        return AuthenticationError("invalid_audience", "Invalid authentication audience.")
    if isinstance(error, jwt.InvalidIssuerError):
        return AuthenticationError("invalid_issuer", "Invalid authentication issuer.")
    return AuthenticationError("invalid_token", "Invalid authentication token.")


def verify_hs256_token(
    token: str, *, secret: str, issuer: str = "", audience: str = "", leeway_seconds: int = 60,
) -> dict[str, Any]:
    if not secret:
        raise AuthenticationError("authentication_not_configured", "Token verification is not configured.")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("typ", "JWT") != "JWT" or header.get("alg") != "HS256":
            raise AuthenticationError("invalid_token", "Unsupported authentication token.")
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], issuer=issuer or None, audience=audience or None,
            leeway=leeway_seconds,
            options={"require": ["exp", "sub"], "verify_iss": bool(issuer), "verify_aud": bool(audience)},
        )
    except jwt.PyJWTError as error:
        raise _jwt_error(error) from error
    if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        raise AuthenticationError("invalid_subject", "Authentication subject is missing.")
    return payload


def _credential_tag(account: LocalAccount, secret: str) -> str:
    identity = [account.subject, account.password, account.role, account.site_id, account.resident_id]
    if account.additional_site_ids:
        identity.append(account.additional_site_ids)
    value = json.dumps(identity)
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _stored_credential_tag(credential: dict, secret: str) -> str:
    value = json.dumps([credential[key] for key in ("auth_subject", "resident_id", "salt", "password_hash")])
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def verify_local_token(token: str, settings: Any, store: Any = None) -> dict[str, Any]:
    if not local_auth_enabled(settings):
        raise AuthenticationError("authentication_not_configured", "Đăng nhập nội bộ chưa được cấu hình trong .env.")
    payload = verify_hs256_token(
        token, secret=settings.auth_jwt_secret,
        issuer=settings.auth_jwt_issuer, audience=settings.auth_jwt_audience,
        leeway_seconds=settings.auth_jwt_leeway_seconds,
    )
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 80:
        raise AuthenticationError("invalid_session", "Authentication session is missing or invalid.")
    generation = getattr(store, "login_reset_generation", None)
    if callable(generation) and payload.get("reset_generation", 0) != generation():
        raise AuthenticationError("session_reset", "Hệ thống đã Reset. Vui lòng đăng nhập lại.")
    account = next((account for account in configured_accounts(settings) if account.subject == payload["sub"]), None)
    credential = None
    if account is None and payload["sub"].startswith("local:") and callable(getattr(store, "get_local_credential", None)):
        credential = store.get_local_credential(auth_subject=payload["sub"])
    expected_tag = _credential_tag(account, settings.auth_jwt_secret) if account else _stored_credential_tag(credential, settings.auth_jwt_secret) if credential else ""
    credential_tag = payload.get("credential_tag")
    if not expected_tag or not isinstance(credential_tag, str) or not re.fullmatch(r"[0-9a-f]{64}", credential_tag) or not hmac.compare_digest(
        credential_tag, expected_tag,
    ):
        raise AuthenticationError("session_expired", "Tài khoản đã thay đổi. Vui lòng đăng nhập lại.")
    return payload


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


class LocalAuthService:
    def __init__(self, settings: Any, store: Any) -> None:
        self.settings = settings
        self.store = store
        self.accounts = {account.username: account for account in configured_accounts(settings)}
        self.password_hashes: dict[str, tuple[bytes, bytes]] = {}
        self.dummy_salt = secrets.token_bytes(16)
        self.dummy_hash = _password_digest(secrets.token_urlsafe(32), self.dummy_salt)
        if local_auth_enabled(settings):
            for account in self.accounts.values():
                salt = secrets.token_bytes(16)
                self.password_hashes[account.username] = (salt, _password_digest(account.password, salt))

    def login_hints(self) -> dict[str, Any]:
        if (
            not local_auth_enabled(self.settings)
            or not self.settings.show_local_login_credentials
            or self.settings.app_env not in {"development", "test"}
        ):
            return {"visible": False, "accounts": []}
        return {
            "visible": True,
            "accounts": [
                {
                    "label": "Ban quản lý" if account.role == "manager" else "Cư dân",
                    "username": account.username,
                    "password": account.password,
                }
                for account in self.accounts.values()
            ],
        }

    def provision_accounts(self) -> None:
        if not local_auth_enabled(self.settings):
            return
        with self.store.transaction():
            for account in self.accounts.values():
                if account.role == "resident":
                    profile = self.store.get_resident(account.resident_id)
                    if not profile or profile["site_id"] != account.site_id:
                        logger.warning("Configured resident account has no matching profile; its login stays disabled.")
                        continue
                self.store.ensure_local_membership(
                    auth_subject=account.subject, role=account.role,
                    site_id=account.site_id, resident_id=account.resident_id,
                    additional_site_ids=account.additional_site_ids,
                )

    def verify_password(self, username: str, password: str) -> str:
        if not local_auth_enabled(self.settings):
            raise AuthenticationError("authentication_not_configured", "Đăng nhập nội bộ chưa được cấu hình trong .env.")
        username = username.strip().casefold()
        account = self.accounts.get(username)
        credential = None
        if account is None and callable(getattr(self.store, "get_local_credential", None)):
            credential = self.store.get_local_credential(username=username)
        salt, expected = self.password_hashes.get(username, (self.dummy_salt, self.dummy_hash))
        if credential:
            salt, expected = bytes.fromhex(credential["salt"]), bytes.fromhex(credential["password_hash"])
        valid = hmac.compare_digest(_password_digest(password, salt), expected)
        if not valid or (account is None and credential is None):
            raise AuthenticationError("invalid_credentials", "Tên đăng nhập hoặc mật khẩu không đúng.")
        subject = account.subject if account else credential["auth_subject"]
        memberships = self.store.get_memberships_for_subject(subject)
        if not any(
            (membership.get("role") == account.role and membership.get("site_id") == account.site_id and membership.get("resident_id") == account.resident_id)
            if account else (membership.get("role") == "resident" and membership.get("resident_id") == credential["resident_id"])
            for membership in memberships
        ):
            raise AuthorizationError("membership_required", "Tài khoản bị khóa hoặc chưa được cấp quyền.")
        return subject

    def login(self, username: str, password: str) -> dict[str, Any]:
        subject = self.verify_password(username, password)
        username = username.strip().casefold()
        account = self.accounts.get(username)
        credential = self.store.get_local_credential(username=username) if account is None else None
        issued_at = int(time.time())
        expires_in = self.settings.auth_session_ttl_seconds
        token = jwt.encode(
            {
                "sub": subject, "iss": self.settings.auth_jwt_issuer,
                "aud": self.settings.auth_jwt_audience, "iat": issued_at,
                "exp": issued_at + expires_in, "session_id": uuid4().hex,
                "reset_generation": self.store.login_reset_generation()
                if callable(getattr(self.store, "login_reset_generation", None)) else 0,
                "credential_tag": _credential_tag(account, self.settings.auth_jwt_secret) if account else _stored_credential_tag(credential, self.settings.auth_jwt_secret),
            },
            self.settings.auth_jwt_secret, algorithm="HS256",
        )
        return {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "expires_at": issued_at + expires_in,
                "password_change_required": bool(credential and credential.get("password_change_required", True))}

    def create_resident_account(self, values: dict, actor: str) -> dict:
        values = {**values, "username": values["username"].strip().casefold()}
        for key in ("full_name", "apartment", "building"):
            values[key] = values[key].strip()
        if len(values["full_name"]) < 2 or not values["apartment"]:
            raise ValueError("invalid_resident_details")
        if values["username"] in self.accounts:
            raise ValueError("username_unavailable")
        fingerprint = hmac.new(self.settings.auth_jwt_secret.encode(), json.dumps(values, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
        existing = self.store.get_local_credential(creation_key=values["idempotency_key"])
        if existing:
            if existing["created_by"] != actor or existing["creation_fingerprint"] != fingerprint:
                raise ValueError("account_creation_conflict")
            return {"username": existing["username"], "profile": self.store.get_resident(existing["resident_id"]), "confirmed_at": existing["confirmed_at"].isoformat(), "duplicate": True}
        salt = secrets.token_bytes(16)
        with self.store.transaction():
            result = self.store.create_resident_account(values, actor=actor, salt=salt.hex(), password_hash=_password_digest(values["password"], salt).hex(), fingerprint=fingerprint)
            if self.settings.settings_encryption_key:
                self.store.save_handover(values["username"], values["password"], self.settings.settings_encryption_key)
            return result

    def change_password(self, subject: str, current_password: str, new_password: str) -> None:
        credential = self.store.get_local_credential(auth_subject=subject)
        if not credential or subject in {account.subject for account in self.accounts.values()}:
            raise ValueError("configured_account_password_unchanged")
        if credential.get("username_change_required", False):
            raise ValueError("initial_credentials_required")
        if not hmac.compare_digest(_password_digest(current_password, bytes.fromhex(credential["salt"])), bytes.fromhex(credential["password_hash"])):
            raise AuthenticationError("current_password_invalid", "Mật khẩu hiện tại không đúng.")
        self._replace_password(credential["username"], credential, new_password)

    def set_initial_password(self, subject: str, username: str, new_password: str) -> None:
        credential = self.store.get_local_credential(auth_subject=subject)
        if not credential or subject in {account.subject for account in self.accounts.values()}:
            raise ValueError("configured_account_password_unchanged")
        username = username.strip().casefold()
        if not re.fullmatch(r"[a-z0-9_.@-]{1,80}", username):
            raise ValueError("invalid_username")
        if not credential.get("password_change_required", False) and not credential.get("username_change_required", False):
            raise AuthorizationError("initial_password_not_required", "Tài khoản đã đặt mật khẩu riêng. Hãy dùng chức năng đổi mật khẩu trong tài khoản.")
        if username in self.accounts:
            raise ValueError("username_unavailable")
        self._validate_new_password(credential, new_password)
        salt = secrets.token_bytes(16)
        self.store.set_initial_credentials(subject, credential["password_hash"], username, salt.hex(), _password_digest(new_password, salt).hex())

    @staticmethod
    def _validate_new_password(credential: dict, new_password: str) -> None:
        if not 8 <= len(new_password) <= 256 or not new_password.strip():
            raise ValueError("new_password_invalid")
        if hmac.compare_digest(_password_digest(new_password, bytes.fromhex(credential["salt"])), bytes.fromhex(credential["password_hash"])):
            raise ValueError("new_password_invalid")

    def _replace_password(self, username: str, credential: dict, new_password: str) -> None:
        self._validate_new_password(credential, new_password)
        salt = secrets.token_bytes(16)
        self.store.change_local_password(username, credential["password_hash"], salt.hex(), _password_digest(new_password, salt).hex())
