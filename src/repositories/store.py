import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    cast,
    create_engine,
    false,
    func,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


REQUEST_STATUS_TRANSITIONS = {
    "submitted": {"in_review", "needs_info", "approved", "rejected"},
    "in_review": {"needs_info", "approved", "rejected"},
    "needs_info": {"rejected"},
    "approved": {"completed"},
    "rejected": set(),
    "completed": set(),
    "cancelled_by_resident": set(),
}
ACTIVE_REQUEST_STATUSES = {"submitted", "in_review", "needs_info", "approved"}


def order_checklist_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regulations = [task for task in tasks if task.get("code") == "confirm_regulations"]
    remaining = [task for task in tasks if task.get("code") != "confirm_regulations"]
    position = min(5, len(remaining))
    return remaining[:position] + regulations + remaining[position:]


NO_NEED_TASK_CODES = {"temporary_residency", "move_in_schedule", "vehicle_registration"}
COMING_SOON_TASK_CODES = {"handover_inspection", "safety_briefing"}
CHECKLIST_TASK_ORDER = (
    "verify_residency", "temporary_residency", "move_in_schedule", "resident_card",
    "vehicle_registration", "account_registration", "internet_registration",
    "handover_inspection", "safety_briefing", "confirm_regulations",
)


def checklist_sort_key(task: dict[str, Any]) -> tuple[int, int]:
    code = task.get("code")
    group = 4 if code == "confirm_regulations" else 0 if code == "verify_residency" else 3 if code in COMING_SOON_TASK_CODES else 1 if task.get("required") else 2
    return group, CHECKLIST_TASK_ORDER.index(code) if code in CHECKLIST_TASK_ORDER else len(CHECKLIST_TASK_ORDER)


REQUEST_TASK_TYPES = {
    "verify_residency": "verify_residency",
    "temporary_residency": "temporary_residency",
    "move_in_schedule": "move_in_schedule",
    "handover_inspection": "handover_inspection",
    "resident_card": "resident_card",
    "emergency_contact": "emergency_contact",
    "account_registration": "account_registration",
    "vehicle_registration": "vehicle_registration",
    "internet_registration": "internet_registration",
}
DEMO_RESIDENT_ID = "R001"
DEMO_SITE_ID = "sunrise-residence"
DEMO_ADVISORY_LOCK_KEY = 0x4D494D06


class ResetInvalidatedError(ValueError):
    def __init__(self):
        super().__init__("session_reset")


class Base(DeclarativeBase):
    pass


class ResidentialSite(Base):
    __tablename__ = "residential_sites"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_checked_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class ResidentialUnit(Base):
    __tablename__ = "residential_units"
    __table_args__ = (
        ForeignKeyConstraint(["site_id"], ["residential_sites.id"], ondelete="CASCADE"),
        CheckConstraint("building IN ('A', 'B', 'C', 'D')", name="ck_residential_units_building"),
        CheckConstraint("floor BETWEEN 1 AND 20", name="ck_residential_units_floor_range"),
        CheckConstraint("room BETWEEN 1 AND 10", name="ck_residential_units_room_range"),
        UniqueConstraint("site_id", "apartment", name="uq_residential_units_site_apartment"),
        Index("ix_residential_units_site_apartment", "site_id", "apartment"),
    )

    site_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    building: Mapped[str] = mapped_column(String(1), primary_key=True)
    floor: Mapped[int] = mapped_column(Integer, primary_key=True)
    room: Mapped[int] = mapped_column(Integer, primary_key=True)
    apartment: Mapped[str] = mapped_column(String(8), nullable=False)


