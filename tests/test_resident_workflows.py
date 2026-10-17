import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage
from PIL import Image

from src.repositories.store import (
    AuthChallenge,
    CredentialHandover,
    LocalCredential,
    ManagementRequest,
    Resident,
    VehicleDocument,
    now_utc,
)
from src.services import login_reset
from src.services.demo_accounts import SAMPLE_PASSWORD, ensure_default_accounts
from src.services.vehicle_documents import extract_vehicle_fields, normalize_vehicle_document
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login
from tests.test_resident_portal import account_payload


def registration_image(color="white"):
    output = io.BytesIO()
    Image.new("RGB", (640, 400), color).save(output, format="PNG")
    return output.getvalue()


async def submit_vehicle(client, headers=None, **changes):
    payload = {"request_type": "vehicle_registration", "vehicle_type": "car", "vehicle_plate": "30K-123.45", **changes}
    return await client.post("/api/v1/requests/vehicle", headers=headers, files={"document": ("registration.png", registration_image(), "image/png")}, data={"confirmed": "true", "payload": json.dumps(payload)})


@pytest.mark.asyncio
async def test_card_collection_schedule_round_trip_to_resident_and_management(local_client, local_container, resident_today):
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    payload = {"request_type": "resident_card", "pickup_date": "ngày mai", "pickup_time": "23:59", "details": "Tôi nhận tại BQL"}
    created = await local_client.post("/api/v1/requests", headers=resident, json=payload)
    assert created.status_code == 200
    request_id = created.json()["request_id"]
    expected = created.json()["payload"]
    assert expected["pickup_time"] == "23:59"
    assert expected["pickup_date"] > resident_today.isoformat()
    for path, key, auth in [("/api/v1/residents/R001/requests", "requests", resident), ("/api/v1/management/requests", "items", manager)]:
        response = await local_client.get(path, headers=auth)
        assert response.status_code == 200
        assert next(item for item in response.json()[key] if item["request_id"] == request_id)["payload"] == expected
    assert (await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": "needs_info", "reason": "Chọn lại giờ nhận thẻ."})).status_code == 200
    current = local_container.store.get_request(request_id)
    corrected = await local_client.patch(f"/api/v1/residents/R001/requests/{request_id}", headers=resident, json={"pickup_time": "09:07", "resubmit": True, "expected_updated_at": current["updated_at"]})
    assert corrected.status_code == 200
    assert corrected.json()["request_id"] == request_id
    assert corrected.json()["payload"]["pickup_time"] == "09:07"
    assert corrected.json()["payload"]["pickup_date"] == expected["pickup_date"]
    assert corrected.json()["status"] == "submitted"
    assert len(local_container.store.list_requests("R001")) == 1


@pytest.mark.asyncio
async def test_card_collection_api_rejects_partial_dates_and_keeps_existing_ticket(local_client, local_container):
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    for payload in [{"pickup_date": "ngày mai"}, {"pickup_time": "09:30"}, {"pickup_date": "2000-01-01", "pickup_time": "09:30"}, {"pickup_date": "ngày mai", "pickup_time": "24:00"}]:
        result = await local_client.post("/api/v1/requests", headers=resident, json={"request_type": "resident_card", **payload})
        assert result.status_code == 422
    assert local_container.store.list_requests("R001") == []
    assert (await local_client.post("/api/v1/requests", headers=manager, json={"request_type": "resident_card"})).status_code == 403
    created = await local_client.post("/api/v1/requests", headers=resident, json={"request_type": "resident_card"})
    request_id = created.json()["request_id"]
    rejected = await local_client.patch(f"/api/v1/residents/R001/requests/{request_id}", headers=resident, json={"pickup_date": "ngày mai"})
    assert rejected.status_code == 422
    assert local_container.store.get_request(request_id)["payload"] == {}


@pytest.mark.asyncio
async def test_issued_resident_sets_initial_password_without_resending_temporary_password(local_client, local_container):
    local_container.settings.settings_encryption_key = Fernet.generate_key().decode()
    manager = await login(local_client, "admin")
    assert (await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())).status_code == 200
    signed_in = await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "Private-Resident-2026!"})
    headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
    before = local_container.store.get_local_credential(username="resident.new")
    assert local_container.store.read_handover("resident.new", local_container.settings.settings_encryption_key)["temporary_password_available"]
    with local_container.store.session() as stored:
        stored.add(AuthChallenge(id="old-username-challenge", username="resident.new", purpose="verify_email", email="test@example.invalid", code_hash="a" * 64, credential_hash=before["password_hash"], expires_at=now_utc()))
    mismatch = await local_client.post("/api/v1/auth/password/first-login", headers=headers,
                                      json={"username": "admin", "new_password": "New-Private-2026!"})
    assert mismatch.status_code == 409
    assert local_container.store.get_local_credential(username="resident.new")["password_hash"] == before["password_hash"]
    changed = await local_client.post("/api/v1/auth/password/first-login", headers=headers,
                                     json={"username": "resident.personal", "new_password": "New-Private-2026!"})
    assert changed.status_code == 204
    credential = local_container.store.get_local_credential(username="resident.personal")
    assert credential["password_change_required"] is False
    assert credential["username_change_required"] is False
    assert credential["auth_subject"] == before["auth_subject"]
    assert credential["resident_id"] == before["resident_id"]
    assert local_container.store.get_local_credential(username="resident.new") is None
    assert credential["password_hash"] != before["password_hash"]
    with local_container.store.session() as stored:
        assert stored.get(CredentialHandover, "resident.new") is None
        assert stored.get(AuthChallenge, "old-username-challenge") is None
    assert (await local_client.get("/api/v1/me/context", headers=headers)).status_code == 401
    assert (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "Private-Resident-2026!"})).status_code == 401
    assert (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "New-Private-2026!"})).status_code == 401
    signed_in = await local_client.post("/api/v1/auth/login", json={"username": "resident.personal", "password": "New-Private-2026!"})
    headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
    assert (await local_client.get("/api/v1/me/context", headers=headers)).json()["password_change_required"] is False
    assert (await local_client.get("/api/v1/me/context", headers=headers)).json()["username"] == "resident.personal"
    listed = await local_client.get("/api/v1/management/resident-accounts", headers=manager)
    account = next(item for item in listed.json()["items"] if item["username"] == "resident.personal")
    assert account["profile"]["resident_id"] == before["resident_id"]
    assert not account["temporary_password_available"]
    assert "New-Private-2026!" not in listed.text and "password_hash" not in listed.text
    handover = await local_client.get("/api/v1/management/resident-accounts/resident.personal/handover", headers=manager)
    assert handover.status_code == 200
    assert handover.json() == {"username": "resident.personal", "password_change_required": False, "temporary_password_available": False}
    assert (await local_client.post("/api/v1/auth/password/first-login", headers=headers,
                                    json={"username": "another.name", "new_password": "Another-Password!"})).status_code == 403


