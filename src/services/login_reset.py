import hashlib
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from src.repositories.store import (
    DEMO_RESIDENT_ID,
    Base,
    LocalCredential,
    Resident,
    Store,
    now_utc,
)
from src.security.local_auth import local_auth_enabled

PROFILE_FIELDS = (
    "full_name", "apartment", "building", "site_id", "site_name", "resident_type",
    "family_members", "vehicle", "needs_internet",
)


class ResetBaseline(Base):
    __tablename__ = "login_reset_baselines"

    resident_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(60))
    profile: Mapped[dict] = mapped_column(JSONB)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    last_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResetAttempt(Base):
    __tablename__ = "login_reset_attempts"

    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)


class ResetRateLimitedError(ValueError):
    pass


def capture_baseline(store: Store, site_id: str) -> None:
    with store.session() as session:
        resident = session.get(Resident, DEMO_RESIDENT_ID)
        if resident is None or resident.site_id != site_id:
            raise ValueError("reset_resident_scope_mismatch")
        statement = insert(ResetBaseline).values(
            resident_id=DEMO_RESIDENT_ID, site_id=site_id,
            profile={name: getattr(resident, name) for name in PROFILE_FIELDS}, generation=0,
        )
        session.execute(statement.on_conflict_do_nothing(index_elements=[ResetBaseline.resident_id]))


def available(app) -> bool:
    return bool(app.settings.login_reset_enabled
                and app.settings.login_reset_code
                and app.settings.local_auth_user_resident_id == DEMO_RESIDENT_ID
                and local_auth_enabled(app.settings))


def record_attempt(store: Store, client_address: str, subject: str = "") -> None:
    now = now_utc()
    address_hash = hashlib.sha256(client_address.encode()).hexdigest()
    limited = False
    with store.session() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(1296649479)"))
        session.query(ResetAttempt).filter(ResetAttempt.window_start < now - timedelta(minutes=10)).delete()
        buckets = [("global", 30), (address_hash, 5)]
        if subject:
            buckets.append((hashlib.sha256(f"reset:{subject}".encode()).hexdigest(), 5))
        for bucket, maximum in buckets:
            row = session.get(ResetAttempt, bucket)
            if row is None:
                row = ResetAttempt(bucket=bucket, window_start=now, attempts=0)
                session.add(row)
            row.attempts += 1
            limited = limited or row.attempts > maximum
    if limited:
        raise ResetRateLimitedError("reset_rate_limited")


def preserved_account_subjects(app) -> tuple[str, ...]:
    from src.services.demo_accounts import SAMPLE_NAMES

    subjects = {account.subject for account in app.local_auth.accounts.values()}
    usernames = {name.strip().casefold() for name in app.settings.login_reset_preserved_usernames.split(",") if name.strip()}
    sample_keys = {f"local-showcase-v1-{index}" for index in range(1, len(SAMPLE_NAMES) + 1)}
    with app.store.session() as session:
        for credential in session.query(LocalCredential).all():
            if credential.username in usernames or credential.auth_subject.removeprefix("local:") in usernames or credential.creation_key in sample_keys:
                subjects.add(credential.auth_subject)
    return tuple(sorted(subjects))


def restore_preserved_profiles(app, resident_ids: tuple[str, ...]) -> None:
    with app.store.session() as session:
        baselines = {row.resident_id: row for row in session.query(ResetBaseline).filter(ResetBaseline.resident_id.in_(resident_ids))}
        for resident in session.query(Resident).filter(Resident.id.in_(resident_ids)).all():
            baseline = baselines.get(resident.id)
            if baseline:
                for name in PROFILE_FIELDS:
                    setattr(resident, name, baseline.profile[name])
            else:
                resident.family_members = 1
                resident.vehicle = "none"
                resident.needs_internet = False
            resident.ownership_confirmed_at = None
            resident.profile_version += 1
            resident.updated_at = now_utc()


def reset_application(app, actor_subject: str = "reset-code") -> dict:
    if not local_auth_enabled(app.settings):
        raise ValueError("reset_account_scope_mismatch")
    with app.store.transaction():
        with app.store.session() as session:
            app.store._acquire_demo_lock(session)
            baseline = session.scalar(select(ResetBaseline).where(
                ResetBaseline.resident_id == DEMO_RESIDENT_ID,
            ).with_for_update())
            resident = session.get(Resident, DEMO_RESIDENT_ID, with_for_update=True)
            if (baseline is None or resident is None
                    or baseline.site_id != app.settings.local_auth_site_id
                    or resident.site_id != baseline.site_id):
                raise ValueError("reset_resident_scope_mismatch")
            if baseline.last_reset_at and now_utc() - baseline.last_reset_at < timedelta(seconds=30):
                raise ResetRateLimitedError("reset_rate_limited")
            subjects = preserved_account_subjects(app)
            resident_ids = app.store.clear_application_data(subjects)
            restore_preserved_profiles(app, resident_ids)
            baseline.generation += 1
            baseline.last_reset_at = now_utc()
            session.flush()
        app.local_auth.provision_accounts()
        from src.services.demo_accounts import restore_sample_credentials

        restore_sample_credentials(app)
        checklist = app.tool_service.build_checklist(DEMO_RESIDENT_ID)
        for resident_id in resident_ids:
            if resident_id != DEMO_RESIDENT_ID:
                app.tool_service.build_checklist(resident_id)
        app.store.record_security_event(
            actor_subject=actor_subject, user_id=None, role="manager", site_id=baseline.site_id,
            action="application.reset", resource_type="application", resource_id="moveinmate",
            outcome="success", correlation_id=f"reset-{baseline.generation}",
        )
    app.agent.reset_project("moveinmate")
    app.private_results.clear()
    return {"reset": True, "resident_id": DEMO_RESIDENT_ID,
            "checklist_count": len(checklist["items"]), "scope": "all_application_data",
            "generation": baseline.generation}
