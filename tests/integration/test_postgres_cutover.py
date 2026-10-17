from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from src.config import Settings
from src.container import AppContainer
from src.services import reset_demo_data, seed_demo_data


def test_empty_test_database_flow_is_repeatable_without_startup_seed(
    postgres_test_database_url, tmp_path, monkeypatch,
):
    """Rebuild only the dedicated test schema through the approved baseline."""
    engine = create_engine(postgres_test_database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "moveinmate_test"
    monkeypatch.setenv("DATABASE_URL", postgres_test_database_url)
    config = Config("alembic.ini")
    settings = Settings(
        _env_file=None, app_env="test", database_url=postgres_test_database_url,
        test_database_url=postgres_test_database_url, allow_demo_reset=True,
        llm_api_key="", openai_api_key="", openrouter_api_key="", gemini_api_key="",
        google_api_key="", gemini_tts_api_key="", omicx_api_key="",
        chroma_persist_dir=str(tmp_path / "chroma"),
    )
    try:
        for _ in range(2):
            command.downgrade(config, "base")
            assert set(inspect(engine).get_table_names()) == {"alembic_version"}
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            app = AppContainer(settings)
            try:
                app.initialize()
                assert app.store.get_resident("R001") is None
                with engine.connect() as connection:
                    for table in (
                        "residents", "checklist_tasks", "management_requests",
                        "request_events", "parking_reminders",
                    ):
                        assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
                seed_demo_data(app, include_requests=True)
                request = app.store.list_requests("R001")[0]
                events = app.store.request_timeline(request["request_id"])
                seed_demo_data(app, include_requests=True)
                assert app.store.list_requests("R001") == [request]
                assert app.store.request_timeline(request["request_id"]) == events
                assert len(app.store.list_tasks("R001")) == 10
                app.initialize()
                assert app.store.list_requests("R001") == [request]
                reset_demo_data(app)
                seed_demo_data(app, include_requests=True)
                assert len(app.store.list_requests("R001")) == 1
                assert len(app.store.list_tasks("R001")) == 10
                with engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM request_events")) == 1
            finally:
                app.close()
    finally:
        command.upgrade(config, "head")
        engine.dispose()
