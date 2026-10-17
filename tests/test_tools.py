import unicodedata
from datetime import timedelta

import pytest

from src.tools.agent_tools import ToolService, normalize_person_name


def test_card_preferred_collection_slot_is_normalized_and_persisted(app_container, resident_today):
    service = app_container.tool_service
    created = service.create_management_request("R001", "resident_card", pickup_date="ngày mai", pickup_time="13:07", details="Nhận tại BQL")
    assert created["created"]
    assert created["status"] == "submitted"
    assert created["payload"] == {"pickup_date": (resident_today + timedelta(days=1)).isoformat(), "pickup_time": "13:07", "details": "Nhận tại BQL"}
    assert service.get_request_status("R001", created["request_id"])["payload"] == created["payload"]
    duplicate = service.create_management_request("R001", "resident_card", pickup_date="ngày kia", pickup_time="09:30")
    assert duplicate["duplicate"]
    assert duplicate["request_id"] == created["request_id"]
    assert duplicate["payload"] == created["payload"]


@pytest.mark.parametrize("fields,missing,invalid", [
    ({"pickup_date": "ngày mai"}, ["pickup_time"], []),
    ({"pickup_time": "09:30"}, ["pickup_date"], []),
    ({"pickup_date": "không rõ", "pickup_time": "09:30"}, [], ["pickup_date"]),
    ({"pickup_date": "2000-01-01", "pickup_time": "09:30"}, [], ["pickup_date"]),
    ({"pickup_date": "ngày mai", "pickup_time": "24:00"}, [], ["pickup_time"]),
    ({"pickup_date": "ngày mai", "pickup_time": "09:60"}, [], ["pickup_time"]),
    ({"pickup_date": "ngày mai", "pickup_time": "9:30"}, [], ["pickup_time"]),
])
def test_card_collection_slot_rejects_partial_or_invalid_values(app_container, resident_today, fields, missing, invalid):
    result = app_container.tool_service.create_management_request("R001", "resident_card", **fields)
    assert not result["created"]
    assert result.get("missing_fields", []) == missing
    assert result.get("invalid_fields", []) == invalid
    assert app_container.store.list_requests("R001") == []


def test_card_slot_can_be_supplemented_on_same_ticket_with_version_check(app_container, resident_today):
    service = app_container.tool_service
    created = service.create_management_request("R001", "resident_card")
    with pytest.raises(ValueError, match="pickup_schedule_incomplete"):
        service.update_management_request("R001", created["request_id"], pickup_date="ngày mai")
    app_container.store.decide_request(created["request_id"], "needs_info", "Bổ sung ngày và giờ nhận thẻ.")
    current = app_container.store.get_request(created["request_id"])
    preview = service.update_management_request("R001", current["request_id"], pickup_date="ngày mai", pickup_time="00:07", preview_only=True)
    assert preview["changes"]["pickup_time"] == "00:07"
    assert app_container.store.get_request(current["request_id"])["payload"] == {}
    updated = service.update_management_request("R001", current["request_id"], pickup_date="ngày mai", pickup_time="00:07", resubmit=True, expected_updated_at=current["updated_at"])
    assert updated["request_id"] == created["request_id"]
    assert updated["status"] == "submitted"
    assert updated["payload"]["pickup_date"] == (resident_today + timedelta(days=1)).isoformat()
    assert updated["payload"]["pickup_time"] == "00:07"
    assert len(app_container.store.list_requests("R001")) == 1
    assert "ngày nhận thẻ" in app_container.store.request_timeline(created["request_id"])[-1]["reason"]
    with pytest.raises(ValueError, match="request_version_conflict"):
        service.update_management_request("R001", current["request_id"], pickup_time="10:30", expected_updated_at=current["updated_at"])


