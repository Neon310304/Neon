from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from src.repositories.store import LocalCredential, Resident
from src.security import LocalAuthService
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login


def account_payload(**changes):
    return {
        "username": "resident.new", "password": "Private-Resident-2026!",
        "full_name": "Nguyễn Thu Hà", "site_id": "sunrise-residence",
        "building": "A", "apartment": "A1205", "resident_type": "tenant",
        "confirmed": True, "idempotency_key": "account-create-unique-01", **changes,
    }


def unconfirm_owner(store):
    with store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = None
    profile = store.get_resident("R001")
    return {"full_name": profile["full_name"], "building": profile["building"], "apartment": profile["apartment"], "expected_profile_version": profile["profile_version"], "confirmed": True, "immutable_acknowledged": True}


@pytest.mark.asyncio
async def test_owner_must_confirm_before_requests_chat_or_profile_mutations(local_client, local_container):
    unconfirm_owner(local_container.store)
    headers = await login(local_client, "user")
    for path, payload in [("/requests", {"request_type": "resident_card", "idempotency_key": "blocked-owner-card"}), ("/agent/chat", {"project_id": "moveinmate", "conversation_id": "ownership-gate", "message": "Đăng ký xe"})]:
        response = await local_client.post("/api/v1" + path, headers=headers, json=payload)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ownership_confirmation_required"
    progress = await local_client.get("/api/v1/residents/R001/progress", headers=headers)
    task = progress.json()["all_tasks"][0]
    assert task["code"] == "verify_residency" and task["required"] is True
    assert task["title"] == "Xác thực quyền sở hữu căn hộ" and task["status"] == "pending"
    assert (await local_client.get("/api/v1/locations", headers=headers)).status_code == 200
    assert (await local_client.get("/api/v1/auth/password/status", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_ownership_confirms_assigned_identity_unlocks_features_and_retries_safely(local_client, local_container):
    payload = unconfirm_owner(local_container.store)
    headers = await login(local_client, "user")
    endpoint = "/api/v1/residents/R001/ownership/confirm"
    response = await local_client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["full_name"] == payload["full_name"]
    assert profile["ownership_confirmed_at"] and profile["profile_version"] == payload["expected_profile_version"] + 1
    assert response.json()["checklist"]["items"][0]["status"] == "completed"
    repeated = await local_client.post(endpoint, headers=headers, json=payload)
    assert repeated.status_code == 200 and repeated.json()["profile"] == profile
    changed = await local_client.post(endpoint, headers=headers, json={**payload, "full_name": "Tên khác"})
    assert changed.status_code == 409 and changed.json()["detail"] == "ownership_information_locked"
    ticket = await local_client.post("/api/v1/requests", headers=headers, json={"request_type": "resident_card", "idempotency_key": "unlocked-owner-card"})
    assert ticket.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"confirmed": False}, {"immutable_acknowledged": False}, {"full_name": "Tên tự thay đổi"}, {"building": "B"}, {"apartment": "NO-SUCH-UNIT"}, {"expected_profile_version": 999}])
async def test_ownership_requires_consent_known_location_and_current_profile(local_client, local_container, changes):
    payload = unconfirm_owner(local_container.store)
    original = local_container.store.get_resident("R001")
    response = await local_client.post("/api/v1/residents/R001/ownership/confirm", headers=await login(local_client, "user"), json={**payload, **changes})
    assert response.status_code in {409, 422}
    assert local_container.store.get_resident("R001") == original


