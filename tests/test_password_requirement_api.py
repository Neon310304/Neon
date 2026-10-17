"""Exercise the existing auth service and HTTP guards without a PostgreSQL server.

The store is a test double; database transactions/migrations need the existing
PostgreSQL integration suite. No production authentication code is replaced.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router
from src.security.local_auth import LocalAuthService, _password_digest
from tests.test_local_auth_core import local_settings as local_settings


class CredentialStore:
    def __init__(self):
        salt = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.credential = {
            "username": "issued", "auth_subject": "local:issued", "resident_id": "R001",
            "salt": salt.hex(), "password_hash": _password_digest("Issued-Password!", salt).hex(),
            "password_change_required": True,
        }
        self.revoked = set()

    def reset_scope(self):
        return nullcontext()

    def get_local_credential(self, username=None, auth_subject=None):
        return dict(self.credential) if username == self.credential["username"] or auth_subject == self.credential["auth_subject"] else None

    def username_available(self, username):
        return username not in {self.credential["username"], "issued", "taken"}

    def set_initial_credentials(self, subject, expected_hash, username, salt, password_hash):
        assert subject == self.credential["auth_subject"] and expected_hash == self.credential["password_hash"]
        if self.credential.get("username_change_required") and username == self.credential["username"]:
            raise ValueError("username_change_required")
        if username != self.credential["username"] and not self.username_available(username):
            raise ValueError("username_unavailable")
        self.credential.update(username=username, salt=salt, password_hash=password_hash, password_change_required=False, username_change_required=False)

    def get_memberships_for_subject(self, subject):
        return [{"user_id": subject, "role": "manager" if subject == "local:admin" else "resident",
                 "site_id": "sunrise-residence", "resident_id": None if subject == "local:admin" else "R001", "status": "active"}]

    def is_auth_session_revoked(self, session_id, subject):
        return (session_id, subject) in self.revoked

    def revoke_auth_session(self, session_id, subject):
        self.revoked.add((session_id, subject))

    def get_resident(self, resident_id):
        return {"resident_id": "R001", "site_id": "sunrise-residence",
                "ownership_confirmed_at": "2026-10-14T00:00:00+00:00"} if resident_id == "R001" else None

    def change_local_password(self, username, expected_hash, salt, password_hash):
        assert username == "issued" and self.credential["password_hash"] == expected_hash
        self.credential.update(salt=salt, password_hash=password_hash, password_change_required=False)


@pytest.fixture
def password_api(local_settings, monkeypatch):
    monkeypatch.setattr("src.api.routes.reserve_usage", lambda *args: True)
    store = CredentialStore()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=local_settings, store=store, local_auth=LocalAuthService(local_settings, store),
        knowledge_store=SimpleNamespace(list_documents=lambda include_body=False: [
            {"source_id": "test", "title": "Test source", **({"body": "Test document"} if include_body else {})},
        ]),
    )
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store, local_settings


def issued_login(client, password="Issued-Password!"):
    response = client.post("/api/v1/auth/login", json={"username": "issued", "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.parametrize("path", [
    "/residents/R001", "/residents/R001/progress", "/residents/R001/household",
    "/residents/R001/requests", "/residents/R001/regulations", "/residents/R001/notifications",
    "/residents/R001/notifications/feed", "/residents/R001/notifications/groups",
    "/requests/unknown?resident_id=R001", "/requests/unknown/vehicle-document", "/knowledge/sources",
    "/knowledge/sources/unknown", "/locations", "/management/resident-accounts", "/management/requests",
])
def test_restricted_account_cannot_read_business_apis(password_api, path):
    client, store, _ = password_api
    headers = {**issued_login(client), "X-Demo-Role": "manager", "X-Must-Change-Password": "false"}
    response = client.get("/api/v1" + path, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_change_required"
    assert store.credential["password_change_required"] is True


@pytest.mark.parametrize("method,path,options", [
    ("post", "/requests", {"json": {"request_type": "general_support", "details": "Test"}}),
    ("put", "/residents/R001", {"json": {
        "resident_type": "tenant", "family_members": 1, "vehicle": "none", "needs_internet": False,
    }}),
    ("post", "/residents/R001/checklist/rebuild", {}),
    ("post", "/residents/R001/regulations/acknowledge", {"json": {
        "source_id": "test", "revision": "a" * 64, "confirmed": True,
    }}),
    ("patch", "/management/requests/unknown", {"json": {"status": "approved", "reason": "Test"}}),
    ("post", "/agent/chat", {"json": {"conversation_id": "test-new-user", "message": "Test"}}),
    ("post", "/agent/chat/stream", {"json": {"conversation_id": "test-new-user", "message": "Test"}}),
    ("post", "/agent/chat/ag-ui", {"json": {"conversation_id": "test-new-user", "message": "Test"}}),
    ("post", "/agent/form-from-files", {"files": {"files": ("note.txt", b"Test", "text/plain")}}),
    ("post", "/speech/synthesize", {"json": {"text": "Test"}}),
])
def test_restricted_account_cannot_write_or_use_ai(password_api, method, path, options):
    client, store, _ = password_api
    response = getattr(client, method)("/api/v1" + path, headers=issued_login(client), **options)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "password_change_required"
    assert store.credential["password_change_required"] is True


def test_password_errors_preserve_requirement_and_success_rotates_session(password_api):
    client, store, _ = password_api
    headers = issued_login(client)
    before = dict(store.credential)
    for current, new, status in [
        ("incorrect", "New-Private-Password!", 400),
        ("Issued-Password!", "short", 422),
        ("Issued-Password!", "Issued-Password!", 422),
        ("Issued-Password!", "        ", 422),
    ]:
        response = client.post("/api/v1/auth/password", headers=headers,
                               json={"current_password": current, "new_password": new})
        assert response.status_code == status
        assert store.credential == before
    context = client.get("/api/v1/me/context", headers=headers).json()
    assert context["password_change_required"] is True
    assert client.get("/api/v1/auth/password/status", headers=headers).json()["change_allowed"] is True
    changed = client.post("/api/v1/auth/password", headers=headers,
                          json={"current_password": "Issued-Password!", "new_password": "New-Private-Password!"})
    assert changed.status_code == 204
    assert store.credential["password_change_required"] is False
    assert store.credential["password_hash"] != before["password_hash"]
    assert client.get("/api/v1/me/context", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "issued", "password": "Issued-Password!"}).status_code == 401
    headers = issued_login(client, "New-Private-Password!")
    after = client.get("/api/v1/me/context", headers=headers).json()
    assert after["password_change_required"] is False
    for field in ("roles", "resident_ids", "site_ids", "manager_site_ids", "user_id"):
        assert after[field] == context[field]
    assert client.get("/api/v1/residents/R001", headers=headers).status_code == 200
    assert client.get("/api/v1/knowledge/sources", headers=headers).json() == {
        "items": [{"source_id": "test", "title": "Test source"}],
    }
    assert client.get("/api/v1/knowledge/sources/test", headers=headers).json()["body"] == "Test document"
    assert client.get("/api/v1/management/resident-accounts", headers=headers).status_code == 403
    # Reapplying the existing requirement is checked from the store, not a JWT claim.
    store.credential["password_change_required"] = True
    assert client.get("/api/v1/residents/R001", headers=headers).status_code == 403


def test_initial_password_uses_verified_session_and_rotates_all_old_tokens(password_api):
    client, store, _ = password_api
    store.credential["username_change_required"] = True
    headers = issued_login(client)
    other_session = issued_login(client)
    response = client.post("/api/v1/auth/password/first-login", headers=headers,
                           json={"username": " My.Own.Name ", "new_password": "My-Private-Password!"})
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert not store.credential["password_change_required"]
    for old_headers in (headers, other_session):
        assert client.get("/api/v1/me/context", headers=old_headers).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "issued", "password": "Issued-Password!"}).status_code == 401
    signed_in = client.post("/api/v1/auth/login", json={"username": "my.own.name", "password": "My-Private-Password!"})
    assert signed_in.status_code == 200
    fresh = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    assert client.get("/api/v1/me/context", headers=fresh).json()["username"] == "my.own.name"
    repeated = client.post("/api/v1/auth/password/first-login", headers=fresh,
                           json={"username": "issued", "new_password": "Another-Password!"})
    assert repeated.status_code == 403
    assert repeated.json()["detail"]["code"] == "initial_password_not_required"
    assert client.post("/api/v1/auth/password", headers=fresh,
                       json={"new_password": "Another-Password!"}).status_code == 422


@pytest.mark.parametrize("values,status", [
    ({"username": "taken", "new_password": "My-Private-Password!"}, 409),
    ({"username": "admin", "new_password": "My-Private-Password!"}, 409),
    ({"username": "bad name", "new_password": "My-Private-Password!"}, 422),
    ({"username": "issued", "new_password": "short"}, 422),
    ({"username": "issued", "new_password": "Issued-Password!"}, 422),
    ({"username": "issued", "new_password": "        "}, 422),
    ({"new_password": "My-Private-Password!"}, 422),
])
def test_initial_password_rejects_other_accounts_and_invalid_values(password_api, values, status):
    client, store, _ = password_api
    store.credential["username_change_required"] = True
    before = dict(store.credential)
    response = client.post("/api/v1/auth/password/first-login", headers=issued_login(client), json=values)
    assert response.status_code == status
    assert store.credential == before


def test_initial_password_requires_live_session_and_rejects_configured_accounts(password_api):
    client, store, settings = password_api
    body = {"username": "issued", "new_password": "My-Private-Password!"}
    assert client.post("/api/v1/auth/password/first-login", json=body).status_code == 401
    headers = issued_login(client)
    payload = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})
    payload["exp"] = payload["iat"] - 120
    expired = jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")
    assert client.post("/api/v1/auth/password/first-login", headers={"Authorization": "Bearer " + expired}, json=body).status_code == 401
    client.post("/api/v1/auth/logout", headers=headers)
    assert client.post("/api/v1/auth/password/first-login", headers=headers, json=body).status_code == 401
    login = client.post("/api/v1/auth/login", json={"username": "user", "password": settings.local_auth_user_password})
    assert client.post("/api/v1/auth/password/first-login", headers={"Authorization": "Bearer " + login.json()["access_token"]},
                       json={**body, "username": "user"}).status_code == 403
    assert store.credential["password_change_required"]


def test_initial_password_keeps_requirement_if_a_concurrent_update_wins(password_api, monkeypatch):
    client, store, _ = password_api
    before = dict(store.credential)

    def conflict(*args):
        raise ValueError("password_changed_concurrently")

    monkeypatch.setattr(store, "set_initial_credentials", conflict)
    response = client.post("/api/v1/auth/password/first-login", headers=issued_login(client),
                           json={"username": "issued", "new_password": "My-Private-Password!"})
    assert response.status_code == 409
    assert store.credential == before


def test_first_setup_cannot_retain_issued_username_or_use_normal_password_change(password_api):
    client, store, _ = password_api
    store.credential["username_change_required"] = True
    headers = issued_login(client)
    unchanged = client.post("/api/v1/auth/password/first-login", headers=headers,
                            json={"username": "issued", "new_password": "My-Private-Password!"})
    assert unchanged.status_code == 422
    assert unchanged.json()["detail"] == "username_change_required"
    normal = client.post("/api/v1/auth/password", headers=headers,
                         json={"current_password": "Issued-Password!", "new_password": "My-Private-Password!"})
    assert normal.status_code == 422
    assert store.credential["username_change_required"]


def test_username_checks_are_authenticated_private_and_case_insensitive(password_api):
    client, store, _ = password_api
    store.credential["username_change_required"] = True
    path = "/api/v1/auth/username-availability"
    assert client.post(path, json={"username": "taken"}).status_code == 401
    headers = issued_login(client)
    for username, available in [("taken", False), (" Issued ", False), ("USER", False), ("free.name", True)]:
        response = client.post(path, headers=headers, json={"username": username})
        assert response.status_code == 200
        assert response.json() == {"available": available}
        assert response.headers["cache-control"] == "no-store, private"
    assert client.post(path, headers=headers, json={"username": "bad name"}).status_code == 422


def test_restricted_logout_revokes_session_and_expiry_cannot_change_password(password_api):
    client, store, settings = password_api
    headers = issued_login(client)
    token = headers["Authorization"].split(" ")[1]
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["exp"] = payload["iat"] - 120
    expired = jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")
    assert client.post("/api/v1/auth/password", headers={"Authorization": "Bearer " + expired},
                       json={"current_password": "Issued-Password!", "new_password": "New-Private-Password!"}).status_code == 401
    assert store.credential["password_change_required"] is True
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/me/context", headers=headers).status_code == 401


def test_configured_accounts_keep_their_existing_behavior(password_api):
    client, _, settings = password_api
    for path in ("/knowledge/sources", "/knowledge/sources/test"):
        assert client.get("/api/v1" + path).status_code == 401
    for username, password, role in [("user", settings.local_auth_user_password, "resident"),
                                     ("admin", settings.local_auth_admin_password, "manager")]:
        response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        context = client.get("/api/v1/me/context", headers=headers).json()
        assert context["password_change_required"] is False
        assert context["roles"] == [role]
