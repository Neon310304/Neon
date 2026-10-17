from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from src.config import Settings
from src.main import app
from src.repositories.store import Resident
from src.services import reset_all_demo_data, reset_demo_data, seed_demo_data

DEMO_TASK_CODES = {
    "confirm_regulations",
    "account_registration",
    "handover_inspection",
    "internet_registration",
    "move_in_schedule",
    "resident_card",
    "safety_briefing",
    "temporary_residency",
    "vehicle_registration",
    "verify_residency",
}


def _delete_demo_test_data(app_container) -> None:
    with app_container.store.engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM parking_reminders WHERE resident_id = :resident_id; "
                "DELETE FROM request_events WHERE request_id IN ("
                "SELECT id FROM management_requests WHERE resident_id = :resident_id"
                "); "
                "DELETE FROM management_requests WHERE resident_id = :resident_id; "
                "DELETE FROM checklist_tasks WHERE resident_id = :resident_id; "
                "DELETE FROM residents WHERE id = :resident_id"
            ),
            {"resident_id": "R001"},
        )


def _table_count(app_container, table: str, resident_id: str) -> int:
    allowed = {
        "residents": "id",
        "checklist_tasks": "resident_id",
        "management_requests": "resident_id",
        "parking_reminders": "resident_id",
    }
    column = allowed[table]
    with app_container.store.engine.connect() as connection:
        return int(
            connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :resident_id"),
                {"resident_id": resident_id},
            )
        )


def test_seed_creates_stable_demo_data_and_is_idempotent(app_container):
    assert "test" in (app_container.store.engine.url.database or "").casefold()
    _delete_demo_test_data(app_container)

    first = seed_demo_data(app_container)
    profile = app_container.store.get_resident("R001")
    tasks = app_container.store.list_tasks("R001")

    assert first == {"resident_id": "R001", "checklist_count": 10}
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
    assert {task["code"] for task in tasks} == DEMO_TASK_CODES

    second = seed_demo_data(app_container)
    assert second == first
    assert _table_count(app_container, "residents", "R001") == 1
    assert _table_count(app_container, "checklist_tasks", "R001") == 10


def test_seed_does_not_overwrite_an_edited_demo_profile(app_container):
    app_container.store.update_resident(
        "R001",
        {
            "full_name": "Cư dân demo đã chỉnh sửa",
            "apartment": "B0901",
            "vehicle": "none",
            "needs_internet": False,
        },
    )

    seed_demo_data(app_container)

    profile = app_container.store.get_resident("R001")
    assert profile is not None
    assert profile["full_name"] == "Cư dân demo đã chỉnh sửa"
    assert profile["apartment"] == "B0901"
    assert profile["profile_version"] == 2


def test_reset_only_replaces_demo_graph_and_demo_flow_still_works(app_container):
    with app_container.store.session() as session:
        session.add(
            Resident(
                id="R999",
                full_name="Non Demo Resident",
                apartment="Z999",
                site_id="other-site",
                site_name="Other Site",
                resident_type="tenant",
                family_members=2,
                vehicle="none",
                needs_internet=False,
            )
        )
    app_container.store.replace_active_checklist(
        "R999",
        [
            {
                "code": "confirm_regulations",
                "title": "Non-demo checklist",
                "required": True,
                "reason": "Must survive demo reset",
            }
        ],
    )
    other_request = app_container.store.create_request(
        "R999",
        "general_support",
        {"details": "must survive"},
        "non-demo-request",
    )
    demo_request = app_container.store.create_request(
        "R001",
        "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "DEMO-RESET"},
        "demo-reset-request",
    )
    app_container.store.decide_request(demo_request["request_id"], "approved", "demo")
    app_container.store.set_parking_due_date(
        "R001", demo_request["request_id"], date(2026, 10, 15)
    )

    result = reset_demo_data(app_container)

    assert result == {"resident_id": "R001", "checklist_count": 10}
    assert app_container.store.get_resident("R999") is not None
    assert app_container.store.get_request(other_request["request_id"]) is not None
    assert _table_count(app_container, "checklist_tasks", "R999") == 1
    assert app_container.store.list_requests("R001") == []
    assert app_container.store.parking_reminders("R001") == {}
    assert {task["code"] for task in app_container.store.list_tasks("R001")} == DEMO_TASK_CODES

    recreated = app_container.store.create_request(
        "R001",
        "resident_card",
        {},
        "after-safe-reset",
    )
    assert recreated["deduplicated"] is False
    assert len(app_container.store.request_timeline(recreated["request_id"])) == 1

    with app_container.store.engine.connect() as connection:
        orphan_events = connection.scalar(
            text(
                "SELECT count(*) FROM request_events e LEFT JOIN management_requests r "
                "ON r.id = e.request_id WHERE r.id IS NULL"
            )
        )
        duplicate_events = connection.scalar(
            text(
                "SELECT count(*) FROM (SELECT request_id, status, count(*) AS copies "
                "FROM request_events GROUP BY request_id, status HAVING count(*) > 1) duplicates"
            )
        )
    assert orphan_events == 0
    assert duplicate_events == 0