def test_card_schedule_fields_cannot_be_applied_to_another_procedure(app_container, resident_today):
    service = app_container.tool_service
    result = service.create_management_request("R001", "internet_registration", details="Tư vấn gói internet", pickup_date="ngày mai", pickup_time="09:30")
    assert not result["created"]
    assert result["invalid_fields"] == ["pickup_date", "pickup_time"]
    item = service.create_management_request("R001", "internet_registration", details="Tư vấn gói internet")
    for field in ("pickup_date", "pickup_time"):
        with pytest.raises(ValueError, match=f"invalid_{field}"):
            service.update_management_request("R001", item["request_id"], **{field: "ngày mai" if field == "pickup_date" else "09:30"})


@pytest.mark.parametrize("question,code", [
    ("Ai hoàn tất ticket internet?", "internet_registration"),
    ("Bao giờ có người lên tư vấn?", "internet_registration"),
    ("Ngày giờ nhận thẻ chọn ở đâu?", "resident_card"),
])
def test_agent_retrieves_updated_application_guidance_without_public_law(app_container, question, code):
    result = app_container.tool_service.search_regulations("R001", question)
    assert result["answer_status"] == "application_guidance"
    assert result["data_partition"] == "application"
    assert result["sources"] == []
    guide = next(item for item in result["guides"] if item["code"] == code)
    text = " ".join(guide["steps"])
    if code == "internet_registration":
        assert "người tư vấn" in text and "được giải quyết" in text
        assert "chưa phải hợp đồng hay lịch lắp đặt" in text
        assert "Lưu ý đầy đủ chỉ nằm trong form đăng ký" in text
        assert "Thông báo này biến mất khi hoàn tất, từ chối hoặc hủy ticket" in text
    else:
        assert "ngày và giờ" in text and "00–23" in text
        assert "chưa phải lịch nhận đã xác nhận" in text


def test_personalized_checklist_uses_profile(app_container):
    result = app_container.tool_service.build_checklist("R001")
    codes = [item["code"] for item in result["items"]]
    assert codes == [
        "verify_residency",
        "temporary_residency",
        "move_in_schedule",
        "resident_card",
        "vehicle_registration",
        "confirm_regulations",
        "account_registration",
        "internet_registration",
        "handover_inspection",
        "safety_briefing",
    ]
    assert all(not item["required"] for item in result["items"][6:])
    assert result["items"][5]["required"]


def test_application_facts_take_precedence_over_demo_card_document(app_container):
    result = app_container.tool_service.search_regulations(
        "R001", "Đăng ký thẻ cư dân cần chuẩn bị gì?", "resident_card"
    )
    assert result["answer_status"] == "application_guidance"
    assert result["guides"][0]["code"] == "resident_card"
    assert result["sources"] == []


@pytest.mark.parametrize("question,source_id", [
    ("Có được để giày dép ngoài hành lang không?", "SRC-COMMON-01"),
    ("Giờ yên tĩnh bắt đầu lúc nào?", "SRC-COMMON-01"),
    ("Khách ở qua đêm có phải khai báo không?", "SRC-VISITOR-RULES-01"),
])
def test_regulation_questions_find_relevant_source(app_container, question, source_id):
    result = app_container.tool_service.search_regulations("R001", question)
    assert result["answer_status"] == "supported"
    assert source_id in {source["source_id"] for source in result["sources"]}
    assert result["sources"][0]["site"] == "Khu Mặt Trời"


def test_rag_fails_closed_for_unsupported_fee(app_container):
    result = app_container.tool_service.search_regulations("R001", "Phí đăng ký thẻ là bao nhiêu?", "resident_card")
    assert result["answer_status"] == "application_guidance"
    assert result["requires_human"] is True
    assert result["sources"] == []
    assert "Phí" in result["unverified_details"][0]