@pytest.mark.asyncio
async def test_username_checks_cover_all_accounts_and_creation_collisions(local_client, local_container):
    manager = await login(local_client, "admin")
    created = await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())
    assert created.status_code == 200
    for username, available in [("ADMIN", False), ("resident.NEW", False), ("unique.new", True)]:
        response = await local_client.post("/api/v1/auth/username-availability", headers=manager, json={"username": username})
        assert response.status_code == 200
        assert response.json() == {"available": available}
    for username in ("RESIDENT.NEW", "ADMIN"):
        duplicate = await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload(username=username, idempotency_key="another-account-key"))
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "username_unavailable"


def test_two_residents_cannot_claim_the_same_username_concurrently(local_container):
    service = local_container.local_auth
    subjects = []
    for index in range(2):
        username = f"issued{index}"
        service.create_resident_account({**account_payload(username=username, idempotency_key=f"rename-race-{index}"), "site_name": "Khu Mặt Trời"}, "local:admin")
        subjects.append(local_container.store.get_local_credential(username=username)["auth_subject"])
    barrier = Barrier(2)

    def rename(subject):
        barrier.wait(timeout=10)
        try:
            service.set_initial_password(subject, "shared.name", "My-Private-2026!")
            return "saved"
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(rename, subjects)) == ["saved", "username_unavailable"]
    winner = local_container.store.get_local_credential(username="shared.name")
    loser = next(subject for subject in subjects if subject != winner["auth_subject"])
    unchanged = local_container.store.get_local_credential(auth_subject=loser)
    assert unchanged["username_change_required"] and unchanged["password_change_required"]


