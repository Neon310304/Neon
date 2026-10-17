import httpx
import pytest

from src.services import OmicxSpeechToText


@pytest.mark.asyncio
async def test_omicx_transcription_uses_server_key_and_vietnamese(monkeypatch):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers.get("X-Api-Key")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            json={
                "trackingId": "track-1",
                "transcript": "Tôi cần đăng ký xe.",
                "audioDuration": 3,
                "processingTimeMs": 500,
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("src.services.omicx.httpx.AsyncClient", client_factory)
    service = OmicxSpeechToText("omx-secret", "https://api.example.test/stt")

    result = await service.transcribe("voice.webm", b"browser-audio", "audio/webm;codecs=opus", "vi")

    assert result["transcript"] == "Tôi cần đăng ký xe."
    assert result["provider"] == "omicx"
    assert captured["api_key"] == "omx-secret"
    assert captured["content_type"].startswith("multipart/form-data;")
    assert b'name="languageCode"' in captured["body"]
    assert b"vi" in captured["body"]
