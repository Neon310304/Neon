from datetime import date

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import router
from src.repositories.store import ResidenceMembership, Resident, User, now_utc
from src.security import LocalAuthService
from src.security.request_guard import RequestGuard


@pytest.fixture
def local_container(app_container):
    settings = app_container.settings.model_copy(update={
        "auth_mode": "required", "auth_provider": "local", "allow_demo_auth": False,
        "login_reset_enabled": False, "login_reset_code": "",
        "auth_jwt_secret": "api-test-signing-secret-with-at-least-32-bytes",
        "auth_jwt_issuer": "moveinmate-local", "auth_jwt_audience": "moveinmate",
        "local_auth_admin_username": "admin", "local_auth_admin_password": "Test-Admin-2026!",
        "local_auth_user_username": "user", "local_auth_user_password": "Test-Resident-2026!",
        "local_auth_site_id": "sunrise-residence", "local_auth_user_resident_id": "R001",
        "local_auth_admin_site_ids": "",
        "show_local_login_credentials": False,
    })
    app_container.settings = settings
    app_container.local_auth = LocalAuthService(settings, app_container.store)
    app_container.local_auth.provision_accounts()
    return app_container


@pytest_asyncio.fixture
async def local_client(local_container):
    with local_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = now_utc()
    app = FastAPI()
    app.state.container = local_container
    app.include_router(router)
    app.add_middleware(RequestGuard)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def login(client, role):
    password = "Test-Admin-2026!" if role == "admin" else "Test-Resident-2026!"
    response = await client.post("/api/v1/auth/login", json={"username": role, "password": password})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["expires_at"] > 0
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
def enabled_reset(local_container):
    from src.services.login_reset import capture_baseline

    local_container.settings.login_reset_enabled = True
    local_container.settings.login_reset_code = "0000"
    capture_baseline(local_container.store, "sunrise-residence")
    return {"code": "0000", "confirm": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("username,role", [("admin", "manager"), ("user", "resident")])
async def test_configured_accounts_can_login(local_client, username, role):
    headers = await login(local_client, username)
    context = await local_client.get("/api/v1/me/context", headers=headers)
    assert context.status_code == 200
    assert context.json()["roles"] == [role]
    assert context.json()["mode"] == "required"


@pytest.mark.asyncio
async def test_user_cannot_escalate_to_manager(local_client):
    response = await local_client.post("/api/v1/auth/login", json={
        "username": "user", "password": "Test-Resident-2026!", "role": "manager",
    })
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}", "X-Demo-Role": "manager"}
    context = await local_client.get("/api/v1/me/context", headers=headers)
    assert context.json()["roles"] == ["resident"]
    assert (await local_client.get("/api/v1/management/requests", headers=headers)).status_code == 403
    assert (await local_client.get("/api/v1/residents/R999", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_read_an_existing_other_resident(local_client, local_container):
    with local_container.store.session() as session:
        session.add(Resident(
            id="R002", full_name="Other resident", apartment="A1206",
            site_id="sunrise-residence", site_name="Test site", resident_type="tenant",
        ))
    headers = await login(local_client, "user")
    assert (await local_client.get("/api/v1/residents/R001", headers=headers)).status_code == 200
    own_progress = await local_client.get("/api/v1/residents/R001/progress", headers=headers)
    assert own_progress.status_code == 200
    assert "a2ui" in own_progress.json()
    assert (await local_client.get("/api/v1/residents/R002/progress", headers=headers)).status_code == 404
    denied = await local_client.get("/api/v1/residents/R002", headers=headers)
    assert denied.status_code == 404
    assert denied.json()["detail"] == "resident_not_found"


@pytest.mark.asyncio
async def test_admin_can_view_management_queue(local_client):
    headers = await login(local_client, "admin")
    assert (await local_client.get("/api/v1/management/requests", headers=headers)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("user", "wrong"), ("unknown", "wrong")])
async def test_incorrect_login_is_rejected(local_client, username, password):
    response = await local_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_logout_revokes_only_that_session(local_client):
    first_session = await login(local_client, "user")
    second_session = await login(local_client, "user")
    assert (await local_client.post("/api/v1/auth/logout", headers=first_session)).status_code == 204
    denied = await local_client.get("/api/v1/me/context", headers=first_session)
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "session_revoked"
    assert (await local_client.get("/api/v1/me/context", headers=second_session)).status_code == 200


@pytest.mark.asyncio
async def test_reinitialization_preserves_session_and_memberships(local_client, local_container):
    headers = await login(local_client, "user")
    local_container.local_auth = LocalAuthService(local_container.settings, local_container.store)
    local_container.local_auth.provision_accounts()
    local_container.local_auth.provision_accounts()
    with local_container.store.session() as session:
        users = session.query(User).filter(User.auth_subject.like("local:%")).all()
        assert len(users) == 2
        assert session.query(ResidenceMembership).filter(ResidenceMembership.user_id.in_(user.id for user in users)).count() == 2
    assert (await local_client.get("/api/v1/me/context", headers=headers)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["user", "membership"])
async def test_restart_does_not_reactivate_suspended_accounts(local_client, local_container, target):
    headers = await login(local_client, "user")
    with local_container.store.session() as session:
        user = session.query(User).filter_by(auth_subject="local:user").one()
        if target == "user":
            user.status = "disabled"
        else:
            session.query(ResidenceMembership).filter_by(user_id=user.id).one().status = "suspended"
    local_container.local_auth.provision_accounts()
    assert (await local_client.get("/api/v1/me/context", headers=headers)).status_code == 403
    response = await local_client.post("/api/v1/auth/login", json={"username": "user", "password": "Test-Resident-2026!"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_password_change_requires_new_login(local_client, local_container):
    headers = await login(local_client, "user")
    settings = local_container.settings.model_copy(update={"local_auth_user_password": "Rotated-Password-2026!"})
    local_container.settings = settings
    local_container.local_auth = LocalAuthService(settings, local_container.store)
    assert (await local_client.get("/api/v1/me/context", headers=headers)).status_code == 401
    old_password = await local_client.post("/api/v1/auth/login", json={"username": "user", "password": "Test-Resident-2026!"})
    assert old_password.status_code == 401
    new_password = await local_client.post("/api/v1/auth/login", json={"username": "user", "password": "Rotated-Password-2026!"})
    assert new_password.status_code == 200


@pytest.mark.asyncio
async def test_public_auth_config_has_no_credentials(local_client):
    response = await local_client.get("/api/v1/auth/config")
    assert response.json() == {"auth_mode": "required", "auth_provider": "local", "enabled": True}
    assert "reset" not in response.text


@pytest.mark.asyncio
async def test_demo_handover_restores_only_original_passwords_and_disappears_after_change(local_client, local_container):
    from cryptography.fernet import Fernet

    from src.repositories.store import CredentialHandover, LocalCredential
    from src.services.demo_accounts import SAMPLE_PASSWORD, seed_sample_accounts

    store = local_container.store
    key = Fernet.generate_key().decode()
    local_container.settings.settings_encryption_key = key
    seed_sample_accounts(local_container, include_requests=False)
    with store.session() as session:
        session.query(CredentialHandover).delete()
    original = store.get_local_credential(username="demo001")
    changed = store.get_local_credential(username="demo002")
    local_container.local_auth.set_initial_password(changed["auth_subject"], "resident.renamed", "Private-Resident-2026!")
    with store.session() as session:
        session.get(LocalCredential, "demo003").password_change_required = False
        reset_account = session.get(LocalCredential, "demo004")
        reset_account.password_hash = "0" * 64
    seed_sample_accounts(local_container, include_requests=False)
    assert store.get_local_credential(username="demo001") == original
    assert store.read_handover("demo001", key)["temporary_password"] == SAMPLE_PASSWORD
    for username in ("resident.renamed", "demo003", "demo004"):
        assert not store.read_handover(username, key)["temporary_password_available"]
    with store.session() as session:
        handover = session.get(CredentialHandover, "demo001")
        encrypted, issued_at = handover.encrypted_password, handover.issued_at
        assert SAMPLE_PASSWORD not in encrypted
    seed_sample_accounts(local_container, include_requests=False)
    with store.session() as session:
        handover = session.get(CredentialHandover, "demo001")
        assert (handover.encrypted_password, handover.issued_at) == (encrypted, issued_at)
    headers = await login(local_client, "admin")
    response = await local_client.get("/api/v1/management/resident-accounts/demo001/handover", headers=headers)
    assert response.status_code == 200 and response.json()["temporary_password"] == SAMPLE_PASSWORD
    assert response.headers["cache-control"] == "no-store, private"
    assert (await local_client.get("/api/v1/management/resident-accounts/demo001/handover", headers=await login(local_client, "user"))).status_code == 403
    accounts = (await local_client.get("/api/v1/management/resident-accounts", headers=headers)).json()["items"]
    assert all("temporary_password" not in account for account in accounts)
    assert next(account for account in accounts if account["username"] == "demo001")["temporary_password_available"]
    local_container.local_auth.set_initial_password(original["auth_subject"], "resident.private", "Private-New-2026!")
    seed_sample_accounts(local_container, include_requests=False)
    result = await local_client.get("/api/v1/management/resident-accounts/resident.private/handover", headers=headers)
    assert result.status_code == 200 and not result.json()["temporary_password_available"]
    assert "temporary_password" not in result.json()
    assert store.get_local_credential(username="demo001") is None
    assert store.list_requests() == []


def test_changing_configured_scope_expires_old_membership(local_container):
    local_container.store.ensure_local_membership(auth_subject="local:admin", role="manager", site_id="another-site")
    memberships = local_container.store.get_memberships_for_subject("local:admin")
    assert [membership["site_id"] for membership in memberships] == ["another-site"]


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,enabled,visible", [
    ("development", True, True), ("test", True, True),
    ("development", False, False), ("production", True, False),
])
async def test_login_hints_require_explicit_local_opt_in(local_client, local_container, environment, enabled, visible):
    settings = local_container.settings.model_copy(update={
        "app_env": environment, "show_local_login_credentials": enabled,
    })
    local_container.local_auth = LocalAuthService(settings, local_container.store)
    response = await local_client.get("/api/v1/auth/login-hints")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["visible"] is visible
    if visible:
        assert body["accounts"] == [
            {"label": "Ban quản lý", "username": "admin", "password": "Test-Admin-2026!"},
            {"label": "Cư dân", "username": "user", "password": "Test-Resident-2026!"},
        ]
        assert settings.auth_jwt_secret not in response.text
    else:
        assert body["accounts"] == []


@pytest.mark.asyncio
async def test_missing_resident_does_not_block_admin_or_seed_a_profile(local_client, local_container):
    with local_container.store.session() as session:
        session.delete(session.get(Resident, "R001"))
    local_container.local_auth.provision_accounts()
    await login(local_client, "admin")
    response = await local_client.post("/api/v1/auth/login", json={"username": "user", "password": "Test-Resident-2026!"})
    assert response.status_code == 403
    with local_container.store.session() as session:
        assert session.query(Resident).count() == 0


@pytest.mark.asyncio
async def test_invalid_scope_does_not_reuse_previous_resident_membership(local_client, local_container):
    old_session = await login(local_client, "user")
    settings = local_container.settings.model_copy(update={"local_auth_site_id": "another-site"})
    local_container.settings = settings
    local_container.local_auth = LocalAuthService(settings, local_container.store)
    local_container.local_auth.provision_accounts()
    assert (await local_client.get("/api/v1/me/context", headers=old_session)).status_code == 401
    response = await local_client.post("/api/v1/auth/login", json={"username": "user", "password": "Test-Resident-2026!"})
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("username,enabled", [("admin", True), ("user", False)])
async def test_demo_reset_capability_works_with_local_login(local_client, enabled_reset, username, enabled):
    headers = await login(local_client, username)
    response = await local_client.get("/api/v1/me/context", headers=headers)
    assert response.json()["demo_reset_enabled"] is False
    assert response.json()["reset_available"] is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", [
    {"login_reset_enabled": False},
    {"login_reset_code": ""},
    {"local_auth_user_resident_id": "R999"},
])
async def test_demo_reset_fails_closed_for_unsafe_configuration(local_client, local_container, enabled_reset, configuration):
    for field, value in configuration.items():
        setattr(local_container.settings, field, value)
    headers = await login(local_client, "admin")
    response = await local_client.get("/api/v1/me/context", headers=headers)
    assert response.json()["demo_reset_enabled"] is False
    assert response.json()["reset_available"] is False
    assert (await local_client.post("/api/v1/management/reset", headers=headers, json=enabled_reset)).status_code == 403
    assert (await local_client.post("/api/v1/demo/reset", headers=headers)).status_code == 410