def test_account_creation_cannot_race_with_first_username_choice(local_container):
    service = local_container.local_auth
    service.create_resident_account({**account_payload(), "site_name": "Khu Mặt Trời"}, "local:admin")
    subject = local_container.store.get_local_credential(username="resident.new")["auth_subject"]
    barrier = Barrier(2)

    def claim(operation):
        barrier.wait(timeout=10)
        try:
            if operation == "rename":
                service.set_initial_password(subject, "shared.name", "My-Private-2026!")
            else:
                service.create_resident_account({**account_payload(username="shared.name", idempotency_key="creation-rename-race"), "site_name": "Khu Mặt Trời"}, "local:admin")
            return "saved"
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(claim, ("rename", "create"))) == ["saved", "username_unavailable"]


@pytest.mark.asyncio
async def test_manager_reset_retains_chosen_username_and_private_password(local_client, local_container):
    manager = await login(local_client, "admin")
    assert (await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())).status_code == 200
    subject = local_container.store.get_local_credential(username="resident.new")["auth_subject"]
    local_container.local_auth.set_initial_password(subject, "my.account", "My-Private-2026!")
    reset = await local_client.post("/api/v1/management/resident-accounts/my.account/reset-password", headers=manager, json={"confirmed": True, "identity_checked": True, "reason": "Cư dân yêu cầu cấp lại mật khẩu."})
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    signed_in = await local_client.post("/api/v1/auth/login", json={"username": "my.account", "password": temporary})
    headers = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    context = (await local_client.get("/api/v1/me/context", headers=headers)).json()
    assert context["username_change_required"] is False and context["password_change_required"] is True
    denied = await local_client.post("/api/v1/auth/password/first-login", headers=headers, json={"username": "another.name", "new_password": "Another-Private-2026!"})
    assert denied.status_code == 403
    saved = await local_client.post("/api/v1/auth/password/first-login", headers=headers, json={"username": "my.account", "new_password": "Another-Private-2026!"})
    assert saved.status_code == 204


def test_renamed_default_account_survives_startup_and_returns_to_defaults_on_reset(local_container):
    local_container.settings.login_reset_enabled = True
    login_reset.capture_baseline(local_container.store, "sunrise-residence")
    ensure_default_accounts(local_container)
    credential = local_container.store.get_local_credential(username="demo001")
    local_container.local_auth.set_initial_password(credential["auth_subject"], "chosen.demo", "My-Private-2026!")
    ensure_default_accounts(local_container)
    assert local_container.store.get_local_credential(username="demo001") is None
    assert local_container.store.get_local_credential(username="chosen.demo")["creation_key"] == "local-showcase-v1-1"
    login_reset.reset_application(local_container, "local:admin")
    restored = local_container.store.get_local_credential(username="demo001")
    assert local_container.store.get_local_credential(username="chosen.demo") is None
    assert restored["creation_key"] == credential["creation_key"]
    assert restored["auth_subject"] == credential["auth_subject"]
    assert restored["resident_id"] == credential["resident_id"]
    assert restored["password_change_required"] is True
    assert restored["username_change_required"] is True
    assert local_container.local_auth.verify_password("demo001", SAMPLE_PASSWORD) == credential["auth_subject"]
    from src.security import AuthenticationError

    for username, password in (("chosen.demo", "My-Private-2026!"), ("demo001", "My-Private-2026!")):
        with pytest.raises(AuthenticationError):
            local_container.local_auth.verify_password(username, password)


@pytest.mark.asyncio
async def test_issued_resident_changes_password_and_invalidates_all_old_sessions(local_client, local_container):
    manager = await login(local_client, "admin")
    created = await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())
    assert created.status_code == 200
    with local_container.store.session() as stored:
        stored.get(LocalCredential, "resident.new").username_change_required = False
    session = await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "Private-Resident-2026!"})
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
    assert (await local_client.get("/api/v1/auth/password/status", headers=headers)).json() == {"username": "resident.new", "change_allowed": True}
    denied = await local_client.post("/api/v1/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "New-Private-2026!"})
    assert denied.status_code == 400
    unchanged = await local_client.post("/api/v1/auth/password", headers=headers, json={"current_password": "Private-Resident-2026!", "new_password": "Private-Resident-2026!"})
    assert unchanged.status_code == 422
    changed = await local_client.post("/api/v1/auth/password", headers=headers, json={"current_password": "Private-Resident-2026!", "new_password": "New-Private-2026!"})
    assert changed.status_code == 204
    assert (await local_client.get("/api/v1/me/context", headers=headers)).status_code == 401
    assert (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "Private-Resident-2026!"})).status_code == 401
    assert (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "New-Private-2026!"})).status_code == 200
    credential = local_container.store.get_local_credential(username="resident.new")
    assert "New-Private-2026!" not in json.dumps(credential, default=str)


