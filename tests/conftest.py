import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from langsmith import tracing_context
from langsmith import utils as langsmith_utils
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from src.config import Settings
from src.container import AppContainer
from src.repositories import Store
from src.services import seed_demo_data


@pytest.fixture(scope="session", autouse=True)
def disable_langsmith_tracing():
    """Keep test runs local, including agent work in background threads."""
    with pytest.MonkeyPatch.context() as patch:
        for prefix in ("LANGCHAIN", "LANGSMITH"):
            for suffix in ("TRACING", "TRACING_V2"):
                patch.setenv(f"{prefix}_{suffix}", "false")
        langsmith_utils.get_env_var.cache_clear()
        try:
            with tracing_context(enabled=False):
                yield
        finally:
            langsmith_utils.get_env_var.cache_clear()


def _isolated_test_database_url(settings: Settings) -> str:
    test_url = settings.require_test_database_url()
    runtime_url = make_url(settings.require_database_url())
    # User/driver differences and host aliases do not isolate a database.
    # Fail closed before migrations or TRUNCATE on ambiguous configuration.
    if make_url(test_url).database == runtime_url.database:
        raise ValueError("test_database_must_differ_from_development")
    return test_url


@pytest.fixture(scope="session")
def validate_postgres_test_target():
    return _isolated_test_database_url


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    settings = Settings()
    test_url = _isolated_test_database_url(settings)
    engine = create_engine(test_url)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != make_url(test_url).database:
                raise ValueError("test_database_target_mismatch")
    finally:
        engine.dispose()
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
    return test_url


@pytest.fixture
def resident_today(monkeypatch) -> date:
    current_time = datetime.now(timezone(timedelta(hours=7)))

    class ResidentDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return current_time.astimezone().replace(tzinfo=None)
            return current_time.astimezone(tz)

    monkeypatch.setattr("src.tools.agent_tools.datetime", ResidentDatetime)
    return current_time.date()


@pytest_asyncio.fixture
async def app_container(tmp_path: Path, postgres_test_database_url: str, monkeypatch) -> AppContainer:
    reset_engine = create_engine(postgres_test_database_url)
    try:
        with reset_engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE password_recovery_requests, application_settings, login_reset_baselines, login_reset_attempts, knowledge_chunks, knowledge_documents, revoked_auth_sessions, security_audit_events, residence_memberships, users, parking_reminders, request_events, management_requests, "
                    "checklist_tasks, residents, residential_sites RESTART IDENTITY CASCADE"
                )
            )
    finally:
        reset_engine.dispose()

    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_provider="auto",
        llm_api_key="",
        openai_api_key="",
        openrouter_api_key="",
        gemini_api_key="",
        google_api_key="",
        gemini_tts_api_key="",
        omicx_api_key="",
        tts_provider="gemini",
        stt_provider="omicx",
        database_url=postgres_test_database_url,
        test_database_url=postgres_test_database_url,
        allow_demo_reset=True,
        auth_mode="demo",
        allow_demo_auth=True,
        chroma_persist_dir=str(tmp_path / "chroma"),
        knowledge_dir=str(Path(__file__).resolve().parents[1] / "src" / "project_packs" / "knowledge"),
        include_demo_knowledge=True,
    )
    result = AppContainer(settings)
    try:
        result.store.initialize()
        seed_demo_data(result)
        result.initialize()
        from src.main import app

        monkeypatch.setattr(app.state, "container", result, raising=False)
        yield result
    finally:
        await result.aclose()


@pytest.fixture
def store_contract(app_container: AppContainer) -> Store:
    """Store backend exercised by the portable persistence contract suite."""
    return app_container.store