@pytest.mark.asyncio
async def test_ownership_cannot_claim_another_owner_or_be_confirmed_by_manager(local_client, local_container):
    payload = unconfirm_owner(local_container.store)
    with local_container.store.session() as session:
        session.add(Resident(id="ANOTHEROWNER", full_name="Chủ căn khác", apartment="C1208", building="C", site_id="sunrise-residence", site_name="Khu Mặt Trời", resident_type="owner"))
    response = await local_client.post("/api/v1/residents/R001/ownership/confirm", headers=await login(local_client, "user"), json={**payload, "building": "C", "apartment": "C1208"})
    assert response.status_code == 409 and response.json()["detail"] == "ownership_information_locked"
    response = await local_client.post("/api/v1/residents/R001/ownership/confirm", headers=await login(local_client, "admin"), json=payload)
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("resident_type", ["tenant", "family_member"])
async def test_non_owners_confirm_assigned_residency_before_other_procedures(local_client, local_container, resident_type):
    payload = unconfirm_owner(local_container.store)
    local_container.store.update_resident("R001", {"resident_type": resident_type})
    payload["expected_profile_version"] = local_container.store.get_resident("R001")["profile_version"]
    headers = await login(local_client, "user")
    ticket = await local_client.post("/api/v1/requests", headers=headers, json={"request_type": "resident_card", "idempotency_key": "non-owner-card"})
    assert ticket.status_code == 403
    response = await local_client.post("/api/v1/residents/R001/ownership/confirm", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["profile"]["full_name"] == payload["full_name"]
    ticket = await local_client.post("/api/v1/requests", headers=headers, json={"request_type": "resident_card", "idempotency_key": "non-owner-card"})
    assert ticket.status_code == 200


@pytest.mark.parametrize("field,value", [("full_name", "Tên khác"), ("apartment", "C1208"), ("building", "C"), ("site_id", "other-site"), ("site_name", "Khu khác"), ("resident_type", "tenant")])
def test_confirmed_owner_identity_is_locked_in_repository(local_client, local_container, field, value):
    original = local_container.store.get_resident("R001")
    with pytest.raises(ValueError, match="ownership_information_locked"):
        local_container.store.update_resident("R001", {field: value})
    assert local_container.store.get_resident("R001") == original


def test_demo_reset_clears_ownership_confirmation(local_client, local_container):
    local_container.store.reset_demo()
    assert local_container.store.get_resident("R001")["ownership_confirmed_at"] is None


def test_two_owners_cannot_replace_assigned_apartments_concurrently(local_client, local_container):
    store = local_container.store
    with store.session() as session:
        for resident_id, resident_type, apartment in [("CLAIM1", "owner", "C1"), ("CLAIM2", "owner", "C2"), ("AVAILABLE", "tenant", "D202")]:
            session.add(Resident(id=resident_id, full_name="Người đăng ký", building="D", apartment=apartment, site_id="sunrise-residence", site_name="Khu Mặt Trời", resident_type=resident_type))
    barrier = Barrier(2)

    def confirm(resident_id):
        barrier.wait(timeout=10)
        try:
            store.confirm_ownership(resident_id, {"full_name": "Người đăng ký", "building": "D", "apartment": "D202", "expected_profile_version": 1})
            return "confirmed"
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, ["CLAIM1", "CLAIM2"]))
    assert sorted(results) == ["ownership_information_locked", "ownership_information_locked"]
    assert store.get_resident("CLAIM1")["apartment"] == "C1"
    assert store.get_resident("CLAIM2")["apartment"] == "C2"