class Resident(Base):
    __tablename__ = "residents"
    __table_args__ = (
        CheckConstraint("family_members BETWEEN 1 AND 20", name="ck_residents_family_members_range"),
        CheckConstraint("vehicle IN ('none', 'motorbike', 'car')", name="ck_residents_vehicle"),
        CheckConstraint("profile_version >= 1", name="ck_residents_profile_version_positive"),
        Index("ix_residents_site_id", "site_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    apartment: Mapped[str] = mapped_column(String(40))
    building: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    site_id: Mapped[str] = mapped_column(String(60))
    site_name: Mapped[str] = mapped_column(String(120))
    resident_type: Mapped[str] = mapped_column(String(30))
    family_members: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    vehicle: Mapped[str] = mapped_column(String(20), default="none", server_default=text("'none'"))
    needs_internet: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    profile_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    ownership_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'disabled')", name="ck_users_status"),
        UniqueConstraint("auth_subject", name="uq_users_auth_subject"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid4().hex)
    auth_subject: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResidenceMembership(Base):
    __tablename__ = "residence_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('resident', 'manager')", name="ck_memberships_role"),
        CheckConstraint("status IN ('active', 'suspended', 'expired')", name="ck_memberships_status"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["resident_id"], ["residents.id"], name="fk_memberships_resident_id_residents", ondelete="CASCADE"
        ),
        UniqueConstraint("user_id", "resident_id", "site_id", "role", name="uq_memberships_identity_scope"),
        Index("ix_memberships_subject_scope", "user_id", "status", "site_id"),
        Index("ix_memberships_resident_scope", "resident_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    resident_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    site_id: Mapped[str] = mapped_column(String(60), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default=text("'active'"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LocalCredential(Base):
    __tablename__ = "local_credentials"
    __table_args__ = (
        ForeignKeyConstraint(["auth_subject"], ["users.auth_subject"], ondelete="CASCADE"),
        ForeignKeyConstraint(["resident_id"], ["residents.id"], ondelete="CASCADE"),
        UniqueConstraint("creation_key", name="uq_local_credentials_creation_key"),
    )

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    auth_subject: Mapped[str] = mapped_column(String(160), unique=True)
    resident_id: Mapped[str] = mapped_column(String(32), unique=True)
    salt: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(64))
    password_change_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    username_change_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    recovery_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_by: Mapped[str] = mapped_column(String(160))
    creation_key: Mapped[str] = mapped_column(String(100))
    creation_fingerprint: Mapped[str] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CredentialHandover(Base):
    __tablename__ = "credential_handovers"
    __table_args__ = (
        ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["username"], ["local_credentials.username"], ondelete="CASCADE"),
        UniqueConstraint("request_id", name="uq_credential_handovers_request_id"),
    )

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    encrypted_password: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ManagerConversation(Base):
    __tablename__ = "manager_conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    auth_subject: Mapped[str] = mapped_column(ForeignKey("users.auth_subject", ondelete="CASCADE"))
    binding: Mapped[str] = mapped_column(String(64))
    scope_hash: Mapped[str] = mapped_column(String(64))
    reset_generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ManagerExecution(Base):
    __tablename__ = "manager_executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("manager_conversations.id", ondelete="CASCADE"))
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RequestVisibility(Base):
    __tablename__ = "request_visibility"
    __table_args__ = (
        ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="CASCADE"),
        Index("ix_request_visibility_subject", "subject_key"),
    )

    request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), ForeignKey("local_credentials.username", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(254))
    code_hash: Mapped[str] = mapped_column(String(64))
    credential_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class PasswordRecoveryRequest(Base):
    __tablename__ = "password_recovery_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    apartment: Mapped[str] = mapped_column(String(40))
    building: Mapped[str] = mapped_column(String(80))
    contact: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RegulationAcknowledgement(Base):
    __tablename__ = "regulation_acknowledgements"
    __table_args__ = (
        ForeignKeyConstraint(["resident_id"], ["residents.id"], ondelete="CASCADE"),
    )

    resident_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    revision: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    version: Mapped[str] = mapped_column(String(160))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_events_created_at", "created_at"),
        Index("ix_security_audit_events_resource", "resource_type", "resource_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_subject: Mapped[str | None] = mapped_column(String(160), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RevokedAuthSession(Base):
    __tablename__ = "revoked_auth_sessions"
    __table_args__ = (Index("ix_revoked_auth_sessions_revoked_at", "revoked_at"),)

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    auth_subject: Mapped[str] = mapped_column(String(160), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChecklistTask(Base):
    __tablename__ = "checklist_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_checklist_tasks_resident_id_residents",
            ondelete="CASCADE",
        ),
        UniqueConstraint("resident_id", "code", name="uq_checklist_tasks_resident_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    resident_id: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(160))
    required: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    status: Mapped[str] = mapped_column(String(30), default="pending", server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    not_needed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManagementRequest(Base):
    __tablename__ = "management_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'in_review', 'needs_info', 'approved', "
            "'rejected', 'completed', 'cancelled_by_resident')",
            name="ck_management_requests_status",
        ),
        ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_management_requests_resident_id_residents",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "resident_id", name="uq_management_requests_id_resident"),
        UniqueConstraint("idempotency_key", name="uq_management_requests_idempotency_key"),
        Index("ix_management_requests_resident_created", "resident_id", text("created_at DESC"), text("id DESC")),
        Index("ix_management_requests_status_updated", "status", text("updated_at DESC")),
        Index("ix_management_requests_resident_type_status", "resident_id", "request_type", "status"),
        Index(
            "uq_management_requests_active_business_key",
            "resident_id",
            "request_type",
            "business_key",
            unique=True,
            postgresql_where=text("status IN ('submitted', 'in_review', 'needs_info', 'approved')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    resident_id: Mapped[str] = mapped_column(String(32))
    request_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="submitted", server_default=text("'submitted'"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RequestEvent(Base):
    __tablename__ = "request_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"],
            ["management_requests.id"],
            name="fk_request_events_request_id_management_requests",
            ondelete="CASCADE",
        ),
        Index("ix_request_events_request_id_id", "request_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class NotificationReceipt(Base):
    __tablename__ = "notification_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["event_id"], ["request_events.id"], ondelete="CASCADE"),
    )

    subject_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class VehicleDocument(Base):
    __tablename__ = "vehicle_documents"
    __table_args__ = (
        ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="CASCADE"),
        CheckConstraint("octet_length(content) <= 5242880", name="ck_vehicle_document_size"),
    )

    request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    media_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(180))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ParkingReminder(Base):
    __tablename__ = "parking_reminders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"],
            ["management_requests.id"],
            name="fk_parking_reminders_request_id_management_requests",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_parking_reminders_resident_id_residents",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["request_id", "resident_id"],
            ["management_requests.id", "management_requests.resident_id"],
            name="fk_parking_reminders_request_resident_management_requests",
            ondelete="CASCADE",
        ),
        Index("ix_parking_reminders_resident_id", "resident_id"),
    )

    request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    resident_id: Mapped[str] = mapped_column(String(32))
    due_date: Mapped[date] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Store:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
        pool_timeout: int = 30,
        connect_timeout: int = 10,
    ):
        if not database_url.strip():
            raise ValueError("database_url_required")
        try:
            url = make_url(database_url)
        except ArgumentError as exc:
            raise ValueError("invalid_database_url") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError("database_must_be_postgresql")
        self.engine = create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            connect_args={"connect_timeout": connect_timeout},
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        self._active_session: ContextVar[Session | None] = ContextVar(
            f"store_session_{id(self)}", default=None
        )
        self._reset_generation: ContextVar[int | None] = ContextVar(
            f"store_reset_generation_{id(self)}", default=None
        )

    def initialize(self) -> None:
        """Verify PostgreSQL connectivity; Alembic owns schema creation."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def list_locations(self, site_ids: set[str], exclude_resident_id: str | None = None) -> list[dict]:
        with self.session() as session:
            units = session.query(ResidentialUnit, ResidentialSite.name).join(ResidentialSite).filter(ResidentialUnit.site_id.in_(site_ids)).order_by(
                ResidentialUnit.site_id, ResidentialUnit.building, ResidentialUnit.floor, ResidentialUnit.room
            ).all()
            owners = {}
            for resident in session.query(Resident).filter(Resident.site_id.in_(site_ids), Resident.resident_type == "owner"):
                owners.setdefault((resident.site_id, self._location_code(resident.apartment)), []).append((self._location_code(resident.building), resident.id))
            result = {}
            for unit, site_name in units:
                owner_ids = [resident_id for building, resident_id in owners.get((unit.site_id, unit.apartment), []) if not building or building == unit.building]
                result[(unit.site_id, unit.building, unit.apartment)] = {
                    "site_id": unit.site_id, "site_name": site_name, "building": unit.building, "apartment": unit.apartment,
                    "floor": unit.floor, "room": unit.room, "owner_registered": bool(owner_ids),
                    "owner_conflict": any(resident_id != exclude_resident_id for resident_id in owner_ids),
                }
            return sorted(result.values(), key=lambda item: (item["site_id"], item["building"], item["apartment"]))

    @staticmethod
    def _location_code(value: str) -> str:
        return re.sub(r"\s+", "", value or "").upper()

    def _owner_exists(self, session: Session, site_id: str, building: str, apartment: str, exclude_resident_id: str | None = None) -> bool:
        normalized_building = self._location_code(building)
        building_column = func.upper(func.regexp_replace(Resident.building, r"\s+", "", "g"))
        selection = session.query(Resident.id).filter(
            Resident.site_id == site_id, Resident.resident_type == "owner",
            func.upper(func.regexp_replace(Resident.apartment, r"\s+", "", "g")) == self._location_code(apartment),
        )
        if normalized_building:
            selection = selection.filter(or_(building_column == normalized_building, building_column == ""))
        if exclude_resident_id:
            selection = selection.filter(Resident.id != exclude_resident_id)
        return selection.first() is not None

    def owner_exists(self, site_id: str, building: str, apartment: str, exclude_resident_id: str | None = None) -> bool:
        with self.session() as session:
            return self._owner_exists(session, site_id, building, apartment, exclude_resident_id)

    def _require_available_owner(self, session: Session, site_id: str, building: str, apartment: str, exclude_resident_id: str | None = None) -> None:
        location = json.dumps([site_id, self._location_code(apartment)], ensure_ascii=False)
        lock_key = int.from_bytes(hashlib.sha256(("apartment-owner:" + location).encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        if self._owner_exists(session, site_id, building, apartment, exclude_resident_id):
            raise ValueError("apartment_owner_exists")

    def list_sites(self, site_ids: set[str]) -> list[dict]:
        with self.session() as session:
            sites = {row.id: {"site_id": row.id, "site_name": row.name, "source_url": row.source_url} for row in session.query(ResidentialSite).filter(ResidentialSite.id.in_(site_ids))}
            return sorted(sites.values(), key=lambda item: (item["site_name"], item["site_id"]))

    def import_sites(self, items: list[dict]) -> int:
        with self.session() as session:
            for item in items:
                statement = postgresql_insert(ResidentialSite).values(**item)
                session.execute(statement.on_conflict_do_update(index_elements=[ResidentialSite.id], set_={key: getattr(statement.excluded, key) for key in ("name", "source_url", "source_checked_on")}))
                self._seed_residential_units(session, item["id"])
            return len(items)

    @staticmethod
    def _seed_residential_units(session: Session, site_id: str) -> None:
        values = [
            {"site_id": site_id, "building": building, "floor": floor, "room": room, "apartment": f"{building}{floor:02d}{room:02d}"}
            for building in ("A", "B", "C", "D") for floor in range(1, 21) for room in range(1, 11)
        ]
        statement = postgresql_insert(ResidentialUnit).values(values)
        session.execute(statement.on_conflict_do_nothing(index_elements=["site_id", "building", "floor", "room"]))

    def get_local_credential(self, username: str | None = None, creation_key: str | None = None, auth_subject: str | None = None) -> dict | None:
        with self.session() as session:
            query = session.query(LocalCredential)
            filters = {"username": username} if username is not None else {"auth_subject": auth_subject} if auth_subject is not None else {"creation_key": creation_key}
            row = query.filter_by(**filters).one_or_none()
            return {column.name: getattr(row, column.name) for column in LocalCredential.__table__.columns} if row else None

    @staticmethod
    def _username_available(session: Session, username: str) -> bool:
        return not (session.get(LocalCredential, username) or session.query(User.id).filter_by(auth_subject="local:" + username).first())

    def username_available(self, username: str) -> bool:
        with self.session() as session:
            return self._username_available(session, username)

    @staticmethod
    def _lock_username(session: Session, username: str) -> None:
        session.execute(text("SELECT pg_advisory_xact_lock(1296649484, hashtext(:username))"), {"username": username})

    def set_initial_credentials(self, subject: str, expected_hash: str, username: str, salt: str, password_hash: str) -> None:
        with self.session() as session:
            credential = session.query(LocalCredential).filter_by(auth_subject=subject).with_for_update().one_or_none()
            if not credential or credential.password_hash != expected_hash:
                raise ValueError("password_changed_concurrently")
            if not credential.password_change_required and not credential.username_change_required:
                raise ValueError("initial_password_not_required")
            previous_username = credential.username
            if credential.username_change_required:
                if username == previous_username:
                    raise ValueError("username_change_required")
                self._lock_username(session, username)
                if not self._username_available(session, username):
                    raise ValueError("username_unavailable")
            elif username != previous_username:
                raise ValueError("username_change_not_allowed")
            session.query(CredentialHandover).filter_by(username=previous_username).delete(synchronize_session=False)
            session.query(AuthChallenge).filter_by(username=previous_username).delete(synchronize_session=False)
            session.query(PasswordRecoveryRequest).filter_by(username=previous_username).update({"username": username}, synchronize_session=False)
            session.flush()
            credential.username = username
            credential.salt = salt
            credential.password_hash = password_hash
            credential.password_change_required = False
            credential.username_change_required = False
            session.flush()

    def change_local_password(self, username: str, expected_hash: str, salt: str, password_hash: str) -> None:
        with self.session() as session:
            credential = session.query(LocalCredential).filter_by(username=username).with_for_update().one_or_none()
            if not credential or credential.password_hash != expected_hash:
                raise ValueError("password_changed_concurrently")
            credential.salt = salt
            credential.password_hash = password_hash
            credential.password_change_required = False
            session.query(CredentialHandover).filter_by(username=username).delete(synchronize_session=False)

    @staticmethod
    def visibility_key(subject: str, list_name: str) -> str:
        return hashlib.sha256((subject + "\0" + list_name).encode()).hexdigest()

    @staticmethod
    def visible_requests(subject: str, list_name: str):
        return ~ManagementRequest.id.in_(
            select(RequestVisibility.request_id).where(
                RequestVisibility.subject_key == Store.visibility_key(subject, list_name)
            )
        )

    def save_handover(self, username: str, password: str, key: str, request_id: str | None = None) -> None:
        try:
            encrypted = Fernet(key.encode("ascii")).encrypt(json.dumps({"username": username, "password": password}).encode()).decode()
        except (ValueError, UnicodeError):
            raise ValueError("settings_encryption_key_invalid") from None
        with self.session() as session:
            credential = session.query(LocalCredential).filter_by(username=username).with_for_update().one_or_none()
            if not credential or not credential.password_change_required:
                raise ValueError("temporary_password_unavailable")
            row = session.get(CredentialHandover, username)
            if row:
                row.encrypted_password = encrypted
                row.issued_at = now_utc()
                if request_id is not None:
                    row.request_id = request_id
            else:
                session.add(CredentialHandover(username=username, encrypted_password=encrypted, request_id=request_id))

    def read_handover(self, username: str, key: str) -> dict:
        with self.session() as session:
            credential = session.query(LocalCredential).filter_by(username=username).with_for_update().one_or_none()
            if not credential:
                raise ValueError("account_not_found")
            row = session.get(CredentialHandover, username)
            result = {"username": username, "password_change_required": credential.password_change_required,
                      "temporary_password_available": bool(row and credential.password_change_required)}
            if not result["temporary_password_available"]:
                return result
            try:
                payload = json.loads(Fernet(key.encode("ascii")).decrypt(row.encrypted_password.encode()))
                if payload["username"] != username:
                    raise ValueError("handover_identity_mismatch")
                result["temporary_password"] = payload["password"]
            except (InvalidToken, UnicodeError, KeyError, ValueError):
                raise ValueError("handover_unavailable") from None
            return result

    def save_vehicle_document(self, request_id: str, content: bytes, media_type: str, filename: str) -> None:
        with self.session() as session:
            session.query(ManagementRequest.id).filter_by(id=request_id).with_for_update().one()
            existing = session.get(VehicleDocument, request_id)
            if existing:
                if existing.sha256 != hashlib.sha256(content).hexdigest():
                    raise ValueError("vehicle_document_conflict")
                return
            session.add(VehicleDocument(request_id=request_id, content=content, media_type=media_type, filename=filename, sha256=hashlib.sha256(content).hexdigest()))

    def replace_vehicle_document(self, resident_id: str, request_id: str, content: bytes, media_type: str) -> None:
        with self.session() as session:
            request = session.query(ManagementRequest).filter_by(id=request_id).with_for_update().one_or_none()
            if not request or request.resident_id != resident_id or request.request_type != "vehicle_registration":
                raise ValueError("request_not_found")
            if request.status not in {"submitted", "in_review", "needs_info"}:
                raise ValueError("request_not_editable")
            document = session.get(VehicleDocument, request_id)
            if document is None:
                document = VehicleDocument(request_id=request_id)
                session.add(document)
            document.content = content
            document.media_type = media_type
            document.filename = "vehicle-registration.jpg"
            document.sha256 = hashlib.sha256(content).hexdigest()

    def get_vehicle_document(self, request_id: str) -> dict | None:
        with self.session() as session:
            row = session.get(VehicleDocument, request_id)
            return {"content": row.content, "media_type": row.media_type, "filename": row.filename, "sha256": row.sha256} if row else None

    def vehicle_document_ids(self, request_ids: list[str]) -> set[str]:
        with self.session() as session:
            return {row.request_id for row in session.query(VehicleDocument.request_id).filter(VehicleDocument.request_id.in_(request_ids))}

    def search_requests(self, *, resident_id: str | None = None, site_ids: set[str] | None = None, query: str = "", request_type: str | None = None, status: str | None = None, date_from: date | None = None, date_to: date | None = None, offset: int = 0, limit: int = 20, management_updates_only: bool = False, subject: str | None = None, list_name: str = "tickets", request_id: str | None = None, terminal_statuses: list[str] | None = None, group_by: str | None = None) -> dict:
        if date_from and date_to and date_from > date_to:
            raise ValueError("invalid_date_range")
        with self.session() as session:
            selection = session.query(ManagementRequest).join(Resident, Resident.id == ManagementRequest.resident_id)
            if subject is not None:
                selection = selection.filter(self.visible_requests(subject, list_name))
            if request_id is not None:
                selection = selection.filter(ManagementRequest.id == request_id)
            ordering = (ManagementRequest.created_at.desc(), ManagementRequest.id.desc())
            if management_updates_only:
                updates = session.query(RequestEvent.request_id, func.max(RequestEvent.id).label("latest_event_id")).filter(
                    RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"})
                ).group_by(RequestEvent.request_id).subquery()
                selection = selection.join(updates, updates.c.request_id == ManagementRequest.id)
                ordering = (updates.c.latest_event_id.desc(), ManagementRequest.id.desc())
            if resident_id:
                selection = selection.filter(ManagementRequest.resident_id == resident_id)
            if site_ids is not None:
                selection = selection.filter(Resident.site_id.in_(site_ids))
            if request_type:
                selection = selection.filter(ManagementRequest.request_type == request_type)
            if query.strip():
                pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                selection = selection.filter(or_(*(column.ilike(pattern, escape="\\") for column in (ManagementRequest.id, Resident.id, Resident.full_name, Resident.apartment, cast(ManagementRequest.payload_json, Text)))))
            local_zone = timezone(timedelta(hours=7))
            if date_from:
                selection = selection.filter(ManagementRequest.created_at >= datetime.combine(date_from, time.min, local_zone))
            if date_to:
                selection = selection.filter(ManagementRequest.created_at < datetime.combine(date_to + timedelta(days=1), time.min, local_zone))
            counts = dict(selection.with_entities(ManagementRequest.status, func.count()).group_by(ManagementRequest.status).all())
            if status in {"active", "finished", "closed"}:
                statuses = ACTIVE_REQUEST_STATUSES if status == "active" else {"completed", "rejected", "cancelled_by_resident"}
                selection = selection.filter(ManagementRequest.status.in_(statuses))
            elif status:
                selection = selection.filter(ManagementRequest.status == status)
            if terminal_statuses is not None:
                selection = selection.filter(ManagementRequest.status.in_(terminal_statuses))
            group_counts = []
            if group_by in {"type", "status"}:
                grouping = ManagementRequest.request_type if group_by == "type" else ManagementRequest.status
                group_counts = [{"site_id": site, "value": value, "total": count} for site, value, count in selection.with_entities(
                    Resident.site_id, grouping, func.count(ManagementRequest.id),
                ).group_by(Resident.site_id, grouping).all()]
                ordering = (Resident.site_id, grouping, *ordering)
            total = selection.count()
            rows = selection.order_by(*ordering).offset(offset).limit(limit).all()
            items = [self._request_dict(row) for row in rows]
            return {"items": items, "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total, "counts_by_status": counts, "group_counts": group_counts}

    def create_resident_account(self, values: dict, *, actor: str, salt: str, password_hash: str, fingerprint: str) -> dict:
        with self.session() as session:
            self._lock_username(session, values["username"])
            if not self._username_available(session, values["username"]):
                raise ValueError("username_unavailable")
            if values["resident_type"] == "owner":
                self._require_available_owner(session, values["site_id"], values["building"], values["apartment"])
            resident_id = "R" + uuid4().hex[:20].upper()
            resident = Resident(
                id=resident_id, full_name=values["full_name"], site_id=values["site_id"],
                site_name=values["site_name"], building=values["building"], apartment=values["apartment"],
                resident_type=values["resident_type"],
            )
            session.add(resident)
            session.flush()
            self.provision_membership(auth_subject="local:" + values["username"], role="resident", site_id=resident.site_id, resident_id=resident_id)
            credential = LocalCredential(
                username=values["username"], auth_subject="local:" + values["username"], resident_id=resident_id,
                salt=salt, password_hash=password_hash, created_by=actor,
                creation_key=values["idempotency_key"], creation_fingerprint=fingerprint,
            )
            session.add(credential)
            session.flush()
            return {"username": credential.username, "profile": self._resident_dict(resident), "confirmed_at": credential.confirmed_at.isoformat()}

    def list_resident_accounts(self, site_ids: set[str]) -> list[dict]:
        with self.session() as session:
            rows = session.query(LocalCredential, Resident, User, ResidenceMembership).join(Resident, Resident.id == LocalCredential.resident_id).join(
                User, User.auth_subject == LocalCredential.auth_subject,
            ).outerjoin(ResidenceMembership, (ResidenceMembership.user_id == User.id) & (ResidenceMembership.resident_id == Resident.id)
                        & (ResidenceMembership.site_id == Resident.site_id) & (ResidenceMembership.role == "resident")).filter(
                Resident.site_id.in_(site_ids)
            ).order_by(LocalCredential.confirmed_at.desc()).all()
            handovers = {row.username for row in session.query(CredentialHandover.username)}
            return [{"username": credential.username, "profile": self._resident_dict(resident), "confirmed_at": credential.confirmed_at.isoformat(),
                     "access_status": user.status if user.status != "active" else membership.status if membership else "inactive",
                     "password_change_required": credential.password_change_required,
                     "temporary_password_available": credential.password_change_required and credential.username in handovers} for credential, resident, user, membership in rows]

    def document_acknowledgements(self, resident_id: str) -> list[dict]:
        with self.session() as session:
            return [{"source_id": row.source_id, "revision": row.revision, "acknowledged_at": row.acknowledged_at.isoformat()} for row in session.query(RegulationAcknowledgement).filter_by(resident_id=resident_id)]

    def acknowledge_document(self, resident_id: str, document: dict) -> None:
        with self.session() as session:
            session.execute(postgresql_insert(RegulationAcknowledgement).values(
                resident_id=resident_id, source_id=document["source_id"], revision=document["revision"],
                title=document["title"], version=document.get("version", ""), acknowledged_at=now_utc(),
            ).on_conflict_do_nothing())

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """Run multiple Store calls in one commit-or-rollback unit of work."""
        if self._active_session.get() is not None:
            yield self
            return
        with self.session_factory.begin() as session:
            self._validate_reset_generation(session)
            token = self._active_session.set(session)
            try:
                yield self
            finally:
                self._active_session.reset(token)

    @contextmanager
    def session(self) -> Iterator[Session]:
        active_session = self._active_session.get()
        if active_session is not None:
            yield active_session
            return
        with self.session_factory.begin() as session:
            self._validate_reset_generation(session)
            yield session

    @staticmethod
    def _login_reset_generation(session: Session) -> int:
        return session.scalar(text(
            "SELECT generation FROM login_reset_baselines WHERE resident_id = :resident_id"
        ), {"resident_id": DEMO_RESIDENT_ID}) or 0

    def login_reset_generation(self) -> int:
        with self.session() as session:
            return self._login_reset_generation(session)

    @contextmanager
    def reset_scope(self) -> Iterator[None]:
        token = self._reset_generation.set(self.login_reset_generation())
        try:
            yield
        finally:
            self._reset_generation.reset(token)

    def _validate_reset_generation(self, session: Session) -> None:
        generation = self._reset_generation.get()
        if generation is None:
            return
        session.execute(text("SELECT pg_advisory_xact_lock_shared(:lock_key)"),
                        {"lock_key": DEMO_ADVISORY_LOCK_KEY})
        if self._login_reset_generation(session) != generation:
            raise ResetInvalidatedError()

    def seed_demo(self) -> None:
        with self.session() as session:
            self._acquire_demo_lock(session)
            self._seed_demo(session)

    @staticmethod
    def _seed_demo(session: Session, *, reset_profile: bool = False) -> None:
        session.execute(
            postgresql_insert(ResidentialSite).values(id=DEMO_SITE_ID, name="Khu Mặt Trời")
            .on_conflict_do_nothing(index_elements=[ResidentialSite.id])
        )
        Store._seed_residential_units(session, DEMO_SITE_ID)
        resident = postgresql_insert(Resident).values(
            id=DEMO_RESIDENT_ID,
            full_name="Nguyen Minh An",
            apartment="A1205",
            building="A",
            site_id=DEMO_SITE_ID,
            site_name="Khu Mặt Trời",
            resident_type="owner",
            family_members=1,
            vehicle="car",
            needs_internet=True,
            profile_version=1,
            updated_at=now_utc(),
        )
        if reset_profile:
            resident = resident.on_conflict_do_update(
                index_elements=[Resident.id],
                set_={column.name: resident.excluded[column.name] for column in Resident.__table__.columns if column.name != "id"},
            )
        else:
            resident = resident.on_conflict_do_nothing(index_elements=[Resident.id])
        session.execute(resident)
        # Synthetic identities are only used by the explicit development/test
        # demo mode.  Production accounts must be provisioned by the selected
        # identity provider and mapped to memberships separately.
        demo_users = (
            ("demo-resident-r001", "demo-resident-r001"),
            ("demo-manager-sunrise", "demo-manager-sunrise"),
        )
        for user_id, subject in demo_users:
            user = session.query(User).filter_by(auth_subject=subject).one_or_none()
            if not user:
                user = User(id=user_id, auth_subject=subject, status="active")
                session.add(user)
                session.flush()
        resident_user = session.query(User).filter_by(auth_subject="demo-resident-r001").one()
        manager_user = session.query(User).filter_by(auth_subject="demo-manager-sunrise").one()
        if not session.query(ResidenceMembership).filter_by(
            user_id=resident_user.id, resident_id=DEMO_RESIDENT_ID, site_id=DEMO_SITE_ID, role="resident"
        ).one_or_none():
            session.add(
                ResidenceMembership(
                    user_id=resident_user.id,
                    resident_id=DEMO_RESIDENT_ID,
                    site_id=DEMO_SITE_ID,
                    role="resident",
                )
            )
        if not session.query(ResidenceMembership).filter_by(
            user_id=manager_user.id, resident_id=None, site_id=DEMO_SITE_ID, role="manager"
        ).one_or_none():
            session.add(
                ResidenceMembership(
                    user_id=manager_user.id,
                    resident_id=None,
                    site_id=DEMO_SITE_ID,
                    role="manager",
                )
            )

    def reset_demo(self) -> None:
        """Reset only the stable synthetic resident and its dependent records."""
        with self.session() as session:
            self._acquire_demo_lock(session)
            request_ids = session.query(ManagementRequest.id).filter_by(
                resident_id=DEMO_RESIDENT_ID
            )
            session.query(ParkingReminder).filter_by(resident_id=DEMO_RESIDENT_ID).delete(
                synchronize_session=False
            )
            session.query(RequestEvent).filter(RequestEvent.request_id.in_(request_ids)).delete(
                synchronize_session=False
            )
            session.query(ManagementRequest).filter_by(resident_id=DEMO_RESIDENT_ID).delete(
                synchronize_session=False
            )
            session.query(RegulationAcknowledgement).filter_by(resident_id=DEMO_RESIDENT_ID).delete(synchronize_session=False)
            session.query(ChecklistTask).filter_by(resident_id=DEMO_RESIDENT_ID).delete(
                synchronize_session=False
            )
            self._seed_demo(session, reset_profile=True)

    def reset_all_demo(self, preserved_subjects: tuple[str, ...] = ()) -> None:
        """Clear demo business data, retaining bootstrap identities and sources."""
        keep_subjects = {"demo-resident-r001", "demo-manager-sunrise", *preserved_subjects}
        with self.session() as session:
            self._acquire_demo_lock(session)
            self.clear_application_data(tuple(keep_subjects))
            self._seed_demo(session, reset_profile=True)

    def clear_application_data(self, preserved_subjects: tuple[str, ...]) -> tuple[str, ...]:
        with self.session() as session:
            self._acquire_demo_lock(session)
            session.query(ManagerConversation).delete()
            resident_ids = {DEMO_RESIDENT_ID, *(
                resident_id for (resident_id,) in session.query(LocalCredential.resident_id).filter(
                    LocalCredential.auth_subject.in_(preserved_subjects)
                )
            )}
            for model in (NotificationReceipt, VehicleDocument, ParkingReminder, RequestEvent,
                          ManagementRequest, RegulationAcknowledgement, ChecklistTask, AuthChallenge, PasswordRecoveryRequest):
                session.query(model).delete(synchronize_session=False)
            session.query(LocalCredential).filter(LocalCredential.auth_subject.not_in(preserved_subjects)).delete(
                synchronize_session=False
            )
            session.query(User).filter(User.auth_subject.not_in(preserved_subjects)).delete(
                synchronize_session=False
            )
            session.query(User).update({"status": "active"}, synchronize_session=False)
            session.query(Resident).filter(Resident.id.not_in(resident_ids)).delete(
                synchronize_session=False
            )
            return tuple(sorted(resident_ids))

    @staticmethod
    def _acquire_demo_lock(session: Session) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": DEMO_ADVISORY_LOCK_KEY},
        )

    def get_resident(self, resident_id: str) -> dict[str, Any] | None:
        with self.session() as session:
            resident = session.get(Resident, resident_id)
            if not resident:
                return None
            result = self._resident_dict(resident)
            result["family_members"] = self._occupant_count(session, resident)
            return result

    def get_memberships_for_subject(self, auth_subject: str) -> list[dict[str, Any]]:
        """Return active, time-valid memberships for a verified subject."""
        now = now_utc()
        with self.session() as session:
            rows = (
                session.query(User, ResidenceMembership)
                .join(ResidenceMembership, ResidenceMembership.user_id == User.id)
                .filter(
                    User.auth_subject == auth_subject,
                    User.status == "active",
                    ResidenceMembership.status == "active",
                    ResidenceMembership.valid_from <= now,
                    (ResidenceMembership.valid_to.is_(None) | (ResidenceMembership.valid_to >= now)),
                )
                .all()
            )
            return [
                {
                    "user_id": user.id,
                    "auth_subject": user.auth_subject,
                    "resident_id": membership.resident_id,
                    "site_id": membership.site_id,
                    "role": membership.role,
                    "status": membership.status,
                }
                for user, membership in rows
            ]

    def provision_membership(
        self,
        *,
        auth_subject: str,
        role: str,
        site_id: str,
        resident_id: str | None = None,
    ) -> dict[str, Any]:
        """Provision an operator-approved identity mapping outside the HTTP API."""
        role = role.strip().casefold()
        auth_subject = auth_subject.strip()
        site_id = site_id.strip()
        if role not in {"resident", "manager"} or not auth_subject or not site_id:
            raise ValueError("invalid_membership")
        if role == "resident" and not resident_id:
            raise ValueError("resident_membership_requires_resident_id")
        if role == "manager" and resident_id:
            raise ValueError("manager_membership_cannot_bind_resident")
        with self.session() as session:
            if resident_id:
                resident = session.get(Resident, resident_id)
                if not resident:
                    raise ValueError("resident_not_found")
                if resident.site_id != site_id:
                    raise ValueError("resident_site_mismatch")
            user = session.query(User).filter_by(auth_subject=auth_subject).one_or_none()
            if not user:
                user = User(auth_subject=auth_subject, status="active")
                session.add(user)
                session.flush()
            elif user.status != "active":
                user.status = "active"
            membership = (
                session.query(ResidenceMembership)
                .filter_by(user_id=user.id, resident_id=resident_id, site_id=site_id, role=role)
                .one_or_none()
            )
            if not membership:
                membership = ResidenceMembership(
                    user_id=user.id,
                    resident_id=resident_id,
                    site_id=site_id,
                    role=role,
                    status="active",
                )
                session.add(membership)
                session.flush()
            else:
                membership.status = "active"
            return {
                "user_id": user.id,
                "auth_subject": user.auth_subject,
                "membership_id": membership.id,
                "role": membership.role,
                "site_id": membership.site_id,
                "resident_id": membership.resident_id,
            }

    def ensure_local_membership(
        self, *, auth_subject: str, role: str, site_id: str, resident_id: str | None = None,
        additional_site_ids: tuple[str, ...] = (),
    ) -> None:
        if role != "manager" and additional_site_ids:
            raise ValueError("resident_must_have_one_site")
        site_ids = {site_id, *additional_site_ids}
        with self.session() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(1296649489)"))
            user = session.query(User).filter_by(auth_subject=auth_subject).one_or_none()
            if user is None:
                self.provision_membership(
                    auth_subject=auth_subject, role=role, site_id=site_id, resident_id=resident_id,
                )
                user = session.query(User).filter_by(auth_subject=auth_subject).one()
            if resident_id:
                resident = session.get(Resident, resident_id)
                if resident is None or resident.site_id != site_id:
                    raise ValueError("local_auth_resident_scope_invalid")
            memberships = session.query(ResidenceMembership).filter_by(user_id=user.id).with_for_update().all()
            matching = [membership for membership in memberships if membership.role == role and membership.site_id in site_ids and membership.resident_id == resident_id]
            for assigned_site in sorted(site_ids - {membership.site_id for membership in matching}):
                session.add(ResidenceMembership(user_id=user.id, role=role, site_id=assigned_site, resident_id=resident_id, status="active"))
            for membership in memberships:
                if membership not in matching and membership.status == "active":
                    membership.status = "expired"
                    membership.valid_to = now_utc()

    def record_security_event(
        self,
        *,
        actor_subject: str | None,
        user_id: str | None,
        role: str | None,
        site_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        correlation_id: str,
    ) -> None:
        """Append a redacted authorization event; never accept raw payloads."""
        with self.session() as session:
            session.add(
                SecurityAuditEvent(
                    actor_subject=actor_subject,
                    user_id=user_id,
                    role=role,
                    site_id=site_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome=outcome,
                    correlation_id=correlation_id,
                )
            )

    def revoke_auth_session(
        self, session_id: str, auth_subject: str, expires_at: datetime | None = None
    ) -> None:
        """Persist a deny-list entry for a signed local session."""
        with self.session() as session:
            existing = session.get(RevokedAuthSession, session_id)
            if existing:
                existing.auth_subject = auth_subject
                existing.expires_at = expires_at
                return
            session.add(
                RevokedAuthSession(
                    session_id=session_id,
                    auth_subject=auth_subject,
                    expires_at=expires_at,
                )
            )

    def is_auth_session_revoked(self, session_id: str, auth_subject: str) -> bool:
        with self.session() as session:
            row = session.get(RevokedAuthSession, session_id)
            return bool(row and row.auth_subject == auth_subject)

    def update_resident(self, resident_id: str, values: dict[str, Any], *, lock_assigned_identity: bool = False) -> dict[str, Any]:
        with self.session() as session:
            resident = session.get(Resident, resident_id, with_for_update=True)
            if not resident:
                raise ValueError("resident_not_found")
            identity_fields = {"full_name", "resident_type", "site_id", "site_name", "building", "apartment"}
            if lock_assigned_identity and resident.resident_type != "owner" and values.get("family_members", resident.family_members) != resident.family_members:
                raise ValueError("owner_role_required")
            if (resident.ownership_confirmed_at or lock_assigned_identity) and any(key in values and values[key] != getattr(resident, key) for key in identity_fields):
                raise ValueError("ownership_information_locked")
            if values.get("resident_type", resident.resident_type) == "owner":
                self._require_available_owner(session, values.get("site_id", resident.site_id), values.get("building", resident.building), values.get("apartment", resident.apartment), resident_id)
            for key, value in values.items():
                setattr(resident, key, value)
            resident.profile_version += 1
            resident.updated_at = now_utc()
            session.flush()
            result = self._resident_dict(resident)
            result["family_members"] = self._occupant_count(session, resident)
            return result

    def confirm_ownership(self, resident_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.session() as session:
            resident = session.get(Resident, resident_id, with_for_update=True)
            if not resident:
                raise ValueError("resident_not_found")
            identity = {key: values[key].strip() for key in ("full_name", "building", "apartment")}
            if len(identity["full_name"]) < 2 or not identity["apartment"]:
                raise ValueError("ownership_information_invalid")
            if any(getattr(resident, key) != value for key, value in identity.items()):
                raise ValueError("ownership_information_locked")
            if resident.ownership_confirmed_at:
                return self._resident_dict(resident)
            if resident.profile_version != values["expected_profile_version"]:
                raise ValueError("ownership_profile_changed")
            if resident.resident_type == "owner":
                self._require_available_owner(session, resident.site_id, identity["building"], identity["apartment"], resident_id)
            resident.ownership_confirmed_at = now_utc()
            resident.profile_version += 1
            resident.updated_at = now_utc()
            session.flush()
            return self._resident_dict(resident)

    def household_records(self, resident_id: str) -> tuple[list[dict], list[dict]]:
        with self.session() as session:
            resident = session.get(Resident, resident_id)
            if not resident:
                raise ValueError("resident_not_found")
            members = session.query(Resident).filter(
                Resident.site_id == resident.site_id,
                func.regexp_replace(func.upper(Resident.apartment), r"\s+", "", "g") == self._location_code(resident.apartment),
                func.regexp_replace(func.upper(Resident.building), r"\s+", "", "g") == self._location_code(resident.building),
                or_(Resident.ownership_confirmed_at.is_not(None), Resident.id == resident_id),
            ).all()
            requests = session.query(ManagementRequest).filter(
                ManagementRequest.resident_id.in_([member.id for member in members])
            ).order_by(ManagementRequest.created_at.desc(), ManagementRequest.id.desc()).all()
            stay_ids = [item.id for item in requests if item.request_type == "temporary_residency"]
            confirmations = dict(session.query(RequestEvent.request_id, func.min(RequestEvent.created_at)).filter(
                RequestEvent.request_id.in_(stay_ids), RequestEvent.status.in_({"approved", "completed"}),
            ).group_by(RequestEvent.request_id).all()) if stay_ids else {}
            request_data = []
            for item in requests:
                data = self._request_dict(item)
                if item.id in confirmations:
                    data["residency_confirmed_at"] = confirmations[item.id].isoformat()
                request_data.append(data)
            return [self._resident_dict(member) for member in members], request_data

    def account_registration_member(self, resident_id: str, source_request_id: str) -> dict:
        with self.session() as session:
            owner = session.get(Resident, resident_id)
            if not owner or owner.resident_type != "owner":
                raise ValueError("owner_role_required")
            source = session.query(ManagementRequest).filter_by(id=source_request_id).with_for_update().one_or_none()
            if not source or source.request_type != "temporary_residency" or source.status not in {"approved", "completed"} or source.payload_json.get("hidden_from_household"):
                raise ValueError("household_member_not_eligible")
            person = session.get(Resident, source.resident_id)
            if not person or person.site_id != owner.site_id or any(self._location_code(getattr(person, key)) != self._location_code(getattr(owner, key)) for key in ("building", "apartment")):
                raise ValueError("household_member_not_eligible")
            payload = source.payload_json
            role = payload.get("resident_type", "family_member")
            if role not in {"tenant", "family_member"} or not payload.get("person_name") or payload.get("app_account_resident_id"):
                raise ValueError("household_member_not_eligible")
            exists = session.query(LocalCredential).join(Resident, Resident.id == LocalCredential.resident_id).filter(
                Resident.site_id == owner.site_id,
                func.regexp_replace(func.upper(Resident.building), r"\s+", "", "g") == self._location_code(owner.building),
                func.regexp_replace(func.upper(Resident.apartment), r"\s+", "", "g") == self._location_code(owner.apartment),
                func.lower(Resident.full_name) == payload["person_name"].strip().lower(),
            ).first()
            citizen_id = payload.get("citizen_id")
            if citizen_id:
                issued_identity = session.query(ManagementRequest.id).join(Resident, Resident.id == ManagementRequest.resident_id).filter(
                    Resident.site_id == owner.site_id,
                    ManagementRequest.request_type == "temporary_residency",
                    ManagementRequest.payload_json["citizen_id"].astext == citizen_id,
                    ManagementRequest.payload_json["app_account_resident_id"].astext.is_not(None),
                ).first()
                if issued_identity:
                    raise ValueError("household_member_not_eligible")
            if exists or payload["person_name"].strip().casefold() == owner.full_name.strip().casefold():
                raise ValueError("household_member_not_eligible")
            return {"household_member_id": source.id, "person_name": payload["person_name"], "resident_type": role,
                    "site_id": owner.site_id, "site_name": owner.site_name, "building": owner.building, "apartment": owner.apartment}

    def issue_requested_account(self, request_id: str, values: dict, create_account, *, temporary_password: str | None = None, encryption_key: str = "") -> dict:
        with self.transaction(), self.session() as session:
            item = session.query(ManagementRequest).filter_by(id=request_id).with_for_update().one_or_none()
            if not item or item.request_type != "account_registration" or item.status not in {"approved", "completed"}:
                raise ValueError("account_request_not_approved")
            existing_id = item.payload_json.get("app_account_resident_id")
            if existing_id:
                credential = session.query(LocalCredential).filter_by(resident_id=existing_id).one_or_none()
                resident = session.get(Resident, existing_id)
                if not credential or not resident:
                    raise ValueError("household_account_already_issued")
                return {"username": credential.username, "profile": self._resident_dict(resident), "confirmed_at": credential.confirmed_at.isoformat(), "duplicate": True}
            member = self.account_registration_member(item.resident_id, item.payload_json.get("household_member_id", ""))
            expected = {"full_name": member["person_name"], **{key: member[key] for key in ("resident_type", "site_id", "building", "apartment")}}
            if any(values[key].strip() != expected[key].strip() for key in expected):
                raise ValueError("account_request_identity_mismatch")
            result = create_account(values)
            account_id = result["profile"]["resident_id"]
            source = session.get(ManagementRequest, member["household_member_id"])
            source.payload_json = {**source.payload_json, "app_account_resident_id": account_id}
            item.payload_json = {**item.payload_json, **member, "app_account_resident_id": account_id}
            self.decide_request(item.id, "completed", "BQL đã cấp tài khoản cư dân phụ thuộc và bàn giao riêng cho cư dân.")
            if temporary_password:
                if not encryption_key:
                    raise ValueError("settings_encryption_key_required")
                self.save_handover(result["username"], temporary_password, encryption_key, request_id=item.id)
            return result

    def hide_request(self, request_id: str, subject: str, list_name: str) -> dict[str, Any]:
        key = self.visibility_key(subject, list_name)
        with self.session() as session:
            item = session.query(ManagementRequest).filter_by(id=request_id).with_for_update().one_or_none()
            if not item:
                raise ValueError("request_not_found")
            if item.status not in {"completed", "rejected", "cancelled_by_resident"}:
                raise ValueError("request_not_terminal")
            session.merge(RequestVisibility(request_id=request_id, subject_key=key, hidden_at=now_utc()))
            return {"request_id": request_id, "hidden": True}

    def hide_requests(self, request_ids: list[str], subject: str, list_name: str) -> dict[str, Any]:
        hidden = 0
        for request_id in dict.fromkeys(request_ids):
            self.hide_request(request_id, subject, list_name)
            hidden += 1
        return {"hidden": hidden}


    @staticmethod
    def _occupant_count(session: Session, resident: Resident) -> int:
        approved = (
            session.query(ManagementRequest)
            .filter(
                ManagementRequest.resident_id == resident.id,
                ManagementRequest.request_type == "temporary_residency",
                ManagementRequest.status.in_({"approved", "completed"}),
            )
            .all()
        )
        people = set()
        for request in approved:
            payload = request.payload_json
            if payload.get("hidden_from_household"):
                continue
            identity = str(payload.get("citizen_id") or payload.get("person_name") or "").strip().casefold()
            if identity:
                people.add(identity)
        return resident.family_members + len(people)

    def replace_active_checklist(self, resident_id: str, items: list[dict[str, Any]]) -> list[dict]:
        with self.transaction(), self.session() as session:
            session.query(Resident).filter_by(id=resident_id).with_for_update().one()
            existing = {
                task.code: task for task in session.query(ChecklistTask).filter_by(resident_id=resident_id).all()
            }
            selected = {item["code"] for item in items}
            for task in existing.values():
                if task.code not in selected:
                    task.active = False
                    task.updated_at = now_utc()
            for item in items:
                task = existing.get(item["code"])
                if task:
                    if any(getattr(task, key) != value for key, value in {**item, "active": True}.items()):
                        task.title = item["title"]
                        task.required = item["required"]
                        task.reason = item["reason"]
                        task.active = True
                        task.updated_at = now_utc()
                else:
                    session.add(ChecklistTask(resident_id=resident_id, **item))
            session.flush()
            self._sync_request_tasks(session, resident_id)
            session.flush()
            return self.list_tasks(resident_id)

    def list_tasks(self, resident_id: str) -> list[dict[str, Any]]:
        with self.session() as session:
            resident = session.get(Resident, resident_id)
            tasks = (
                session.query(ChecklistTask)
                .filter_by(resident_id=resident_id, active=True)
                .order_by(ChecklistTask.id)
                .all()
            )
            requests = (
                session.query(ManagementRequest)
                .filter_by(resident_id=resident_id)
                .order_by(ManagementRequest.created_at.desc(), ManagementRequest.id.desc())
                .all()
            )
            latest = {}
            for request in requests:
                latest.setdefault(request.request_type, request)
            completed_stay = next((request for request in requests if request.request_type == "temporary_residency" and request.status == "completed"), None)
            result = []
            for task in tasks:
                item = self._task_dict(task)
                item["request_id"] = None
                if task.code == "verify_residency" and resident:
                    item["title"] = "Xác thực quyền sở hữu căn hộ" if resident.resident_type == "owner" else "Xác nhận thông tin cư trú"
                    item["status"] = "completed" if resident.ownership_confirmed_at else "pending"
                    item["updated_at"] = (resident.ownership_confirmed_at or resident.updated_at).isoformat()
                elif task.code in REQUEST_TASK_TYPES:
                    related = completed_stay if task.code == "temporary_residency" and completed_stay else latest.get(task.code)
                    not_needed = task.code in NO_NEED_TASK_CODES and task.not_needed_at and (not related or related.updated_at <= task.not_needed_at)
                    item["completion_reason"] = "not_needed" if not_needed else None
                    item["status"] = "completed" if not_needed else related.status if related else "pending"
                    item["request_id"] = related.id if related and not not_needed else None
                    item["updated_at"] = (task.not_needed_at if not_needed else related.updated_at if related else task.updated_at).isoformat()
                    has_active_request = any(request.request_type == task.code and request.status in ACTIVE_REQUEST_STATUSES for request in requests)
                    item["can_mark_not_needed"] = task.code in NO_NEED_TASK_CODES and item["status"] != "completed" and not has_active_request
                    if task.code == "temporary_residency" and resident and resident.resident_type != "owner":
                        item["can_mark_not_needed"] = False
                item["coming_soon"] = task.code in COMING_SOON_TASK_CODES
                result.append(item)
            return order_checklist_tasks(sorted(result, key=checklist_sort_key))

    def mark_task_not_needed(self, resident_id: str, code: str, expected_updated_at: datetime, expected_profile_version: int) -> dict[str, Any]:
        if code not in NO_NEED_TASK_CODES:
            raise ValueError("task_no_need_not_allowed")
        with self.transaction():
            with self.session() as session:
                resident = session.query(Resident).filter_by(id=resident_id).with_for_update().one_or_none()
                if not resident:
                    raise ValueError("resident_not_found")
                if not resident.ownership_confirmed_at:
                    raise ValueError("ownership_confirmation_required")
                if code == "temporary_residency" and resident.resident_type != "owner":
                    raise ValueError("owner_role_required")
                if resident.profile_version != expected_profile_version:
                    raise ValueError("checklist_changed")
                task = session.query(ChecklistTask).filter_by(resident_id=resident_id, code=code, active=True).with_for_update().one_or_none()
                if not task:
                    raise ValueError("task_not_found")
                if session.query(ManagementRequest.id).filter(
                    ManagementRequest.resident_id == resident_id,
                    ManagementRequest.request_type == code,
                    ManagementRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                ).first():
                    raise ValueError("task_has_active_request")
                current = next(item for item in self.list_tasks(resident_id) if item["code"] == code)
                if current.get("completion_reason") == "not_needed":
                    return {**current, "already_recorded": True}
                if datetime.fromisoformat(current["updated_at"]) != expected_updated_at:
                    raise ValueError("checklist_changed")
                if current["status"] == "completed":
                    raise ValueError("task_already_completed")
                task.not_needed_at = now_utc()
                task.status = "completed"
                task.updated_at = task.not_needed_at
                session.flush()
                return {**next(item for item in self.list_tasks(resident_id) if item["code"] == code), "already_recorded": False}

    def update_task(self, resident_id: str, code: str, status: str) -> dict[str, Any]:
        with self.session() as session:
            task = session.query(ChecklistTask).filter_by(resident_id=resident_id, code=code, active=True).one_or_none()
            if not task:
                raise ValueError("task_not_found")
            task.status = status
            task.updated_at = now_utc()
            session.flush()
            return self._task_dict(task)

    def create_request(
        self,
        resident_id: str,
        request_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        with self.session() as session:
            resident = session.query(Resident).filter_by(id=resident_id).with_for_update().one_or_none()
            if not resident:
                raise ValueError("resident_not_found")
            if request_type in {"temporary_residency", "account_registration"} and resident.resident_type != "owner":
                raise ValueError("owner_role_required")
            business_key = self._business_key(request_type, payload)
            if idempotency_key:
                found = session.query(ManagementRequest).filter_by(idempotency_key=idempotency_key).one_or_none()
                if found:
                    return self._idempotency_result(found, resident_id, request_type, business_key)

            found = (
                session.query(ManagementRequest)
                .filter(
                    ManagementRequest.resident_id == resident_id,
                    ManagementRequest.request_type == request_type,
                    ManagementRequest.business_key == business_key,
                    ManagementRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                )
                .order_by(ManagementRequest.created_at.desc())
                .first()
            )
            if found:
                return self._deduplicated_result(found, "matching_open_request")

            request = ManagementRequest(
                id=f"REQ-{uuid4().hex[:8].upper()}",
                resident_id=resident_id,
                request_type=request_type,
                payload_json=dict(payload),
                idempotency_key=idempotency_key,
                business_key=business_key,
            )
            try:
                # Keep the outer use-case transaction usable after a competing
                # insert wins either unique constraint.
                with session.begin_nested():
                    session.add(request)
                    session.flush()
            except IntegrityError as exc:
                constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if constraint_name not in {
                    "uq_management_requests_idempotency_key",
                    "uq_management_requests_active_business_key",
                }:
                    raise
                if idempotency_key:
                    winner = (
                        session.query(ManagementRequest)
                        .filter_by(idempotency_key=idempotency_key)
                        .one_or_none()
                    )
                    if winner:
                        return self._idempotency_result(
                            winner,
                            resident_id,
                            request_type,
                            business_key,
                        )
                winner = (
                    session.query(ManagementRequest)
                    .filter(
                        ManagementRequest.resident_id == resident_id,
                        ManagementRequest.request_type == request_type,
                        ManagementRequest.business_key == business_key,
                        ManagementRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                    )
                    .one_or_none()
                )
                if winner:
                    return self._deduplicated_result(winner, "matching_open_request")
                raise
            task_code = REQUEST_TASK_TYPES.get(request_type)
            if task_code:
                task = (
                    session.query(ChecklistTask)
                    .filter_by(resident_id=resident_id, code=task_code, active=True)
                    .one_or_none()
                )
                if task:
                    task.status = "submitted"
                    task.not_needed_at = None
                    task.updated_at = now_utc()
            session.add(RequestEvent(request_id=request.id, status="submitted"))
            return {
                **self._request_dict(request),
                "deduplicated": False,
                "deduplication_reason": None,
            }

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self.session() as session:
            request = session.get(ManagementRequest, request_id)
            return self._request_dict(request) if request else None

    def list_requests(
        self,
        resident_id: str | None = None,
        request_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.session() as session:
            query = session.query(ManagementRequest)
            if resident_id:
                query = query.filter(ManagementRequest.resident_id == resident_id)
            if request_type:
                query = query.filter(ManagementRequest.request_type == request_type)
            if status:
                query = query.filter(ManagementRequest.status == status)
            rows = query.order_by(ManagementRequest.created_at.desc()).all()
            return [self._request_dict(row) for row in rows]

    def request_timeline(self, request_id: str) -> list[dict[str, Any]]:
        def utc_iso(value: datetime) -> str:
            return value.astimezone(UTC).isoformat()

        with self.session() as session:
            events = session.query(RequestEvent).filter_by(request_id=request_id).order_by(RequestEvent.id).all()
            if events:
                return [{"status": item.status, "reason": item.reason, "created_at": utc_iso(item.created_at)} for item in events]
            request = session.get(ManagementRequest, request_id)
            return ([{"status": request.status, "reason": request.decision_reason, "created_at": utc_iso(request.updated_at)}]
                    if request else [])

    def list_management_notifications(self, resident_id: str, after_id: int = 0, limit: int = 50, subject: str | None = None) -> list[dict[str, Any]]:
        """Return management decisions for this resident, in delivery order."""
        with self.session() as session:
            rows = (
                session.query(RequestEvent, ManagementRequest)
                .join(ManagementRequest, RequestEvent.request_id == ManagementRequest.id)
                .filter(
                    ManagementRequest.resident_id == resident_id,
                    self.visible_requests(subject, "notifications") if subject is not None else true(),
                    RequestEvent.id > after_id,
                    RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"}),
                )
                .order_by(RequestEvent.id)
                .limit(max(1, min(limit, 50)))
                .all()
            )
            return [
                {"event_id": event.id, "request_id": request.id, "request_type": request.request_type,
                 "status": event.status, "reason": event.reason, "created_at": event.created_at.isoformat()}
                for event, request in rows
            ]

    def management_notification_feed(self, resident_id: str, subject: str, before_id: int = 0, limit: int = 30) -> dict[str, Any]:
        subject_key = hashlib.sha256(subject.encode()).hexdigest()
        with self.session() as session:
            query = (
                session.query(RequestEvent, ManagementRequest, NotificationReceipt.read_at)
                .join(ManagementRequest, RequestEvent.request_id == ManagementRequest.id)
                .outerjoin(NotificationReceipt, (NotificationReceipt.event_id == RequestEvent.id) & (NotificationReceipt.subject_key == subject_key))
                .filter(ManagementRequest.resident_id == resident_id,
                        self.visible_requests(subject, "notifications"),
                        RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"}))
            )
            unread_count = query.filter(NotificationReceipt.event_id.is_(None)).count()
            latest = query.order_by(RequestEvent.id.desc()).first()
            if before_id:
                query = query.filter(RequestEvent.id < before_id)
            rows = query.order_by(RequestEvent.id.desc()).limit(limit + 1).all()
            items = [
                {"event_id": event.id, "request_id": item.id, "request_type": item.request_type,
                 "status": event.status, "reason": event.reason, "created_at": event.created_at.isoformat(),
                 "read_at": read_at.isoformat() if read_at else None}
                for event, item, read_at in rows[:limit]
            ]
            return {"items": items, "unread_count": unread_count,
                    "latest_event_id": latest[0].id if latest else 0, "has_more": len(rows) > limit,
                    "next_before_id": items[-1]["event_id"] if items else 0}

    def management_notification_groups(self, resident_id: str, subject: str, **filters) -> dict:
        result = self.search_requests(resident_id=resident_id, management_updates_only=True, subject=subject, list_name="notifications", **filters)
        summary = self.management_notification_feed(resident_id, subject, limit=1)
        subject_key = hashlib.sha256(subject.encode()).hexdigest()
        groups = {item["request_id"]: item for item in result["items"]}
        for item in groups.values():
            account_id = item.get("payload", {}).get("app_account_resident_id") if item["request_type"] == "account_registration" else None
            with self.session() as session:
                credential = session.query(LocalCredential).filter_by(resident_id=account_id).one_or_none() if account_id else None
                item["account_handover_available"] = bool(credential)
                item["temporary_password_available"] = bool(credential and credential.password_change_required and session.get(CredentialHandover, credential.username))
            item.pop("payload", None)
            item["events"] = []
            item["unread_count"] = 0
        with self.session() as session:
            rows = session.query(RequestEvent, NotificationReceipt.read_at).outerjoin(
                NotificationReceipt, (NotificationReceipt.event_id == RequestEvent.id) & (NotificationReceipt.subject_key == subject_key)
            ).filter(RequestEvent.request_id.in_(groups), RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"})).order_by(RequestEvent.id.desc()).all()
            for event, read_at in rows:
                group = groups[event.request_id]
                group["events"].append({"event_id": event.id, "status": event.status, "reason": event.reason,
                                        "created_at": event.created_at.isoformat(), "read_at": read_at.isoformat() if read_at else None})
                group["unread_count"] += int(read_at is None)
        return {**result, "unread_count": summary["unread_count"], "latest_event_id": summary["latest_event_id"]}

    def read_management_notifications(self, resident_id: str, subject: str, *, event_id: int | None = None, through_event_id: int | None = None, request_id: str | None = None) -> int:
        """Acknowledge only authorized events observed by the client, idempotently."""
        subject_key = hashlib.sha256(subject.encode()).hexdigest()
        with self.session() as session:
            query = session.query(RequestEvent.id).join(ManagementRequest, RequestEvent.request_id == ManagementRequest.id).filter(
                ManagementRequest.resident_id == resident_id,
                self.visible_requests(subject, "notifications"),
                RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"}),
            )
            if event_id is not None:
                query = query.filter(RequestEvent.id == event_id)
            elif through_event_id is not None:
                query = query.filter(RequestEvent.id <= through_event_id)
            else:
                raise ValueError("notification_boundary_required")
            if request_id is not None:
                query = query.filter(ManagementRequest.id == request_id)
            ids = [row[0] for row in query.all()]
            if (event_id is not None or request_id is not None) and not ids:
                raise ValueError("notification_not_found")
            if ids:
                session.execute(postgresql_insert(NotificationReceipt).values([
                    {"subject_key": subject_key, "event_id": value, "read_at": now_utc()} for value in ids
                ]).on_conflict_do_nothing())
            return len(ids)

    def latest_management_event_id(self, resident_id: str) -> int:
        with self.session() as session:
            row = (
                session.query(RequestEvent.id)
                .join(ManagementRequest, RequestEvent.request_id == ManagementRequest.id)
                .filter(
                    ManagementRequest.resident_id == resident_id,
                    RequestEvent.status.in_({"in_review", "needs_info", "approved", "rejected", "completed"}),
                )
                .order_by(RequestEvent.id.desc())
                .first()
            )
            return int(row[0]) if row else 0

    def parking_reminders(self, resident_id: str) -> dict[str, dict[str, str]]:
        with self.session() as session:
            rows = session.query(ParkingReminder).filter_by(resident_id=resident_id).all()
            return {
                row.request_id: {"due_date": row.due_date.isoformat(), "updated_at": row.updated_at.isoformat()}
                for row in rows
            }

    def set_parking_due_date(self, resident_id: str, request_id: str, due_date: date) -> dict[str, str]:
        with self.session() as session:
            request = session.get(ManagementRequest, request_id)
            if not request or request.resident_id != resident_id or request.request_type != "vehicle_registration":
                raise ValueError("vehicle_request_not_found")
            if request.status not in {"approved", "completed"}:
                raise ValueError("vehicle_not_approved")
            reminder = session.get(ParkingReminder, request_id)
            if not reminder:
                reminder = ParkingReminder(request_id=request_id, resident_id=resident_id, due_date=due_date)
                session.add(reminder)
            else:
                reminder.due_date = due_date
                reminder.updated_at = now_utc()
            session.flush()
            return {"request_id": request_id, "due_date": reminder.due_date.isoformat(), "updated_at": reminder.updated_at.isoformat()}

    def cancel_request(self, resident_id: str, request_id: str) -> dict[str, Any]:
        with self.session() as session:
            request = (
                session.query(ManagementRequest)
                .filter_by(id=request_id)
                .with_for_update()
                .one_or_none()
            )
            if not request or request.resident_id != resident_id:
                raise ValueError("request_not_found")
            if request.status == "cancelled_by_resident":
                return self._request_dict(request)
            if request.status in {"completed", "rejected"}:
                raise ValueError("request_not_editable")
            request.status = "cancelled_by_resident"
            request.decision_reason = "Cư dân đã hủy ticket."
            request.updated_at = now_utc()
            session.add(RequestEvent(request_id=request.id, status=request.status, reason=request.decision_reason))
            self._sync_request_tasks(session, resident_id)
            session.flush()
            return self._request_dict(request)

    def update_request(
        self, resident_id: str, request_id: str, changes: dict[str, Any], resubmit: bool = False,
        *, expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Add information to the same open ticket and keep its audit trail."""
        with self.session() as session:
            request = (
                session.query(ManagementRequest)
                .filter_by(id=request_id)
                .with_for_update()
                .one_or_none()
            )
            if not request or request.resident_id != resident_id:
                raise ValueError("request_not_found")
            if expected_updated_at is not None and request.updated_at.isoformat() != expected_updated_at:
                raise ValueError("request_version_conflict")
            if request.status not in {"submitted", "in_review", "needs_info"}:
                raise ValueError("request_not_editable")
            if not changes and not resubmit:
                raise ValueError("update_content_required")
            payload = dict(request.payload_json)
            payload.update(changes)
            request.payload_json = payload
            request.business_key = self._business_key(request.request_type, payload)
            previous_status = request.status
            if resubmit or previous_status == "needs_info":
                request.status = "submitted"
                request.decision_reason = None
                payload["resubmission_count"] = int(payload.get("resubmission_count", 0)) + 1
                request.payload_json = dict(payload)
            request.updated_at = now_utc()
            reason = "Cư dân đã gửi lại hồ sơ" if resubmit else "Cư dân đã bổ sung thông tin"
            field_labels = {"person_name": "họ tên người cư trú", "citizen_id": "CCCD", "visitor_name": "tên khách", "visit_date": "ngày đến", "stay_start": "ngày bắt đầu cư trú", "move_date": "ngày chuyển đồ", "move_time": "giờ chuyển đồ", "pickup_date": "ngày nhận thẻ", "pickup_time": "giờ nhận thẻ", "vehicle_plate": "biển số", "vehicle_type": "loại xe", "details": "nội dung"}
            if changes:
                reason += " · Đã cập nhật: " + ", ".join(field_labels.get(field, field) for field in changes)
            session.add(RequestEvent(request_id=request.id, status=request.status, reason=reason))
            try:
                session.flush()
            except IntegrityError as exc:
                if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_management_requests_active_business_key":
                    raise ValueError("request_duplicate") from exc
                raise
            self._sync_request_tasks(session, resident_id)
            session.flush()
            return self._request_dict(request)

    def remove_temporary_resident(self, resident_id: str, request_id: str, *, expected_updated_at: str | None = None) -> dict[str, Any]:
        return self.remove_household_request(resident_id, request_id, "temporary_residency", expected_updated_at=expected_updated_at)

    def remove_household_request(
        self, resident_id: str, request_id: str, request_type: str, *, expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Remove a household entry atomically while retaining its ticket and documents."""
        reasons = {
            "temporary_residency": "Chủ sở hữu đã gỡ người này khỏi danh sách căn hộ.",
            "vehicle_registration": "Cư dân đã gỡ phương tiện khỏi danh sách căn hộ.",
            "internet_registration": "Cư dân đã hủy đăng ký dịch vụ internet trong ứng dụng. Liên hệ nhà mạng để xác nhận hủy hợp đồng thực tế.",
        }
        if request_type not in reasons:
            raise ValueError("request_not_found")
        with self.session() as session:
            if request_type == "temporary_residency":
                session.query(ManagementRequest).filter_by(resident_id=resident_id, request_type="account_registration").order_by(ManagementRequest.id).with_for_update().all()
            actor = session.query(Resident).filter_by(id=resident_id).with_for_update().one_or_none()
            request = (
                session.query(ManagementRequest)
                .filter_by(id=request_id)
                .with_for_update()
                .one_or_none()
            )
            sender = session.get(Resident, request.resident_id) if request else None
            if not actor or not sender or request.request_type != request_type or actor.site_id != sender.site_id or any(
                self._location_code(getattr(actor, key)) != self._location_code(getattr(sender, key)) for key in ("building", "apartment")
            ) or (request.resident_id != resident_id and (actor.resident_type != "owner" or not actor.ownership_confirmed_at or not sender.ownership_confirmed_at)):
                raise ValueError("request_not_found")
            if request_type == "temporary_residency" and actor.resident_type != "owner":
                raise ValueError("owner_role_required")
            payload = dict(request.payload_json)
            if payload.get("hidden_from_household"):
                return self._request_dict(request)
            if expected_updated_at is not None and request.updated_at != datetime.fromisoformat(expected_updated_at):
                raise ValueError("request_version_conflict")
            entries = [request]
            if request_type == "temporary_residency":
                if payload.get("resident_type") == "owner" or payload.get("app_account_resident_id") == resident_id:
                    raise ValueError("household_owner_not_removable")
                others = session.query(ManagementRequest).filter_by(resident_id=request.resident_id, request_type=request_type).order_by(ManagementRequest.id).with_for_update().all()
                for other in others:
                    if other.id == request.id or other.payload_json.get("hidden_from_household"):
                        continue
                    same_person = payload.get("citizen_id") == other.payload_json.get("citizen_id") if payload.get("citizen_id") else str(payload.get("person_name", "")).strip().casefold() == str(other.payload_json.get("person_name", "")).strip().casefold()
                    if same_person:
                        entries.append(other)
                if self._suspend_removed_household_accounts(session, sender, entries):
                    reasons[request_type] += " Quyền sử dụng tài khoản web liên kết đã tạm ngừng."
            removed_at = now_utc()
            for entry in entries:
                entry.payload_json = {**entry.payload_json, "hidden_from_household": True}
                if entry.status not in {"rejected", "cancelled_by_resident"}:
                    entry.status = "cancelled_by_resident"
                    entry.decision_reason = reasons[request_type]
                entry.updated_at = removed_at
                session.add(RequestEvent(request_id=entry.id, status=entry.status, reason=reasons[request_type]))
            if request_type == "temporary_residency":
                related = session.query(ManagementRequest).filter(
                    ManagementRequest.request_type == "account_registration",
                    ManagementRequest.payload_json["household_member_id"].astext.in_([entry.id for entry in entries]),
                    ManagementRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                ).with_for_update().all()
                for account_request in related:
                    account_request.status = "cancelled_by_resident"
                    account_request.decision_reason = "Người cư trú đã được gỡ khỏi danh sách căn hộ."
                    account_request.updated_at = request.updated_at
                    session.add(RequestEvent(request_id=account_request.id, status=account_request.status, reason=account_request.decision_reason))
                    self._sync_request_tasks(session, account_request.resident_id)
            session.flush()
            self._sync_request_tasks(session, request.resident_id)
            session.flush()
            return self._request_dict(request)

    @classmethod
    def _suspend_removed_household_accounts(cls, session: Session, sender: Resident, entries: list[ManagementRequest]) -> bool:
        suspended = False
        sources = {entry.id: entry for entry in entries}
        issued = session.query(ManagementRequest).filter(
            ManagementRequest.resident_id == sender.id,
            ManagementRequest.request_type == "account_registration",
            ManagementRequest.status == "completed",
            ManagementRequest.payload_json["household_member_id"].astext.in_(sources),
        ).order_by(ManagementRequest.id).with_for_update().all()
        for account_request in issued:
            source = sources[account_request.payload_json["household_member_id"]]
            account_id = account_request.payload_json.get("app_account_resident_id")
            if not account_id or account_id == sender.id or account_id != source.payload_json.get("app_account_resident_id"):
                continue
            credential = session.query(LocalCredential).filter_by(resident_id=account_id, creation_key="dependent:" + account_request.id).one_or_none()
            member = session.get(Resident, account_id)
            if not credential or not member or member.resident_type not in {"tenant", "family_member"} or member.site_id != sender.site_id or any(
                cls._location_code(getattr(member, key)) != cls._location_code(getattr(sender, key)) for key in ("building", "apartment")
            ):
                continue
            user = session.query(User).filter_by(auth_subject=credential.auth_subject).with_for_update().one()
            memberships = session.query(ResidenceMembership).filter_by(user_id=user.id).order_by(ResidenceMembership.id).with_for_update().all()
            for membership in memberships:
                if membership.role == "resident" and membership.resident_id == member.id and membership.site_id == sender.site_id and membership.status == "active":
                    membership.status = "suspended"
                    suspended = True
            if user.status == "active" and not any(membership.status == "active" for membership in memberships):
                user.status = "suspended"
        return suspended

    @staticmethod
    def _request_identity(request_type: str, payload: dict[str, Any]) -> tuple[Any, ...]:
        """Return the business identity used only for active-request deduplication."""

        def normalized_text(value: Any) -> str:
            text = unicodedata.normalize("NFD", str(value or "").casefold())
            text = "".join(character for character in text if not unicodedata.combining(character))
            return " ".join(text.split())

        if request_type in {"resident_card", "card_replacement"}:
            return (request_type,)
        if request_type == "vehicle_registration":
            plate = re.sub(r"[^A-Z0-9]", "", str(payload.get("vehicle_plate", "")).upper())
            return (request_type, payload.get("vehicle_type", ""), plate)
        if request_type == "visitor_registration":
            visit_date_text = str(payload.get("visit_date", "")).strip()
            visit_date = re.sub(r"[^0-9]", "", visit_date_text)
            for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    visit_date = datetime.strptime(visit_date_text, date_format).date().isoformat()
                    break
                except ValueError:
                    continue
            return (request_type, normalized_text(payload.get("visitor_name")), visit_date)
        if request_type == "temporary_residency":
            return (request_type, normalized_text(payload.get("person_name")), payload.get("stay_start", ""))
        return (request_type, json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @classmethod
    def _business_key(cls, request_type: str, payload: dict[str, Any]) -> str:
        identity = cls._request_identity(request_type, payload)
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _idempotency_result(
        cls,
        found: ManagementRequest,
        resident_id: str,
        request_type: str,
        business_key: str,
    ) -> dict[str, Any]:
        if (
            found.resident_id != resident_id
            or found.request_type != request_type
            or found.business_key != business_key
        ):
            raise ValueError("idempotency_key_conflict")
        return cls._deduplicated_result(found, "idempotency_key")

    @classmethod
    def _deduplicated_result(
        cls,
        found: ManagementRequest,
        reason: str,
    ) -> dict[str, Any]:
        return {
            **cls._request_dict(found),
            "deduplicated": True,
            "deduplication_reason": reason,
        }

    def lock_management_requests(self, request_ids: list[str], site_ids: set[str]) -> list[dict]:
        if self._active_session.get() is None:
            raise RuntimeError("management_locks_require_transaction")
        with self.session() as session:
            rows = session.query(ManagementRequest).join(Resident, Resident.id == ManagementRequest.resident_id).filter(
                ManagementRequest.id.in_(request_ids), Resident.site_id.in_(site_ids),
            ).order_by(ManagementRequest.id).with_for_update().all()
            return [self._request_dict(row) for row in rows]

    def decide_request(self, request_id: str, status: str, reason: str) -> dict[str, Any]:
        with self.session() as session:
            request = (
                session.query(ManagementRequest)
                .filter_by(id=request_id)
                .with_for_update()
                .one_or_none()
            )
            if not request:
                raise ValueError("request_not_found")
            if status not in REQUEST_STATUS_TRANSITIONS.get(request.status, set()):
                raise ValueError("invalid_status_transition")
            if request.request_type == "account_registration" and status == "completed" and not request.payload_json.get("app_account_resident_id"):
                raise ValueError("account_issuance_required")
            request.status = status
            request.decision_reason = reason
            request.updated_at = now_utc()
            session.add(RequestEvent(request_id=request.id, status=status, reason=reason))
            self._sync_request_tasks(session, request.resident_id)
            session.flush()
            return self._request_dict(request)

    @staticmethod
    def _sync_request_tasks(session: Session, resident_id: str) -> None:
        resident = session.get(Resident, resident_id)
        for request_type, task_code in REQUEST_TASK_TYPES.items():
            task = (
                session.query(ChecklistTask)
                .filter_by(resident_id=resident_id, code=task_code, active=True)
                .one_or_none()
            )
            if not task:
                continue
            if task_code == "verify_residency" and resident:
                task.status = "completed" if resident.ownership_confirmed_at else "pending"
                task.updated_at = resident.ownership_confirmed_at or resident.updated_at
                continue
            latest = (
                session.query(ManagementRequest)
                .filter_by(resident_id=resident_id, request_type=request_type)
                .order_by(ManagementRequest.created_at.desc(), ManagementRequest.id.desc())
                .first()
            )
            if request_type == "temporary_residency":
                completed = (
                    session.query(ManagementRequest)
                    .filter_by(resident_id=resident_id, request_type=request_type, status="completed")
                    .order_by(ManagementRequest.created_at.desc())
                    .first()
                )
                latest = completed or latest
            not_needed = task_code in NO_NEED_TASK_CODES and task.not_needed_at and (not latest or latest.updated_at <= task.not_needed_at)
            status = "completed" if not_needed else latest.status if latest else "pending"
            if task.status != status:
                task.status = status
                task.updated_at = now_utc()

    @staticmethod
    def _resident_dict(row: Resident) -> dict[str, Any]:
        return {
            "resident_id": row.id,
            "full_name": row.full_name,
            "apartment": row.apartment,
            "building": row.building,
            "site_id": row.site_id,
            "site_name": row.site_name,
            "resident_type": row.resident_type,
            "family_members": row.family_members,
            "declared_family_members": row.family_members,
            "vehicle": row.vehicle,
            "needs_internet": row.needs_internet,
            "profile_version": row.profile_version,
            "ownership_confirmed_at": row.ownership_confirmed_at.isoformat() if row.ownership_confirmed_at else None,
        }

    @staticmethod
    def _task_dict(row: ChecklistTask) -> dict[str, Any]:
        return {
            "code": row.code,
            "title": row.title,
            "required": row.required,
            "status": row.status,
            "reason": row.reason,
            "updated_at": row.updated_at.isoformat(),
            "not_needed_at": row.not_needed_at.isoformat() if row.not_needed_at else None,
            "completion_reason": None,
            "can_mark_not_needed": False,
        }

    @staticmethod
    def _request_dict(row: ManagementRequest) -> dict[str, Any]:
        return {
            "request_id": row.id,
            "resident_id": row.resident_id,
            "request_type": row.request_type,
            "status": row.status,
            "payload": dict(row.payload_json),
            "decision_reason": row.decision_reason,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
