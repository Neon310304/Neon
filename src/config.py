from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PLACEHOLDER_MARKERS = ("your-", "your_", "placeholder", "change-me", "changeme")


def configured_value(value: str) -> str:
    """Return a normalized value, or an empty string for template placeholders."""
    normalized = value.strip()
    lowered = normalized.lower()
    if not normalized:
        return ""
    if normalized.startswith("<") and normalized.endswith(">"):
        return ""
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return ""
    return normalized


@dataclass(frozen=True)
class LLMConnection:
    provider: str
    api_key: str = field(repr=False)
    model: str
    base_url: str | None = field(default=None, repr=False)
    default_headers: dict[str, str] | None = None


class Settings(BaseSettings):
    app_name: str = "MoveInMate AI"
    app_env: str = "development"
    settings_source: Literal["env", "postgresql"] = "env"
    settings_encryption_key: str = Field(default="", repr=False)
    login_reset_enabled: bool = False
    login_reset_code: str = Field(default="", repr=False)
    login_reset_preserved_usernames: str = ""

    # `auto` selects the first configured provider in this order:
    # custom, OpenAI, OpenRouter, Gemini.
    llm_provider: Literal["auto", "openai", "openrouter", "gemini", "custom"] = "auto"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_base_url: str = Field(default="", repr=False)

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-5-nano"
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_model: str = "openai/gpt-5-nano"
    openrouter_site_url: str = "http://localhost:8000"
    openrouter_app_name: str = "MoveInMate AI"
    gemini_api_key: str = Field(default="", repr=False)
    google_api_key: str = Field(default="", repr=False)
    gemini_model: str = "gemini-3.1-flash-lite"

    # Gemini Flash TTS has a free tier and supports multilingual speech.
    # It reuses GEMINI_API_KEY/GOOGLE_API_KEY unless a separate key is supplied.
    gemini_tts_api_key: str = Field(default="", repr=False)
    gemini_tts_model: str = "gemini-3.1-flash-tts-preview"
    gemini_tts_voice: str = "Aoede"
    gemini_tts_base_url: str = Field(default="https://generativelanguage.googleapis.com", repr=False)

    omicx_api_key: str = Field(default="", repr=False)
    omicx_stt_url: str = Field(default="https://api.omicx.one/stt", repr=False)

    tts_provider: Literal["gemini", "ptit"] = "gemini"
    stt_provider: Literal["omicx", "ptit"] = "omicx"
    ptit_tts_url: str = Field(default="", repr=False)
    ptit_stt_url: str = Field(default="", repr=False)
    chat_requests_per_10_minutes: int = Field(default=30, ge=1, le=1000)
    speech_requests_per_10_minutes: int = Field(default=60, ge=1, le=1000)
    transcription_requests_per_10_minutes: int = Field(default=10, ge=1, le=100)
    document_extractions_per_10_minutes: int = Field(default=5, ge=1, le=100)
    ai_requests_per_10_minutes: int = Field(default=300, ge=1)
    ai_ingress_requests_per_10_minutes: int = Field(default=600, ge=1)
    ai_ip_requests_per_10_minutes: int = Field(default=120, ge=1)
    ai_llm_requests_per_10_minutes: int = Field(default=120, ge=1)
    ai_tts_requests_per_10_minutes: int = Field(default=240, ge=1)
    ai_tts_chars_per_10_minutes: int = Field(default=60000, ge=1)
    ai_stt_requests_per_10_minutes: int = Field(default=30, ge=1)
    ai_ocr_requests_per_10_minutes: int = Field(default=30, ge=1)
    ai_max_concurrent: int = Field(default=6, ge=1)
    ai_llm_max_concurrent: int = Field(default=3, ge=1)
    ai_tts_max_concurrent: int = Field(default=2, ge=1)
    ai_stt_max_concurrent: int = Field(default=1, ge=1)
    ai_ocr_max_concurrent: int = Field(default=1, ge=1)
    ai_llm_timeout_seconds: int = Field(default=60, ge=5, le=180)
    ptit_tts_timeout_seconds: int = Field(default=30, ge=5, le=90)

    auth_mode: Literal["required", "demo"] = "required"
    auth_provider: Literal["local"] = "local"
    allow_demo_auth: bool = False
    auth_jwt_secret: str = Field(default="", repr=False)
    auth_jwt_issuer: str = "moveinmate-local"
    auth_jwt_audience: str = "moveinmate"
    auth_jwt_leeway_seconds: int = Field(default=60, ge=0, le=300)
    auth_session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
    local_auth_admin_username: str = "admin"
    local_auth_admin_password: str = Field(default="", repr=False)
    local_auth_user_username: str = "user"
    local_auth_user_password: str = Field(default="", repr=False)
    local_auth_site_id: str = "sunrise-residence"
    local_auth_admin_site_ids: str = ""
    local_auth_user_resident_id: str = "R001"
    show_local_login_credentials: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_username: str = Field(default="", repr=False)
    smtp_password: str = Field(default="", repr=False)
    smtp_from: str = ""

    # Runtime persistence is PostgreSQL-only. An empty value fails explicitly
    # when the application constructs its Store; there is no database fallback.
    database_url: str = Field(default="", repr=False)
    test_database_url: str = Field(default="", repr=False)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: int = Field(default=30, ge=1)
    db_connect_timeout_seconds: int = Field(default=10, ge=1)
    db_host_override: str = ""
    allow_demo_reset: bool = False
    chroma_persist_dir: str = "./.runtime/chroma"
    knowledge_dir: str = "./src/project_packs/knowledge"
    include_demo_knowledge: bool = False
    allowed_origins: str = "http://localhost:8000"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def ensure_runtime_dirs(self) -> None:
        Path(self.chroma_persist_dir).mkdir(parents=True, exist_ok=True)

    def require_test_database_url(self) -> str:
        """Return a PostgreSQL test URL only when it is isolated from development."""
        raw_test_url = self.test_database_url.strip()
        if not raw_test_url:
            raise ValueError("test_database_url_required")
        try:
            test_url = self._database_url_with_host_override(make_url(raw_test_url))
            development_url = self._database_url_with_host_override(make_url(self.database_url.strip()))
        except ArgumentError as exc:
            raise ValueError("invalid_database_url") from exc
        if test_url.get_backend_name() != "postgresql":
            raise ValueError("test_database_must_be_postgresql")
        if test_url == development_url:
            raise ValueError("test_database_must_differ_from_development")
        if "test" not in (test_url.database or "").casefold():
            raise ValueError("test_database_name_must_contain_test")
        return test_url.render_as_string(hide_password=False)

    def require_database_url(self) -> str:
        """Return the configured PostgreSQL runtime URL without a fallback."""
        raw_url = self.database_url.strip()
        if not raw_url:
            raise ValueError("database_url_required")
        try:
            url = self._database_url_with_host_override(make_url(raw_url))
        except ArgumentError as exc:
            raise ValueError("invalid_database_url") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError("database_must_be_postgresql")
        return url.render_as_string(hide_password=False)

    def validate_demo_database_target(self) -> None:
        """Fail closed to the two dedicated Compose demo databases."""
        environment = self.app_env.strip().casefold()
        if environment not in {"development", "test"}:
            raise ValueError("demo_environment_not_allowed")
        url = make_url(self.require_database_url())
        expected_database = {
            "development": "moveinmate_dev",
            "test": "moveinmate_test",
        }[environment]
        if url.database != expected_database:
            raise ValueError("demo_database_target_not_allowed")
        if environment == "development" and self.test_database_url.strip():
            try:
                test_url = make_url(self.test_database_url.strip())
            except ArgumentError as exc:
                raise ValueError("demo_database_target_not_allowed") from exc
            # Credentials/driver/host aliases do not make the same database
            # name a safe development target. Ambiguous configuration fails closed.
            if test_url.database == url.database:
                raise ValueError("demo_database_target_not_allowed")

    def validate_demo_reset(self) -> None:
        if not self.allow_demo_reset:
            raise ValueError("demo_reset_disabled")
        self.validate_demo_database_target()

    def _database_url_with_host_override(self, url: URL) -> URL:
        host = self.db_host_override.strip()
        return url.set(host=host) if host and url.get_backend_name() == "postgresql" else url

    def resolve_llm(self) -> LLMConnection | None:
        provider = self.llm_provider
        custom_key = configured_value(self.llm_api_key)
        custom_model = configured_value(self.llm_model)
        custom_base_url = configured_value(self.llm_base_url)
        openai_key = configured_value(self.openai_api_key)
        openrouter_key = configured_value(self.openrouter_api_key)
        gemini_key = configured_value(self.gemini_api_key) or configured_value(self.google_api_key)

        if provider == "auto":
            if custom_key and custom_base_url and custom_model:
                provider = "custom"
            elif openai_key:
                provider = "openai"
            elif openrouter_key:
                provider = "openrouter"
            elif gemini_key:
                provider = "gemini"
            else:
                return None

        if provider == "openai" and openai_key:
            return LLMConnection("openai", openai_key, self.openai_model.strip())
        if provider == "openrouter" and openrouter_key:
            headers = {
                "HTTP-Referer": self.openrouter_site_url.strip(),
                "X-OpenRouter-Title": self.openrouter_app_name.strip(),
            }
            return LLMConnection(
                "openrouter",
                openrouter_key,
                self.openrouter_model.strip(),
                OPENROUTER_BASE_URL,
                {key: value for key, value in headers.items() if value},
            )
        if provider == "gemini" and gemini_key:
            return LLMConnection(
                "gemini",
                gemini_key,
                self.gemini_model.strip(),
                GEMINI_OPENAI_BASE_URL,
                {"x-goog-api-client": "moveinmate-ai/1.0.0"},
            )
        if provider == "custom" and custom_key and custom_base_url and custom_model:
            return LLMConnection("custom", custom_key, custom_model, custom_base_url)
        return None

    def llm_summary(self) -> tuple[str, str]:
        connection = self.resolve_llm()
        if connection:
            return connection.provider, connection.model
        if self.llm_provider == "openai":
            return "openai", self.openai_model
        if self.llm_provider == "openrouter":
            return "openrouter", self.openrouter_model
        if self.llm_provider == "gemini":
            return "gemini", self.gemini_model
        if self.llm_provider == "custom":
            return "custom", self.llm_model
        return "unconfigured", ""

    def resolve_gemini_tts_key(self) -> str:
        return (
            configured_value(self.gemini_tts_api_key)
            or configured_value(self.gemini_api_key)
            or configured_value(self.google_api_key)
        )


@lru_cache
def get_settings() -> Settings:
    from src.repositories.settings import load_database_settings

    return load_database_settings(Settings())
