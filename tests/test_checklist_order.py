import pytest

from src.repositories.store import order_checklist_tasks


@pytest.mark.parametrize("position", [0, 4, 5, 9])
def test_regulations_are_sixth_without_reordering_other_tasks(position):
    tasks = [
        {"code": code, "status": "completed" if index % 2 else "pending", "request_id": f"REQ-{index}"}
        for index, code in enumerate([
            "verify_residency", "temporary_residency", "move_in_schedule", "resident_card",
            "vehicle_registration", "account_registration", "internet_registration",
            "handover_inspection", "safety_briefing",
        ])
    ]
    regulations = {"code": "confirm_regulations", "status": "completed"}
    tasks.insert(position, regulations)
    original = list(tasks)
    ordered = order_checklist_tasks(tasks)
    assert ordered[5] is regulations
    assert [item for item in ordered if item is not regulations] == [item for item in original if item is not regulations]
    assert tasks == original
    assert order_checklist_tasks(ordered) == ordered


@pytest.mark.parametrize("size", [0, 1, 4])
def test_short_checklists_do_not_invent_steps(size):
    tasks = [{"code": f"task-{index}"} for index in range(size)]
    regulations = {"code": "confirm_regulations"}
    assert order_checklist_tasks([regulations, *tasks]) == [*tasks, regulations]
    assert order_checklist_tasks(tasks) == tasks
