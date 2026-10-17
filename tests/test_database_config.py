import pytest

from src.config import Settings

DEVELOPMENT_URL = "postgresql+psycopg2://moveinmate:example-only@localhost:5432/moveinmate_dev"
TEST_URL = "postgresql+psycopg2://moveinmate:example-only@localhost:5432/moveinmate_test"


def test_test_database_url_accepts_separate_postgresql_database():
    settings = Settings(
        database_url=DEVELOPMENT_URL,
        test_database_url=TEST_URL,
        db_host_override="",
    )

    assert settings.require_test_database_url() == TEST_URL


@pytest.mark.parametrize(
    ("test_url", "error"),
    [
        ("", "test_database_url_required"),
        ("sqlite:///:memory:", "test_database_must_be_postgresql"),
        (DEVELOPMENT_URL, "test_database_must_differ_from_development"),
        (
            "postgresql+psycopg2://moveinmate:example-only@localhost:5432/secondary",
            "test_database_name_must_contain_test",
        ),
    ],
)
def test_test_database_url_rejects_unsafe_targets(test_url: str, error: str):
    settings = Settings(
        database_url=DEVELOPMENT_URL,
        test_database_url=test_url,
        db_host_override="",
    )

    with pytest.raises(ValueError, match=f"^{error}$"):
        settings.require_test_database_url()


def test_database_pool_and_reset_defaults_are_bounded_and_safe():
    settings = Settings()

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5
    assert settings.db_pool_timeout_seconds == 30
    assert settings.db_connect_timeout_seconds == 10
    assert settings.allow_demo_reset is False


def test_runtime_database_url_rejects_missing_or_sqlite_configuration():
    with pytest.raises(ValueError, match="^database_url_required$"):
        Settings(_env_file=None, database_url="").require_database_url()
    with pytest.raises(ValueError, match="^database_must_be_postgresql$"):
        Settings(_env_file=None, database_url="sqlite:///:memory:").require_database_url()


def test_database_host_override_changes_only_the_host():
    settings = Settings(
        _env_file=None,
        database_url=DEVELOPMENT_URL,
        db_host_override="postgres",
    )

    resolved = settings.require_database_url()

    assert resolved == DEVELOPMENT_URL.replace("localhost", "postgres")
