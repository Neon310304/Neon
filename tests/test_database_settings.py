import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from src.repositories.settings import DATABASE_FIELDS, SettingsStore, load_database_settings


def test_settings_roundtrip_encrypted_and_override_environment(app_container, monkeypatch):
    key = Fernet.generate_key().decode()
    settings = app_container.settings.model_copy(update={
        "openai_api_key": "private-test-api-key", "openai_model": "configured-in-postgres",
        "login_reset_code": "0000",
    })
    repository = SettingsStore(app_container.store, key)
    assert repository.save(settings) == len(DATABASE_FIELDS)
    with app_container.store.engine.connect() as connection:
        payloads = connection.execute(text("SELECT encrypted_value FROM application_settings")).scalars().all()
        assert all("private-test-api-key" not in value and "0000" not in value for value in payloads)
    monkeypatch.setenv("OPENAI_MODEL", "stale-env-model")
    bootstrap = settings.model_copy(update={
        "settings_source": "postgresql", "settings_encryption_key": key,
        "openai_api_key": "", "openai_model": "fallback",
        "local_auth_admin_password": "retained-local-password",
        "tts_provider": "ptit", "stt_provider": "ptit",
        "ptit_tts_url": "https://speech.example.test/synthesize",
        "ptit_stt_url": "https://speech.example.test/transcribe",
    })
    loaded = load_database_settings(bootstrap, app_container.store)
    assert loaded.openai_api_key == settings.openai_api_key
    assert loaded.openai_model == "configured-in-postgres"
    assert loaded.login_reset_code == "0000"
    assert loaded.local_auth_admin_password == "retained-local-password"
    assert loaded.database_url == bootstrap.database_url
    assert loaded.auth_mode == bootstrap.auth_mode
    for name in ("tts_provider", "stt_provider", "ptit_tts_url", "ptit_stt_url"):
        assert name not in DATABASE_FIELDS
        assert getattr(loaded, name) == getattr(bootstrap, name)


def test_database_settings_wrong_key_and_incomplete_fail_closed(app_container):
    repository = SettingsStore(app_container.store, Fernet.generate_key().decode())
    with pytest.raises(ValueError, match="incomplete"):
        repository.load(app_container.settings)
    repository.save(app_container.settings)
    with pytest.raises(ValueError, match="decryption"):
        SettingsStore(app_container.store, Fernet.generate_key().decode()).load(app_container.settings)


def test_encrypted_settings_cannot_be_swapped_between_fields(app_container):
    repository = SettingsStore(app_container.store, Fernet.generate_key().decode())
    repository.save(app_container.settings)
    with app_container.store.engine.begin() as connection:
        connection.execute(text("UPDATE application_settings SET encrypted_value = (SELECT encrypted_value FROM application_settings WHERE name='openai_model') WHERE name='openai_api_key'"))
    with pytest.raises(ValueError, match="identity_mismatch"):
        repository.load(app_container.settings)


def test_environment_mode_does_not_read_database(app_container):
    assert load_database_settings(app_container.settings) is app_container.settings