@pytest.mark.asyncio
async def test_reference_inventory_keeps_application_guides_separate_from_documents(local_client):
    headers = await login(local_client, "user")
    response = await local_client.get("/api/v1/residents/R001/regulations", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert all(guide["data_partition"] == "application" for guide in data["guides"])
    assert any(guide["code"] == "vehicle_registration" for guide in data["guides"])
    assert "demo_workflow" not in data
    assert not any(item["source_id"].startswith("application") for item in data["items"])


@pytest.mark.asyncio
async def test_manager_creates_account_with_hashed_password_and_scoped_login(local_client, local_container):
    headers = await login(local_client, "admin")
    response = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload())
    assert response.status_code == 200
    created = response.json()
    resident_id = created["profile"]["resident_id"]
    assert created["profile"]["building"] == "A"
    assert created["confirmed_at"]
    assert "password" not in response.text
    credential = local_container.store.get_local_credential(username="resident.new")
    assert credential["password_hash"] != account_payload()["password"]
    assert len(credential["password_hash"]) == 64
    assert credential["created_by"] == "local:admin"
    sign_in = await local_client.post("/api/v1/auth/login", json={"username": "RESIDENT.NEW", "password": account_payload()["password"]})
    assert sign_in.status_code == 200
    resident_headers = {"Authorization": "Bearer " + sign_in.json()["access_token"]}
    context = (await local_client.get("/api/v1/me/context", headers=resident_headers)).json()
    assert context["roles"] == ["resident"]
    assert context["resident_id"] == resident_id
    assert context["password_change_required"] is True
    assert sign_in.json()["password_change_required"] is True
    blocked = await local_client.get(f"/api/v1/residents/{resident_id}/progress", headers=resident_headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "password_change_required"
    changed = await local_client.post("/api/v1/auth/password/first-login", headers=resident_headers, json={"username": "resident.personal", "new_password": "New-Private-2026!"})
    assert changed.status_code == 204
    sign_in = await local_client.post("/api/v1/auth/login", json={"username": "resident.personal", "password": "New-Private-2026!"})
    assert sign_in.json()["password_change_required"] is False
    resident_headers = {"Authorization": "Bearer " + sign_in.json()["access_token"]}
    assert (await local_client.get("/api/v1/residents/R001", headers=resident_headers)).status_code == 403
    assert (await local_client.get("/api/v1/management/resident-accounts", headers=resident_headers)).status_code == 403
    progress = (await local_client.get(f"/api/v1/residents/{resident_id}/progress", headers=resident_headers)).json()
    assert progress["total"] > 0 and progress["completed"] == 0
    local_container.local_auth = LocalAuthService(local_container.settings, local_container.store)
    local_container.local_auth.provision_accounts()
    reloaded = await local_client.get("/api/v1/me/context", headers=resident_headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["password_change_required"] is False
    assert (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": "wrong"})).status_code == 401
    listed = await local_client.get("/api/v1/management/resident-accounts", headers=headers)
    assert listed.json()["items"][0]["username"] == "resident.personal"
    assert "temporary_password" not in listed.json()["items"][0]
    assert "password_hash" not in listed.text and "salt" not in listed.text


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,status", [({"confirmed": False}, 422), ({"password": "short"}, 422), ({"username": "admin"}, 409), ({"username": "USER"}, 409), ({"site_id": "other-site"}, 403), ({"full_name": "  "}, 422)])
async def test_account_creation_rejects_invalid_confirmation_scope_and_credentials(local_client, local_container, changes, status):
    response = await local_client.post("/api/v1/management/resident-accounts", headers=await login(local_client, "admin"), json=account_payload(**changes))
    assert response.status_code == status
    with local_container.store.session() as session:
        assert session.query(LocalCredential).count() == 0
        assert session.query(Resident).count() == 1


@pytest.mark.asyncio
async def test_resident_cannot_create_accounts(local_client):
    response = await local_client.post("/api/v1/management/resident-accounts", headers=await login(local_client, "user"), json=account_payload())
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_account_retry_is_idempotent_and_changes_conflict(local_client, local_container):
    headers = await login(local_client, "admin")
    first = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload())
    repeat = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload())
    assert repeat.status_code == 200 and repeat.json()["duplicate"]
    assert repeat.json()["profile"]["resident_id"] == first.json()["profile"]["resident_id"]
    conflict = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(apartment="A1206"))
    assert conflict.status_code == 409
    duplicate_name = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(idempotency_key="another-key-0002"))
    assert duplicate_name.status_code == 409
    with local_container.store.session() as session:
        assert session.query(LocalCredential).count() == 1
        assert session.query(Resident).count() == 2


@pytest.mark.asyncio
async def test_location_suggestions_only_expose_scoped_addresses(local_client, local_container):
    with local_container.store.session() as session:
        session.add(Resident(id="R999", full_name="Private other resident", apartment="SECRET-01", site_id="other-site", site_name="Other site", resident_type="owner"))
    for role in ("user", "admin"):
        response = await local_client.get("/api/v1/locations", headers=await login(local_client, role))
        assert response.status_code == 200
        assert len(response.json()["items"]) == 800
        assigned = next(item for item in response.json()["items"] if item["apartment"] == "A1205")
        assert assigned["owner_registered"] is True
        assert assigned["owner_conflict"] is (role == "admin")
        assert "full_name" not in response.text and "SECRET" not in response.text
    assert (await local_client.get("/api/v1/locations")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("building,apartment", [("", " A1205 "), ("", "a 1205"), ("A", "A1205")])
async def test_second_owner_is_blocked_before_account_or_membership_creation(local_client, local_container, building, apartment):
    headers = await login(local_client, "admin")
    response = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(resident_type="owner", building=building, apartment=apartment))
    assert response.status_code == 409
    assert response.json()["detail"] == "apartment_owner_exists"
    assert local_container.store.get_local_credential(username="resident.new") is None
    assert local_container.store.get_memberships_for_subject("local:resident.new") == []
    assert len(local_container.store.list_locations({"sunrise-residence"})) == 800