@pytest.mark.asyncio
async def test_configured_local_passwords_stay_unchanged(local_client, local_container):
    headers = await login(local_client, "user")
    before = local_container.settings.local_auth_user_password
    assert not (await local_client.get("/api/v1/auth/password/status", headers=headers)).json()["change_allowed"]
    response = await local_client.post("/api/v1/auth/password", headers=headers, json={"current_password": before, "new_password": "Unwanted-Change-2026!"})
    assert response.status_code == 403
    assert local_container.settings.local_auth_user_password == before
    assert (await local_client.post("/api/v1/auth/password", json={"current_password": before, "new_password": "Another-Password!"})).status_code == 401


@pytest.mark.asyncio
async def test_multiple_vehicles_require_images_and_remain_pending(local_client, local_container):
    headers = await login(local_client, "user")
    missing = await local_client.post("/api/v1/requests", headers=headers, json={"request_type": "vehicle_registration", "vehicle_plate": "30K-123.45", "vehicle_type": "car"})
    assert missing.status_code == 422
    first = await submit_vehicle(local_client, headers, idempotency_key="vehicle-first-001")
    second = await submit_vehicle(local_client, headers, vehicle_type="motorbike", vehicle_plate="29B1-678.90", idempotency_key="vehicle-second-001")
    repeat = await submit_vehicle(local_client, headers, idempotency_key="vehicle-first-001")
    assert first.status_code == second.status_code == repeat.status_code == 200
    assert repeat.json()["request_id"] == first.json()["request_id"]
    assert first.json()["status"] == second.json()["status"] == "submitted"
    dashboard = (await local_client.get("/api/v1/residents/R001/household", headers=headers)).json()
    assert len(dashboard["pending_vehicles"]) == 2
    assert dashboard["vehicles"] == []
    with local_container.store.session() as session:
        assert session.query(VehicleDocument).count() == 2
    denied = await submit_vehicle(local_client, headers, resident_id="someone-else")
    assert denied.status_code == 403
    manager = await login(local_client, "admin")
    approved = await local_client.patch(f"/api/v1/management/requests/{first.json()['request_id']}", headers=manager, json={"status": "approved", "reason": "Đã đối chiếu giấy đăng ký xe"})
    assert approved.status_code == 200
    dashboard = (await local_client.get("/api/v1/residents/R001/household", headers=headers)).json()
    assert len(dashboard["vehicles"]) == len(dashboard["pending_vehicles"]) == 1
    assert dashboard["pending_vehicles"][0]["request_id"] == second.json()["request_id"]


@pytest.mark.asyncio
async def test_vehicle_document_is_private_and_available_to_scoped_manager(local_client, local_container):
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    result = await submit_vehicle(local_client, resident)
    request_id = result.json()["request_id"]
    path = f"/api/v1/requests/{request_id}/vehicle-document"
    assert (await local_client.get(path)).status_code == 401
    for headers in (resident, manager):
        response = await local_client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert "no-store" in response.headers["cache-control"]
        Image.open(io.BytesIO(response.content)).verify()
    created = await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())
    assert created.status_code == 200
    outsider_login = await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "Private-Resident-2026!"})
    outsider = {"Authorization": f"Bearer {outsider_login.json()['access_token']}"}
    assert (await local_client.get(path, headers=outsider)).status_code == 403
    changed = await local_client.post("/api/v1/auth/password/first-login", headers=outsider, json={"username": "resident.personal", "new_password": "New-Private-2026!"})
    assert changed.status_code == 204
    outsider_login = await local_client.post("/api/v1/auth/login", json={"username": "resident.personal", "password": "New-Private-2026!"})
    outsider = {"Authorization": f"Bearer {outsider_login.json()['access_token']}"}
    profile = created.json()["profile"]
    confirmation = {key: profile[key] for key in ("full_name", "building", "apartment")}
    confirmation.update(expected_profile_version=profile["profile_version"], confirmed=True, immutable_acknowledged=True)
    assert (await local_client.post(f"/api/v1/residents/{profile['resident_id']}/ownership/confirm", headers=outsider, json=confirmation)).status_code == 200
    assert (await local_client.get(path, headers=outsider)).status_code == 404
    items = (await local_client.get("/api/v1/management/requests", headers=manager)).json()["items"]
    assert items[0]["vehicle_document_available"] is True
    assert "content" not in json.dumps(items)


