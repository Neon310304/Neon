import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from src.repositories.store import (
    AuthChallenge,
    CredentialHandover,
    LocalCredential,
    ManagementRequest,
    PasswordRecoveryRequest,
    RequestEvent,
    ResidenceMembership,
    Resident,
    User,
    now_utc,
)
from src.security.local_auth import _password_digest

SAMPLE_PASSWORD = "Resident123!"
SAMPLE_NAMES = ("Nguyễn An", "Trần Bình", "Lê Chi", "Phạm Dũng", "Hoàng Hà", "Vũ Linh", "Đỗ Nam", "Bùi Mai", "Đặng Phúc", "Phan Hương")
SAMPLE_STATUSES = ("submitted", "in_review", "needs_info", "approved", "completed", "rejected")
SAMPLE_NOTES = ("Đèn hành lang chưa sáng", "Cần sửa vòi nước khu chung", "Cửa thang máy đóng chậm", "Đề nghị kiểm tra tiếng ồn", "Cần hướng dẫn thẻ cư dân", "Lịch sử dụng phòng sinh hoạt", "Đèn bãi xe bị hỏng", "Cần vệ sinh sảnh tòa nhà", "Kiểm tra khóa cửa khu chung", "Hỏi lịch bảo trì thang máy")


def sample_login_hints(app) -> list[dict]:
    if app.settings.app_env not in {"development", "test"} or not app.settings.show_local_login_credentials:
        return []
    result = []
    for index, name in enumerate(SAMPLE_NAMES, 1):
        credential = app.store.get_local_credential(username=f"demo{index:03d}")
        if credential and credential["creation_key"] == f"local-showcase-v1-{index}" and hmac.compare_digest(_password_digest(SAMPLE_PASSWORD, bytes.fromhex(credential["salt"])).hex(), credential["password_hash"]):
            result.append({"label": f"Cư dân · {name}", "username": credential["username"], "password": SAMPLE_PASSWORD})
    return result


def seed_sample_accounts(app, *, include_requests: bool = True) -> dict:
    app.settings.validate_demo_database_target()
    return _seed_sample_accounts(app, include_requests=include_requests)


def ensure_default_accounts(app) -> dict | None:
    if not app.settings.login_reset_enabled or set(app.local_auth.accounts) != {"user", "admin"}:
        return None
    return _seed_sample_accounts(app, include_requests=False)


def restore_sample_credentials(app) -> None:
    with app.store.transaction():
        with app.store.session() as session:
            app.store._acquire_demo_lock(session)
            keys = {f"local-showcase-v1-{index}": index for index in range(1, len(SAMPLE_NAMES) + 1)}
            rows = session.query(LocalCredential).filter(LocalCredential.creation_key.in_(keys)).order_by(LocalCredential.creation_key).with_for_update().all()
            subjects = {row.auth_subject for row in rows}
            indices = range(1, len(SAMPLE_NAMES) + 1) if set(app.local_auth.accounts) == {"user", "admin"} else [keys[row.creation_key] for row in rows]
            for index in indices:
                username = f"demo{index:03d}"
                app.store._lock_username(session, username)
                existing = session.get(LocalCredential, username)
                if existing and existing.auth_subject not in subjects:
                    raise ValueError("sample_username_conflict")
                existing_user = session.query(User).filter_by(auth_subject=f"local:{username}").one_or_none()
                if existing_user and existing_user.auth_subject not in subjects:
                    raise ValueError("sample_username_conflict")
            usernames = [row.username for row in rows]
            session.query(CredentialHandover).filter(CredentialHandover.username.in_(usernames)).delete(synchronize_session=False)
            session.query(AuthChallenge).filter(AuthChallenge.username.in_(usernames)).delete(synchronize_session=False)
            session.query(PasswordRecoveryRequest).filter(PasswordRecoveryRequest.username.in_(usernames)).delete(synchronize_session=False)
            session.flush()
            for row in rows:
                row.username = f"reset-{uuid4().hex}"
            session.flush()
            for row in rows:
                index = keys[row.creation_key]
                salt = secrets.token_bytes(16)
                row.username = f"demo{index:03d}"
                row.salt = salt.hex()
                row.password_hash = _password_digest(SAMPLE_PASSWORD, salt).hex()
                row.password_change_required = True
                row.username_change_required = True
                row.recovery_email = None
                resident = session.get(Resident, row.resident_id)
                resident.full_name = SAMPLE_NAMES[index - 1]
                resident.family_members = 1
                resident.vehicle = "none"
                resident.needs_internet = False
                resident.ownership_confirmed_at = None
                user = session.query(User).filter_by(auth_subject=row.auth_subject).one()
                user.status = "active"
                for membership in session.query(ResidenceMembership).filter_by(user_id=user.id, resident_id=row.resident_id, role="resident", site_id=resident.site_id):
                    membership.status = "active"
                    membership.valid_from = now_utc()
                    membership.valid_to = None
            session.flush()
            if set(app.local_auth.accounts) == {"user", "admin"}:
                _seed_sample_accounts(app, include_requests=False)
            elif app.settings.settings_encryption_key:
                for row in rows:
                    app.store.save_handover(row.username, SAMPLE_PASSWORD, app.settings.settings_encryption_key)


