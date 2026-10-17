from dataclasses import dataclass
from uuid import uuid4

import httpx

MAX_OMICX_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass
class OmicxTranscriptionError(RuntimeError):
    code: str
    message: str
    status_code: int
    retry_after: str | None = None

    def __str__(self) -> str:
        return self.message


class OmicxSpeechToText:
    """Server-side proxy for OMI Technology's OMICX Voice API."""

    def __init__(self, api_key: str, endpoint: str):
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.endpoint)

    async def transcribe(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        language_code: str = "vi",
    ) -> dict:
        if not self.configured:
            raise OmicxTranscriptionError(
                "omicx_not_configured",
                "OMICX Voice chưa được cấu hình API key.",
                503,
            )
        if not content:
            raise OmicxTranscriptionError("audio_required", "Bản ghi âm đang trống.", 422)
        if len(content) > MAX_OMICX_AUDIO_BYTES:
            raise OmicxTranscriptionError(
                "audio_too_large",
                "Bản ghi âm vượt quá giới hạn 25 MB của OMICX Voice.",
                413,
            )

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
                response = await client.post(
                    self.endpoint,
                    headers={
                        "X-Api-Key": self.api_key,
                        "Idempotency-Key": str(uuid4()),
                    },
                    files={"file": (filename, content, content_type)},
                    data={"languageCode": language_code},
                )
        except httpx.TimeoutException as exc:
            raise OmicxTranscriptionError(
                "omicx_timeout", "OMICX Voice xử lý quá thời gian. Vui lòng thử lại.", 504
            ) from exc
        except httpx.HTTPError as exc:
            raise OmicxTranscriptionError("omicx_unavailable", "Không thể kết nối đến OMICX Voice.", 502) from exc

        if not response.is_success:
            retry_after = response.headers.get("Retry-After")
            messages = {
                401: "API key OMICX Voice không hợp lệ.",
                403: "Tài khoản OMICX Voice không có quyền sử dụng Speech2Text.",
                409: "Bản ghi này đang được OMICX Voice xử lý.",
                429: "OMICX Voice đang giới hạn lưu lượng. Vui lòng thử lại sau.",
                502: "Nhà cung cấp nhận dạng giọng nói tạm thời gặp lỗi.",
                503: "OMICX Voice đang quá tải. Vui lòng thử lại sau.",
            }
            status_code = response.status_code if response.status_code < 500 else 503
            raise OmicxTranscriptionError(
                "omicx_request_failed",
                messages.get(response.status_code, "OMICX Voice không thể xử lý bản ghi âm."),
                status_code,
                retry_after,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OmicxTranscriptionError(
                "omicx_invalid_response", "OMICX Voice trả về dữ liệu không hợp lệ.", 502
            ) from exc

        transcript = str(payload.get("transcript", "")).strip()
        if not transcript:
            raise OmicxTranscriptionError("speech_not_detected", "OMICX Voice chưa nhận ra lời nói trong bản ghi.", 422)
        return {
            "transcript": transcript,
            "tracking_id": payload.get("trackingId"),
            "audio_duration": payload.get("audioDuration"),
            "processing_time_ms": payload.get("processingTimeMs"),
            "provider": "omicx",
        }