@pytest.mark.asyncio
@pytest.mark.parametrize("resident_type", ["tenant", "family_member"])
async def test_shared_apartment_allows_tenant_and_family_member(local_client, resident_type):
    headers = await login(local_client, "admin")
    response = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(resident_type=resident_type, building="", apartment="A1205"))
    assert response.status_code == 200
    assert response.json()["profile"]["resident_type"] == resident_type


def test_owner_scope_separates_known_buildings_and_sites(local_container):
    store = local_container.store
    store.update_resident("R001", {"building": "A"})
    assert store.owner_exists("sunrise-residence", " a ", "a1205")
    assert store.owner_exists("sunrise-residence", "", "A1205")
    assert not store.owner_exists("sunrise-residence", "B", "A1205")
    assert not store.owner_exists("another-site", "A", "A1205")
    assert not store.owner_exists("sunrise-residence", "A", "A1205", "R001")
    with pytest.raises(ValueError, match="apartment_owner_exists"):
        store.create_resident_account(account_payload(resident_type="owner", building=" a ", apartment="a 1205", site_name="Khu Mặt Trời"), actor="local:admin", salt="00", password_hash="00", fingerprint="owner-scope")


@pytest.mark.asyncio
async def test_resident_profile_cannot_be_used_to_claim_a_second_owner(local_client, local_container):
    store = local_container.store
    with store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = None
    store.update_resident("R001", {"resident_type": "tenant"})
    with store.session() as session:
        session.add(Resident(id="EXISTINGOWNER", full_name="Chủ sở hữu", apartment="A1205", building="", site_id="sunrise-residence", site_name="Khu Mặt Trời", resident_type="owner"))
    original = store.get_resident("R001")
    headers = await login(local_client, "user")
    response = await local_client.put("/api/v1/residents/R001", headers=headers, json={"resident_type": "owner", "family_members": 1, "vehicle": "none", "needs_internet": False})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ownership_confirmation_required"
    assert store.get_resident("R001") == original
    locations = (await local_client.get("/api/v1/locations", headers=headers)).json()["items"]
    assert next(item for item in locations if item["apartment"] == "A1205")["owner_conflict"] is True


@pytest.mark.asyncio
async def test_confirmed_owner_can_update_preferences_but_not_identity(local_client, local_container):
    headers = await login(local_client, "user")
    original = local_container.store.get_resident("R001")
    values = {"resident_type": "owner", "family_members": 2, "vehicle": "none", "needs_internet": False}
    response = await local_client.put("/api/v1/residents/R001", headers=headers, json=values)
    assert response.status_code == 200
    response = await local_client.put("/api/v1/residents/R001", headers=headers, json={**values, "full_name": "Nguyễn An cập nhật"})
    assert response.status_code == 409
    assert response.json()["detail"] == "ownership_information_locked"
    assert local_container.store.get_resident("R001")["full_name"] == original["full_name"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"full_name": "Tên tự sửa"}, {"resident_type": "owner"}, {"building": "B"}, {"apartment": "B2010"}, {"site_id": "other-site"}])
async def test_tenants_cannot_change_assigned_identity_even_before_confirmation(local_client, local_container, changes):
    store = local_container.store
    with store.session() as session:
        resident = session.get(Resident, "R001")
        resident.resident_type = "tenant"
        resident.ownership_confirmed_at = None
    original = store.get_resident("R001")
    response = await local_client.put("/api/v1/residents/R001", headers=await login(local_client, "user"), json={"family_members": 2, "vehicle": "none", "needs_internet": False, **changes})
    assert response.status_code in {403, 409, 422}
    assert store.get_resident("R001") == original


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"apartment": "A2110"}, {"apartment": "D2011"}, {"building": "E", "apartment": "E0101"}, {"building": "B", "apartment": "A1205"}])
async def test_manager_cannot_assign_units_outside_catalog(local_client, changes):
    response = await local_client.post("/api/v1/management/resident-accounts", headers=await login(local_client, "admin"), json=account_payload(**changes))
    assert response.status_code == 422
    assert response.json()["detail"] == "apartment_not_available"