def _seed_sample_accounts(app, *, include_requests: bool) -> dict:
    if not app.local_auth.accounts or not app.store.get_resident("R001"):
        raise ValueError("bootstrap_accounts_required")
    site_id = app.settings.local_auth_site_id
    manager = next(account for account in app.local_auth.accounts.values() if account.role == "manager")
    sites = app.store.list_sites({manager.site_id, *manager.additional_site_ids})
    sites.sort(key=lambda site: (site["site_id"] != site_id, site["site_id"]))
    if not sites:
        raise ValueError("site_required")
    actor = manager.subject
    added = 0
    with app.store.transaction():
        with app.store.session() as session:
            app.store._acquire_demo_lock(session)
        for index, name in enumerate(SAMPLE_NAMES, 1):
            username = f"demo{index:03d}"
            credential = app.store.get_local_credential(creation_key=f"local-showcase-v1-{index}") or app.store.get_local_credential(username=username)
            if credential and credential["creation_key"] != f"local-showcase-v1-{index}":
                raise ValueError("sample_username_conflict")
            if not credential:
                site = sites[(index - 1) % len(sites)]
                building = ("A", "B", "C", "D")[(index - 1) % 4]
                apartment = f"{building}{index + 5:02d}{index:02d}"
                resident_type = ("owner", "tenant", "family_member")[(index - 1) % 3]
                if resident_type == "owner" and app.store.owner_exists(site["site_id"], building, apartment):
                    resident_type = "family_member"
                result = app.local_auth.create_resident_account({"username": username, "password": SAMPLE_PASSWORD, "full_name": name, "site_id": site["site_id"], "site_name": site["site_name"], "building": building, "apartment": apartment, "resident_type": resident_type, "idempotency_key": f"local-showcase-v1-{index}"}, actor)
                resident_id = result["profile"]["resident_id"]
                app.tool_service.build_checklist(resident_id)
            else:
                resident_id = credential["resident_id"]
            if app.settings.settings_encryption_key:
                with app.store.session() as session:
                    row = session.query(LocalCredential).filter_by(creation_key=f"local-showcase-v1-{index}").with_for_update().one_or_none()
                    if (row and row.password_change_required and not session.get(CredentialHandover, row.username)
                            and hmac.compare_digest(_password_digest(SAMPLE_PASSWORD, bytes.fromhex(row.salt)).hex(), row.password_hash)):
                        app.store.save_handover(row.username, SAMPLE_PASSWORD, app.settings.settings_encryption_key)
            if not include_requests:
                continue
            for ticket_index, request_type in enumerate(("resident_card", "verify_residency", "general_support")):
                key = f"local-showcase-v1-{index}-{request_type}"
                with app.store.session() as session:
                    exists = session.query(ManagementRequest.id).filter_by(idempotency_key=key).first()
                if exists:
                    continue
                payload = {"details": SAMPLE_NOTES[index - 1]} if request_type == "general_support" else {}
                ticket = app.store.create_request(resident_id, request_type, payload, key)
                status = SAMPLE_STATUSES[(index - 1 + ticket_index) % len(SAMPLE_STATUSES)]
                transitions = {"submitted": [], "in_review": ["in_review"], "needs_info": ["in_review", "needs_info"], "approved": ["in_review", "approved"], "completed": ["in_review", "approved", "completed"], "rejected": ["in_review", "rejected"]}[status]
                for transition in transitions:
                    reason = "Vui lòng bổ sung họ tên đúng như hồ sơ đã cấp." if transition == "needs_info" else "Thông tin không khớp hồ sơ; vui lòng liên hệ BQL." if transition == "rejected" else ""
                    app.store.decide_request(ticket["request_id"], transition, reason)
                with app.store.session() as session:
                    row = session.get(ManagementRequest, ticket["request_id"])
                    row.created_at = now_utc() - timedelta(days=index * 6 + ticket_index)
                    row.updated_at = now_utc() - timedelta(days=index)
                    events = session.query(RequestEvent).filter_by(request_id=row.id).order_by(RequestEvent.id).all()
                    if len(events) == 1:
                        row.updated_at = row.created_at
                    for event_index, event in enumerate(events):
                        event.created_at = row.created_at + (row.updated_at - row.created_at) * event_index / max(1, len(events) - 1)
                added += 1
    return {"accounts": len(SAMPLE_NAMES), "tickets_added": added, "scope": "local_only"}


def replace_sample_accounts(app) -> dict:
    app.settings.validate_demo_database_target()
    accounts = tuple(app.local_auth.accounts.values())
    if len(accounts) != 2 or {account.role for account in accounts} != {"resident", "manager"}:
        raise ValueError("bootstrap_accounts_required")
    preserved_subjects = {account.subject for account in accounts}
    preserved_residents = {account.resident_id for account in accounts if account.resident_id}
    if not preserved_residents or any(not app.store.get_resident(resident_id) for resident_id in preserved_residents):
        raise ValueError("bootstrap_accounts_required")
    with app.store.transaction():
        with app.store.session() as session:
            app.store._acquire_demo_lock(session)
            additional_usernames = {name.strip().casefold() for name in app.settings.login_reset_preserved_usernames.split(",") if name.strip()}
            for credential in session.query(LocalCredential).filter(LocalCredential.username.in_(additional_usernames)):
                preserved_subjects.add(credential.auth_subject)
                preserved_residents.add(credential.resident_id)
            removed_accounts = session.query(LocalCredential).filter(LocalCredential.auth_subject.not_in(preserved_subjects)).count()
            kept_usernames = {*app.local_auth.accounts, *additional_usernames}
            session.query(PasswordRecoveryRequest).filter(PasswordRecoveryRequest.username.not_in(kept_usernames)).delete(synchronize_session=False)
            session.query(ManagementRequest).filter(ManagementRequest.resident_id.not_in(preserved_residents)).delete(synchronize_session=False)
            session.query(User).filter(User.auth_subject.not_in(preserved_subjects)).delete(synchronize_session=False)
            session.query(Resident).filter(Resident.id.not_in(preserved_residents)).delete(synchronize_session=False)
        app.local_auth.provision_accounts()
        result = seed_sample_accounts(app, include_requests=False)
    return {**result, "removed_accounts": removed_accounts}