def test_public_law_does_not_establish_building_card_procedure(app_container, monkeypatch):
    monkeypatch.setattr(app_container.retriever, "search", lambda *args: [{
        "source_id": "TEST-PUBLIC-LAW", "title": "Public housing law", "version": "test",
        "site": "Pháp luật Việt Nam", "site_id": "national", "effective_date": "", "section": "Test section",
        "score": 1.0, "text": "A general legal rule unrelated to building card issuance.", "legal_status": "not_verified",
    }])
    result = app_container.tool_service.search_regulations("R001", "Hướng dẫn đăng ký thẻ cư dân")
    assert result["answer_status"] == "application_guidance"
    assert result["guides"][0]["form"] == "resident_card"
    assert not result["sources"]
    assert "Quy định riêng của tòa nhà cần tài liệu BQL đúng khu đã xác minh" in result["source_notice"]


def test_rag_supports_visitor_registration_guidance(app_container):
    result = app_container.tool_service.search_regulations(
        "R001", "Người nhà đến chơi cần đăng ký thế nào?", "visitor_registration"
    )
    assert result["answer_status"] == "application_guidance"
    assert result["guides"][0]["form"] == "visitor_registration"


def test_request_collects_missing_slot_then_creates(app_container):
    app_container.tool_service.build_checklist("R001")
    missing = app_container.tool_service.create_management_request("R001", "vehicle_registration")
    assert missing == {
        "created": False,
        "missing_fields": ["vehicle_plate"],
        "next_action": "ask_resident",
    }

    created = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "30K-123.45", "vehicle-R001-1"
    )
    assert created["created"] is True
    assert created["status"] == "submitted"
    assert created["payload"]["vehicle_plate"] == "30K-123.45"
    assert created["payload"]["vehicle_type"] == "car"
    progress = app_container.tool_service.get_onboarding_progress("R001")
    vehicle_task = next(item for item in progress["all_tasks"] if item["code"] == "vehicle_registration")
    assert vehicle_task["status"] == "submitted"

    motorbike = app_container.tool_service.create_management_request(
        "R001",
        "vehicle_registration",
        "29E2-986.07",
        "vehicle-R001-2",
        "motorbike",
    )
    assert motorbike["payload"]["vehicle_type"] == "motorbike"

    verbose_motorbike = app_container.tool_service.create_management_request(
        "R001",
        "vehicle_registration",
        "29E2-986.08",
        "vehicle-R001-3",
        "xe máy Honda SH Mode",
    )
    assert verbose_motorbike["payload"]["vehicle_type"] == "motorbike"


def test_agent_tool_cannot_complete_approval_task(app_container):
    with pytest.raises(ValueError, match="management_approval_required"):
        app_container.tool_service.update_task_status("R001", "vehicle_registration", "completed")


def test_resident_can_create_card_replacement_and_visitor_requests(app_container, resident_today):
    visit_date = (resident_today + timedelta(days=1)).isoformat()
    card = app_container.tool_service.create_management_request(
        "R001", "card_replacement", idempotency_key="lost-card-R001-1"
    )
    assert card["created"] is True
    assert card["payload"] == {"reason": "lost_card"}

    missing = app_container.tool_service.create_management_request("R001", "visitor_registration")
    assert missing == {
        "created": False,
        "missing_fields": ["visitor_name", "visit_date"],
        "next_action": "ask_resident",
    }

    visitor = app_container.tool_service.create_management_request(
        "R001",
        "visitor_registration",
        idempotency_key="visitor-R001-1",
        visitor_name="Nguyễn Minh Bình",
        visit_date=visit_date,
        details="Người nhà đến chơi",
    )
    assert visitor["created"] is True
    assert visitor["payload"] == {
        "visitor_name": "Nguyễn Minh Bình",
        "visit_date": visit_date,
        "details": "Người nhà đến chơi",
    }


def test_completing_visitor_request_does_not_complete_resident_card(app_container, resident_today):
    created = app_container.tool_service.create_management_request(
        "R001",
        "visitor_registration",
        idempotency_key="visitor-R001-complete",
        visitor_name="Nguyễn Minh Bình",
        visit_date=(resident_today + timedelta(days=1)).isoformat(),
    )
    app_container.store.decide_request(created["request_id"], "approved", "Thông tin hợp lệ")
    app_container.store.decide_request(created["request_id"], "completed", "Đã xử lý")

    progress = app_container.tool_service.get_onboarding_progress("R001")
    card = next(item for item in progress["all_tasks"] if item["code"] == "resident_card")
    assert card["status"] == "pending"