def test_seed_failure_rolls_back_resident_and_checklist(app_container):
    _delete_demo_test_data(app_container)

    def fail_checklist_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO CHECKLIST_TASKS"):
            raise OperationalError(statement, {}, RuntimeError("forced_seed_failure"))

    event.listen(app_container.store.engine, "before_cursor_execute", fail_checklist_insert)
    try:
        with pytest.raises(OperationalError):
            seed_demo_data(app_container)
    finally:
        event.remove(app_container.store.engine, "before_cursor_execute", fail_checklist_insert)

    assert _table_count(app_container, "residents", "R001") == 0
    assert _table_count(app_container, "checklist_tasks", "R001") == 0


def test_reset_failure_rolls_back_existing_demo_graph(app_container):
    app_container.store.update_resident(
        "R001",
        {"full_name": "Edited Before Reset", "apartment": "C0707"},
    )
    request = app_container.store.create_request(
        "R001",
        "general_support",
        {"details": "must survive failed reset"},
        "failed-reset-request",
    )

    def fail_resident_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO RESIDENTS"):
            raise OperationalError(statement, {}, RuntimeError("forced_reset_failure"))

    event.listen(app_container.store.engine, "before_cursor_execute", fail_resident_insert)
    try:
        with pytest.raises(OperationalError):
            reset_demo_data(app_container)
    finally:
        event.remove(app_container.store.engine, "before_cursor_execute", fail_resident_insert)

    profile = app_container.store.get_resident("R001")
    assert profile is not None
    assert profile["full_name"] == "Edited Before Reset"
    assert profile["apartment"] == "C0707"
    assert app_container.store.get_request(request["request_id"]) is not None
    assert len(app_container.store.request_timeline(request["request_id"])) == 1


def test_full_reset_failure_rolls_back_all_residents_and_requests(app_container):
    with app_container.store.session() as session:
        session.add(Resident(id="R999", full_name="Other Demo", apartment="A1808",
                             site_id="sunrise-residence", site_name="Demo", resident_type="owner"))
    request = app_container.store.create_request("R999", "general_support", {"details": "keep on failure"}, "full-reset-rollback")

    def fail_checklist(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO CHECKLIST_TASKS"):
            raise OperationalError(statement, {}, RuntimeError("forced_full_reset_failure"))

    event.listen(app_container.store.engine, "before_cursor_execute", fail_checklist)
    try:
        with pytest.raises(OperationalError):
            reset_all_demo_data(app_container)
    finally:
        event.remove(app_container.store.engine, "before_cursor_execute", fail_checklist)
    assert app_container.store.get_resident("R999") is not None
    assert app_container.store.get_request(request["request_id"]) is not None
    assert len(app_container.store.request_timeline(request["request_id"])) == 1


@pytest.mark.asyncio
async def test_reset_api_requires_flag_and_safe_environment(app_container):
    app.state.container = app_container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        app_container.settings.allow_demo_reset = False
        disabled = await client.post("/api/v1/demo/reset")
        assert disabled.status_code == 410
        assert disabled.json()["detail"] == "reset_endpoint_retired"

        app_container.settings.allow_demo_reset = True
        app_container.settings.app_env = "production"
        production = await client.post("/api/v1/demo/reset")
        assert production.status_code == 410
        assert production.json()["detail"] == "reset_endpoint_retired"
        assert (await client.post("/api/v1/auth/reset", json={"code": "0000", "confirm": True})).status_code == 410


def test_demo_target_validation_rejects_mismatched_database_label():
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_demo_reset=True,
        database_url=(
            "postgresql+psycopg2://moveinmate:example-only@localhost:5432/moveinmate_dev"
        ),
    )

    with pytest.raises(ValueError, match="^demo_database_target_not_allowed$"):
        settings.validate_demo_reset()


