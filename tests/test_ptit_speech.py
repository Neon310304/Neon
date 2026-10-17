import json

import httpx
import pytest
import pytest_asyncio

from src.config import Settings
from src.services.gemini_tts import pcm_to_wav
from src.services.ptit_speech import PtitSpeechError, PtitSpeechToText, PtitTextToSpeech


@pytest_asyncio.fixture
async def mock_ptit(monkeypatch):
    real_client = httpx.AsyncClient
    clients = []

    def configure(handler):
        transport = httpx.MockTransport(handler)

        def client_factory(*args, **kwargs):
            client = real_client(*args, **(kwargs | {"transport": transport}))
            clients.append(client)
            return client

        monkeypatch.setattr("src.services.ptit_speech.httpx.AsyncClient", client_factory)

    try:
        yield configure
    finally:
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_ptit_tts_posts_json_and_returns_real_wav_bytes(mock_ptit):
    audio = pcm_to_wav(b"\x00\x00\x01\x00")
    captured = {}

    async def handler(request):
        captured.update(url=str(request.url), payload=json.loads(await request.aread()))
        assert request.headers["content-type"] == "application/json"
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, content=audio, headers={"Content-Type": "audio/wav"})

    mock_ptit(handler)
    service = PtitTextToSpeech("https://speech.example.test/synthesize")
    assert service.configured
    assert await service.synthesize(" Xin chào. ") == audio
    assert captured == {"url": service.endpoint, "payload": {"text": "Xin chào."}}


@pytest.mark.asyncio
async def test_ptit_stt_uploads_audio_file_and_maps_text_to_frontend_contract(mock_ptit):
    async def handler(request):
        body = await request.aread()
        assert b'name="audio_file"' in body
        assert b'filename="voice.webm"' in body
        assert b"browser-audio" in body
        assert b"languageCode" not in body
        assert "authorization" not in request.headers
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        return httpx.Response(200, json={"text": " Đăng ký xe. ", "duration_seconds": 3, "sample_rate": 16000})

    mock_ptit(handler)
    result = await PtitSpeechToText("https://speech.example.test/transcribe").transcribe(
        "voice.webm", b"browser-audio", "audio/webm", "vi",
    )
    assert result == {"transcript": "Đăng ký xe.", "audio_duration": 3, "sample_rate": 16000, "provider": "ptit"}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["", "http://speech.example.test/synthesize", "https://", "https://user:secret@speech.example.test/synthesize"])
async def test_ptit_requires_configured_https_endpoints(endpoint):
    with pytest.raises(PtitSpeechError, match="chưa được cấu hình") as raised:
        await PtitTextToSpeech(endpoint).synthesize("Xin chào")
    assert raised.value.status_code == 503
    with pytest.raises(PtitSpeechError) as raised:
        await PtitSpeechToText(endpoint).transcribe("voice.wav", b"audio", "audio/wav")
    assert raised.value.code == "ptit_stt_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,code", [("  ", "tts_text_required"), ("x" * 5001, "tts_text_too_long"), ("BQL " * 500, "tts_text_too_long")], ids=["empty", "too-long", "expanded-too-long"])
async def test_ptit_rejects_invalid_text_before_network(text, code):
    with pytest.raises(PtitSpeechError) as raised:
        await PtitTextToSpeech("https://speech.example.test/synthesize").synthesize(text)
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_ptit_normalizes_only_standalone_bql_before_sending(mock_ptit):
    audio = pcm_to_wav(b"\x00\x00")

    async def handler(request):
        assert json.loads(await request.aread()) == {"text": "ban quản lí, ban quản lí! ABQL BQL1"}
        return httpx.Response(200, content=audio)

    mock_ptit(handler)
    assert await PtitTextToSpeech("https://speech.example.test/synthesize").synthesize("BQL, bql! ABQL BQL1") == audio


@pytest.mark.asyncio
@pytest.mark.parametrize("size,code", [(0, "audio_required"), (25 * 1024 * 1024 + 1, "audio_too_large")], ids=["empty", "too-large"])
async def test_ptit_rejects_invalid_audio_before_network(size, code):
    content = b"x" * size
    with pytest.raises(PtitSpeechError) as raised:
        await PtitSpeechToText("https://speech.example.test/transcribe").transcribe("voice.wav", content, "audio/wav")
    assert raised.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not-audio", pcm_to_wav(b"")])
async def test_ptit_rejects_invalid_or_empty_tts_audio(mock_ptit, content):
    mock_ptit(lambda request: httpx.Response(200, content=content))
    with pytest.raises(PtitSpeechError) as raised:
        await PtitTextToSpeech("https://speech.example.test/synthesize").synthesize("Xin chào")
    assert raised.value.code == "ptit_tts_invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({"text": " "}, "speech_not_detected"), ({"text": None}, "ptit_stt_invalid_response"),
    ({"text": 42}, "ptit_stt_invalid_response"), ({"transcript": "wrong contract"}, "ptit_stt_invalid_response"),
    ([], "ptit_stt_invalid_response"),
])
async def test_ptit_rejects_missing_or_invalid_transcripts(mock_ptit, payload, code):
    mock_ptit(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(PtitSpeechError) as raised:
        await PtitSpeechToText("https://speech.example.test/transcribe").transcribe("voice.wav", b"audio", "audio/wav")
    assert raised.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(401, 401), (429, 429), (500, 502), (302, 502)])
async def test_ptit_maps_errors_and_preserves_retry_after_without_leaking_upstream(mock_ptit, status, expected):
    mock_ptit(lambda request: httpx.Response(status, text="private upstream details", headers={"Retry-After": "10"}))
    for service, arguments in [
        (PtitTextToSpeech("https://speech.example.test/synthesize").synthesize, ("Xin chào",)),
        (PtitSpeechToText("https://speech.example.test/transcribe").transcribe, ("voice.wav", b"audio", "audio/wav")),
    ]:
        with pytest.raises(PtitSpeechError) as raised:
            await service(*arguments)
        assert raised.value.status_code == expected
        assert raised.value.retry_after == "10"
        assert "private upstream details" not in str(raised.value)


@pytest.mark.asyncio
async def test_ptit_maps_network_timeout(mock_ptit):
    def handler(request):
        raise httpx.ReadTimeout("internal url details", request=request)

    mock_ptit(handler)
    with pytest.raises(PtitSpeechError) as raised:
        await PtitTextToSpeech("https://speech.example.test/synthesize").synthesize("Xin chào")
    assert raised.value.status_code == 504
    assert "internal url details" not in str(raised.value)


def test_ptit_environment_configuration_is_loaded_without_api_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TTS_PROVIDER=ptit\nSTT_PROVIDER=ptit\nPTIT_TTS_URL=https://speech.example.test/synthesize\nPTIT_STT_URL=https://speech.example.test/transcribe\n", encoding="utf-8")
    settings = Settings(_env_file=path)
    assert settings.tts_provider == settings.stt_provider == "ptit"
    assert settings.ptit_tts_url.endswith("/synthesize")
    assert settings.ptit_stt_url.endswith("/transcribe")
