import io
import unicodedata
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pypdf import PdfWriter

from src.services.attachments import AttachmentError, PreparedAttachment, build_human_content, prepare_attachment
from src.services.form_extraction import extract_form_fields, validate_fields
from src.services.usage_limits import PrivateResultCache, reserve_usage
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login
from tests.test_resident_portal import account_payload


class ExtractionModel:
    def __init__(self, values, form_type="temporary_residency"):
        self.values = values
        self.form_type = form_type
        self.messages = []
        self.calls = 0

    def bind_tools(self, *args, **kwargs):
        return self

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.messages = messages
        self.calls += 1
        return AIMessage(content="", tool_calls=[{"name": "AttachmentExtraction", "args": {
            "form_type": self.form_type, "values": self.values, "unreadable_fields": ["citizen_id", "role"],
        }, "id": "extraction-test"}])


def test_extraction_validation_drops_unreadable_unrelated_and_unsupported_fields(resident_today):
    visit_date = resident_today.isoformat()
    evidence = f"Nguyễn Thu Hà, CCCD 012345678901, ngày {visit_date}"
    result = validate_fields("temporary_residency", {
        "person_name": "Nguyễn Thu Hà", "citizen_id": "012345678901", "visit_date": visit_date,
        "role": "manager", "apartment": "A999", "visitor_name": "Other", "full_name": "Fabricated",
    }, evidence, False)
    assert result == {"person_name": "Nguyễn Thu Hà", "citizen_id": "012345678901", "visit_date": visit_date}
    assert validate_fields("temporary_residency", {"person_name": "Fabricated", "citizen_id": "123", "move_date": "not a date"}, evidence, False) == {}
    assert validate_fields("vehicle_registration", {"vehicle_plate": "invalid", "vehicle_type": "admin"}, "", True) == {}
    assert validate_fields("profile", {"resident_type": "tenant", "needs_internet": "true"}, "CCCD: 012345678901", False) == {}
    assert validate_fields("profile", {"family_members": 2, "needs_internet": False}, "Số người: 2. Không cần internet.", False) == {"family_members": "2", "needs_internet": "false"}


def test_card_pickup_extraction_keeps_only_evidenced_valid_appointment_fields(resident_today):
    values = {"pickup_date": resident_today.isoformat(), "pickup_time": "09:07", "apartment": "A999"}
    assert validate_fields("resident_card", values, f"Nhận thẻ ngày {resident_today.isoformat()} lúc 09:07", False) == {"pickup_date": resident_today.isoformat(), "pickup_time": "09:07"}
    assert validate_fields("resident_card", values, "Chưa chọn ngày giờ nhận thẻ", False) == {}
    assert validate_fields("resident_card", {"pickup_date": "2000-01-01", "pickup_time": "24:00"}, "", True) == {}


def test_residency_draft_retains_only_a_supported_explicit_household_role():
    assert validate_fields("temporary_residency", {"resident_type": "tenant"}, "Người thuê", False) == {"resident_type": "tenant"}
    assert validate_fields("temporary_residency", {"resident_type": "owner"}, "Chủ sở hữu", False) == {}


@pytest.mark.parametrize("evidence", ["CCCD 012345678901X", "CCCD: '012345678901X'", "CCCD 0123456789012"])
def test_extracted_id_must_match_the_entire_supplied_identifier(evidence):
    assert validate_fields("temporary_residency", {"citizen_id": "012345678901"}, evidence, False) == {}


