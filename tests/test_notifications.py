from datetime import UTC, date, datetime

import pytest

from src.repositories.store import ManagementRequest, NotificationReceipt, RequestEvent, Resident
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login


def add_events(store, count=1, resident_id="R001"):
    request = store.create_request(resident_id, "resident_card", {"name": resident_id})
    with store.session() as session:
        events = [RequestEvent(request_id=request["request_id"], status="in_review", reason=f"Cập nhật {i}") for i in range(count)]
        session.add_all(events)
        session.flush()
        return request["request_id"], [event.id for event in events]


@pytest.mark.asyncio
async def test_notification_authorization_and_receipts_survive_new_login(local_client, local_container):
    store = local_container.store
    _, ids = add_events(store)
    with store.session() as session:
        session.add(Resident(id="R002", full_name="Other resident", apartment="A102", site_id="sunrise-residence", site_name="Sunrise", resident_type="owner"))
    _, other_ids = add_events(store, resident_id="R002")
    path = "/api/v1/residents/R001/notifications"
    headers = await login(local_client, "user")
    manager = await login(local_client, "admin")
    assert (await local_client.get(path + "/feed")).status_code == 401
    assert (await local_client.get(path + "/feed", headers=manager)).status_code == 404
    assert (await local_client.get("/api/v1/residents/R002/notifications/feed", headers=headers)).status_code == 404
    assert (await local_client.post(f"{path}/{other_ids[0]}/read", headers=headers)).status_code == 404
    assert (await local_client.post(path + "/read-all", headers=headers, json={})).status_code == 422
    first = (await local_client.get(path + "/feed", headers=headers)).json()
    assert first["unread_count"] == 1
    assert [item["event_id"] for item in first["items"]] == ids
    assert (await local_client.post(f"{path}/{ids[0]}/read", headers=headers)).status_code == 200
    assert (await local_client.post(f"{path}/{ids[0]}/read", headers=headers)).status_code == 200
    new_headers = await login(local_client, "user")
    reloaded = (await local_client.get(path + "/feed", headers=new_headers)).json()
    assert reloaded["unread_count"] == 0
    assert reloaded["items"][0]["read_at"]
    with store.session() as session:
        assert session.query(NotificationReceipt).count() == 1


def test_feed_pagination_bounded_read_all_account_isolation_and_delete_cascade(app_container):
    store = app_container.store
    request_id, ids = add_events(store, 67)
    feed = store.management_notification_feed("R001", "first")
    observed_latest = feed["latest_event_id"]
    assert feed["unread_count"] == 67
    seen = [item["event_id"] for item in feed["items"]]
    while feed["has_more"]:
        feed = store.management_notification_feed("R001", "first", feed["next_before_id"])
        seen.extend(item["event_id"] for item in feed["items"])
    assert seen == list(reversed(ids))
    _, new_ids = add_events(store)
    store.read_management_notifications("R001", "first", through_event_id=observed_latest)
    store.read_management_notifications("R001", "first", through_event_id=observed_latest)
    current = store.management_notification_feed("R001", "first")
    assert current["unread_count"] == 1
    assert current["items"][0]["event_id"] == new_ids[0]
    assert current["items"][0]["read_at"] is None
    assert store.management_notification_feed("R001", "second")["unread_count"] == 68
    with pytest.raises(ValueError, match="notification_boundary_required"):
        store.read_management_notifications("R001", "first")
    with store.session() as session:
        session.query(ManagementRequest).filter_by(id=request_id).delete()
    with store.session() as session:
        assert session.query(NotificationReceipt).count() == 0


def test_groups_paginate_tickets_without_splitting_or_losing_old_events(app_container):
    store = app_container.store
    first_id, first_ids = add_events(store, 67)
    second = store.create_request("R001", "general_support", {"details": "Thang máy"})
    store.decide_request(second["request_id"], "in_review", "Đã tiếp nhận")
    store.create_request("R001", "general_support", {"details": "Chưa có phản hồi"})
    groups = store.management_notification_groups("R001", "user", limit=1)
    assert groups["total"] == 2
    assert groups["has_more"]
    assert groups["items"][0]["request_id"] == second["request_id"]
    older = store.management_notification_groups("R001", "user", offset=1, limit=1)
    assert older["items"][0]["request_id"] == first_id
    assert [event["event_id"] for event in older["items"][0]["events"]] == list(reversed(first_ids))
    assert older["items"][0]["unread_count"] == 67
    assert not older["has_more"]
    store.decide_request(first_id, "approved", "Cập nhật mới của ticket cũ")
    reordered = store.management_notification_groups("R001", "user", limit=1)
    assert reordered["items"][0]["request_id"] == first_id
    assert len(reordered["items"][0]["events"]) == 68