def test_approved_is_not_completed(app_container):
    app_container.tool_service.build_checklist("R001")
    created = app_container.tool_service.create_management_request(
        "R001", "resident_card", idempotency_key="card-R001-1"
    )
    approved = app_container.store.decide_request(created["request_id"], "approved", "Hồ sơ hợp lệ")
    progress = app_container.tool_service.get_onboarding_progress("R001")
    card = next(item for item in progress["all_tasks"] if item["code"] == "resident_card")
    assert approved["status"] == "approved"
    assert card["status"] != "completed"


def test_checklist_built_after_request_reflects_request_status(app_container):
    created = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "29E2-986.07", vehicle_type="motorbike"
    )
    progress = app_container.tool_service.get_onboarding_progress("R001")
    vehicle = next(item for item in progress["all_tasks"] if item["code"] == "vehicle_registration")
    assert vehicle["status"] == "submitted"

    app_container.store.decide_request(created["request_id"], "rejected", "Biển số chưa khớp")
    progress = app_container.tool_service.get_onboarding_progress("R001")
    vehicle = next(item for item in progress["all_tasks"] if item["code"] == "vehicle_registration")
    assert vehicle["status"] == "rejected"


def test_resident_request_history_is_exact_scoped_and_filterable(app_container):
    card = app_container.tool_service.create_management_request("R001", "card_replacement")
    vehicle = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "29E2-986.07", vehicle_type="motorbike"
    )
    app_container.store.decide_request(vehicle["request_id"], "approved", "Hồ sơ hợp lệ")

    history = app_container.tool_service.list_resident_requests("R001")
    assert history["total"] == 2
    assert history["active"] == 2
    assert history["finished"] == 0
    assert history["counts_by_type"] == {
        "vehicle_registration": 1,
        "card_replacement": 1,
    }
    assert history["counts_by_status"] == {"submitted": 1, "approved": 1}
    assert {item["request_id"] for item in history["requests"]} == {
        card["request_id"],
        vehicle["request_id"],
    }

    approved = app_container.tool_service.list_resident_requests("R001", status="đã duyệt")
    assert approved["total"] == 1
    assert approved["requests"][0]["request_id"] == vehicle["request_id"]
    cards = app_container.tool_service.list_resident_requests("R001", request_type="mất thẻ")
    assert cards["total"] == 1
    assert cards["requests"][0]["request_id"] == card["request_id"]
    active = app_container.tool_service.list_resident_requests("R001", status="chưa xong")
    assert active["total"] == 2


def test_resident_request_history_validates_filters_and_limit(app_container):
    app_container.tool_service.create_management_request("R001", "resident_card")
    app_container.tool_service.create_management_request("R001", "card_replacement")

    limited = app_container.tool_service.list_resident_requests("R001", limit=1)
    assert limited["total"] == 2
    assert len(limited["requests"]) == 1
    assert limited["truncated"] is True
    with pytest.raises(ValueError, match="unsupported_request_type"):
        app_container.tool_service.list_resident_requests("R001", request_type="maintenance")
    with pytest.raises(ValueError, match="invalid_request_status"):
        app_container.tool_service.list_resident_requests("R001", status="unknown")
    with pytest.raises(ValueError, match="resident_not_found"):
        app_container.tool_service.list_resident_requests("R404")


def test_duplicate_active_requests_return_existing_record(app_container):
    first = app_container.tool_service.create_management_request("R001", "card_replacement")
    second = app_container.tool_service.create_management_request(
        "R001", "card_replacement", details="Mất thẻ ở sảnh"
    )

    assert first["created"] is True
    assert first["duplicate"] is False
    assert second["created"] is False
    assert second["duplicate"] is True
    assert second["request_id"] == first["request_id"]
    assert len(app_container.store.list_requests("R001")) == 1


