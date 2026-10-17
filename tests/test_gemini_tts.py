import base64
import json

import httpx
import pytest

from src.services import GeminiSpeechError, GeminiTextToSpeech


@pytest.mark.asyncio
async def test_gemini_tts_auto_detects_language_and_returns_wav(monkeypatch):
    captured = {}
    pcm = b"\x00\x00\x01\x00"

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers.get("x-goog-api-key")
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(await request.aread())
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/L16;codec=pcm;rate=24000",
                                        "data": base64.b64encode(pcm).decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("src.services.gemini_tts.httpx.AsyncClient", client_factory)
    service = GeminiTextToSpeech(
        "gemini-secret",
        "gemini-3.1-flash-tts-preview",
        "Aoede",
    )

    audio = await service.synthesize("Xin chào cư dân. BQL, bql! ABQL BQL1")

    assert audio.startswith(b"RIFF")
    assert b"WAVE" in audio[:16]
    assert captured["api_key"] == "gemini-secret"
    assert captured["url"].endswith(
        "/v1beta/models/gemini-3.1-flash-tts-preview:generateContent"
    )
    prompt = captured["payload"]["contents"][0]["parts"][0]["text"]
    assert "Detect the language" in prompt
    assert "Xin chào cư dân." in prompt
    assert prompt.endswith("Xin chào cư dân. ban quản lí, ban quản lí! ABQL BQL1")
    config = captured["payload"]["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    assert config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Aoede"


@pytest.mark.asyncio
async def test_gemini_tts_requires_server_side_key():
    service = GeminiTextToSpeech("", "gemini-3.1-flash-tts-preview", "Aoede")

    with pytest.raises(GeminiSpeechError) as raised:
        await service.synthesize("Xin chào")

    assert raised.value.code == "gemini_tts_not_configured"
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_gemini_tts_rejects_expansion_over_provider_limit():
    service = GeminiTextToSpeech("test-key", "test-model", "test-voice")
    with pytest.raises(GeminiSpeechError) as raised:
        await service.synthesize("BQL " * 500)
    assert raised.value.code == "tts_text_too_long"