def test_group_filters_use_current_status_and_vietnam_sent_dates(app_container):
    store = app_container.store
    first_id, _ = add_events(store, 2)
    second = store.create_request("R001", "general_support", {"details": "Phản ánh thang máy"})
    store.decide_request(second["request_id"], "rejected", "Không hợp lệ")
    with store.session() as session:
        session.get(ManagementRequest, first_id).created_at = datetime(2026, 10, 8, 17, tzinfo=UTC)
        session.get(ManagementRequest, second["request_id"]).created_at = datetime(2026, 10, 9, 17, tzinfo=UTC)
    groups = store.management_notification_groups("R001", "user", query="thang máy", request_type="general_support", status="rejected", date_from=date(2026, 10, 10), date_to=date(2026, 10, 10))
    assert groups["total"] == 1
    assert groups["items"][0]["request_id"] == second["request_id"]
    assert groups["items"][0]["status"] == "rejected"
    assert store.management_notification_groups("R001", "user", date_to=date(2026, 10, 9))["total"] == 1
    assert store.management_notification_groups("R001", "user", request_type="resident_card")["total"] == 1
    assert store.management_notification_groups("R001", "user", query=first_id)["total"] == 1
    with pytest.raises(ValueError, match="invalid_date_range"):
        store.management_notification_groups("R001", "user", date_from=date(2026, 10, 10), date_to=date(2026, 10, 9))


@pytest.mark.asyncio
async def test_group_api_read_scope_authorization_and_concurrent_updates(local_client, local_container):
    store = local_container.store
    request_id, ids = add_events(store, 3)
    second = store.create_request("R001", "general_support", {"details": "Sửa điện"})
    store.decide_request(second["request_id"], "in_review", "Chờ kỹ thuật")
    with store.session() as session:
        session.add(Resident(id="R002", full_name="Other", apartment="A102", site_id="sunrise-residence", site_name="Sunrise", resident_type="owner"))
    outsider, _ = add_events(store, resident_id="R002")
    headers = await login(local_client, "user")
    manager = await login(local_client, "admin")
    path = "/api/v1/residents/R001/notifications"
    assert (await local_client.get(path + "/groups")).status_code == 401
    assert (await local_client.get(path + "/groups", headers=manager)).status_code == 404
    assert (await local_client.get("/api/v1/residents/R002/notifications/groups", headers=headers)).status_code == 404
    for query in ("status=bad", "request_type=bad", "date_from=2026-10-10&date_to=2026-10-01", "limit=51", "offset=-1"):
        assert (await local_client.get(path + "/groups?" + query, headers=headers)).status_code == 422
    groups = (await local_client.get(path + "/groups?q=" + request_id, headers=headers)).json()
    assert groups["total"] == 1
    assert len(groups["items"][0]["events"]) == 3
    with store.session() as session:
        concurrent = RequestEvent(request_id=request_id, status="in_review", reason="Thông báo mới sau khi mở")
        session.add(concurrent)
        session.flush()
        concurrent_id = concurrent.id
    body = {"through_event_id": ids[-1]}
    route = f"{path}/tickets/{request_id}/read"
    assert (await local_client.post(route, headers=headers, json=body)).status_code == 200
    assert (await local_client.post(route, headers=headers, json=body)).status_code == 200
    assert (await local_client.post(f"{path}/tickets/{outsider}/read", headers=headers, json={"through_event_id": concurrent_id})).status_code == 404
    assert (await local_client.post(route, headers=headers, json={})).status_code == 422
    refreshed = (await local_client.get(path + "/groups", headers=headers)).json()
    assert refreshed["unread_count"] == 2
    group = next(item for item in refreshed["items"] if item["request_id"] == request_id)
    assert group["unread_count"] == 1
    assert group["events"][0]["event_id"] == concurrent_id
    assert group["events"][0]["read_at"] is None
    assert all(event["read_at"] for event in group["events"][1:])