def test_duplicate_identity_normalizes_vehicle_and_visitor_input(app_container, resident_today):
    visit_date = resident_today + timedelta(days=1)
    vehicle = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "29E2-986.07", vehicle_type="motorbike"
    )
    same_vehicle = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "29e2 98607", vehicle_type="xe máy"
    )
    visitor = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình", visit_date=visit_date.strftime("%d/%m/%Y")
    )
    same_visitor = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="nguyen   minh binh", visit_date=visit_date.isoformat()
    )
    another_visitor_date = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình", visit_date=(visit_date + timedelta(days=1)).isoformat()
    )

    assert same_vehicle["duplicate"] is True
    assert same_vehicle["request_id"] == vehicle["request_id"]
    assert same_visitor["duplicate"] is True
    assert same_visitor["request_id"] == visitor["request_id"]
    assert another_visitor_date["created"] is True


def test_finished_or_rejected_request_can_be_submitted_again(app_container):
    completed = app_container.tool_service.create_management_request("R001", "resident_card")
    app_container.store.decide_request(completed["request_id"], "approved", "Hồ sơ hợp lệ")
    app_container.store.decide_request(completed["request_id"], "completed", "Đã cấp thẻ")
    next_card = app_container.tool_service.create_management_request("R001", "resident_card")

    rejected = app_container.tool_service.create_management_request("R001", "card_replacement")
    app_container.store.decide_request(rejected["request_id"], "rejected", "Không đủ thông tin")
    next_replacement = app_container.tool_service.create_management_request("R001", "card_replacement")

    assert next_card["created"] is True
    assert next_card["request_id"] != completed["request_id"]
    assert next_replacement["created"] is True
    assert next_replacement["request_id"] != rejected["request_id"]

    finished = app_container.tool_service.list_resident_requests("R001", status="đã kết thúc")
    assert finished["total"] == 2
    assert {item["status"] for item in finished["requests"]} == {"completed", "rejected"}


def test_idempotency_key_reuses_original_even_after_completion(app_container):
    first = app_container.tool_service.create_management_request(
        "R001", "resident_card", idempotency_key="stable-client-action"
    )
    app_container.store.decide_request(first["request_id"], "approved", "Hồ sơ hợp lệ")
    app_container.store.decide_request(first["request_id"], "completed", "Đã cấp thẻ")
    replay = app_container.tool_service.create_management_request(
        "R001", "resident_card", idempotency_key="stable-client-action"
    )

    assert replay["duplicate"] is True
    assert replay["request_id"] == first["request_id"]


def test_idempotency_key_rejects_different_operation(app_container):
    app_container.tool_service.create_management_request(
        "R001", "resident_card", idempotency_key="same-key-different-operation"
    )
    conflict = app_container.tool_service.create_management_request(
        "R001", "card_replacement", idempotency_key="same-key-different-operation"
    )

    assert conflict == {
        "created": False,
        "conflict": True,
        "error": "idempotency_key_conflict",
        "next_action": "do_not_retry",
    }


def test_request_input_validation_prevents_bad_business_data(app_container):
    bad_plate = app_container.tool_service.create_management_request(
        "R001", "vehicle_registration", "không biết", vehicle_type="motorbike"
    )
    bad_visitor = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="1", visit_date="không nhớ"
    )
    past_visitor = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình", visit_date="2000-01-01"
    )

    assert bad_plate["invalid_fields"] == ["vehicle_plate"]
    assert bad_visitor["invalid_fields"] == ["visitor_name", "visit_date"]
    assert bad_visitor["field_errors"]["visit_date"] == "invalid_date_format"
    assert past_visitor["field_errors"]["visit_date"] == "visit_date_in_past"
    assert app_container.store.list_requests("R001") == []


