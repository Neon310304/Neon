import asyncio
import io
import wave
from dataclasses import dataclass

import httpx

from .gemini_tts import MAX_TTS_TEXT_LENGTH
from .omicx import MAX_OMICX_AUDIO_BYTES
from .public_speech import normalize_speech_text


@dataclass
class PtitSpeechError(RuntimeError):
    code: str
    message: str
    status_code: int
    retry_after: str | None = None

    def __str__(self) -> str:
        return self.message


def check_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    status_code = response.status_code
    if status_code == 429:
        message = "Dịch vụ giọng nói đang giới hạn lưu lượng. Vui lòng thử lại sau."
    elif status_code in (401, 403):
        message = "Dịch vụ giọng nói từ chối truy cập. Vui lòng báo người quản lý hệ thống."
    else:
        message = "Dịch vụ giọng nói chưa thể xử lý yêu cầu. Vui lòng thử lại."
    raise PtitSpeechError(
        "ptit_speech_request_failed", message,
        status_code if status_code in (401, 403, 413, 422, 429) else 502,
        response.headers.get("Retry-After"),
    )


def configured_endpoint(value: str) -> bool:
    try:
        endpoint = httpx.URL(value)
        return endpoint.scheme == "https" and bool(endpoint.host) and not endpoint.userinfo
    except httpx.InvalidURL:
        return False


class PtitTextToSpeech:
    def __init__(self, endpoint: str, timeout_seconds: int = 30):
        self.endpoint = endpoint.strip()
        self.timeout_seconds = timeout_seconds
        self.client = None

    async def aclose(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    @property
    def configured(self) -> bool:
        return configured_endpoint(self.endpoint)

    async def synthesize(self, text: str) -> bytes:
        if not self.configured:
            raise PtitSpeechError("ptit_tts_not_configured", "Giọng đọc PTIT chưa được cấu hình.", 503)
        text = normalize_speech_text(text.strip())
        if not text:
            raise PtitSpeechError("tts_text_required", "Nội dung cần đọc đang trống.", 422)
        if len(text) > MAX_TTS_TEXT_LENGTH:
            raise PtitSpeechError("tts_text_too_long", "Nội dung cần đọc vượt quá 5000 ký tự.", 413)
        try:
            if self.client is None or self.client.is_closed:
                self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=10.0), follow_redirects=False)
            async with asyncio.timeout(self.timeout_seconds):
                async with self.client.stream("POST", self.endpoint, json={"text": text}) as response:
                    check_response(response)
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(output) + len(chunk) > MAX_OMICX_AUDIO_BYTES:
                            raise PtitSpeechError("ptit_tts_invalid_response", "Tệp giọng đọc vượt quá giới hạn cho phép.", 502)
                        output.extend(chunk)
        except (httpx.TimeoutException, TimeoutError):
            raise PtitSpeechError("ptit_speech_timeout", "Dịch vụ giọng nói xử lý quá thời gian. Vui lòng thử lại.", 504) from None
        except httpx.HTTPError:
            raise PtitSpeechError("ptit_speech_unavailable", "Không thể kết nối dịch vụ giọng nói PTIT.", 502) from None
        audio = bytes(output)
        try:
            with wave.open(io.BytesIO(audio), "rb") as recording:
                if not recording.getnframes():
                    raise ValueError("empty_audio")
        except (wave.Error, EOFError, ValueError):
            raise PtitSpeechError("ptit_tts_invalid_response", "Dịch vụ chưa trả về âm thanh WAV hợp lệ.", 502) from None
        return audio


class PtitSpeechToText:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint.strip()

    @property
    def configured(self) -> bool:
        return configured_endpoint(self.endpoint)

    async def transcribe(
        self, filename: str, content: bytes, content_type: str, language_code: str = "vi",
    ) -> dict:
        if not self.configured:
            raise PtitSpeechError("ptit_stt_not_configured", "Nhập bằng giọng nói PTIT chưa được cấu hình.", 503)
        if not content:
            raise PtitSpeechError("audio_required", "Bản ghi âm đang trống.", 422)
        if len(content) > MAX_OMICX_AUDIO_BYTES:
            raise PtitSpeechError("audio_too_large", "Bản ghi âm vượt quá giới hạn 25 MB.", 413)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
                response = await client.post(
                    self.endpoint, files={"audio_file": (filename, content, content_type)},
                )
        except httpx.TimeoutException:
            raise PtitSpeechError("ptit_speech_timeout", "Dịch vụ giọng nói xử lý quá thời gian. Vui lòng thử lại.", 504) from None
        except httpx.HTTPError:
            raise PtitSpeechError("ptit_speech_unavailable", "Không thể kết nối dịch vụ giọng nói PTIT.", 502) from None
        check_response(response)
        try:
            payload = response.json()
            transcript = payload["text"]
            if not isinstance(transcript, str):
                raise ValueError("invalid_transcript")
        except (ValueError, KeyError, TypeError):
            raise PtitSpeechError("ptit_stt_invalid_response", "Dịch vụ chưa trả về văn bản hợp lệ.", 502) from None
        if not transcript.strip():
            raise PtitSpeechError("speech_not_detected", "Chưa nhận ra lời nói. Vui lòng thử ghi âm lại.", 422)
        return {
            "transcript": transcript.strip(), "audio_duration": payload.get("duration_seconds"),
            "sample_rate": payload.get("sample_rate"), "provider": "ptit",
        }
