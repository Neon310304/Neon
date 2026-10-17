from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from langsmith import utils as langsmith_utils
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.config import Settings
from src.container import AppContainer
from src.repositories import Store
from src.repositories.store import ChecklistTask, ParkingReminder, RequestEvent, Resident


def test_store_rejects_sqlite_without_fallback():
    with pytest.raises(ValueError, match="^database_must_be_postgresql$"):
        Store("sqlite:///:memory:")


def test_store_persists_after_session_and_engine_reopen(app_container):
    store = app_container.store
    updated = store.update_resident(
        "R001",
        {
            "resident_type": "tenant",
            "family_members": 3,
            "vehicle": "motorbike",
            "needs_internet": False,
        },
    )
    assert updated["profile_version"] == 2
    database_url = store.engine.url.render_as_string(hide_password=False)
    store.engine.dispose()

    reopened = Store(database_url)
    try:
        reopened.initialize()
        persisted = reopened.get_resident("R001")
        assert persisted is not None
        assert persisted["resident_type"] == "tenant"
        assert persisted["family_members"] == 3
        assert persisted["profile_version"] == 2
    finally:
        reopened.engine.dispose()


def test_unit_of_work_commits_request_and_event_together(app_container):
    store = app_container.store
    with store.transaction():
        created = store.create_request(
            "R001",
            "general_support",
            {"details": "Kiểm tra transaction PostgreSQL"},
            "postgres-commit-contract",
        )

    with store.engine.connect() as connection:
        request_count = connection.scalar(
            text("SELECT count(*) FROM management_requests WHERE id = :request_id"),
            {"request_id": created["request_id"]},
        )
        event_count = connection.scalar(
            text("SELECT count(*) FROM request_events WHERE request_id = :request_id"),
            {"request_id": created["request_id"]},
        )
    assert request_count == 1
    assert event_count == 1


def test_unit_of_work_rolls_back_every_store_call(app_container):
    store = app_container.store
    with pytest.raises(RuntimeError, match="forced_mid_transaction_failure"):
        with store.transaction():
            store.update_resident(
                "R001",
                {
                    "resident_type": "tenant",
                    "family_members": 4,
                    "vehicle": "none",
                    "needs_internet": False,
                },
            )
            store.create_request(
                "R001",
                "general_support",
                {"details": "Bản ghi phải rollback"},
                "postgres-rollback-contract",
            )
            raise RuntimeError("forced_mid_transaction_failure")

    profile = store.get_resident("R001")
    assert profile is not None
    assert profile["resident_type"] == "owner"
    assert profile["family_members"] == 1
    assert store.list_requests("R001") == []


def test_entire_graph_persists_in_a_new_app_container(app_container):
    store = app_container.store
    store.update_resident("R001", {"full_name": "PostgreSQL Restart Demo"})
    store.update_task("R001", "confirm_regulations", "completed")
    request = store.create_request(
        "R001", "vehicle_registration",
        {"vehicle_type": "car", "vehicle_plate": "DB07-RESTART"}, "db07-restart",
    )
    request_id = request["request_id"]
    store.decide_request(request_id, "approved", "Synthetic approval")
    store.set_parking_due_date("R001", request_id, date(2026, 10, 20))
    before = {
        "profile": store.get_resident("R001"),
        "request": store.get_request(request_id),
        "events": store.request_timeline(request_id),
        "reminders": store.parking_reminders("R001"),
    }
    tasks = {row["code"]: row["status"] for row in store.list_tasks("R001")}
    store.engine.dispose()
    reopened = AppContainer(app_container.settings)
    try:
        reopened.initialize()
        assert reopened.store.get_resident("R001") == before["profile"]
        assert reopened.store.get_request(request_id) == before["request"]
        assert reopened.store.request_timeline(request_id) == before["events"]
        assert reopened.store.parking_reminders("R001") == before["reminders"]
        assert {row["code"]: row["status"] for row in reopened.store.list_tasks("R001")} == tasks
    finally:
        reopened.close()


