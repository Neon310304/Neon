import json

import pytest

from src.repositories.store import Resident
from src.tools.agent_tools import make_langchain_tools
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login
from tests.test_resident_workflows import registration_image, submit_vehicle


def residency_ticket(app_container):
    return app_container.tool_service.create_management_request(
        "R001", "temporary_residency", person_name="Nguyễn Văn Sai",
        citizen_id="001234567890", details="Ghi chú ban đầu",
    )


def test_name_correction_replaces_same_ticket_and_retains_history(app_container):
    item = residency_ticket(app_container)
    request_id = item["request_id"]
    app_container.store.decide_request(request_id, "needs_info", "Tên không khớp CCCD")
    current = app_container.tool_service.get_request_status("R001", request_id)
    result = app_container.tool_service.update_management_request(
        "R001", request_id, person_name="  Nguyễn Văn Đúng  ", resubmit=True,
        expected_updated_at=current["updated_at"],
    )
    assert result["request_id"] == request_id
    assert result["payload"]["person_name"] == "Nguyễn Văn Đúng"
    assert result["payload"]["citizen_id"] == "001234567890"
    assert result["payload"]["details"] == "Ghi chú ban đầu"
    assert result["status"] == "submitted"
    assert result["decision_reason"] is None
    assert result["payload"]["resubmission_count"] == 1
    assert len(app_container.store.list_requests("R001")) == 1
    events = app_container.store.request_timeline(request_id)
    assert [event["status"] for event in events] == ["submitted", "needs_info", "submitted"]
    assert events[1]["reason"] == "Tên không khớp CCCD"
    assert "họ tên người cư trú" in events[-1]["reason"]
    for state in ["in_review", "needs_info"]:
        app_container.store.decide_request(request_id, state, "Kiểm tra lại hồ sơ")
    second = app_container.tool_service.update_management_request("R001", request_id, details="Đã đối chiếu", resubmit=True)
    assert second["payload"]["resubmission_count"] == 2
    app_container.store.decide_request(request_id, "approved", "Hồ sơ hợp lệ")
    assert app_container.store.get_request(request_id)["status"] == "approved"
    app_container.store.decide_request(request_id, "completed", "Đã xử lý")


@pytest.mark.parametrize("changes,error", [
    ({"person_name": "123"}, "invalid_person_name"),
    ({"person_name": " "}, "invalid_person_name"),
    ({"person_name": "A" * 121}, "invalid_person_name"),
    ({"citizen_id": ""}, "invalid_citizen_id"),
    ({"move_date": ""}, "invalid_move_date"),
    ({"visitor_name": "Tên khách"}, "invalid_visitor_name"),
    ({"vehicle_plate": "30K12345"}, "invalid_vehicle_plate"),
    ({"replacement_details": "A" * 1001}, "invalid_details"),
])
def test_invalid_fields_do_not_mutate_the_ticket(app_container, changes, error):
    item = residency_ticket(app_container)
    with pytest.raises(ValueError, match=error):
        app_container.tool_service.update_management_request("R001", item["request_id"], **changes, resubmit=True)
    assert app_container.store.get_request(item["request_id"])["payload"] == item["payload"]
    assert len(app_container.store.request_timeline(item["request_id"])) == 1


def test_stale_version_cannot_overwrite_a_manager_update(app_container):
    item = residency_ticket(app_container)
    app_container.store.decide_request(item["request_id"], "needs_info", "Sửa họ tên")
    with pytest.raises(ValueError, match="request_version_conflict"):
        app_container.tool_service.update_management_request(
            "R001", item["request_id"], person_name="Nguyễn Văn Đúng", resubmit=True,
            expected_updated_at=item["updated_at"],
        )
    assert app_container.store.get_request(item["request_id"])["payload"]["person_name"] == "Nguyễn Văn Sai"