@pytest.mark.asyncio
async def test_demo_reset_requires_manager_login(local_client, local_container, enabled_reset):
    with local_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = None
    local_container.store.update_resident("R001", {"apartment": "B0901"})
    assert (await local_client.post("/api/v1/management/reset", json=enabled_reset)).status_code == 401
    headers = await login(local_client, "user")
    assert (await local_client.post("/api/v1/management/reset", headers=headers, json=enabled_reset)).status_code == 403
    assert local_container.store.get_resident("R001")["apartment"] == "B0901"


@pytest.mark.asyncio
async def test_demo_reset_rejects_manager_from_another_site(local_client, local_container, enabled_reset):
    headers = await login(local_client, "admin")
    local_container.store.ensure_local_membership(auth_subject="local:admin", role="manager", site_id="another-site")
    context = await local_client.get("/api/v1/me/context", headers=headers)
    assert context.json()["demo_reset_enabled"] is False
    assert context.json()["reset_available"] is False
    assert (await local_client.post("/api/v1/management/reset", headers=headers, json=enabled_reset)).status_code == 403


@pytest.mark.asyncio
async def test_demo_reset_rejects_a_demo_id_in_another_site(local_client, local_container, enabled_reset):
    with local_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = None
    local_container.store.update_resident("R001", {"site_id": "another-site"})
    headers = await login(local_client, "admin")
    context = await local_client.get("/api/v1/me/context", headers=headers)
    assert context.json()["demo_reset_enabled"] is False
    assert (await local_client.post("/api/v1/management/reset", headers=headers, json=enabled_reset)).status_code == 409
    assert local_container.store.get_resident("R001")["site_id"] == "another-site"