def test_relative_visitor_date_is_normalized(app_container, resident_today):
    created = app_container.tool_service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình", visit_date="ngày mai"
    )

    assert created["created"] is True
    assert created["payload"]["visit_date"] == (resident_today + timedelta(days=1)).isoformat()


def test_visitor_date_accepts_today_and_rejects_yesterday(app_container, resident_today):
    service = app_container.tool_service
    today_request = service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình", visit_date=resident_today.isoformat()
    )
    yesterday_request = service.create_management_request(
        "R001", "visitor_registration", visitor_name="Nguyễn Minh Bình",
        visit_date=(resident_today - timedelta(days=1)).isoformat(),
    )

    assert today_request["created"] is True
    assert today_request["payload"]["visit_date"] == resident_today.isoformat()
    assert yesterday_request["created"] is False
    assert yesterday_request["field_errors"]["visit_date"] == "visit_date_in_past"
    assert len(app_container.store.list_requests("R001")) == 1


def test_request_id_lookup_is_case_insensitive(app_container):
    created = app_container.tool_service.create_management_request("R001", "card_replacement")

    found = app_container.tool_service.get_request_status("R001", created["request_id"].lower())
    assert found["found"] is True
    assert found["request_id"] == created["request_id"]


def test_demo_profile_uses_accented_site_name(app_container):
    assert app_container.tool_service.get_resident_profile("R001")["site_name"] == "Khu Mặt Trời"


def test_temporary_residency_and_complaint_never_become_resident_card(app_container, resident_today):
    service = app_container.tool_service
    assert service.create_management_request("R001", "đăng ký tạm trú")["missing_fields"] == ["person_name", "citizen_id"]
    stay = service.create_management_request(
        "R001", "người cùng ở", person_name="Trần Văn Lan", visit_date="ngày kia",
        details="Người thân ở cùng căn hộ A1205", citizen_id="001234567890",
    )
    complaint = service.create_management_request("R001", "phản ánh", details="Đèn hành lang tầng 12 bị hỏng")
    assert stay["request_type"] == "temporary_residency"
    assert stay["payload"]["person_name"] == "Trần Văn Lan"
    assert stay["payload"]["stay_start"] == (resident_today + timedelta(days=2)).isoformat()
    assert complaint["request_type"] == "general_support"
    assert complaint["payload"]["details"] == "Đèn hành lang tầng 12 bị hỏng"
    tasks = {item["code"]: item["status"] for item in service.get_onboarding_progress("R001")["all_tasks"]}
    assert tasks["temporary_residency"] == "submitted"
    assert tasks["resident_card"] == "pending"


def test_ticket_lifecycle_tracks_review_rejection_and_resident_cancellation(app_container):
    service = app_container.tool_service
    request = service.create_management_request("R001", "temporary_residency", person_name="Nguyễn Văn Bình", citizen_id="001234567890")
    request_id = request["request_id"]
    for status, label in (("in_review", "Đã nhận"), ("needs_info", "Thiếu số liên lạc"), ("approved", "Đủ thông tin")):
        if status == "approved":
            service.update_management_request("R001", request_id, details="Đã bổ sung liên hệ", resubmit=True)
        app_container.store.decide_request(request_id, status, label)
        task = next(item for item in service.get_onboarding_progress("R001")["all_tasks"] if item["code"] == "temporary_residency")
        assert task["status"] == status
        assert task["request_id"] == request_id
    cancelled = service.cancel_management_request("R001", request_id)
    assert cancelled["status"] == "cancelled_by_resident"
    assert service.list_resident_requests("R001", status="finished")["total"] == 1
    assert [item["status"] for item in app_container.store.request_timeline(request_id)] == [
        "submitted", "in_review", "needs_info", "submitted", "approved", "cancelled_by_resident",
    ]
    with pytest.raises(ValueError, match="invalid_status_transition"):
        app_container.store.decide_request(request_id, "completed", "Đã làm")


