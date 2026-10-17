from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.fernet import Fernet

from src.repositories.store import (
    LocalCredential,
    ManagementRequest,
    ResidenceMembership,
    Resident,
    SecurityAuditEvent,
    User,
    now_utc,
)
from src.security import AuthorizationError, LocalAuthService
from src.services.ticket_delivery import account_preview, issue_account
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login
from tests.test_resident_workflows import submit_vehicle


def household_request(app, request_type, status="submitted"):
    fields = {
        "temporary_residency": {"person_name": "Nguyễn Minh An", "citizen_id": "001234567890"},
        "vehicle_registration": {"vehicle_plate": "30K-123.45", "vehicle_type": "car"},
        "internet_registration": {"details": "Đăng ký internet cho căn hộ"},
    }
    item = app.tool_service.create_management_request("R001", request_type, **fields[request_type])
    if status == "completed":
        app.store.decide_request(item["request_id"], "approved", "Đã đối chiếu")
    if status != "submitted":
        app.store.decide_request(item["request_id"], status, "Đã kiểm tra hồ sơ")
    return app.store.get_request(item["request_id"])


def issued_household_account(app):
    app.settings.settings_encryption_key = Fernet.generate_key().decode()
    source = household_request(app, "temporary_residency", "approved")
    account = app.tool_service.create_management_request("R001", "account_registration", household_member_id=source["request_id"])
    preview = account_preview(app, {"sunrise-residence"}, "local:admin", account["request_id"])
    created = issue_account(app, {"sunrise-residence"}, "local:admin", account["request_id"], preview["review_token"])
    return app.store.get_request(source["request_id"]), account, created


@pytest.mark.parametrize("status", ["submitted", "in_review", "needs_info", "approved", "completed", "rejected"])
@pytest.mark.parametrize("request_type", ["temporary_residency", "vehicle_registration", "internet_registration"])
def test_household_delete_needs_no_separate_ticket_cancellation(app_container, request_type, status):
    item = household_request(app_container, request_type, status)
    store = app_container.store
    before = store.request_timeline(item["request_id"])
    removed = store.remove_household_request("R001", item["request_id"], request_type, expected_updated_at=item["updated_at"])
    assert removed["payload"]["hidden_from_household"] is True
    assert removed["status"] == ("rejected" if status == "rejected" else "cancelled_by_resident")
    history = store.request_timeline(item["request_id"])
    assert history[:-1] == before
    assert store.get_request(item["request_id"]) is not None
    assert len(store.list_requests("R001")) == 1
    store.remove_household_request("R001", item["request_id"], request_type, expected_updated_at=item["updated_at"])
    assert store.request_timeline(item["request_id"]) == history
    dashboard = app_container.tool_service.get_household_dashboard("R001")
    assert dashboard["occupants"]["temporary_residency_requests"] == []
    assert not dashboard["vehicles"] and not dashboard["pending_vehicles"]
    assert not dashboard["internet"]["can_cancel"]
    assert dashboard["occupants"]["recorded_count"] == 1
    with pytest.raises(ValueError, match="invalid_status_transition"):
        store.decide_request(item["request_id"], "approved", "Không được khôi phục nhầm")


def test_rejected_person_disappears_automatically_without_hiding_the_ticket(app_container):
    item = household_request(app_container, "temporary_residency")
    assert app_container.tool_service.get_household_dashboard("R001")["occupants"]["temporary_residency_requests"]
    app_container.store.decide_request(item["request_id"], "rejected", "Giấy tờ không khớp")
    dashboard = app_container.tool_service.get_household_dashboard("R001")
    assert not dashboard["occupants"]["temporary_residency_requests"]
    assert all(person["person_name"] != "Nguyễn Minh An" for person in dashboard["occupants"]["people"])
    assert app_container.store.get_request(item["request_id"])["status"] == "rejected"
    assert len(app_container.store.request_timeline(item["request_id"])) == 2


