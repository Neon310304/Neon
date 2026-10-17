from contextvars import Context
from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.routes import router
from src.repositories.store import (
    NotificationReceipt,
    RequestEvent,
    ResetInvalidatedError,
    Resident,
    VehicleDocument,
)
from src.security import LocalAuthService
from src.security.request_guard import RequestGuard
from src.services.login_reset import capture_baseline, reset_application


@pytest.fixture
def reset_container(app_container):
    app_container.settings = app_container.settings.model_copy(update={
        "app_env": "production", "login_reset_enabled": True, "login_reset_code": "0000",
        "auth_mode": "required", "auth_provider": "local", "allow_demo_auth": False,
        "auth_jwt_secret": "reset-test-signing-secret-with-at-least-32-bytes",
        "auth_jwt_issuer": "moveinmate-local", "auth_jwt_audience": "moveinmate",
        "local_auth_admin_username": "admin", "local_auth_admin_password": "Test-Admin-2026!",
        "local_auth_user_username": "demo001", "local_auth_user_password": "Test-Resident-2026!",
        "local_auth_site_id": "sunrise-residence", "local_auth_user_resident_id": "R001",
        "local_auth_admin_site_ids": "vinhomes-central-park",
    })
    app_container.local_auth = LocalAuthService(app_container.settings, app_container.store)
    app_container.local_auth.provision_accounts()
    capture_baseline(app_container.store, "sunrise-residence")
    return app_container


async def request_reset(app_container, payload=None):
    application = FastAPI()
    application.state.container = app_container
    application.include_router(router)
    application.add_middleware(RequestGuard)
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        token = app_container.local_auth.login("admin", "Test-Admin-2026!")["access_token"]
        return await client.post("/api/v1/management/reset", headers={"Authorization": f"Bearer {token}"}, json=payload or {"code": "0000", "confirm": True})


@pytest.mark.asyncio
async def test_reset_requires_explicit_enable_and_confirmation(reset_container):
    assert (await request_reset(reset_container, {"code": "0000"})).status_code == 400
    reset_container.settings.login_reset_enabled = False
    assert (await request_reset(reset_container)).status_code == 403


@pytest.mark.asyncio
async def test_wrong_codes_are_limited_across_requests(reset_container):
    for _ in range(5):
        assert (await request_reset(reset_container, {"code": "incorrect", "confirm": True})).status_code == 401
    response = await request_reset(reset_container)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    with reset_container.store.engine.connect() as connection:
        assert connection.scalar(text("SELECT generation FROM login_reset_baselines")) == 0


