import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

EXPECTED_TABLES = {
    "ai_usage_buckets", "ai_request_leases",
    "manager_conversations", "manager_executions",
    "credential_handovers", "request_visibility",
    "notification_receipts",
    "vehicle_documents",
    "application_settings", "login_reset_baselines", "login_reset_attempts",
    "alembic_version",
    "checklist_tasks",
    "management_requests",
    "parking_reminders",
    "request_events",
    "residents",
    "users",
    "residence_memberships",
    "security_audit_events",
    "revoked_auth_sessions",
    "knowledge_documents",
    "knowledge_chunks",
    "local_credentials",
    "auth_challenges",
    "password_recovery_requests",
    "regulation_acknowledgements",
    "residential_sites",
    "residential_units",
}


@pytest.fixture(scope="module")
def migrated_test_database(postgres_test_database_url: str):
    engine = create_engine(postgres_test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_baseline_revision_and_tables(migrated_test_database):
    inspector = inspect(migrated_test_database)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    with migrated_test_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    quota_columns = {column["name"]: column for column in inspector.get_columns("ai_usage_buckets")}
    lease_columns = {column["name"]: column for column in inspector.get_columns("ai_request_leases")}
    assert {"id", "window_start", "used"} <= set(quota_columns)
    assert {"id", "operation", "expires_at"} <= set(lease_columns)


def test_ownership_confirmation_is_nullable_and_timezone_aware(migrated_test_database):
    columns = {item["name"]: item for item in inspect(migrated_test_database).get_columns("residents")}
    assert columns["ownership_confirmed_at"]["nullable"] is True
    assert columns["ownership_confirmed_at"]["type"].timezone is True


def test_issued_credentials_require_first_password_change(migrated_test_database):
    columns = {item["name"]: item for item in inspect(migrated_test_database).get_columns("local_credentials")}
    assert columns["password_change_required"]["nullable"] is False
    assert "true" in columns["password_change_required"]["default"].lower()
    assert columns["username_change_required"]["nullable"] is False
    assert "true" in columns["username_change_required"]["default"].lower()
    assert columns["recovery_email"]["nullable"] is True


def test_recovery_challenge_cascades_with_account_and_uses_utc_expiry(migrated_test_database):
    inspector = inspect(migrated_test_database)
    foreign_keys = inspector.get_foreign_keys("auth_challenges")
    assert any(item["referred_table"] == "local_credentials" and item["options"]["ondelete"] == "CASCADE" for item in foreign_keys)
    columns = {column["name"]: column for column in inspector.get_columns("auth_challenges")}
    assert columns["expires_at"]["type"].timezone is True
    assert "code" not in columns
    assert columns["code_hash"]["type"].length == 64


def test_baseline_postgresql_types(migrated_test_database):
    inspector = inspect(migrated_test_database)
    request_columns = {column["name"]: column for column in inspector.get_columns("management_requests")}
    assert isinstance(request_columns["payload_json"]["type"], JSONB)
    assert request_columns["created_at"]["type"].timezone is True
    assert request_columns["updated_at"]["type"].timezone is True

    task_columns = {column["name"]: column for column in inspector.get_columns("checklist_tasks")}
    event_columns = {column["name"]: column for column in inspector.get_columns("request_events")}
    assert task_columns["id"]["identity"] is not None
    assert event_columns["id"]["identity"] is not None


def test_baseline_constraints_and_indexes(migrated_test_database):
    inspector = inspect(migrated_test_database)
    resident_checks = {constraint["name"] for constraint in inspector.get_check_constraints("residents")}
    assert resident_checks == {
        "ck_residents_family_members_range",
        "ck_residents_profile_version_positive",
        "ck_residents_vehicle",
    }

    task_uniques = {constraint["name"] for constraint in inspector.get_unique_constraints("checklist_tasks")}
    assert "uq_checklist_tasks_resident_code" in task_uniques

    request_uniques = {constraint["name"] for constraint in inspector.get_unique_constraints("management_requests")}
    assert request_uniques == {
        "uq_management_requests_id_resident",
        "uq_management_requests_idempotency_key",
    }
    request_indexes = {index["name"]: index for index in inspector.get_indexes("management_requests")}
    assert request_indexes["uq_management_requests_active_business_key"]["unique"] is True
    assert request_indexes["uq_management_requests_active_business_key"]["dialect_options"]["postgresql_where"]

    reminder_fks = {constraint["name"] for constraint in inspector.get_foreign_keys("parking_reminders")}
    assert reminder_fks == {
        "fk_parking_reminders_request_id_management_requests",
        "fk_parking_reminders_request_resident_management_requests",
        "fk_parking_reminders_resident_id_residents",
    }
