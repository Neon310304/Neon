import base64
import hashlib
import hmac
import json
import time

import pytest

from src.security import (
    AuthContext,
    AuthenticationError,
    build_auth_context,
    can_access_resident,
    verify_hs256_token,
)

TEST_SECRET = "test-signing-secret-with-at-least-32-bytes"


def _token(payload: dict, secret: str = TEST_SECRET) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = encode({"typ": "JWT", "alg": "HS256"})
    body = encode(payload)
    signature = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def test_hs256_verifier_checks_claims_and_signature():
    pytest.importorskip("jwt")
    payload = {"sub": "user-1", "iss": "issuer", "aud": "audience", "exp": time.time() + 60}
    assert verify_hs256_token(
        _token(payload), secret=TEST_SECRET, issuer="issuer", audience="audience"
    )["sub"] == "user-1"

    with pytest.raises(AuthenticationError, match="Invalid authentication token"):
        verify_hs256_token(_token(payload, "wrong-secret"), secret=TEST_SECRET)


def test_hs256_verifier_rejects_expired_or_wrong_audience():
    pytest.importorskip("jwt")
    with pytest.raises(AuthenticationError, match="expired"):
        verify_hs256_token(_token({"sub": "user-1", "exp": time.time() - 120}), secret=TEST_SECRET)
    with pytest.raises(AuthenticationError, match="audience"):
        verify_hs256_token(
            _token({"sub": "user-1", "aud": "wrong", "exp": time.time() + 60}),
            secret=TEST_SECRET,
            audience="expected",
        )


def test_resident_scope_is_owner_only():
    context = AuthContext(
        auth_subject="user-1",
        user_id="internal-1",
        roles=frozenset({"resident"}),
        resident_ids=frozenset({"R001"}),
        site_ids=frozenset({"sunrise-residence"}),
    )
    assert can_access_resident(context, "R001")
    assert not can_access_resident(context, "R002")


def test_demo_auth_is_rejected_outside_development_and_test():
    request = type("Request", (), {"headers": {}})()
    settings = type(
        "Settings",
        (),
        {
            "auth_mode": "demo",
            "allow_demo_auth": True,
            "app_env": "production",
        },
    )()
    with pytest.raises(AuthenticationError, match="Demo authentication is disabled"):
        build_auth_context(request, settings, object())


def test_required_auth_maps_verified_subject_to_active_membership(monkeypatch):
    pytest.importorskip("jwt")
    payload = {
        "sub": "subject-1",
        "iss": "moveinmate-local",
        "aud": "moveinmate",
        "exp": time.time() + 60,
        "iat": time.time(),
        "session_id": "session-1",
    }
    request = type("Request", (), {"headers": {"Authorization": f"Bearer {_token(payload)}"}})()
    settings = type(
        "Settings",
        (),
        {
            "auth_mode": "required",
            "auth_provider": "local",
            "app_env": "test",
            "auth_jwt_leeway_seconds": 60,
        },
    )()

    monkeypatch.setattr("src.security.verify_local_token", lambda *args, **kwargs: payload)

    class Store:
        @staticmethod
        def get_memberships_for_subject(subject):
            assert subject == "subject-1"
            return [{"user_id": "user-1", "resident_id": "R001", "site_id": "site-a", "role": "resident"}]

        @staticmethod
        def is_auth_session_revoked(session_id, subject):
            return False

    context = build_auth_context(request, settings, Store())
    assert context.user_id == "user-1"
    assert context.resident_id == "R001"
    assert context.site_ids == frozenset({"site-a"})