@pytest.mark.asyncio
async def test_reset_all_accounts_workflows_and_conversations(reset_container):
    store = reset_container.store
    original = store.get_resident("R001")
    with store.session() as session:
        session.add(Resident(id="R999", full_name="Untouched", apartment="A999", site_id="other", site_name="Other", resident_type="owner"))
        session.query(Resident).filter_by(id="R001").update({"full_name": "Changed", "vehicle": "none"})
        session.execute(text("UPDATE checklist_tasks SET status='completed' WHERE resident_id='R001'"))
    store.create_request("R001", "resident_card", {}, "reset-own-request")
    with store.transaction():
        extra_account = reset_container.local_auth.create_resident_account({
            "username": "additional-resident", "password": "Test-Extra-2026!",
            "full_name": "Additional Resident", "apartment": "B202", "building": "B",
            "site_id": "sunrise-residence", "site_name": "Khu Mặt Trời", "resident_type": "owner",
            "idempotency_key": "reset-extra-account",
        }, actor="local:admin")
    extra_id = extra_account["profile"]["resident_id"]
    store.create_request(extra_id, "temporary_residency", {"person_name": "Added Person"}, "reset-extra-request")
    vehicle = store.create_request("R999", "vehicle_registration", {"vehicle_type": "car", "vehicle_plate": "51A-12345"}, "reset-other-request")
    store.decide_request(vehicle["request_id"], "approved", "Approved for reset test")
    store.set_parking_due_date("R999", vehicle["request_id"], date(2026, 10, 15))
    store.acknowledge_document("R999", {"source_id": "reset-document", "revision": "v1", "title": "Reading"})
    with store.session() as session:
        event_id = session.query(RequestEvent.id).filter_by(request_id=vehicle["request_id"]).first()[0]
        session.add(NotificationReceipt(subject_key="reset-test-subject", event_id=event_id))
        session.add(VehicleDocument(request_id=vehicle["request_id"], content=b"test-document",
                                    filename="registration.png", media_type="image/png", sha256="0" * 64))
    preserved_tables = ("knowledge_documents", "knowledge_chunks", "residential_sites", "residential_units", "application_settings")
    with store.engine.connect() as connection:
        counts = {table: connection.scalar(text(f"SELECT count(*) FROM {table}")) for table in preserved_tables}
    agent = reset_container.agent
    agent._bindings.update({"own": ("moveinmate", "R001"), "other": ("moveinmate", "R999"),
                            "manager": ("moveinmate", "local:admin"), "extra": ("moveinmate", extra_id)})
    agent._graphs.update({name: object() for name in agent._bindings})
    response = await request_reset(reset_container)
    assert response.status_code == 200
    assert response.json()["reset"] is True
    assert response.json()["scope"] == "all_application_data"
    assert response.json()["generation"] == 1
    assert store.get_resident("R001")["full_name"] == original["full_name"]
    assert store.get_resident("R001")["vehicle"] == original["vehicle"]
    assert not store.list_requests("R001")
    assert not store.list_requests()
    assert store.get_resident("R999") is None
    assert store.get_resident(extra_id) is None
    assert not agent._graphs and not agent._bindings
    with store.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM checklist_tasks WHERE resident_id='R001' AND status='completed'")) == 0
        assert connection.scalar(text("SELECT count(*) FROM residents")) == 1
        assert set(connection.execute(text("SELECT auth_subject FROM users")).scalars()) == {"local:admin", "local:demo001"}
        for table in ("local_credentials", "request_events", "notification_receipts", "vehicle_documents",
                      "regulation_acknowledgements", "parking_reminders"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        for table in preserved_tables:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == counts[table]
        assert connection.scalar(text("SELECT count(*) FROM security_audit_events WHERE action='application.reset'")) == 1
    assert {item["site_id"] for item in store.get_memberships_for_subject("local:admin")} == {
        "sunrise-residence", "vinhomes-central-park",
    }
    assert (await request_reset(reset_container)).status_code == 429


@pytest.mark.asyncio
async def test_changed_site_fails_closed(reset_container):
    with reset_container.store.session() as session:
        session.query(Resident).filter_by(id="R001").update({"site_id": "another-site"})
    assert (await request_reset(reset_container)).status_code == 409


@pytest.mark.asyncio
async def test_reset_invalidates_every_session_but_original_logins_survive(reset_container):
    service = reset_container.local_auth
    with reset_container.store.transaction():
        service.create_resident_account({
            "username": "new-resident", "password": "Test-Extra-2026!",
            "full_name": "New Resident", "apartment": "B303", "building": "B",
            "site_id": "sunrise-residence", "site_name": "Khu Mặt Trời", "resident_type": "owner", "idempotency_key": "session-extra",
        }, actor="local:admin")
    accounts = (("admin", "Test-Admin-2026!"), ("demo001", "Test-Resident-2026!"), ("new-resident", "Test-Extra-2026!"))
    tokens = [service.login(username, password)["access_token"] for username, password in accounts]
    application = FastAPI()
    application.state.container = reset_container
    application.include_router(router)
    application.add_middleware(RequestGuard)
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        for token in tokens:
            assert (await client.get("/api/v1/me/context", headers={"Authorization": f"Bearer {token}"})).status_code == 200
        assert (await request_reset(reset_container)).status_code == 200
        for token in tokens:
            assert (await client.get("/api/v1/me/context", headers={"Authorization": f"Bearer {token}"})).status_code == 401
            assert (await client.post("/api/v1/management/resident-accounts", json={}, headers={"Authorization": f"Bearer {token}"})).status_code == 401
        for username, password in accounts[:2]:
            response = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert response.status_code == 200
            token = response.json()["access_token"]
            assert (await client.get("/api/v1/me/context", headers={"Authorization": f"Bearer {token}"})).status_code == 200
        assert (await client.post("/api/v1/auth/login", json={"username": "new-resident", "password": "Test-Extra-2026!"})).status_code == 401
        assert (await client.post("/api/v1/demo/reset", headers={"Authorization": f"Bearer {token}"})).status_code == 410


def test_full_reset_rolls_back_accounts_profiles_and_memory(reset_container, monkeypatch):
    store = reset_container.store
    ticket = store.create_request("R001", "general_support", {"details": "keep after failure"}, "rollback-reset")
    store.update_resident("R001", {"full_name": "Edited"})
    reset_container.agent._bindings["old"] = ("moveinmate", "R001")
    reset_container.agent._graphs["old"] = object()
    with store.engine.connect() as connection:
        users_before = connection.scalar(text("SELECT count(*) FROM users"))

    def fail_checklist(*args):
        raise RuntimeError("forced-reset-failure")

    monkeypatch.setattr(reset_container.tool_service, "build_checklist", fail_checklist)
    with pytest.raises(RuntimeError, match="forced-reset-failure"):
        reset_application(reset_container)
    assert store.get_request(ticket["request_id"]) is not None
    assert store.get_resident("R001")["full_name"] == "Edited"
    assert store.login_reset_generation() == 0
    assert "old" in reset_container.agent._graphs
    with store.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == users_before
        assert connection.scalar(text("SELECT count(*) FROM security_audit_events WHERE action='application.reset'")) == 0


def test_inflight_request_cannot_recreate_data_after_reset(reset_container):
    store = reset_container.store
    with store.reset_scope():
        assert store.get_resident("R001") is not None
        Context().run(reset_application, reset_container)
        with pytest.raises(ResetInvalidatedError):
            store.create_request("R001", "general_support", {"details": "stale chat"}, "after-reset-race")
    assert not store.list_requests()
