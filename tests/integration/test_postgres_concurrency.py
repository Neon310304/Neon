from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from src.repositories import Store


def _independent_store(app_container) -> Store:
    url = app_container.store.engine.url.render_as_string(hide_password=False)
    return Store(url)


def test_two_connections_create_one_active_request(app_container):
    first_store = _independent_store(app_container)
    second_store = _independent_store(app_container)
    barrier = Barrier(2)
    backend_ids = set()

    def create(store: Store, idempotency_key: str) -> dict:
        with store.transaction():
            with store.session() as session:
                backend_ids.add(session.scalar(text("SELECT pg_backend_pid()")))
            barrier.wait(timeout=5)
            return store.create_request(
                "R001", "vehicle_registration",
                {"vehicle_type": "car", "vehicle_plate": "51a 123.45"}, idempotency_key,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create, first_store, "concurrent-create-1"),
                executor.submit(create, second_store, "concurrent-create-2"),
            ]
            results = [future.result(timeout=10) for future in futures]
    finally:
        first_store.engine.dispose()
        second_store.engine.dispose()

    assert len(backend_ids) == 2
    assert {result["request_id"] for result in results} == {results[0]["request_id"]}
    assert sorted(result["deduplicated"] for result in results) == [False, True]
    duplicate = next(result for result in results if result["deduplicated"])
    assert duplicate["deduplication_reason"] == "matching_open_request"

    with app_container.store.engine.connect() as connection:
        request_count = connection.scalar(
            text(
                "SELECT count(*) FROM management_requests "
                "WHERE resident_id = 'R001' AND request_type = 'vehicle_registration'"
            )
        )
        event_count = connection.scalar(
            text("SELECT count(*) FROM request_events WHERE request_id = :request_id"),
            {"request_id": results[0]["request_id"]},
        )
    assert request_count == 1
    assert event_count == 1


def test_concurrent_idempotency_key_keeps_original_semantics(app_container):
    first_store = _independent_store(app_container)
    second_store = _independent_store(app_container)
    barrier = Barrier(2)
    backend_ids = set()

    def create(store: Store) -> dict:
        with store.transaction():
            with store.session() as session:
                backend_ids.add(session.scalar(text("SELECT pg_backend_pid()")))
            barrier.wait(timeout=5)
            return store.create_request(
                "R001", "visitor_registration",
                {"visitor_name": "Khách Demo", "visit_date": "01/10/2026"}, "same-concurrent-action",
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=10)
                for future in (
                    executor.submit(create, first_store),
                    executor.submit(create, second_store),
                )
            ]
    finally:
        first_store.engine.dispose()
        second_store.engine.dispose()

    assert len(backend_ids) == 2
    assert {result["request_id"] for result in results} == {results[0]["request_id"]}
    assert sorted(result["deduplicated"] for result in results) == [False, True]
    duplicate = next(result for result in results if result["deduplicated"])
    assert duplicate["deduplication_reason"] == "idempotency_key"
    assert len(app_container.store.list_requests("R001")) == 1
    assert len(app_container.store.request_timeline(results[0]["request_id"])) == 1


def test_competing_decisions_are_serialized_by_row_lock(app_container):
    request = app_container.store.create_request("R001", "resident_card", {})
    first_store = _independent_store(app_container)
    second_store = _independent_store(app_container)
    barrier = Barrier(2)

    def decide(store: Store, status: str) -> tuple[str, str]:
        barrier.wait(timeout=5)
        try:
            result = store.decide_request(request["request_id"], status, status)
            return ("success", result["status"])
        except ValueError as exc:
            return ("error", str(exc))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=10)
                for future in (
                    executor.submit(decide, first_store, "approved"),
                    executor.submit(decide, second_store, "rejected"),
                )
            ]
    finally:
        first_store.engine.dispose()
        second_store.engine.dispose()

    assert sum(kind == "success" for kind, _ in results) == 1
    assert sum(value == "invalid_status_transition" for _, value in results) == 1
    timeline = app_container.store.request_timeline(request["request_id"])
    assert len(timeline) == 2
    assert timeline[0]["status"] == "submitted"
    assert timeline[1]["status"] in {"approved", "rejected"}


def test_database_error_rolls_back_request_and_event(app_container):
    store = app_container.store

    def fail_event_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO REQUEST_EVENTS"):
            raise OperationalError(statement, {}, RuntimeError("forced_database_failure"))

    event.listen(store.engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(OperationalError):
            store.create_request(
                "R001",
                "general_support",
                {"details": "must rollback"},
                "forced-database-failure",
            )
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_event_insert)

    with store.engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM management_requests "
                "WHERE idempotency_key = 'forced-database-failure'"
            )
        ) == 0
        assert connection.scalar(text("SELECT count(*) FROM request_events")) == 0
