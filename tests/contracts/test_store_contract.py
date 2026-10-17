from datetime import date, datetime

import pytest

from src.repositories import Store


def _checklist_items() -> list[dict]:
    return [
        {
            "code": "confirm_regulations",
            "title": "Xác nhận nội quy demo",
            "required": True,
            "reason": "Áp dụng cho mọi hồ sơ demo.",
        },
        {
            "code": "vehicle_registration",
            "title": "Đăng ký phương tiện demo",
            "required": True,
            "reason": "Hồ sơ demo có ô tô.",
        },
    ]


def _assert_iso_timestamp(value: str) -> None:
    assert datetime.fromisoformat(value)


def test_profile_and_checklist_crud_ordering_contract(store_contract: Store):
    profile = store_contract.get_resident("R001")
    assert profile == {
        "resident_id": "R001",
        "full_name": "Nguyen Minh An",
        "apartment": "A1205",
        "building": "A",
        "site_id": "sunrise-residence",
        "site_name": "Khu Mặt Trời",
        "resident_type": "owner",
        "family_members": 1,
        "declared_family_members": 1,
        "vehicle": "car",
        "needs_internet": True,
        "profile_version": 1,
        "ownership_confirmed_at": None,
    }

    updated = store_contract.update_resident(
        "R001",
        {"resident_type": "tenant", "family_members": 2, "vehicle": "motorbike", "needs_internet": False},
    )
    assert updated["profile_version"] == 2
    assert updated["resident_type"] == "tenant"
    assert updated["family_members"] == 2

    initial = store_contract.replace_active_checklist("R001", _checklist_items())
    assert [item["code"] for item in initial] == ["vehicle_registration", "confirm_regulations"]
    assert all(item["status"] == "pending" for item in initial)
    assert all(datetime.fromisoformat(item["updated_at"]) for item in initial)

    store_contract.replace_active_checklist("R001", [_checklist_items()[1]])
    reactivated = store_contract.replace_active_checklist("R001", list(reversed(_checklist_items())))
    assert [item["code"] for item in reactivated] == ["vehicle_registration", "confirm_regulations"]

    completed = store_contract.update_task("R001", "confirm_regulations", "completed")
    assert completed["status"] == "completed"
    assert next(item for item in store_contract.list_tasks("R001") if item["code"] == "confirm_regulations")["status"] == "completed"


def test_request_deduplication_and_idempotency_contract(store_contract: Store):
    store_contract.replace_active_checklist("R001", _checklist_items())
    first = store_contract.create_request(
        "R001",
        "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "51A-123.45"},
        "contract-vehicle-1",
    )
    replay = store_contract.create_request(
        "R001",
        "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "51A-123.45"},
        "contract-vehicle-1",
    )
    matching_open = store_contract.create_request(
        "R001",
        "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "51a 12345"},
    )

    assert first["deduplicated"] is False
    assert replay["request_id"] == first["request_id"]
    assert replay["deduplication_reason"] == "idempotency_key"
    assert matching_open["request_id"] == first["request_id"]
    assert matching_open["deduplication_reason"] == "matching_open_request"
    assert len(store_contract.list_requests("R001")) == 1
    assert next(item for item in store_contract.list_tasks("R001") if item["code"] == "vehicle_registration")["status"] == "submitted"

    with pytest.raises(ValueError, match="^idempotency_key_conflict$"):
        store_contract.create_request("R001", "resident_card", {}, "contract-vehicle-1")


