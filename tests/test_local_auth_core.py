"""Checks for local authentication configuration and role pairing."""

import jwt
import pytest

from src.config import Settings
from src.security import AuthContext, can_access_resident, public_auth_config
from src.security.errors import AuthenticationError
from src.security.local_auth import LocalAuthService, verify_local_token


@pytest.fixture
def local_settings():
    return Settings(
        _env_file=None, auth_mode="required", auth_provider="local",
        auth_jwt_secret="test-local-signing-secret-at-least-32-bytes",
        local_auth_admin_username="admin", local_auth_admin_password="Test-Admin-2026!",
        local_auth_user_username="user", local_auth_user_password="Test-Resident-2026!",
        local_auth_site_id="sunrise-residence", local_auth_user_resident_id="R001",
    )


def test_role_site_pairing_does_not_grant_manager_site_to_resident():
    context = AuthContext(
        auth_subject="subject",
        user_id="user",
        roles=frozenset({"resident", "manager"}),
        role_site_ids={"resident": frozenset({"site-resident"}), "manager": frozenset({"site-manager"})},
        role_resident_ids={"resident": frozenset({"R001"})},
    )

    assert context.resident_site_ids == frozenset({"site-resident"})
    assert context.manager_site_ids == frozenset({"site-manager"})
    assert can_access_resident(context, "R001")
    assert "site-manager" not in context.resident_site_ids


def test_public_auth_config_only_returns_allowlisted_fields(local_settings):
    assert public_auth_config(local_settings) == {
        "auth_mode": "required",
        "auth_provider": "local",
        "enabled": True,
    }


def test_requested_local_trial_passwords_can_login(local_settings):
    settings = local_settings.model_copy(update={
        "app_env": "development", "local_auth_admin_password": "test-manager-password-731",
        "local_auth_user_password": "unit741",
    })
    service = LocalAuthService(settings, MemberStore())
    token = service.login("user", "unit741")["access_token"]
    assert verify_local_token(token, settings)["sub"] == "local:user"


def test_short_trial_password_is_not_accepted_in_production(local_settings):
    settings = local_settings.model_copy(update={"app_env": "production", "local_auth_user_password": "unit741"})
    assert public_auth_config(settings)["enabled"] is False


@pytest.mark.parametrize("updates", [
    {"auth_jwt_secret": ""}, {"auth_jwt_secret": "too-short"},
    {"local_auth_admin_password": ""}, {"local_auth_user_password": "short"},
    {"local_auth_user_username": "ADMIN"}, {"local_auth_admin_username": "invalid name"},
    {"local_auth_site_id": ""}, {"local_auth_user_resident_id": ""},
])
def test_incomplete_local_auth_configuration_fails_closed(local_settings, updates):
    settings = local_settings.model_copy(update=updates)
    assert public_auth_config(settings)["enabled"] is False


def test_passwords_are_not_in_settings_repr(local_settings):
    assert local_settings.local_auth_admin_password not in repr(local_settings)
    assert local_settings.local_auth_user_password not in repr(local_settings)
    assert local_settings.auth_jwt_secret not in repr(local_settings)


class MemberStore:
    @staticmethod
    def get_memberships_for_subject(subject):
        return [{
            "user_id": "local-user", "auth_subject": subject, "role": "resident",
            "site_id": "sunrise-residence", "resident_id": "R001",
        }]


def test_login_uses_salted_password_hashes_and_local_signature(local_settings):
    service = LocalAuthService(local_settings, MemberStore())
    salt, password_hash = service.password_hashes["user"]
    assert len(salt) == 16
    assert len(password_hash) == 32
    token = service.login(" USER ", local_settings.local_auth_user_password)["access_token"]
    payload = verify_local_token(token, local_settings)
    assert payload["sub"] == "local:user"
    assert payload["session_id"]
    assert "role" not in payload


@pytest.mark.parametrize("username,password", [("unknown", "incorrect"), ("user", "incorrect")])
def test_invalid_credentials_have_the_same_error(local_settings, username, password):
    with pytest.raises(AuthenticationError) as error:
        LocalAuthService(local_settings, MemberStore()).login(username, password)
    assert error.value.code == "invalid_credentials"


def test_password_rotation_invalidates_old_token(local_settings):
    token = LocalAuthService(local_settings, MemberStore()).login(
        "user", local_settings.local_auth_user_password,
    )["access_token"]
    rotated = local_settings.model_copy(update={"local_auth_user_password": "New-Resident-Password!"})
    with pytest.raises(AuthenticationError) as error:
        verify_local_token(token, rotated)
    assert error.value.code == "session_expired"


def test_unsigned_and_expired_tokens_are_rejected(local_settings):
    token = LocalAuthService(local_settings, MemberStore()).login(
        "user", local_settings.local_auth_user_password,
    )["access_token"]
    payload = jwt.decode(token, options={"verify_signature": False})
    with pytest.raises(AuthenticationError):
        verify_local_token(jwt.encode(payload, key="", algorithm="none"), local_settings)
    payload["exp"] = payload["iat"] - 120
    with pytest.raises(AuthenticationError) as error:
        verify_local_token(jwt.encode(payload, local_settings.auth_jwt_secret, algorithm="HS256"), local_settings)
    assert error.value.code == "token_expired"