def test_only_self_service_checklist_tasks_can_be_completed_by_agent(app_container):
    with pytest.raises(ValueError, match="management_approval_required"):
        app_container.tool_service.update_task_status("R001", "temporary_residency", "completed")
    with pytest.raises(ValueError, match="reading_confirmation_required"):
        app_container.tool_service.update_task_status("R001", "confirm_regulations", "completed")


def test_day_after_tomorrow_aliases_are_resolved_in_vietnam_time(app_container, resident_today):
    expected = (resident_today + timedelta(days=2)).isoformat()
    for index, expression in enumerate(("ngày kia", "ngày mốt", "the day after tomorrow")):
        result = app_container.tool_service.create_management_request(
            "R001", "visitor_registration", visitor_name=f"Khách thử {index}", visit_date=expression,
        )
        assert result["payload"]["visit_date"] == expected


def test_household_dashboard_displays_vehicle_registrant_and_status_without_reminders(app_container, resident_today):
    service = app_container.tool_service
    vehicle = service.create_management_request(
        "R001", "vehicle_registration", vehicle_plate="30K-123.45", vehicle_type="car"
    )
    assert service.get_household_dashboard("R001")["vehicles"] == []
    assert service.get_household_dashboard("R001")["pending_vehicles"][0]["status"] == "submitted"
    app_container.store.decide_request(vehicle["request_id"], "approved", "Xe hợp lệ")
    dashboard = service.get_household_dashboard("R001")
    assert dashboard["occupants"]["recorded_count"] == 1
    assert dashboard["as_of_date"] == resident_today.isoformat()
    assert dashboard["vehicles"][0]["vehicle_plate"] == "30K-123.45"
    assert dashboard["vehicles"][0]["owner_name"] == service.get_resident_profile("R001")["full_name"]
    assert dashboard["vehicles"][0]["status"] == "approved"
    assert "days_remaining" not in dashboard["vehicles"][0]
    assert not hasattr(service, "set_parking_due_date")
    assert dashboard["internet"]["status"] == "pending"


def test_household_member_count_is_not_inflated_by_request(app_container):
    service = app_container.tool_service
    assert service.get_household_dashboard("R001")["occupants"]["recorded_count"] == 1
    stay = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="001234567890")
    assert service.get_household_dashboard("R001")["occupants"]["recorded_count"] == 1
    app_container.store.decide_request(stay["request_id"], "approved", "Đã duyệt tạm trú")
    dashboard = service.get_household_dashboard("R001")
    assert dashboard["occupants"]["recorded_count"] == 2
    assert dashboard["occupants"]["temporary_residency_requests"][0]["status"] == "approved"
    assert service.get_resident_profile("R001")["family_members"] == 2


def test_one_completed_resident_keeps_checklist_done_when_another_ticket_changes(app_container):
    service = app_container.tool_service
    first = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="001234567890")
    app_container.store.decide_request(first["request_id"], "approved", "Hợp lệ")
    app_container.store.decide_request(first["request_id"], "completed", "Đã vào ở")
    second = service.create_management_request("R001", "temporary_residency", person_name="Nguyễn Văn Bình", citizen_id="001234567891")
    service.cancel_management_request("R001", second["request_id"])
    task = next(item for item in service.get_onboarding_progress("R001")["all_tasks"] if item["code"] == "temporary_residency")
    assert task["status"] == "completed"
    assert task["request_id"] == first["request_id"]
    service.remove_temporary_resident("R001", second["request_id"])
    assert [item["request_id"] for item in service.get_household_dashboard("R001")["occupants"]["temporary_residency_requests"]] == [first["request_id"]]
    assert service.get_request_status("R001", second["request_id"])["found"]


