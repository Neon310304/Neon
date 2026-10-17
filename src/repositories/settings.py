import json
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from src.config import Settings
from src.repositories.store import Base, Store, now_utc

BOOTSTRAP_FIELDS = frozenset({
    "app_env", "settings_source", "settings_encryption_key", "database_url", "test_database_url",
    "db_host_override", "auth_mode", "auth_provider", "allow_demo_auth", "allow_demo_reset",
    "show_local_login_credentials", "include_demo_knowledge", "chroma_persist_dir", "knowledge_dir",
    "login_reset_enabled", "login_reset_preserved_usernames",
    "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from",
    "tts_provider", "stt_provider", "ptit_tts_url", "ptit_stt_url",
    "chat_requests_per_10_minutes", "speech_requests_per_10_minutes",
    "transcription_requests_per_10_minutes", "document_extractions_per_10_minutes",
})
DATABASE_FIELDS = frozenset(Settings.model_fields) - BOOTSTRAP_FIELDS - {
    name for name in Settings.model_fields if name.startswith(("local_auth_", "ai_")) or name == "ptit_tts_timeout_seconds"
}


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SettingsStore:
    def __init__(self, store: Store, key: str):
        self.store = store
        try:
            self.cipher = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeError):
            raise ValueError("settings_encryption_key_invalid") from None

    def save(self, settings: Settings) -> int:
        values = settings.model_dump()
        with self.store.session() as session:
            for name in sorted(DATABASE_FIELDS):
                payload = json.dumps({"name": name, "value": values[name]}, ensure_ascii=False).encode()
                statement = insert(ApplicationSetting).values(
                    name=name, encrypted_value=self.cipher.encrypt(payload).decode(), updated_at=now_utc(),
                )
                session.execute(statement.on_conflict_do_update(
                    index_elements=[ApplicationSetting.name],
                    set_={"encrypted_value": statement.excluded.encrypted_value, "updated_at": now_utc()},
                ))
        return len(DATABASE_FIELDS)

    def load(self, bootstrap: Settings) -> Settings:
        values = bootstrap.model_dump()
        with self.store.session() as session:
            rows = session.scalars(select(ApplicationSetting)).all()
            if {row.name for row in rows} != DATABASE_FIELDS:
                raise ValueError("database_settings_incomplete_or_unknown")
            try:
                for row in rows:
                    payload = json.loads(self.cipher.decrypt(row.encrypted_value.encode()))
                    if payload["name"] != row.name:
                        raise ValueError("database_setting_identity_mismatch")
                    values[row.name] = payload["value"]
            except (InvalidToken, UnicodeError, KeyError, json.JSONDecodeError):
                raise ValueError("database_settings_decryption_failed") from None
        try:
            return Settings(_env_file=None, **values)
        except ValidationError:
            raise ValueError("database_settings_invalid") from None


def load_database_settings(settings: Settings, store: Store | None = None) -> Settings:
    if settings.settings_source != "postgresql":
        return settings
    owned_store = store is None
    store = store or Store(settings.require_database_url())
    try:
        return SettingsStore(store, settings.settings_encryption_key).load(settings)
    finally:
        if owned_store:
            store.engine.dispose()
