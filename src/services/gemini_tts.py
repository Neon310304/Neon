import base64
import binascii
import io
import wave
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .public_speech import normalize_speech_text

MAX_TTS_TEXT_LENGTH = 5000


@dataclass
class GeminiSpeechError(RuntimeError):
    code: str
    message: str
    status_code: int
    retry_after: str | None = None

    def __str__(self) -> str:
        return self.message


def pcm_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap Gemini's raw mono, signed 16-bit PCM in a browser-playable WAV file."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


class GeminiTextToSpeech:
    """Generate multilingual speech through the Gemini Developer API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        base_url: str = "https://generativelanguage.googleapis.com",
    ):
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.voice = voice.strip()
        self.base_url = base_url.strip().rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model and self.voice and self.base_url)

    async def synthesize(self, text: str) -> bytes:
        if not self.configured:
            raise GeminiSpeechError(
                "gemini_tts_not_configured",
                "Giọng đọc Gemini chưa được cấu hình API key.",
                503,
            )

        transcript = normalize_speech_text(text.strip())
        if not transcript:
            raise GeminiSpeechError("tts_text_required", "Nội dung cần đọc đang trống.", 422)
        if len(transcript) > MAX_TTS_TEXT_LENGTH:
            raise GeminiSpeechError(
                "tts_text_too_long",
                f"Nội dung cần đọc vượt quá {MAX_TTS_TEXT_LENGTH} ký tự.",
                413,
            )

        url = (
            f"{self.base_url}/v1beta/models/{quote(self.model, safe='')}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Detect the language of the transcript below. Synthesize clear, "
                                "natural and friendly speech in that same language. Read the "
                                "transcript verbatim and do not read these instructions.\n\n"
                                "TRANSCRIPT:\n" + transcript
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.voice}
                    }
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                response = await client.post(url, headers={"x-goog-api-key": self.api_key}, json=payload)
        except httpx.TimeoutException:
            raise GeminiSpeechError("gemini_tts_timeout", "Giọng đọc xử lý quá thời gian. Vui lòng thử lại.", 504) from None
        except httpx.HTTPError:
            raise GeminiSpeechError("gemini_tts_unavailable", "Không thể kết nối dịch vụ giọng đọc.", 502) from None
        if not response.is_success:
            raise self._request_error(response)
        try:
            return self._audio_from_response(response)
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
            raise GeminiSpeechError("gemini_tts_invalid_response", "Dịch vụ chưa trả về giọng đọc hợp lệ.", 502) from None

    @staticmethod
    def _audio_from_response(response: httpx.Response) -> bytes:
        payload = response.json()
        inline_data = None
        for candidate in payload["candidates"]:
            for part in candidate["content"]["parts"]:
                inline_data = part.get("inlineData") or part.get("inline_data")
                if inline_data and inline_data.get("data"):
                    break
            if inline_data and inline_data.get("data"):
                break
        if not inline_data:
            raise ValueError("missing inline audio")
        encoded = inline_data["data"]
        audio = base64.b64decode(encoded, validate=True)
        if not audio:
            raise ValueError("empty inline audio")
        mime_type = str(
            inline_data.get("mimeType") or inline_data.get("mime_type") or ""
        ).lower()
        if audio.startswith(b"RIFF") or "wav" in mime_type:
            return audio
        return pcm_to_wav(audio)

    @staticmethod
    def _request_error(response: httpx.Response) -> GeminiSpeechError:
        retry_after = response.headers.get("Retry-After")
        if response.status_code in {401, 403}:
            return GeminiSpeechError(
                "gemini_tts_authentication_failed",
                "API key Gemini không hợp lệ hoặc chưa được cấp quyền dùng model TTS.",
                503,
                retry_after,
            )
        if response.status_code == 429:
            return GeminiSpeechError(
                "gemini_tts_rate_limited",
                "Giọng đọc Gemini đã chạm giới hạn miễn phí. Vui lòng thử lại sau.",
                429,
                retry_after,
            )
        return GeminiSpeechError(
            "gemini_tts_request_failed",
            "Gemini tạm thời không thể tạo giọng đọc. Vui lòng thử lại.",
            502 if response.status_code >= 500 else 400,
            retry_after,
        )