def test_jsonb_and_timestamptz_round_trip(app_container):
    payload = {"details": "Tiếng Việt", "nested": {"items": [1, True, None, "Đủ"]}}
    store = app_container.store
    created = store.create_request("R001", "general_support", payload, "db07-jsonb")
    with store.engine.begin() as connection:
        connection.execute(text("SET LOCAL TIME ZONE 'Asia/Bangkok'"))
        row = connection.execute(text(
            "SELECT r.payload_json, jsonb_typeof(r.payload_json), "
            "r.created_at, r.updated_at, e.created_at "
            "FROM management_requests r JOIN request_events e ON e.request_id = r.id "
            "WHERE r.id = :id"
        ), {"id": created["request_id"]}).one()
    assert row[0] == payload
    assert row[1] == "object"
    for timestamp in row[2:]:
        assert timestamp.utcoffset() == timedelta(hours=7)
    public_created = datetime.fromisoformat(created["created_at"])
    assert public_created.utcoffset() == timedelta(0)
    assert row[2].astimezone(UTC) == public_created
    assert store.get_request(created["request_id"])["payload"] == payload


@pytest.mark.parametrize("model", [ChecklistTask, RequestEvent])
def test_postgresql_rejects_orphan_children(app_container, model):
    child = (
        ChecklistTask(resident_id="MISSING", code="orphan", title="Orphan", reason="Test")
        if model is ChecklistTask
        else RequestEvent(request_id="REQ-MISSING", status="submitted")
    )
    with pytest.raises(IntegrityError) as failure:
        with app_container.store.session() as session:
            session.add(child)
    assert failure.value.orig.pgcode == "23503"


def test_postgresql_rejects_reminder_for_another_resident(app_container):
    store = app_container.store
    with store.session() as session:
        session.add(Resident(
            id="R002", full_name="Other synthetic resident", apartment="B0002",
            site_id="sunrise-residence", site_name="Khu Mặt Trời", resident_type="owner",
        ))
    request = store.create_request("R001", "vehicle_registration", {"vehicle_plate": "DB07-FK"})
    with pytest.raises(IntegrityError) as failure:
        with store.session() as session:
            session.add(ParkingReminder(
                request_id=request["request_id"], resident_id="R002", due_date=date(2026, 10, 20),
            ))
    assert failure.value.orig.diag.constraint_name == "fk_parking_reminders_request_resident_management_requests"
    assert store.parking_reminders("R002") == {}


def test_resident_delete_is_restricted_and_request_children_cascade(app_container):
    store = app_container.store
    request = store.create_request("R001", "vehicle_registration", {"vehicle_plate": "DB07-CASCADE"})
    request_id = request["request_id"]
    store.decide_request(request_id, "approved", "Synthetic approval")
    store.set_parking_due_date("R001", request_id, date(2026, 10, 20))
    tasks = store.list_tasks("R001")
    with pytest.raises(IntegrityError) as failure:
        with store.engine.begin() as connection:
            connection.execute(text("DELETE FROM residents WHERE id = 'R001'"))
    assert failure.value.orig.pgcode == "23503"
    assert store.list_tasks("R001") == tasks
    with store.engine.begin() as connection:
        connection.execute(text("DELETE FROM management_requests WHERE id = :id"), {"id": request_id})
    assert store.request_timeline(request_id) == []
    assert store.parking_reminders("R001") == {}
    assert store.get_resident("R001") is not None


@pytest.mark.parametrize("test_url", [
    "postgresql://other_user@localhost:5432/moveinmate_test",
    "postgresql+psycopg2://other_user@postgres/moveinmate_test",
])
def test_fixture_rejects_same_database_before_migration_or_reset(validate_postgres_test_target, test_url):
    settings = Settings(
        _env_file=None, db_host_override="",
        database_url="postgresql+psycopg2://runtime_user@localhost/moveinmate_test",
        test_database_url=test_url,
    )
    with pytest.raises(ValueError, match="^test_database_must_differ_from_development$"):
        validate_postgres_test_target(settings)


def test_langsmith_is_disabled_in_tests_and_worker_threads():
    assert langsmith_utils.tracing_is_enabled() is False
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(langsmith_utils.tracing_is_enabled).result(timeout=5) is False