@pytest.mark.asyncio
async def test_attachment_intent_can_switch_away_from_an_active_internet_draft(local_client, local_container, monkeypatch):
    model = ExtractionModel({"vehicle_plate": "29E2-986.07", "vehicle_type": "motorbike"}, "vehicle_registration")
    monkeypatch.setattr(local_container, "_model", lambda: model)
    headers = await login(local_client, "user")
    response = await local_client.post("/api/v1/agent/form-from-files", headers=headers,
        data={"message": "đăng kí cho tôi xe này", "active_form_type": "internet_registration"},
        files={"files": ("vehicle.txt", "Xe máy, biển số 29E2-986.07".encode(), "text/plain")})
    assert response.status_code == 200
    assert response.json()["form_draft"]["form_type"] == "vehicle_registration"
    assert response.json()["form_draft"]["values"] == {"vehicle_plate": "29E2-986.07", "vehicle_type": "motorbike"}
    assert "Requested form: vehicle_registration" in model.messages[0].content
    assert "Active draft context: internet_registration" in model.messages[0].content
    assert local_container.store.list_requests("R001") == []


@pytest.mark.asyncio
async def test_attachment_with_multiple_procedures_asks_before_extraction(local_client, local_container, monkeypatch):
    monkeypatch.setattr(local_container, "_model", lambda: pytest.fail("Ask which procedure first without invoking OCR"))
    response = await local_client.post("/api/v1/agent/form-from-files", headers=await login(local_client, "user"),
        data={"message": "Đăng ký thẻ cư dân và lịch chuyển đồ giúp tôi"},
        files={"files": ("note.txt", b"fixture", "text/plain")})
    assert response.status_code == 200
    assert "chọn một thủ tục" in response.json()["answer"]
    assert response.json()["form_draft"]["form_type"] == ""


@pytest.mark.parametrize("field, form_type", [("person_name", "temporary_residency"), ("visitor_name", "visitor_registration"), ("full_name", "profile")])
def test_name_normalization_remains_grounded(field, form_type):
    evidence = unicodedata.normalize("NFD", "đăng kí tạm trú cho anh phạm   văn hùng")
    assert validate_fields(form_type, {field: "anh phạm văn hùng"}, evidence, False) == {field: "Phạm Văn Hùng"}
    assert validate_fields(form_type, {field: "anh Nguyễn Văn Hùng"}, evidence, False) == {}
    assert validate_fields(form_type, {field: "Phạm Văn Hùng"}, "pham van hung", False) == {}
    assert validate_fields(form_type, {field: "Hùng"}, "HùngAnh", False) == {}
    assert validate_fields(form_type, {field: "nguyễn anh tuấn"}, "NGUYỄN ANH TUẤN", False) == {field: "Nguyễn Anh Tuấn"}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "đăng kí cho tôi xe này"])
async def test_vehicle_document_inference_is_read_only_and_allowlisted(message):
    model = ExtractionModel({"vehicle_plate": "30k-123.45", "vehicle_type": "car", "full_name": "Other Owner", "person_name": "Other Owner", "citizen_id": "001234567890"}, "vehicle_registration")
    attachment = PreparedAttachment("vehicle.png", "image", "image/png", 1, data_url="data:image/png;base64,AA==")
    result = await extract_form_fields(SimpleNamespace(_model=lambda: model), [attachment], message)
    assert result["form_draft"]["form_type"] == "vehicle_registration"
    assert result["form_draft"]["values"] == {"vehicle_plate": "30K-123.45", "vehicle_type": "car"}
    assert result["requires_confirmation"] is True
    instructions = model.messages[0].content
    assert "VEHICLE REGISTRATION document sent alone" in instructions
    assert "generic identity card" in instructions
    assert "never authorizes submission or profile changes" in instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type, expected", [("temporary_residency", "Đăng ký tạm trú"), ("", "Chưa xác định rõ thủ tục")])
async def test_empty_extraction_explains_detected_form_and_unreadable_fields(form_type, expected):
    model = ExtractionModel({}, form_type)
    result = await extract_form_fields(SimpleNamespace(_model=lambda: model), [], "")
    assert expected in result["answer"]
    assert result["form_draft"]["values"] == {}
    assert result["requires_confirmation"] is True
    if form_type:
        assert "số CCCD" in result["answer"]
        assert result["form_draft"]["unreadable_fields"] == ["citizen_id"]
    else:
        assert result["form_draft"]["unreadable_fields"] == []