def test_approved_person_delete_updates_count_candidates_and_checklist(app_container):
    item = household_request(app_container, "temporary_residency", "completed")
    before = app_container.tool_service.get_household_dashboard("R001")
    person = next(person for person in before["occupants"]["people"] if person["person_name"] == "Nguyễn Minh An")
    assert person["can_remove"] and person["request_id"] == item["request_id"]
    assert before["occupants"]["recorded_count"] == 2
    assert before["account_candidates"]
    app_container.tool_service.remove_temporary_resident("R001", item["request_id"])
    after = app_container.tool_service.get_household_dashboard("R001")
    assert after["occupants"]["recorded_count"] == 1
    assert not after["account_candidates"]
    assert all(person["person_name"] != "Nguyễn Minh An" for person in after["occupants"]["people"])
    task = next(task for task in app_container.tool_service.get_onboarding_progress("R001")["all_tasks"] if task["code"] == "temporary_residency")
    assert task["status"] == "cancelled_by_resident"
    assert task["can_mark_not_needed"]


def test_person_delete_cancels_pending_account_request(app_container):
    person = household_request(app_container, "temporary_residency", "approved")
    account = app_container.tool_service.create_management_request("R001", "account_registration", household_member_id=person["request_id"])
    assert account["created"]
    app_container.tool_service.remove_temporary_resident("R001", person["request_id"])
    assert app_container.store.get_request(account["request_id"])["status"] == "cancelled_by_resident"
    with pytest.raises(ValueError, match="household_member_not_eligible"):
        app_container.store.account_registration_member("R001", person["request_id"])


def add_apartment_resident(store, resident_id, apartment="A1205", building="A", site_id="sunrise-residence", resident_type="tenant"):
    with store.session() as session:
        session.get(Resident, "R001").building = "A"
        session.get(Resident, "R001").ownership_confirmed_at = now_utc()
        session.add(Resident(id=resident_id, full_name="Cư dân " + resident_id, apartment=apartment, building=building,
                             site_id=site_id, site_name="Khu dân cư", resident_type=resident_type, ownership_confirmed_at=now_utc()))


def test_vehicle_delete_obeys_household_owner_or_registrant_permissions(app_container):
    store = app_container.store
    add_apartment_resident(store, "FAMILY", resident_type="family_member")
    add_apartment_resident(store, "TENANT")
    item = store.create_request("FAMILY", "vehicle_registration", {"vehicle_plate": "29A-678.90", "vehicle_type": "car"})
    dashboard = app_container.tool_service.get_household_dashboard("TENANT")
    assert dashboard["pending_vehicles"][0]["can_remove"] is False
    with pytest.raises(ValueError, match="request_not_found"):
        store.remove_household_request("TENANT", item["request_id"], "vehicle_registration")
    for actor in ("R001", "FAMILY"):
        assert app_container.tool_service.get_household_dashboard(actor)["pending_vehicles"][0]["can_remove"] is True
    store.remove_household_request("FAMILY", item["request_id"], "vehicle_registration")
    assert not app_container.tool_service.get_household_dashboard("R001")["pending_vehicles"]
    second = store.create_request("TENANT", "vehicle_registration", {"vehicle_plate": "29A-123.45", "vehicle_type": "car"})
    assert store.remove_household_request("R001", second["request_id"], "vehicle_registration")["status"] == "cancelled_by_resident"


@pytest.mark.parametrize("location", [{"apartment": "A1206"}, {"building": "B"}, {"site_id": "other-site"}])
def test_owner_cannot_delete_another_apartments_vehicle(app_container, location):
    add_apartment_resident(app_container.store, "OTHER", **location)
    item = app_container.store.create_request("OTHER", "vehicle_registration", {"vehicle_plate": "29A-678.90", "vehicle_type": "car"})
    with pytest.raises(ValueError, match="request_not_found"):
        app_container.store.remove_household_request("R001", item["request_id"], "vehicle_registration")
    assert app_container.store.get_request(item["request_id"])["status"] == "submitted"


def test_stale_household_delete_does_not_erase_new_manager_decision(app_container):
    item = household_request(app_container, "vehicle_registration")
    app_container.store.decide_request(item["request_id"], "needs_info", "Bổ sung giấy tờ")
    with pytest.raises(ValueError, match="request_version_conflict"):
        app_container.store.remove_household_request("R001", item["request_id"], "vehicle_registration", expected_updated_at=item["updated_at"])
    current = app_container.store.get_request(item["request_id"])
    assert current["status"] == "needs_info" and not current["payload"].get("hidden_from_household")


