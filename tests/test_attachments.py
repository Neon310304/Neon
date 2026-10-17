import io
import zipfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.agent_core.runtime import AgentResult
from src.main import app
from src.repositories.store import Resident, now_utc
from src.services.attachments import (
    AttachmentError,
    build_human_content,
    prepare_attachment,
)


@pytest_asyncio.fixture
async def client(app_container):
    with app_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = now_utc()
    app.state.container = app_container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Demo-Role": "resident"}) as value:
        yield value


def test_text_document_is_extracted_without_persistence():
    attachment = prepare_attachment("huong-dan.txt", "Nội dung thử nghiệm".encode())

    assert attachment.kind == "document"
    assert attachment.text == "Nội dung thử nghiệm"
    assert attachment.data_url is None


def test_docx_text_is_extracted_in_memory():
    buffer = io.BytesIO()
    document_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Giay to cu dan</w:t></w:r></w:p></w:body>
    </w:document>"""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    attachment = prepare_attachment("hoso.docx", buffer.getvalue())

    assert attachment.text == "Giay to cu dan"


def test_image_signature_is_checked_and_converted_to_data_url():
    attachment = prepare_attachment("anh.png", b"\x89PNG\r\n\x1a\nimage-data")
    content = build_human_content("Ảnh này là gì?", [attachment])

    assert attachment.kind == "image"
    assert attachment.data_url.startswith("data:image/png;base64,")
    assert isinstance(content, list)
    assert any(part["type"] == "image_url" for part in content)

    with pytest.raises(AttachmentError, match="không phải ảnh hợp lệ"):
        prepare_attachment("gia.png", b"not-an-image")


class CapturingAgent:
    def __init__(self):
        self.message = None

    async def run(self, project_id, actor_id, conversation_id, message, language):
        self.message = message
        return AgentResult(answer="Đã đọc tệp.")


@pytest.mark.asyncio
async def test_multipart_chat_extracts_document_for_agent(client, app_container):
    agent = CapturingAgent()
    app_container.agent = agent

    response = await client.post(
        "/api/v1/agent/chat-with-files",
        data={
            "project_id": "moveinmate",
            "actor_id": "R001",
            "conversation_id": "attachment-test",
            "message": "Tóm tắt tệp này",
            "language": "vi",
        },
        files={"files": ("note.txt", "Xin chào cư dân".encode(), "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "Đã đọc tệp."
    assert isinstance(agent.message, list)
    assert agent.message[0]["text"] == "Tóm tắt tệp này"
    assert "Xin chào cư dân" in agent.message[1]["text"]


@pytest.mark.asyncio
async def test_attachment_only_message_has_explicit_read_only_purpose(client, app_container):
    agent = CapturingAgent()
    app_container.agent = agent
    response = await client.post("/api/v1/agent/chat-with-files", data={"conversation_id": "image-only"},
        files={"files": ("document.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")})
    assert response.status_code == 200
    assert "giấy tờ" in agent.message[0]["text"]
    assert "chưa lưu hoặc gửi" in agent.message[0]["text"]
    assert response.json()["policy_code"] is None
    assert any(part["type"] == "image_url" for part in agent.message)
    assert all(part["image_url"]["detail"] == "high" for part in agent.message if part["type"] == "image_url")


@pytest.mark.asyncio
async def test_attachment_abuse_is_blocked_before_reading_file_or_calling_agent(client, app_container, monkeypatch):
    agent = CapturingAgent()
    app_container.agent = agent

    def forbidden(*args):
        pytest.fail("Blocked intent must not parse or inspect an attachment")

    monkeypatch.setattr("src.api.routes.prepare_attachment", forbidden)
    response = await client.post("/api/v1/agent/chat-with-files", data={
        "conversation_id": "abuse-with-photo", "message": "Tạo 1 bài hát về cư dân"},
        files={"files": ("document.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")})
    assert response.status_code == 200
    assert response.json()["policy_code"] == "out_of_scope"
    assert response.json()["tools_used"] == []
    assert agent.message is None


@pytest.mark.asyncio
async def test_multipart_chat_rejects_unsupported_files(client):
    response = await client.post(
        "/api/v1/agent/chat-with-files",
        data={"conversation_id": "attachment-test", "message": "Đọc tệp"},
        files={"files": ("payload.exe", b"not allowed", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_file_type"