def test_concurrent_owner_claims_create_only_one_account(local_container):
    barrier = Barrier(2)
    def claim(number):
        values = account_payload(username=f"claim{number}", resident_type="owner", building="C", apartment="C1208", site_name="Khu Mặt Trời", idempotency_key=f"concurrent-owner-{number}")
        barrier.wait(timeout=10)
        try:
            with local_container.store.transaction():
                local_container.local_auth.create_resident_account(values, "local:admin")
            return "created"
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, [1, 2]))
    assert sorted(results) == ["apartment_owner_exists", "created"]
    assert len(local_container.store.list_resident_accounts({"sunrise-residence"})) == 1


@pytest.mark.asyncio
async def test_document_confirmation_is_explicit_versioned_and_persistent(local_client, local_container):
    headers = await login(local_client, "user")
    documents = (await local_client.get("/api/v1/residents/R001/regulations", headers=headers)).json()["items"]
    assert documents
    first = documents[0]
    payload = {"source_id": first["source_id"], "revision": first["revision"], "confirmed": True}
    assert (await local_client.post("/api/v1/residents/R001/regulations/acknowledge", headers=headers, json={**payload, "confirmed": False})).status_code == 422
    assert (await local_client.post("/api/v1/residents/R001/regulations/acknowledge", headers=headers, json={**payload, "revision": "0" * 64})).status_code == 409
    assert (await local_client.post("/api/v1/residents/R999/regulations/acknowledge", headers=headers, json=payload)).status_code == 404
    for document in documents:
        response = await local_client.post("/api/v1/residents/R001/regulations/acknowledge", headers=headers, json={"source_id": document["source_id"], "revision": document["revision"], "confirmed": True})
        assert response.status_code == 200
    repeat = await local_client.post("/api/v1/residents/R001/regulations/acknowledge", headers=headers, json=payload)
    assert repeat.status_code == 200
    assert len(local_container.store.document_acknowledgements("R001")) == len(documents)
    progress = (await local_client.get("/api/v1/residents/R001/progress", headers=headers)).json()
    assert next(task for task in progress["all_tasks"] if task["code"] == "confirm_regulations")["status"] == "completed"
    reloaded = (await local_client.get("/api/v1/residents/R001/regulations", headers=headers)).json()["items"]
    assert all(document["acknowledged_at"] for document in reloaded)
    original = local_container.retriever.list_documents
    local_container.retriever.list_documents = lambda site: [{**item, "body": item["body"] + "\nNội dung cập nhật"} for item in original(site)]
    changed = (await local_client.get("/api/v1/residents/R001/progress", headers=headers)).json()
    assert next(task for task in changed["all_tasks"] if task["code"] == "confirm_regulations")["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_building", ["", "A"])
async def test_residency_selection_uses_server_identity_without_changing_membership(local_client, local_container, target_building):
    headers = await login(local_client, "user")
    profile = local_container.store.get_resident("R001")
    payload = {"request_type": "verify_residency", "residency_purpose": "current", "target_apartment": "A1205", "target_building": target_building, "idempotency_key": "residency-selection-01"}
    created = await local_client.post("/api/v1/requests", headers=headers, json=payload)
    assert created.status_code == 200
    assert profile["full_name"] in created.json()["payload"]["details"]
    assert local_container.store.get_resident("R001") == profile
    invalid = await local_client.post("/api/v1/requests", headers=headers, json={**payload, "target_apartment": "unlisted"})
    assert invalid.status_code == 422
    wrong_building = await local_client.post("/api/v1/requests", headers=headers, json={**payload, "target_building": "B"})
    assert wrong_building.status_code == 422


@pytest.mark.asyncio
async def test_general_support_is_a_real_ticket_in_management_queue(local_client):
    headers = await login(local_client, "user")
    payload = {"request_type": "general_support", "details": "Kỹ thuật căn hộ: nhờ kiểm tra đường nước.", "idempotency_key": "general-support-01"}
    response = await local_client.post("/api/v1/requests", headers=headers, json=payload)
    assert response.status_code == 200 and response.json()["created"]
    repeat = await local_client.post("/api/v1/requests", headers=headers, json=payload)
    assert repeat.status_code == 200 and repeat.json()["duplicate"]
    queue = (await local_client.get("/api/v1/management/requests", headers=await login(local_client, "admin"))).json()
    assert any(item["request_id"] == response.json()["request_id"] for item in queue["items"])