def test_resubmit_updates_original_ticket_and_keeps_history(app_container, resident_today):
    service = app_container.tool_service
    request = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="001234567890")
    request_id = request["request_id"]
    app_container.store.decide_request(request_id, "needs_info", "Bổ sung ngày bắt đầu")
    updated = service.update_management_request("R001", request_id, details="Bắt đầu ở ngày mai", move_date="ngày mai", resubmit=True)
    assert updated["request_id"] == request_id
    assert updated["status"] == "submitted"
    assert updated["payload"]["stay_start"] == (resident_today + timedelta(days=1)).isoformat()
    assert service.list_resident_requests("R001")["total"] == 1
    assert [event["status"] for event in app_container.store.request_timeline(request_id)] == ["submitted", "needs_info", "submitted"]
    assert updated["decision_reason"] is None


def test_cancelled_ticket_is_terminal_and_retains_history(app_container):
    service = app_container.tool_service
    first = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="001234567890")
    request_id = first["request_id"]
    service.cancel_management_request("R001", request_id)
    service.remove_temporary_resident("R001", request_id)
    with pytest.raises(ValueError, match="request_not_editable"):
        service.update_management_request("R001", request_id, citizen_id="001234567891", resubmit=True)
    assert service.get_request_status("R001", request_id)["status"] == "cancelled_by_resident"
    assert service.list_resident_requests("R001")["total"] == 1


def test_moving_ticket_requires_day_and_hour(app_container, resident_today):
    service = app_container.tool_service
    assert service.create_management_request("R001", "move_in_schedule")["missing_fields"] == ["visit_date", "move_time"]
    request = service.create_management_request("R001", "move_in_schedule", visit_date="ngày mai", move_time="09:30")
    assert request["created"]
    assert request["payload"]["move_time"] == "09:30"
    assert request["payload"]["move_date"] == (resident_today + timedelta(days=1)).isoformat()


def test_temporary_residency_rejects_invalid_citizen_id(app_container):
    service = app_container.tool_service
    response = service.create_management_request("R001", "temporary_residency", person_name="Trần Văn Lan", citizen_id="12345")
    assert response["invalid_fields"] == ["citizen_id"]
    assert service.list_resident_requests("R001")["total"] == 0


@pytest.mark.parametrize("value, conversational, expected", [
    ("  phạm   văn\thùng  ", False, "Phạm Văn Hùng"),
    ("anh phạm văn hùng", True, "Phạm Văn Hùng"),
    ("anh phạm văn hùng", False, "Anh Phạm Văn Hùng"),
    ("NGUYỄN ANH TUẤN", True, "Nguyễn Anh Tuấn"),
    ("pham van hung", True, "Pham Van Hung"),
    ("", True, ""),
    (None, False, ""),
])
def test_normalize_person_name(value, conversational, expected):
    assert normalize_person_name(value, conversational=conversational) == expected
    if value:
        assert normalize_person_name(unicodedata.normalize("NFD", value), conversational=conversational) == expected


@pytest.mark.parametrize("request_type, field", [("temporary_residency", "person_name"), ("visitor_registration", "visitor_name")])
def test_request_name_normalization_in_service_previews(monkeypatch, resident_today, request_type, field):
    service = ToolService(store=None, retriever=None)
    profile = {"resident_type": "owner", "full_name": "ASSIGNED identity"}
    monkeypatch.setattr(service, "get_resident_profile", lambda resident_id: profile)
    result = service.create_management_request(
        "R001", request_type, **{field: " anh   PHẠM VĂN HÙNG "},
        citizen_id="001234567890", visit_date=resident_today.isoformat(), preview_only=True,
    )
    assert result["payload"][field] == "Anh Phạm Văn Hùng"
    current = {"found": True, "request_type": request_type, "status": "submitted", "updated_at": "version", "payload": result["payload"]}
    monkeypatch.setattr(service, "get_request_status", lambda *args: current)
    updated = service.update_management_request("R001", "REQ-TEST", **{field: " NGUYỄN  ANH TUẤN "}, preview_only=True)
    assert updated["changes"][field] == "Nguyễn Anh Tuấn"
    assert profile["full_name"] == "ASSIGNED identity"