@pytest.mark.asyncio
async def test_demo_reset_preserves_configured_login_and_clears_all_conversations(local_client, local_container, enabled_reset):
    admin_headers = await login(local_client, "admin")
    user_headers = await login(local_client, "user")
    before = (await local_client.get("/api/v1/me/context", headers=user_headers)).json()
    memberships = local_container.store.get_memberships_for_subject("local:user")
    with local_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = None
    local_container.store.update_resident("R001", {"apartment": "B0901"})
    local_container.store.create_request("R001", "resident_card", {}, "reset-local-login")
    local_container.agent._graphs.update({"demo-chat": object(), "other-chat": object()})
    local_container.agent._bindings.update({"demo-chat": ("moveinmate", "R001"), "other-chat": ("moveinmate", "R999")})
    response = await local_client.post("/api/v1/management/reset", headers=admin_headers, json=enabled_reset)
    assert response.status_code == 200
    assert response.json()["reset"] is True
    assert response.json()["scope"] == "all_application_data"
    assert local_container.store.list_requests("R001") == []
    assert local_container.store.get_resident("R001")["apartment"] == "A1205"
    assert len(local_container.store.list_tasks("R001")) == 10
    assert local_container.store.get_memberships_for_subject("local:user") == memberships
    assert (await local_client.get("/api/v1/me/context", headers=user_headers)).status_code == 401
    assert (await local_client.get("/api/v1/me/context", headers=admin_headers)).status_code == 401
    fresh_headers = await login(local_client, "user")
    fresh_context = (await local_client.get("/api/v1/me/context", headers=fresh_headers)).json()
    assert fresh_context == before
    await login(local_client, "admin")
    assert local_container.agent._graphs == {}
    assert local_container.agent._bindings == {}