@pytest.mark.parametrize(
    "database", ["moveinmate_test", "moveinmate_dev_test", "production_dev", "dev", "MOVEINMATE_DEV"]
)
def test_development_reset_rejects_test_database(database):
    settings = Settings(
        _env_file=None,
        app_env="development",
        allow_demo_reset=True,
        database_url=f"postgresql+psycopg2://localhost/{database}",
    )
    with pytest.raises(ValueError, match="^demo_database_target_not_allowed$"):
        settings.validate_demo_reset()


def test_development_reset_rejects_test_target_with_different_credentials():
    settings = Settings(
        _env_file=None,
        app_env="development",
        allow_demo_reset=True,
        database_url="postgresql+psycopg2://dev_user@localhost/moveinmate_dev",
        test_database_url="postgresql://test_user@postgres:5432/moveinmate_dev",
    )
    with pytest.raises(ValueError, match="^demo_database_target_not_allowed$"):
        settings.validate_demo_reset()


@pytest.mark.parametrize(
    ("environment", "database"), [("development", "moveinmate_dev"), ("test", "moveinmate_test")]
)
def test_demo_reset_accepts_only_dedicated_targets(environment, database):
    Settings(
        _env_file=None,
        app_env=environment,
        allow_demo_reset=True,
        database_url=f"postgresql+psycopg2://localhost/{database}",
    ).validate_demo_reset()


def test_command_scenario_is_repeatable_and_preserves_decisions(app_container):
    _delete_demo_test_data(app_container)
    seed_demo_data(app_container, include_requests=True)
    request = app_container.store.list_requests("R001")[0]
    request_id = request["request_id"]
    assert request["request_type"] == "resident_card"
    assert request["status"] == "submitted"
    assert len(app_container.store.request_timeline(request_id)) == 1
    tasks = {task["code"]: task for task in app_container.store.list_tasks("R001")}
    assert tasks["resident_card"]["status"] == "submitted"

    app_container.store.decide_request(request_id, "approved", "Demo manager decision")
    before = app_container.store.get_request(request_id)
    timeline = app_container.store.request_timeline(request_id)
    seed_demo_data(app_container, include_requests=True)
    assert app_container.store.list_requests("R001") == [before]
    assert app_container.store.request_timeline(request_id) == timeline

    reset_demo_data(app_container)
    seed_demo_data(app_container, include_requests=True)
    requests = app_container.store.list_requests("R001")
    assert len(requests) == 1
    assert requests[0]["status"] == "submitted"
    assert len(app_container.store.request_timeline(requests[0]["request_id"])) == 1
    assert _table_count(app_container, "checklist_tasks", "R001") == 10


def test_scenario_event_failure_rolls_back_entire_seed(app_container):
    _delete_demo_test_data(app_container)

    def fail_event(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO REQUEST_EVENTS"):
            raise OperationalError(statement, {}, RuntimeError("forced_event_failure"))

    event.listen(app_container.store.engine, "before_cursor_execute", fail_event)
    try:
        with pytest.raises(OperationalError):
            seed_demo_data(app_container, include_requests=True)
    finally:
        event.remove(app_container.store.engine, "before_cursor_execute", fail_event)
    for table in ("residents", "checklist_tasks", "management_requests"):
        assert _table_count(app_container, table, "R001") == 0


def test_seed_does_not_reopen_existing_terminal_request_with_other_key(app_container):
    request = app_container.store.create_request("R001", "resident_card", {}, "existing-card")
    request_id = request["request_id"]
    app_container.store.decide_request(request_id, "approved", "Existing decision")
    app_container.store.decide_request(request_id, "completed", "Existing completion")
    before = app_container.store.get_request(request_id)
    timeline = app_container.store.request_timeline(request_id)
    seed_demo_data(app_container, include_requests=True)
    seed_demo_data(app_container, include_requests=True)
    assert app_container.store.list_requests("R001") == [before]
    assert app_container.store.request_timeline(request_id) == timeline