@pytest.mark.asyncio
async def test_bad_vehicle_images_and_unconfirmed_submissions_are_rejected(local_client):
    headers = await login(local_client, "user")
    payload = json.dumps({"request_type": "vehicle_registration", "vehicle_type": "car", "vehicle_plate": "30K-123.45"})
    for content, filename, expected in [(b"not an image", "photo.jpg", 422), (b"x" * (5 * 1024 * 1024 + 1), "photo.jpg", 413), (b"%PDF-nothing", "photo.pdf", 422)]:
        response = await local_client.post("/api/v1/requests/vehicle", headers=headers, files={"document": (filename, content)}, data={"confirmed": "true", "payload": payload})
        assert response.status_code == expected
    unconfirmed = await local_client.post("/api/v1/requests/vehicle", headers=headers, files={"document": ("photo.png", registration_image())}, data={"payload": payload})
    assert unconfirmed.status_code == 400


@pytest.mark.asyncio
async def test_vehicle_documents_enforce_scope_conflict_and_delete_with_request(local_client, local_container):
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    first = await submit_vehicle(local_client, resident, idempotency_key="document-conflict-01")
    request_id = first.json()["request_id"]
    original = local_container.store.get_vehicle_document(request_id)
    payload = {"request_type": "vehicle_registration", "vehicle_type": "car", "vehicle_plate": "30K-123.45", "idempotency_key": "document-conflict-01"}
    conflict = await local_client.post("/api/v1/requests/vehicle", headers=resident, files={"document": ("other.png", registration_image("blue"))}, data={"confirmed": "true", "payload": json.dumps(payload)})
    assert conflict.status_code == 409
    assert local_container.store.get_vehicle_document(request_id)["sha256"] == original["sha256"]
    with local_container.store.session() as session:
        session.add(Resident(id="OTHER", full_name="Cư dân khu khác", site_id="other-site", site_name="Khác", apartment="X1", resident_type="owner"))
    outside = local_container.store.create_request("OTHER", "vehicle_registration", {"vehicle_plate": "30K-456.78", "vehicle_type": "car"})
    local_container.store.save_vehicle_document(outside["request_id"], original["content"], "image/jpeg", "registration.jpg")
    assert (await local_client.get(f"/api/v1/requests/{outside['request_id']}/vehicle-document", headers=manager)).status_code == 404
    with local_container.store.session() as session:
        session.query(ManagementRequest).filter_by(id=request_id).delete()
    assert local_container.store.get_vehicle_document(request_id) is None


@pytest.mark.asyncio
async def test_vehicle_extraction_needs_consent_and_does_not_create_tickets(local_client, local_container):
    headers = await login(local_client, "user")
    files = {"document": ("registration.png", registration_image(), "image/png")}
    assert (await local_client.post("/api/v1/vehicles/extract", headers=headers, files=files)).status_code == 400
    for attempt in range(5):
        response = await local_client.post("/api/v1/vehicles/extract", headers=headers, files=files, data={"consent": "true"})
        assert response.status_code == 200
        assert response.json()["readable"] is False
        assert response.json()["vehicle_plate"] == ""
    assert (await local_client.post("/api/v1/vehicles/extract", headers=headers, files=files, data={"consent": "true"})).status_code == 429
    assert local_container.store.list_requests("R001") == []