def test_delete_does_not_leave_duplicate_residency_records_visible_or_block_future_registration(app_container):
    first = household_request(app_container, "temporary_residency", "completed")
    second = app_container.tool_service.create_management_request("R001", "temporary_residency", person_name="Nguyễn Minh An", citizen_id="001234567890", visit_date="ngày mai")
    app_container.store.decide_request(second["request_id"], "approved", "Hợp lệ")
    app_container.tool_service.remove_temporary_resident("R001", first["request_id"])
    dashboard = app_container.tool_service.get_household_dashboard("R001")
    assert dashboard["occupants"]["recorded_count"] == 1
    assert not dashboard["occupants"]["temporary_residency_requests"]
    assert not dashboard["account_candidates"]
    again = household_request(app_container, "temporary_residency")
    assert again["request_id"] not in {first["request_id"], second["request_id"]}
    assert len(app_container.tool_service.get_household_dashboard("R001")["occupants"]["temporary_residency_requests"]) == 1


def test_removing_a_person_cannot_race_dependent_account_issuance(local_container):
    app = local_container
    app.settings.settings_encryption_key = Fernet.generate_key().decode()
    person = household_request(app, "temporary_residency", "approved")
    account = app.tool_service.create_management_request("R001", "account_registration", household_member_id=person["request_id"])
    preview = account_preview(app, {"sunrise-residence"}, "local:admin", account["request_id"])
    barrier = Barrier(2)

    def issue():
        barrier.wait(timeout=10)
        try:
            return issue_account(app, {"sunrise-residence"}, "local:admin", account["request_id"], preview["review_token"])
        except ValueError as error:
            return {"error": str(error)}

    def remove():
        barrier.wait(timeout=10)
        return app.store.remove_temporary_resident("R001", person["request_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        issuing = pool.submit(issue)
        removing = pool.submit(remove)
        result = issuing.result(timeout=20)
        assert removing.result(timeout=20)["payload"]["hidden_from_household"]
    ticket = app.store.get_request(account["request_id"])
    assert ticket["status"] in {"completed", "cancelled_by_resident"}
    assert app.tool_service.get_household_dashboard("R001")["occupants"]["recorded_count"] == 1
    assert not app.tool_service.get_household_dashboard("R001")["account_candidates"]
    if "username" in result:
        credential = app.store.get_local_credential(username=result["username"])
        assert credential is not None
        assert app.store.get_memberships_for_subject(credential["auth_subject"]) == []
    else:
        assert result["error"] in {"household_member_not_eligible", "account_request_not_eligible", "review_changed"}


@pytest.mark.asyncio
async def test_vehicle_delete_api_requires_confirmation_scope_version_and_keeps_document(local_client, local_container):
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    item = (await submit_vehicle(local_client, resident)).json()
    request_id = item["request_id"]
    path = f"/api/v1/residents/R001/requests/{request_id}/remove-vehicle"
    payload = {"confirmed": True, "expected_updated_at": item["updated_at"]}
    assert (await local_client.post(path, json=payload)).status_code == 401
    assert (await local_client.post(path, headers=manager, json=payload)).status_code == 404
    assert (await local_client.post(path, headers=resident, json={**payload, "confirmed": False})).status_code == 422
    removed = await local_client.post(path, headers=resident, json=payload)
    assert removed.status_code == 200
    assert (await local_client.post(path, headers=resident, json=payload)).status_code == 200
    assert (await local_client.get(f"/api/v1/requests/{request_id}/vehicle-document", headers=manager)).status_code == 200
    assert local_container.store.get_vehicle_document(request_id) is not None
    with local_container.store.session() as session:
        assert session.query(SecurityAuditEvent).filter_by(action="household.remove", resource_id=request_id).count() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("request_type,action", [("temporary_residency", "remove-person"), ("internet_registration", "cancel-internet")])
async def test_resident_can_delete_household_entry_directly_from_completed_ticket(local_client, local_container, request_type, action):
    headers = await login(local_client, "user")
    item = household_request(local_container, request_type, "completed")
    response = await local_client.post(f"/api/v1/residents/R001/requests/{item['request_id']}/{action}", headers=headers,
                                       json={"confirmed": True, "expected_updated_at": item["updated_at"]})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled_by_resident"
    dashboard = (await local_client.get("/api/v1/residents/R001/household", headers=headers)).json()
    assert not dashboard["internet"]["can_cancel"]
    assert dashboard["occupants"]["recorded_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("resident_type", ["tenant", "family_member"])
@pytest.mark.parametrize("residency_confirmed", [False, True])
async def test_deleting_a_source_person_suspends_access_but_preserves_account_and_history(local_client, local_container, resident_type, residency_confirmed):
    local_container.settings.settings_encryption_key = Fernet.generate_key().decode()
    manager = await login(local_client, "admin")
    resident = await login(local_client, "user")
    source = household_request(local_container, "temporary_residency", "approved")
    with local_container.store.session() as session:
        ticket = session.get(ManagementRequest, source["request_id"])
        ticket.payload_json = {**ticket.payload_json, "resident_type": resident_type}
    account = local_container.tool_service.create_management_request("R001", "account_registration", household_member_id=source["request_id"])
    local_container.store.decide_request(account["request_id"], "approved", "Đã đối chiếu")
    preview = await local_client.get(f"/api/v1/management/requests/{account['request_id']}/dependent-account-preview", headers=manager)
    assert preview.status_code == 200
    created = await local_client.post(f"/api/v1/management/requests/{account['request_id']}/issue-dependent-account", headers=manager,
                                      json={"confirmed": True, "review_token": preview.json()["review_token"]})
    assert created.status_code == 200
    issued_credential = local_container.store.get_local_credential(username=created.json()["username"])
    local_container.local_auth.set_initial_password(issued_credential["auth_subject"], "member.changed", "Personal123!")
    username = "member.changed"
    credential = local_container.store.get_local_credential(username=username)
    account_id = credential["resident_id"]
    token = local_container.local_auth.login(username, "Personal123!")
    member_headers = {"Authorization": f"Bearer {token['access_token']}"}
    assert (await local_client.get("/api/v1/me/context", headers=member_headers)).status_code == 200
    with local_container.store.session() as session:
        if residency_confirmed:
            session.get(Resident, account_id).ownership_confirmed_at = now_utc()
    before = local_container.tool_service.get_household_dashboard("R001")
    assert len([person for person in before["occupants"]["people"] if person["person_name"] == "Nguyễn Minh An"]) == 1
    source = local_container.store.get_request(source["request_id"])
    response = await local_client.post(f"/api/v1/residents/R001/requests/{source['request_id']}/remove-person", headers=resident,
                                       json={"confirmed": True, "expected_updated_at": source["updated_at"]})
    assert response.status_code == 200
    after = local_container.tool_service.get_household_dashboard("R001")
    assert all(person["person_name"] != "Nguyễn Minh An" for person in after["occupants"]["people"])
    with local_container.store.session() as session:
        assert session.get(LocalCredential, username).password_hash == credential["password_hash"]
        assert session.get(Resident, account_id) is not None
        assert session.get(ManagementRequest, account["request_id"]).status == "completed"
        user = session.query(User).filter_by(auth_subject=credential["auth_subject"]).one()
        assert user.status == "suspended"
        assert session.query(ResidenceMembership).filter_by(user_id=user.id, resident_id=account_id).one().status == "suspended"
    assert local_container.store.request_timeline(source["request_id"])[-1]["status"] == "cancelled_by_resident"
    assert "tạm ngừng" in local_container.store.request_timeline(source["request_id"])[-1]["reason"]
    for path in ("/api/v1/me/context", f"/api/v1/residents/{account_id}", f"/api/v1/residents/{account_id}/household"):
        denied = await local_client.get(path, headers=member_headers)
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "membership_required"
    denied_login = await local_client.post("/api/v1/auth/login", json={"username": username, "password": "Personal123!"})
    assert denied_login.status_code == 403
    assert denied_login.json()["detail"]["code"] == "membership_required"
    directory = (await local_client.get("/api/v1/management/resident-accounts", headers=manager)).json()["items"]
    assert next(item for item in directory if item["username"] == username)["access_status"] == "suspended"
    reset = await local_client.post(f"/api/v1/management/resident-accounts/{username}/reset-password", headers=manager,
                                   json={"confirmed": True, "identity_checked": True, "reason": "Đã đối chiếu trực tiếp tại BQL"})
    assert reset.status_code == 200
    local_container.local_auth = LocalAuthService(local_container.settings, local_container.store)
    local_container.local_auth.provision_accounts()
    local_container.store.ensure_local_membership(auth_subject=credential["auth_subject"], role="resident", site_id="sunrise-residence", resident_id=account_id)
    with pytest.raises(AuthorizationError) as denied:
        local_container.local_auth.login(username, reset.json()["temporary_password"])
    assert denied.value.code == "membership_required"
    assert (await local_client.get("/api/v1/me/context", headers=resident)).status_code == 200
    assert (await local_client.get("/api/v1/management/requests", headers=manager)).status_code == 200


@pytest.mark.parametrize("changed_location", [{"site_id": "other-site"}, {"building": "B"}, {"apartment": "A1206"}, {"resident_type": "owner"}])
def test_person_delete_cannot_suspend_a_linked_account_outside_its_dependent_scope(local_container, changed_location):
    source, _, created = issued_household_account(local_container)
    credential = local_container.store.get_local_credential(username=created["username"])
    with local_container.store.session() as session:
        member = session.get(Resident, credential["resident_id"])
        for field, value in changed_location.items():
            setattr(member, field, value)
    local_container.store.remove_temporary_resident("R001", source["request_id"])
    assert local_container.store.get_memberships_for_subject(credential["auth_subject"])


def test_person_delete_does_not_suspend_same_name_or_forged_account_links(local_container):
    source, account, created = issued_household_account(local_container)
    unrelated = local_container.local_auth.create_resident_account({"username": "unrelated", "password": "Original123!", "full_name": "Nguyễn Minh An",
        "site_id": "sunrise-residence", "site_name": "Khu dân cư", "building": "A", "apartment": "A1205", "resident_type": "tenant", "idempotency_key": "unrelated-account"}, "local:admin")
    with local_container.store.session() as session:
        for request_id in (source["request_id"], account["request_id"]):
            ticket = session.get(ManagementRequest, request_id)
            ticket.payload_json = {**ticket.payload_json, "app_account_resident_id": unrelated["profile"]["resident_id"]}
    local_container.store.remove_temporary_resident("R001", source["request_id"])
    assert local_container.local_auth.login("unrelated", "Original123!")["access_token"]
    assert local_container.local_auth.login(created["username"], created["temporary_password"])["access_token"]


def test_person_delete_suspension_rolls_back_with_the_household_change(local_container):
    source, _, created = issued_household_account(local_container)
    before = local_container.store.request_timeline(source["request_id"])
    with pytest.raises(RuntimeError, match="rollback"):
        with local_container.store.transaction():
            local_container.store.remove_temporary_resident("R001", source["request_id"])
            raise RuntimeError("rollback")
    assert not local_container.store.get_request(source["request_id"])["payload"].get("hidden_from_household")
    assert local_container.store.request_timeline(source["request_id"]) == before
    assert local_container.local_auth.login(created["username"], created["temporary_password"])["access_token"]


def test_deleting_a_duplicate_person_suspends_the_original_issued_account(local_container):
    source, _, created = issued_household_account(local_container)
    duplicate = local_container.tool_service.create_management_request("R001", "temporary_residency", person_name="Nguyễn Minh An",
                                                                      citizen_id="001234567890", visit_date="ngày mai")
    local_container.store.remove_temporary_resident("R001", duplicate["request_id"])
    assert local_container.store.get_request(source["request_id"])["payload"]["hidden_from_household"]
    with pytest.raises(AuthorizationError) as denied:
        local_container.local_auth.login(created["username"], created["temporary_password"])
    assert denied.value.code == "membership_required"


def test_person_delete_does_not_suspend_an_independent_manager_membership(local_container):
    source, _, created = issued_household_account(local_container)
    credential = local_container.store.get_local_credential(username=created["username"])
    local_container.store.provision_membership(auth_subject=credential["auth_subject"], role="manager", site_id="sunrise-residence")
    local_container.store.remove_temporary_resident("R001", source["request_id"])
    memberships = local_container.store.get_memberships_for_subject(credential["auth_subject"])
    assert [membership["role"] for membership in memberships] == ["manager"]
    with local_container.store.session() as session:
        assert session.query(ResidenceMembership).filter_by(resident_id=credential["resident_id"]).one().status == "suspended"


@pytest.mark.parametrize("request_type", ["vehicle_registration", "internet_registration"])
def test_vehicle_and_internet_removal_do_not_suspend_a_persons_account(local_container, request_type):
    _, _, created = issued_household_account(local_container)
    request = household_request(local_container, request_type)
    local_container.store.remove_household_request("R001", request["request_id"], request_type)
    assert local_container.local_auth.login(created["username"], created["temporary_password"])["access_token"]