@pytest.mark.asyncio
async def test_full_demo_reset_removes_created_residents_accounts_and_workflows(local_client, local_container, enabled_reset):
    from sqlalchemy import text

    headers = await login(local_client, "admin")
    created = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json={
        "username": "reset.created", "password": "Demo-Resident-Only!",
        "full_name": "Reset Demo Resident", "site_id": "sunrise-residence",
        "apartment": "A1808", "building": "A", "confirmed": True,
        "idempotency_key": "full-reset-created-account",
    })
    assert created.status_code == 200
    resident_id = created.json()["profile"]["resident_id"]
    store = local_container.store
    store.create_request(resident_id, "general_support", {"details": "demo"}, "other-resident-ticket")
    vehicle = store.create_request(resident_id, "vehicle_registration", {
        "vehicle_type": "car", "vehicle_plate": "DEMO-RESET",
    }, "other-resident-vehicle")
    store.decide_request(vehicle["request_id"], "approved", "demo")
    store.set_parking_due_date(resident_id, vehicle["request_id"], date(2026, 10, 12))
    store.acknowledge_document(resident_id, {"source_id": "reset-source", "revision": "a" * 64, "title": "Demo", "version": "1"})
    local_container.tool_service.build_checklist(resident_id)
    store.create_request("R001", "general_support", {"details": "demo"}, "sample-resident-ticket")
    store.import_sites([{"id": "preserved-site", "name": "Preserved site", "source_url": "https://example.org/", "source_checked_on": date(2026, 10, 9)}])
    source_documents = store.engine.connect()
    try:
        sources_before = source_documents.scalar(text("SELECT count(*) FROM knowledge_documents"))
    finally:
        source_documents.close()
    response = await local_client.post("/api/v1/management/reset", headers=headers, json=enabled_reset)
    assert response.status_code == 200
    assert store.get_resident(resident_id) is None
    assert store.get_local_credential(username="reset.created") is None
    assert store.get_resident("R001")["apartment"] == "A1205"
    with store.engine.connect() as connection:
        for table in ("management_requests", "request_events", "parking_reminders", "regulation_acknowledgements"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert connection.scalar(text("SELECT count(*) FROM residents")) == 11
        assert set(connection.execute(text("SELECT username FROM local_credentials")).scalars()) == {f"demo{index:03d}" for index in range(1, 11)}
        assert connection.scalar(text("SELECT count(*) FROM knowledge_documents")) == sources_before
    assert store.list_sites({"preserved-site"})[0]["site_name"] == "Preserved site"
    assert (await local_client.post("/api/v1/auth/login", json={"username": "reset.created", "password": "Demo-Resident-Only!"})).status_code == 401
    await login(local_client, "admin")
    await login(local_client, "user")