def test_request_state_timeline_and_notification_order_contract(store_contract: Store):
    request = store_contract.create_request("R001", "resident_card", {}, "contract-card-1")
    request_id = request["request_id"]

    for status, reason in (
        ("in_review", "Đã tiếp nhận hồ sơ demo"),
        ("needs_info", "Cần bổ sung dữ liệu demo"),
        ("approved", "Đã duyệt hồ sơ demo"),
        ("completed", "Đã hoàn tất hồ sơ demo"),
    ):
        if status == "approved":
            store_contract.update_request("R001", request_id, {"details": "Đã bổ sung dữ liệu theo yêu cầu"}, True)
        result = store_contract.decide_request(request_id, status, reason)
        assert result["status"] == status

    timeline = store_contract.request_timeline(request_id)
    assert [event["status"] for event in timeline] == [
        "submitted",
        "in_review",
        "needs_info",
        "submitted",
        "approved",
        "completed",
    ]
    assert all(datetime.fromisoformat(event["created_at"]).tzinfo is not None for event in timeline)

    notifications = store_contract.list_management_notifications("R001")
    assert [event["status"] for event in notifications] == [
        "in_review",
        "needs_info",
        "approved",
        "completed",
    ]
    assert [event["event_id"] for event in notifications] == sorted(event["event_id"] for event in notifications)
    assert store_contract.latest_management_event_id("R001") == notifications[-1]["event_id"]

    with pytest.raises(ValueError, match="^invalid_status_transition$"):
        store_contract.decide_request(request_id, "approved", "Không được mở lại")


def test_approved_is_not_completed_and_finished_request_can_be_recreated(store_contract: Store):
    store_contract.replace_active_checklist(
        "R001",
        [
            {
                "code": "resident_card",
                "title": "Thẻ cư dân demo",
                "required": True,
                "reason": "Hồ sơ demo cần thẻ cư dân.",
            }
        ],
    )
    first = store_contract.create_request("R001", "resident_card", {})
    store_contract.decide_request(first["request_id"], "approved", "Đã duyệt")
    assert store_contract.list_tasks("R001")[0]["status"] == "approved"

    store_contract.decide_request(first["request_id"], "completed", "Đã hoàn tất")
    second = store_contract.create_request("R001", "resident_card", {})
    assert second["request_id"] != first["request_id"]
    assert second["deduplicated"] is False
    assert [item["request_id"] for item in store_contract.list_requests("R001")] == [
        second["request_id"],
        first["request_id"],
    ]


def test_request_update_cancel_and_event_history_contract(store_contract: Store):
    request = store_contract.create_request(
        "R001",
        "temporary_residency",
        {"person_name": "Người ở thử nghiệm", "stay_start": "2026-10-01"},
    )
    request_id = request["request_id"]
    store_contract.decide_request(request_id, "needs_info", "Cần dữ liệu bổ sung")
    updated = store_contract.update_request(
        "R001",
        request_id,
        {"details": "Dữ liệu mô phỏng đã bổ sung"},
        resubmit=True,
    )
    assert updated["status"] == "submitted"
    assert updated["payload"]["details"] == "Dữ liệu mô phỏng đã bổ sung"

    cancelled = store_contract.cancel_request("R001", request_id)
    assert cancelled["status"] == "cancelled_by_resident"
    assert [event["status"] for event in store_contract.request_timeline(request_id)] == [
        "submitted",
        "needs_info",
        "submitted",
        "cancelled_by_resident",
    ]


def test_parking_reminder_and_household_count_contract(store_contract: Store):
    vehicle = store_contract.create_request(
        "R001",
        "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "DEMO-001"},
    )
    with pytest.raises(ValueError, match="^vehicle_not_approved$"):
        store_contract.set_parking_due_date("R001", vehicle["request_id"], date(2026, 10, 15))

    store_contract.decide_request(vehicle["request_id"], "approved", "Phương tiện demo hợp lệ")
    reminder = store_contract.set_parking_due_date("R001", vehicle["request_id"], date(2026, 10, 15))
    assert reminder["due_date"] == "2026-10-15"
    _assert_iso_timestamp(reminder["updated_at"])
    assert store_contract.parking_reminders("R001")[vehicle["request_id"]]["due_date"] == "2026-10-15"

    stay = store_contract.create_request(
        "R001",
        "temporary_residency",
        {"person_name": "Thành viên mô phỏng", "stay_start": "2026-10-01"},
    )
    store_contract.decide_request(stay["request_id"], "approved", "Hồ sơ demo hợp lệ")
    store_contract.decide_request(stay["request_id"], "completed", "Đã hoàn tất demo")
    assert store_contract.get_resident("R001")["family_members"] == 2