@pytest.mark.asyncio
async def test_extraction_only_returns_read_only_draft_without_business_tools():
    model = ExtractionModel({"person_name": "Nguyễn Thu Hà", "citizen_id": "999999999999", "role": "manager"})
    app = SimpleNamespace(_model=lambda: model)
    result = await extract_form_fields(app, [prepare_attachment("note.txt", "Họ tên: Nguyễn Thu Hà".encode())], "Điền đăng ký tạm trú", "temporary_residency")
    assert result["form_draft"]["values"] == {"person_name": "Nguyễn Thu Hà"}
    assert result["form_draft"]["unreadable_fields"] == ["citizen_id"]
    assert result["requires_confirmation"] is True
    assert "untrusted" in model.messages[0].content
    assert "Never submit" in model.messages[0].content


def test_scanned_pdf_pages_are_images_and_long_scans_are_bounded():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(output)
    attachment = prepare_attachment("scan.pdf", output.getvalue())
    assert len(attachment.image_urls) == 1
    assert attachment.image_urls[0].startswith("data:image/jpeg;base64,")
    assert any(item["type"] == "image_url" for item in build_human_content("Đọc tạm trú", [attachment]))
    output = io.BytesIO()
    for _ in range(6):
        writer.add_blank_page(width=600, height=800)
    writer.write(output)
    with pytest.raises(AttachmentError, match="tối đa 6 trang"):
        prepare_attachment("long-scan.pdf", output.getvalue())


@pytest.mark.asyncio
async def test_form_api_caches_per_session_and_never_writes_resident_or_ticket(local_client, local_container, monkeypatch):
    model = ExtractionModel({"person_name": "Nguyễn Thu Hà"})
    monkeypatch.setattr(local_container, "_model", lambda: model)
    headers = await login(local_client, "user")
    profile = local_container.store.get_resident("R001")
    tickets = local_container.store.list_requests("R001")
    for _ in range(2):
        response = await local_client.post("/api/v1/agent/form-from-files", headers=headers, data={"form_type": "temporary_residency"}, files={"files": ("note.txt", "Họ tên: Nguyễn Thu Hà".encode(), "text/plain")})
        assert response.status_code == 200
        assert response.json()["form_draft"]["values"] == {"person_name": "Nguyễn Thu Hà"}
    assert model.calls == 1
    assert local_container.store.get_resident("R001") == profile
    assert local_container.store.list_requests("R001") == tickets
    new_session = await login(local_client, "user")
    response = await local_client.post("/api/v1/agent/form-from-files", headers=new_session, data={"form_type": "temporary_residency"}, files={"files": ("note.txt", "Họ tên: Nguyễn Thu Hà".encode(), "text/plain")})
    assert response.status_code == 200
    assert model.calls == 2


@pytest.mark.asyncio
async def test_form_api_blocks_abuse_before_parsing_and_requires_resident(local_client, local_container, monkeypatch):
    def forbidden(*args):
        pytest.fail("must not parse attachment")
    monkeypatch.setattr("src.api.routes.prepare_attachment", forbidden)
    headers = await login(local_client, "user")
    files = {"files": ("document.txt", b"ignored", "text/plain")}
    result = await local_client.post("/api/v1/agent/form-from-files", headers=headers, data={"message": "Tạo một bài hát về cư dân"}, files=files)
    assert result.status_code == 200
    assert result.json()["policy_code"] == "out_of_scope"
    assert (await local_client.post("/api/v1/agent/form-from-files", files=files)).status_code == 401
    assert (await local_client.post("/api/v1/agent/form-from-files", headers=await login(local_client, "admin"), files=files)).status_code == 403