def test_duplicate_correction_rolls_back_payload_status_and_event(app_container):
    item = residency_ticket(app_container)
    app_container.tool_service.create_management_request(
        "R001", "temporary_residency", person_name="Nguyễn Văn Đúng", citizen_id="001234567891",
    )
    app_container.store.decide_request(item["request_id"], "needs_info", "Sửa CCCD")
    with pytest.raises(ValueError, match="request_duplicate"):
        app_container.tool_service.update_management_request(
            "R001", item["request_id"], person_name="Nguyễn Văn Đúng", citizen_id="001234567891", resubmit=True,
        )
    current = app_container.store.get_request(item["request_id"])
    assert current["status"] == "needs_info"
    assert current["payload"] == item["payload"]
    assert len(app_container.store.request_timeline(item["request_id"])) == 2


def test_other_workflow_fields_replace_old_values(app_container, resident_today):
    service = app_container.tool_service
    visitor = service.create_management_request("R001", "visitor_registration", visitor_name="Tên Cũ", visit_date="ngày mai")
    result = service.update_management_request("R001", visitor["request_id"], visitor_name="Tên Mới", visit_date="ngày kia", resubmit=True)
    assert result["payload"]["visitor_name"] == "Tên Mới"
    assert result["payload"]["visit_date"] != visitor["payload"]["visit_date"]
    ticket = service.create_management_request("R001", "general_support", details="Nội dung sai")
    result = service.update_management_request("R001", ticket["request_id"], replacement_details="Nội dung chính xác", resubmit=True)
    assert result["payload"]["details"] == "Nội dung chính xác"


def test_agent_tool_supports_replacing_name_on_existing_ticket(app_container):
    item = residency_ticket(app_container)
    update = next(tool for tool in make_langchain_tools(app_container.tool_service, "R001") if tool.name == "update_management_request")
    result = json.loads(update.invoke({"request_id": item["request_id"], "person_name": "Nguyễn Văn Đúng", "resubmit": True}))
    assert result["payload"]["person_name"] == "Nguyễn Văn Đúng"
    assert result["request_id"] == item["request_id"]
    assert len(app_container.store.list_requests("R001")) == 1