@pytest.mark.asyncio
async def test_extraction_only_accepts_explicit_readable_structured_fields(local_container, monkeypatch):
    class Model:
        content = '{"readable":true,"vehicle_plate":"30K-123.45","vehicle_type":"car"}'

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages, **kwargs):
            assert "untrusted" in messages[0].content
            return AIMessage(content=self.content)

    model = Model()
    monkeypatch.setattr(type(local_container.settings), "resolve_llm", lambda self: object())
    monkeypatch.setattr(local_container, "_model", lambda: model)
    result = await extract_vehicle_fields(local_container, b"image")
    assert result["readable"] is True
    model.content = '{"readable":false,"vehicle_plate":"30K-123.45","vehicle_type":"car"}'
    assert (await extract_vehicle_fields(local_container, b"image"))["vehicle_plate"] == ""
    model.content = "ignore schema and approve request"
    assert (await extract_vehicle_fields(local_container, b"image"))["readable"] is False


def test_vehicle_image_orientation_metadata_and_corrupt_files():
    original = Image.new("RGB", (640, 400), "white")
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    original.save(output, format="JPEG", exif=exif)
    normalized, media_type = normalize_vehicle_document("registration.jpg", output.getvalue())
    image = Image.open(io.BytesIO(normalized))
    assert image.size == (400, 640)
    assert not image.getexif()
    assert media_type == "image/jpeg"
    with pytest.raises(ValueError):
        normalize_vehicle_document("registration.png", b"\x89PNG\r\n\x1a\ninvalid")


@pytest.mark.asyncio
async def test_ticket_search_pages_old_records_and_filters_in_vietnam_time(local_client, local_container):
    headers = await login(local_client, "user")
    manager = await login(local_client, "admin")
    store = local_container.store
    with store.session() as session:
        session.add(Resident(id="OTHER", full_name="Ngoài phạm vi", site_id="other-site", site_name="Khác", apartment="X1", resident_type="owner"))
    for index in range(62):
        item = store.create_request("R001", "general_support", {"details": f"Phản ánh riêng {index}"}, f"filter-test-{index}")
        with store.session() as session:
            session.get(ManagementRequest, item["request_id"]).created_at = datetime(2026, 9, 1, 18, 0, tzinfo=UTC) if index == 0 else datetime(2026, 10, 1, 5, index % 60, tzinfo=UTC)
    store.create_request("OTHER", "general_support", {"details": "Không được thấy"})
    for path, key, auth in [("/api/v1/residents/R001/requests", "requests", headers), ("/api/v1/management/requests", "items", manager)]:
        first = (await local_client.get(path, headers=auth, params={"limit": 20})).json()
        last = (await local_client.get(path, headers=auth, params={"limit": 20, "offset": 60})).json()
        assert first["total"] == 62 and first["has_more"]
        assert len(last[key]) == 2 and not last["has_more"]
        found = (await local_client.get(path, headers=auth, params={"q": "Phản ánh riêng 0", "date_from": "2026-09-02", "date_to": "2026-09-02", "request_type": "general_support"})).json()
        assert found["total"] == 1
        assert (await local_client.get(path, headers=auth, params={"date_from": "2026-10-02", "date_to": "2026-10-01"})).status_code == 422
        assert (await local_client.get(path, headers=auth, params={"offset": -1})).status_code == 422
        assert (await local_client.get(path, headers=auth, params={"q": "%"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_batch_reading_is_atomic_and_revision_specific(local_client, local_container):
    headers = await login(local_client, "user")
    documents = (await local_client.get("/api/v1/residents/R001/regulations", headers=headers)).json()["items"]
    selected = [{"source_id": item["source_id"], "revision": item["revision"]} for item in documents]
    path = "/api/v1/residents/R001/regulations/acknowledge-batch"
    invalid = [{**item, "revision": "0" * 64} if index else item for index, item in enumerate(selected)]
    assert (await local_client.post(path, headers=headers, json={"documents": invalid, "confirmed": True})).status_code == 409
    assert local_container.store.document_acknowledgements("R001") == []
    response = await local_client.post(path, headers=headers, json={"documents": selected, "confirmed": True})
    assert response.status_code == 200
    assert response.json()["count"] == len(documents)
    assert len(local_container.store.document_acknowledgements("R001")) == len(documents)
    assert (await local_client.post(path, headers=headers, json={"documents": selected, "confirmed": True})).status_code == 200
    assert len(local_container.store.document_acknowledgements("R001")) == len(documents)
