"""Local username/password identity and database-backed business roles."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from src.security.errors import AuthenticationError, AuthorizationError
from src.security.local_auth import (
    LocalAuthService,
    local_auth_enabled,
    verify_hs256_token,
    verify_local_token,
)


def _freeze_scope_map(value: Mapping[str, Iterable[str]]) -> Mapping[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for role, scopes in value.items():
        normalized_role = str(role).strip().casefold()
        if not normalized_role:
            continue
        if isinstance(scopes, str):
            scopes = (scopes,)
        result[normalized_role] = frozenset(
            str(scope).strip() for scope in scopes if str(scope).strip()
        )
    return MappingProxyType(result)


@dataclass(frozen=True)
class AuthContext:
    auth_subject: str
    user_id: str | None
    roles: frozenset[str] = field(default_factory=frozenset)
    # Compatibility projections only: permission checks use the role mappings.
    site_ids: frozenset[str] = field(default_factory=frozenset)
    resident_ids: frozenset[str] = field(default_factory=frozenset)
    mode: str = "required"
    role_site_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)
    role_resident_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)
    session_id: str | None = None
    password_change_required: bool = False
    username: str = ""
    username_change_required: bool = False

    def __post_init__(self) -> None:
        roles = frozenset(str(role).strip().casefold() for role in self.roles if str(role).strip())
        role_sites = dict(self.role_site_ids)
        role_residents = dict(self.role_resident_ids)
        # Older, single-role callers remain compatible. Never infer pairings
        # from flattened sites when a principal has more than one role.
        if len(roles) == 1:
            role = next(iter(roles))
            if not role_sites and self.site_ids:
                role_sites[role] = self.site_ids
            if not role_residents and self.resident_ids:
                role_residents[role] = self.resident_ids
        role_sites = _freeze_scope_map(role_sites)
        role_residents = _freeze_scope_map(role_residents)
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "role_site_ids", role_sites)
        object.__setattr__(self, "role_resident_ids", role_residents)
        object.__setattr__(self, "site_ids", frozenset(site for sites in role_sites.values() for site in sites))
        object.__setattr__(self, "resident_ids", role_residents.get("resident", frozenset()))

    @property
    def resident_id(self) -> str | None:
        return min(self.resident_ids) if self.resident_ids else None

    @property
    def manager_site_ids(self) -> frozenset[str]:
        return self.role_site_ids.get("manager", frozenset())

    @property
    def resident_site_ids(self) -> frozenset[str]:
        return self.role_site_ids.get("resident", frozenset())


def public_auth_config(settings: Any) -> dict[str, Any]:
    return {
        "auth_mode": str(getattr(settings, "auth_mode", "required")),
        "auth_provider": "local",
        "enabled": local_auth_enabled(settings),
    }


def _header_token(request: Any) -> str:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise AuthenticationError("authentication_required", "Bearer authentication is required.")
    return token.strip()


def _normalise_memberships(
    rows: Iterable[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]], str | None]:
    role_sites: dict[str, set[str]] = {}
    role_residents: dict[str, set[str]] = {}
    user_id: str | None = None
    for row in rows:
        role = str(row.get("role") or "").strip().casefold()
        site_id = str(row.get("site_id") or "").strip()
        resident_id = str(row.get("resident_id") or "").strip()
        row_user_id = str(row.get("user_id") or "").strip()
        if role not in {"resident", "manager"} or not site_id or not row_user_id:
            continue
        if row.get("status", "active") != "active" or (role == "resident" and not resident_id):
            continue
        if user_id and user_id != row_user_id:
            raise AuthorizationError("membership_invalid", "Membership identity is inconsistent.")
        user_id = row_user_id
        role_sites.setdefault(role, set()).add(site_id)
        if role == "resident":
            role_residents.setdefault(role, set()).add(resident_id)
    return role_sites, role_residents, user_id


def build_auth_context(request: Any, settings: Any, store: Any) -> AuthContext:
    """Resolve verified identity, session revocation and active memberships."""
    environment = str(getattr(settings, "app_env", "development")).strip().casefold()
    mode = str(getattr(settings, "auth_mode", "required")).strip().casefold()
    if mode == "demo":
        if not getattr(settings, "allow_demo_auth", False) or environment not in {"development", "test"}:
            raise AuthenticationError("demo_auth_disabled", "Demo authentication is disabled.")
        # A route must never force a manager identity; this header is only for
        # an explicitly enabled local demo session.
        role = request.headers.get("X-Demo-Role", "resident").strip().casefold()
        if role not in {"resident", "manager"}:
            raise AuthorizationError("invalid_demo_role", "Unknown demo role.")
        return AuthContext(
            auth_subject="demo-manager-sunrise" if role == "manager" else "demo-resident-r001",
            user_id="demo-manager-sunrise" if role == "manager" else "demo-resident-r001",
            roles=frozenset({role}),
            role_site_ids={role: frozenset({"sunrise-residence"})},
            role_resident_ids={"resident": frozenset({"R001"})} if role == "resident" else {},
            mode="demo",
        )
    if mode != "required":
        raise AuthenticationError("authentication_not_configured", "Authentication mode is not configured.")
    token = _header_token(request)
    payload = verify_local_token(token, settings, store)
    session_id = payload["session_id"]
    is_revoked = getattr(store, "is_auth_session_revoked", None)
    if not callable(is_revoked):
        raise AuthenticationError("authentication_not_configured", "Session validation is not configured.")
    try:
        revoked = bool(is_revoked(session_id, payload["sub"]))
    except Exception as error:
        raise AuthenticationError("authentication_unavailable", "Session validation is temporarily unavailable.") from error
    if revoked:
        raise AuthenticationError("session_revoked", "Authentication session has been signed out.")
    get_memberships = getattr(store, "get_memberships_for_subject", None)
    if not callable(get_memberships):
        raise AuthenticationError("authentication_not_configured", "Membership lookup is not configured.")
    role_sites, role_residents, user_id = _normalise_memberships(get_memberships(payload["sub"]))
    if not role_sites or not user_id:
        raise AuthorizationError("membership_required", "No active membership is assigned to this account.")
    credential = store.get_local_credential(auth_subject=payload["sub"]) if callable(getattr(store, "get_local_credential", None)) else None
    return AuthContext(
        auth_subject=payload["sub"],
        user_id=user_id,
        roles=frozenset(role_sites),
        role_site_ids=role_sites,
        role_resident_ids=role_residents,
        mode="required",
        session_id=session_id,
        password_change_required=bool(credential and (credential.get("password_change_required", True) or credential.get("username_change_required", False))),
        username=credential["username"] if credential else payload["sub"].removeprefix("local:"),
        username_change_required=bool(credential and credential.get("username_change_required", False)),
    )


def require_role(context: AuthContext, role: str) -> None:
    if role.casefold() not in context.roles:
        raise AuthorizationError("forbidden", "This operation is not permitted for the current role.")


def can_access_resident(context: AuthContext, resident_id: str) -> bool:
    return "resident" in context.roles and resident_id in context.role_resident_ids.get("resident", frozenset())


__all__ = [
    "AuthContext",
    "AuthenticationError",
    "AuthorizationError",
    "LocalAuthService",
    "build_auth_context",
    "can_access_resident",
    "public_auth_config",
    "require_role",
    "verify_hs256_token",
    "verify_local_token",
]