@pytest.mark.asyncio
async def test_api_correction_and_manager_sequence(local_client, local_container):
    item = residency_ticket(local_container)
    request_id = item["request_id"]
    resident = await login(local_client, "user")
    manager = await login(local_client, "admin")
    for status in ["submitted", "in_review", "needs_info"]:
        if status != "submitted":
            result = await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": status, "reason": "Sửa họ tên trên hồ sơ"})
            assert result.status_code == 200
        denied = await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": "completed", "reason": "Không được bỏ qua duyệt"})
        assert denied.status_code == 409
    current = local_container.store.get_request(request_id)
    path = f"/api/v1/residents/R001/requests/{request_id}"
    payload = {"person_name": "Nguyễn Văn Đúng", "resubmit": True, "expected_updated_at": current["updated_at"]}
    assert (await local_client.patch(path, headers=manager, json=payload)).status_code == 404
    updated = await local_client.patch(path, headers=resident, json=payload)
    assert updated.status_code == 200
    assert updated.json()["status"] == "submitted"
    assert updated.json()["request_id"] == request_id
    assert updated.json()["payload"]["person_name"] == "Nguyễn Văn Đúng"
    assert (await local_client.patch(path, headers=resident, json=payload)).status_code == 409
    queue = await local_client.get("/api/v1/management/requests", headers=manager)
    assert queue.json()["items"][0]["payload"]["person_name"] == "Nguyễn Văn Đúng"
    for status in ["in_review", "needs_info"]:
        assert (await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": status, "reason": "Đối chiếu lại hồ sơ"})).status_code == 200
    await local_client.patch(path, headers=resident, json={"replacement_details": "Đã đối chiếu", "resubmit": True})
    assert (await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": "approved", "reason": "Hồ sơ hợp lệ"})).status_code == 200
    assert (await local_client.patch(path, headers=resident, json={"person_name": "Không được sửa"})).status_code == 409
    assert (await local_client.patch(f"/api/v1/management/requests/{request_id}", headers=manager, json={"status": "completed", "reason": "Đã xử lý xong"})).status_code == 200


@pytest.mark.asyncio
async def test_other_resident_cannot_correct_ticket(local_client, local_container):
    with local_container.store.session() as session:
        session.add(Resident(id="R002", full_name="Người khác", apartment="A1206", site_id="sunrise-residence", site_name="Test site", resident_type="owner"))
    item = local_container.tool_service.create_management_request("R002", "temporary_residency", person_name="Tên Người Khác", citizen_id="001234567890")
    headers = await login(local_client, "user")
    result = await local_client.patch(f"/api/v1/residents/R001/requests/{item['request_id']}", headers=headers, json={"person_name": "Không được đổi", "resubmit": True})
    assert result.status_code == 404
    assert local_container.store.get_request(item["request_id"])["payload"]["person_name"] == "Tên Người Khác"


@pytest.mark.asyncio
async def test_vehicle_correction_preserves_or_replaces_mandatory_document(local_client, local_container):
    resident = await login(local_client, "user")
    item = (await submit_vehicle(local_client, resident)).json()
    request_id = item["request_id"]
    original = local_container.store.get_vehicle_document(request_id)
    local_container.store.decide_request(request_id, "needs_info", "Biển số sai, ảnh mờ")
    path = f"/api/v1/residents/R001/requests/{request_id}"
    result = await local_client.patch(path, headers=resident, json={"vehicle_plate": "30K-234.56", "resubmit": True})
    assert result.status_code == 200
    assert local_container.store.get_vehicle_document(request_id)["sha256"] == original["sha256"]
    upload = {"document": ("clear.png", registration_image("green"), "image/png")}
    data = {"payload": json.dumps({"vehicle_type": "motorbike", "resubmit": True, "expected_updated_at": result.json()["updated_at"]}), "confirmed": "true"}
    result = await local_client.patch(path + "/vehicle", headers=resident, files=upload, data=data)
    assert result.status_code == 200
    assert result.json()["request_id"] == request_id
    assert result.json()["payload"]["vehicle_type"] == "motorbike"
    assert local_container.store.get_vehicle_document(request_id)["sha256"] != original["sha256"]
    assert len(local_container.store.list_requests("R001")) == 1
    assert (await local_client.patch(path + "/vehicle", headers=resident, files=upload, data=data)).status_code == 409
    assert len(local_container.store.request_timeline(request_id)) == 4


@pytest.mark.asyncio
async def test_invalid_vehicle_correction_cannot_replace_document(local_client, local_container):
    resident = await login(local_client, "user")
    item = (await submit_vehicle(local_client, resident)).json()
    request_id = item["request_id"]
    original = local_container.store.get_vehicle_document(request_id)
    response = await local_client.patch(
        f"/api/v1/residents/R001/requests/{request_id}/vehicle", headers=resident,
        files={"document": ("new.png", registration_image("green"), "image/png")},
        data={"payload": json.dumps({"vehicle_plate": "ABCDE", "resubmit": True}), "confirmed": "true"},
    )
    assert response.status_code == 422
    assert local_container.store.get_vehicle_document(request_id)["sha256"] == original["sha256"]
    assert len(local_container.store.request_timeline(request_id)) == 1


@pytest.mark.asyncio
async def test_legacy_vehicle_ticket_must_attach_document_before_resubmitting(local_client, local_container):
    item = local_container.tool_service.create_management_request("R001", "vehicle_registration", vehicle_plate="30K-123.45", vehicle_type="car")
    resident = await login(local_client, "user")
    path = f"/api/v1/residents/R001/requests/{item['request_id']}"
    response = await local_client.patch(path, headers=resident, json={"vehicle_plate": "30K-234.56", "resubmit": True})
    assert response.status_code == 422
    assert response.json()["detail"] == "vehicle_document_required"
    response = await local_client.patch(
        path + "/vehicle", headers=resident,
        files={"document": ("new.png", registration_image(), "image/png")},
        data={"payload": json.dumps({"vehicle_plate": "30K-234.56", "resubmit": True}), "confirmed": "true"},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == item["request_id"]
    assert response.json()["vehicle_document_available"] is True