@pytest.mark.asyncio
async def test_form_quota_counts_new_reads_but_cached_read_and_manual_forms_still_work(local_client, local_container, monkeypatch):
    model = ExtractionModel({"person_name": "Nguyễn Thu Hà"})
    monkeypatch.setattr(local_container, "_model", lambda: model)
    local_container.settings.document_extractions_per_10_minutes = 1
    headers = await login(local_client, "user")
    for name, status in [("one.txt", 200), ("one.txt", 200), ("two.txt", 429)]:
        response = await local_client.post("/api/v1/agent/form-from-files", headers=headers, data={"form_type": "temporary_residency"}, files={"files": (name, "Nguyễn Thu Hà".encode(), "text/plain")})
        assert response.status_code == status
    assert (await local_client.get("/api/v1/residents/R001/progress", headers=headers)).status_code == 200
    assert model.calls == 1


@pytest.mark.asyncio
async def test_first_login_blocks_business_reads_writes_chat_and_files(local_client, local_container):
    manager = await login(local_client, "admin")
    created = (await local_client.post("/api/v1/management/resident-accounts", headers=manager, json=account_payload())).json()
    session = (await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": account_payload()["password"]})).json()
    headers = {"Authorization": "Bearer " + session["access_token"]}
    resident_id = created["profile"]["resident_id"]
    for method, path, kwargs in [
        ("get", f"/api/v1/residents/{resident_id}", {}),
        ("get", f"/api/v1/residents/{resident_id}/progress", {}),
        ("post", "/api/v1/requests", {"json": {"request_type": "general_support", "details": "Test"}}),
        ("post", "/api/v1/agent/chat", {"json": {"project_id": "moveinmate", "conversation_id": "new-user", "message": "Thủ tục cư dân"}}),
        ("post", "/api/v1/agent/form-from-files", {"files": {"files": ("note.txt", b"Test", "text/plain")}}),
    ]:
        response = await getattr(local_client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "password_change_required"
    assert local_container.store.get_local_credential(username="resident.new")["password_change_required"] is True
    assert (await local_client.post("/api/v1/auth/logout", headers=headers)).status_code == 204


def test_private_cache_is_bounded_expires_and_clears(monkeypatch):
    cache = PrivateResultCache(max_bytes=6, ttl=10)
    monkeypatch.setattr("src.services.usage_limits.time.monotonic", lambda: 1)
    cache.put(("one",), b"abc")
    cache.put(("two",), b"defg")
    assert cache.get(("one",)) is None
    assert cache.size == 4
    monkeypatch.setattr("src.services.usage_limits.time.monotonic", lambda: 12)
    assert cache.get(("two",)) is None
    assert cache.size == 0
    cache.put(("one",), b"abcdefg")
    assert cache.size == 0
    cache.clear()


def test_usage_budget_is_per_account_and_operation(app_container):
    assert reserve_usage(app_container.store, "one", "ocr", 1)
    assert not reserve_usage(app_container.store, "one", "ocr", 1)
    assert reserve_usage(app_container.store, "two", "ocr", 1)
    assert reserve_usage(app_container.store, "one", "tts", 1)


@pytest.mark.asyncio
async def test_tts_cache_and_quota_are_private_and_preserve_provider(local_client, local_container):
    calls = []
    async def synthesize(text):
        calls.append(text)
        return b"test-audio"
    local_container.text_to_speech = SimpleNamespace(synthesize=synthesize)
    local_container.settings.speech_requests_per_10_minutes = 1
    local_container.settings.tts_provider = "ptit"
    headers = await login(local_client, "user")
    for text, status in [("Xin chào.", 200), ("Xin chào.", 200), ("Nội dung khác.", 429)]:
        response = await local_client.post("/api/v1/speech/synthesize", headers=headers, json={"text": text})
        assert response.status_code == status
        if status == 200:
            assert response.headers["x-tts-provider"] == "ptit"
    assert calls == ["Xin chào."]
    manager = await login(local_client, "admin")
    assert (await local_client.post("/api/v1/speech/synthesize", headers=manager, json={"text": "Xin chào."})).status_code == 200
    assert calls == ["Xin chào.", "Xin chào."]
