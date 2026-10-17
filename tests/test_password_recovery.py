from datetime import timedelta

import pytest
from cryptography.fernet import Fernet

from src.repositories.store import (
    AuthChallenge,
    LocalCredential,
    ManagementRequest,
    PasswordRecoveryRequest,
    Resident,
    ResidentialSite,
    User,
    now_utc,
)
from src.security import AuthenticationError
from src.security.local_auth import LocalAuthService, verify_local_token
from src.services import login_reset
from src.services.demo_accounts import (
    SAMPLE_PASSWORD,
    replace_sample_accounts,
    sample_login_hints,
    seed_sample_accounts,
)
from src.services.password_recovery import (
    EmailConfirm,
    EmailStart,
    ManagerPasswordReset,
    RecoveryIntake,
    confirm_email,
    intake_recovery,
    manager_reset,
    recovery_requests,
    start_email,
)
from tests.test_local_auth_api import local_client, local_container, login

__all__ = ["local_client", "local_container"]


def issued(app, username="resident001", site="sunrise-residence"):
    with app.store.transaction():
        return app.local_auth.create_resident_account({"username": username, "password": "Original123!", "full_name": "Nguyễn An", "site_id": site, "site_name": "Khu dân cư", "building": "A", "apartment": "A1201", "resident_type": "owner", "idempotency_key": f"account-{username}"}, "local:admin")


def reset_payload(**values):
    return ManagerPasswordReset(confirmed=True, identity_checked=True, reason="Đã đối chiếu trực tiếp tại BQL", **values)


@pytest.mark.asyncio
async def test_manager_reset_invalidates_sessions_and_requires_change(local_client, local_container):
    issued(local_container)
    old = local_container.local_auth.login("resident001", "Original123!")
    headers = await login(local_client, "admin")
    reset = await local_client.post("/api/v1/management/resident-accounts/resident001/reset-password", headers=headers, json=reset_payload().model_dump())
    assert reset.status_code == 200
    assert reset.headers["cache-control"] == "no-store"
    secret = reset.json()["temporary_password"]
    assert len(secret) >= 16
    assert (await local_client.get("/api/v1/me/context", headers={"Authorization": f"Bearer {old['access_token']}"})).status_code == 401
    fresh = local_container.local_auth.login("resident001", secret)
    assert fresh["password_change_required"]
    blocked = await local_client.get("/api/v1/residents/" + issued_id(local_container), headers={"Authorization": f"Bearer {fresh['access_token']}"})
    assert blocked.status_code == 403
    with pytest.raises(ValueError, match="initial_credentials_required"):
        local_container.local_auth.change_password("local:resident001", secret, "Personal123!")
    local_container.local_auth.set_initial_password("local:resident001", "resident.personal", "Personal123!")
    assert not local_container.local_auth.login("resident.personal", "Personal123!")["password_change_required"]


def issued_id(app):
    return app.store.get_local_credential(username="resident001")["resident_id"]


@pytest.mark.asyncio
async def test_resident_cannot_reset_or_list_requests(local_client, local_container):
    issued(local_container)
    headers = await login(local_client, "user")
    assert (await local_client.post("/api/v1/management/resident-accounts/resident001/reset-password", headers=headers, json=reset_payload().model_dump())).status_code == 403
    assert (await local_client.get("/api/v1/management/password-recovery", headers=headers)).status_code == 403
    assert (await local_client.get("/api/v1/management/password-recovery")).status_code == 401


@pytest.mark.asyncio
async def test_reset_scope_confirmation_bootstrap(local_client, local_container):
    issued(local_container, "other", "another-site")
    headers = await login(local_client, "admin")
    for username in ("other", "admin", "user", "missing"):
        assert (await local_client.post(f"/api/v1/management/resident-accounts/{username}/reset-password", headers=headers, json=reset_payload().model_dump())).status_code == 404
    issued(local_container)
    assert (await local_client.post("/api/v1/management/resident-accounts/resident001/reset-password", headers=headers, json={"confirmed": True, "reason": "abcde"})).status_code == 422


@pytest.mark.asyncio
async def test_reject_recovery_is_scoped_and_leaves_password_unchanged(local_client, local_container):
    issued(local_container)
    issued(local_container, "other", "another-site")
    for username in ("resident001", "other"):
        intake_recovery(local_container.store, RecoveryIntake(username=username, full_name="Wrong name", apartment="X1", contact="0900000000", confirmed=True))
    headers = await login(local_client, "admin")
    with local_container.store.session() as session:
        rows = {row.username: row.id for row in session.query(PasswordRecoveryRequest).all()}
    body = {"confirmed": True, "reason": "Chưa xác minh được cư dân"}
    assert (await local_client.post(f"/api/v1/management/password-recovery/{rows['other']}/reject", json=body, headers=headers)).status_code == 404
    assert (await local_client.post(f"/api/v1/management/password-recovery/{rows['resident001']}/reject", json=body, headers=headers)).status_code == 204
    assert local_container.local_auth.login("resident001", "Original123!")
    assert not recovery_requests(local_container.store, {"sunrise-residence"})


@pytest.mark.asyncio
async def test_intake_is_generic_and_never_changes_password(local_client, local_container):
    issued(local_container)
    for username in ("resident001", "unknown"):
        response = await local_client.post("/api/v1/auth/recovery/requests", json={"username": username, "full_name": "Nguyễn An", "apartment": "A1201", "contact": "0900000000", "confirmed": True})
        assert response.status_code == 202
        assert "password" not in response.text
    assert local_container.local_auth.login("resident001", "Original123!")
    rows = recovery_requests(local_container.store, {"sunrise-residence"})
    assert len(rows) == 1
    assert rows[0]["matches_profile"]
    assert not recovery_requests(local_container.store, {"another-site"})
    manager_reset(local_container, {"sunrise-residence"}, "resident001", reset_payload(recovery_id=rows[0]["request_id"]))
    assert not recovery_requests(local_container.store, {"sunrise-residence"})
    with pytest.raises(ValueError, match="recovery_not_pending"):
        manager_reset(local_container, {"sunrise-residence"}, "resident001", reset_payload(recovery_id=rows[0]["request_id"]))


@pytest.mark.asyncio
async def test_no_smtp_no_fake_delivery_and_rate_limit(local_client, local_container):
    local_container.settings.smtp_host = ""
    assert (await local_client.get("/api/v1/auth/recovery/config")).json()["email_enabled"] is False
    result = await local_client.post("/api/v1/auth/recovery/email", json={"username": "missing", "email": "test@example.com"})
    assert result.status_code == 503
    for attempt in range(6):
        result = await local_client.post("/api/v1/auth/recovery/requests", json={"username": "missing", "full_name": "Test", "apartment": "A1", "contact": "0900000000", "confirmed": True})
        assert result.status_code == (202 if attempt < 5 else 429)


def configured_mail(app, monkeypatch):
    codes = []
    app.settings.smtp_host = "smtp.example.com"
    app.settings.smtp_from = "support@example.com"
    monkeypatch.setattr("src.services.password_recovery.send_code", lambda settings, email, code: codes.append((email, code)))
    return codes


def verify_email(app, codes):
    result = start_email(app, EmailStart(username="resident001", email="an@example.com", current_password="Original123!"), "verify_email", "local:resident001")
    confirm_email(app, EmailConfirm(challenge_id=result["challenge_id"], code=codes[-1][1]), "verify_email", "local:resident001")


def test_verified_email_one_use_expiry_and_password_rotation(local_container, monkeypatch):
    issued(local_container)
    codes = configured_mail(local_container, monkeypatch)
    verify_email(local_container, codes)
    old = local_container.local_auth.login("resident001", "Original123!")["access_token"]
    result = start_email(local_container, EmailStart(username="resident001", email="an@example.com"), "reset_password")
    payload = EmailConfirm(challenge_id=result["challenge_id"], code=codes[-1][1], new_password="NewSecret123!")
    confirm_email(local_container, payload, "reset_password")
    with pytest.raises(ValueError):
        confirm_email(local_container, payload, "reset_password")
    with pytest.raises(Exception):
        verify_local_token(old, local_container.settings, local_container.store)
    assert not local_container.local_auth.login("resident001", "NewSecret123!")["password_change_required"]
    result = start_email(local_container, EmailStart(username="resident001", email="an@example.com"), "reset_password")
    with local_container.store.session() as session:
        session.get(AuthChallenge, result["challenge_id"]).expires_at = now_utc() - timedelta(seconds=1)
    with pytest.raises(ValueError):
        confirm_email(local_container, EmailConfirm(challenge_id=result["challenge_id"], code=codes[-1][1], new_password="Another123!"), "reset_password")


def test_wrong_codes_persist_limit_and_other_subject_blocked(local_container, monkeypatch):
    issued(local_container)
    codes = configured_mail(local_container, monkeypatch)
    result = start_email(local_container, EmailStart(username="resident001", email="an@example.com", current_password="Original123!"), "verify_email", "local:resident001")
    correct = EmailConfirm(challenge_id=result["challenge_id"], code=codes[-1][1])
    with pytest.raises(ValueError):
        confirm_email(local_container, correct, "verify_email", "local:other")
    wrong = "000000" if correct.code != "000000" else "999999"
    for attempt in range(5):
        with pytest.raises(ValueError):
            confirm_email(local_container, correct.model_copy(update={"code": wrong}), "verify_email", "local:resident001")
    with pytest.raises(ValueError):
        confirm_email(local_container, correct, "verify_email", "local:resident001")
    assert not local_container.store.get_local_credential(username="resident001")["recovery_email"]


def test_email_verification_retires_other_registration_codes(local_container, monkeypatch):
    issued(local_container)
    codes = configured_mail(local_container, monkeypatch)
    first = start_email(local_container, EmailStart(username="resident001", email="first@example.com", current_password="Original123!"), "verify_email", "local:resident001")
    first_code = codes[-1][1]
    second = start_email(local_container, EmailStart(username="resident001", email="second@example.com", current_password="Original123!"), "verify_email", "local:resident001")
    confirm_email(local_container, EmailConfirm(challenge_id=second["challenge_id"], code=codes[-1][1]), "verify_email", "local:resident001")
    with pytest.raises(ValueError):
        confirm_email(local_container, EmailConfirm(challenge_id=first["challenge_id"], code=first_code), "verify_email", "local:resident001")
    assert local_container.store.get_local_credential(username="resident001")["recovery_email"] == "second@example.com"


def test_email_not_sent_to_unverified_address_or_stale_credentials(local_container, monkeypatch):
    issued(local_container)
    codes = configured_mail(local_container, monkeypatch)
    start_email(local_container, EmailStart(username="resident001", email="attacker@example.com"), "reset_password")
    assert not codes
    verify_email(local_container, codes)
    result = start_email(local_container, EmailStart(username="resident001", email="new@example.com", current_password="Original123!"), "verify_email", "local:resident001")
    local_container.local_auth.set_initial_password("local:resident001", "resident.personal", "NewSecret123!")
    with pytest.raises(ValueError):
        confirm_email(local_container, EmailConfirm(challenge_id=result["challenge_id"], code=codes[-1][1]), "verify_email", "local:resident001")
    assert local_container.store.get_local_credential(username="resident.personal")["recovery_email"] == "an@example.com"


@pytest.mark.asyncio
async def test_email_http_flow_generic_and_delivery_failure(local_client, local_container, monkeypatch):
    issued(local_container)
    codes = configured_mail(local_container, monkeypatch)
    verify_email(local_container, codes)
    responses = []
    for username in ("resident001", "missing"):
        result = await local_client.post("/api/v1/auth/recovery/email", json={"username": username, "email": "an@example.com"})
        assert result.status_code == 200
        responses.append(result.json()["message"])
    assert responses[0] == responses[1]
    monkeypatch.setattr("src.services.password_recovery.send_code", lambda *args: (_ for _ in ()).throw(ValueError("email_delivery_unavailable")))
    assert (await local_client.post("/api/v1/auth/recovery/email", json={"username": "resident001", "email": "an@example.com"})).status_code == 200


@pytest.mark.parametrize("reset_mode", ["login", "management"])
def test_local_seed_idempotent_and_reset_preserves_test_accounts(local_container, reset_mode):
    from src.services.demo_data import reset_all_demo_data

    local_container.settings.settings_encryption_key = Fernet.generate_key().decode()
    result = seed_sample_accounts(local_container)
    assert result["tickets_added"] == 30
    assert seed_sample_accounts(local_container)["tickets_added"] == 0
    local_container.settings.show_local_login_credentials = True
    assert len(sample_login_hints(local_container)) == 10
    assert local_container.local_auth.login("demo001", SAMPLE_PASSWORD)["password_change_required"]
    local_container.local_auth.set_initial_password("local:demo001", "demo.personal", "Personal123!")
    old_token = local_container.local_auth.login("demo.personal", "Personal123!")["access_token"]
    credential = local_container.store.get_local_credential(username="demo.personal")
    resident_id = credential["resident_id"]
    local_container.store.update_resident(resident_id, {"full_name": "Tên đã sửa", "vehicle": "car"})
    with local_container.store.session() as session:
        session.get(Resident, resident_id).ownership_confirmed_at = now_utc()
    issued(local_container, "not-retained")
    intake_recovery(local_container.store, RecoveryIntake(username="demo001", full_name="Nguyễn An", apartment="A1201", contact="0900000000", confirmed=True))
    login_reset.capture_baseline(local_container.store, "sunrise-residence")
    reset = login_reset.reset_application if reset_mode == "login" else reset_all_demo_data
    for attempt in range(2):
        if attempt and reset_mode == "login":
            with local_container.store.session() as session:
                session.get(login_reset.ResetBaseline, "R001").last_reset_at -= timedelta(seconds=31)
        reset(local_container)
        for username in (f"demo{index:03d}" for index in range(1, 11)):
            assert local_container.local_auth.login(username, SAMPLE_PASSWORD)["password_change_required"]
            restored = local_container.store.get_local_credential(username=username)
            assert restored["username_change_required"] and restored["password_change_required"]
            assert local_container.store.read_handover(username, local_container.settings.settings_encryption_key)["temporary_password"] == SAMPLE_PASSWORD
            profile_id = local_container.store.get_local_credential(username=username)["resident_id"]
            assert local_container.store.list_tasks(profile_id)
            assert all(task["status"] == "pending" for task in local_container.store.list_tasks(profile_id))
            assert local_container.store.get_resident(profile_id)["ownership_confirmed_at"] is None
    with local_container.store.session() as session:
        assert session.query(LocalCredential).count() == 10
        assert session.query(PasswordRecoveryRequest).count() == 0
        assert session.query(AuthChallenge).count() == 0
        assert session.query(Resident).count() == 11
        assert session.query(ManagementRequest).count() == 0
    assert local_container.store.get_resident(resident_id)["full_name"] == "Nguyễn An"
    assert local_container.store.get_resident(resident_id)["vehicle"] == "none"
    restored = local_container.store.get_local_credential(username="demo001")
    assert restored["resident_id"] == resident_id and restored["auth_subject"] == credential["auth_subject"]
    assert local_container.store.get_local_credential(username="demo.personal") is None
    with pytest.raises(AuthenticationError):
        local_container.local_auth.login("demo.personal", "Personal123!")
    with pytest.raises(AuthenticationError):
        local_container.local_auth.login("demo001", "Personal123!")
    with pytest.raises(AuthenticationError):
        verify_local_token(old_token, local_container.settings, local_container.store)
    assert local_container.store.get_local_credential(username="vuongtq") is None
    assert not local_container.store.get_local_credential(username="not-retained")
    assert len(sample_login_hints(local_container)) == 10
    assert local_container.local_auth.login("admin", "Test-Admin-2026!")
    assert local_container.local_auth.login("user", "Test-Resident-2026!")


def test_replacement_preserves_configured_accounts_and_profile_and_removes_old_residents(local_container):
    app = local_container
    issued(app, "oldresident")
    old = app.store.get_local_credential(username="oldresident")
    old_token = app.local_auth.login("oldresident", "Original123!")["access_token"]
    app.store.create_request(old["resident_id"], "general_support", {"details": "Ticket cũ"}, "old-account-ticket")
    base_ticket = app.store.create_request("R001", "general_support", {"details": "Giữ ticket gốc"}, "keep-user-ticket")
    profile = app.store.get_resident("R001")
    user_token = app.local_auth.login("user", "Test-Resident-2026!")["access_token"]
    sites = {"sample-north": "Khu phía Bắc", "sample-south": "Khu phía Nam"}
    with app.store.session() as session:
        for site_id, name in sites.items():
            session.add(ResidentialSite(id=site_id, name=name))
    app.settings.local_auth_admin_site_ids = ",".join(sites)
    app.local_auth = LocalAuthService(app.settings, app.store)
    result = replace_sample_accounts(app)
    assert result == {"accounts": 10, "tickets_added": 0, "scope": "local_only", "removed_accounts": 1}
    assert app.store.get_resident("R001") == profile
    assert app.store.get_request(base_ticket["request_id"])
    assert not app.store.get_resident(old["resident_id"])
    assert not app.store.get_local_credential(username="oldresident")
    with pytest.raises(AuthenticationError) as failure:
        verify_local_token(old_token, app.settings, app.store)
    assert failure.value.code == "session_expired"
    assert verify_local_token(user_token, app.settings, app.store)["sub"] == "local:user"
    with app.store.session() as session:
        assert session.query(User).count() == 12
        assert session.query(Resident).count() == 11
        assert session.query(ManagementRequest).count() == 1
    entries = app.store.list_resident_accounts({app.settings.local_auth_site_id, *sites})
    assert len(entries) == 10
    assert {item["profile"]["site_id"] for item in entries} == {app.settings.local_auth_site_id, *sites}
    assert len({(item["profile"]["site_id"], item["profile"]["building"], item["profile"]["apartment"]) for item in entries}) == 10
    assert {item["profile"]["resident_type"] for item in entries} == {"owner", "tenant", "family_member"}
    for entry in entries:
        assert app.local_auth.login(entry["username"], SAMPLE_PASSWORD)["password_change_required"]
        assert entry["profile"]["ownership_confirmed_at"] is None


@pytest.mark.asyncio
async def test_manager_reset_restores_all_ten_renamed_accounts_and_handover(local_client, local_container):
    app = local_container
    app.settings.settings_encryption_key = Fernet.generate_key().decode()
    app.settings.login_reset_enabled = True
    app.settings.login_reset_code = "0000"
    seed_sample_accounts(app, include_requests=False)
    original_ids = {}
    for index in range(1, 11):
        username = f"demo{index:03d}"
        original_ids[username] = app.store.get_local_credential(username=username)["resident_id"]
        app.local_auth.set_initial_password(f"local:{username}", f"personal{index:03d}", f"Personal{index:03d}!")
    login_reset.capture_baseline(app.store, "sunrise-residence")
    old_headers = await login(local_client, "admin")
    result = await local_client.post("/api/v1/management/reset", headers=old_headers, json={"code": "0000", "confirm": True})
    assert result.status_code == 200 and result.json()["reset"]
    assert (await local_client.get("/api/v1/management/resident-accounts", headers=old_headers)).status_code == 401
    accounts = await local_client.get("/api/v1/management/resident-accounts", headers=await login(local_client, "admin"))
    assert accounts.status_code == 200
    entries = accounts.json()["items"]
    assert {entry["username"] for entry in entries} == set(original_ids)
    assert all(entry["temporary_password_available"] and entry["password_change_required"] for entry in entries)
    assert SAMPLE_PASSWORD not in accounts.text
    for index in range(1, 11):
        username = f"demo{index:03d}"
        credential = app.store.get_local_credential(username=username)
        assert credential["resident_id"] == original_ids[username]
        assert app.local_auth.login(username, SAMPLE_PASSWORD)["password_change_required"]
        assert credential["username_change_required"]
        assert app.store.read_handover(username, app.settings.settings_encryption_key)["temporary_password"] == SAMPLE_PASSWORD
        assert not app.store.get_local_credential(username=f"personal{index:03d}")
        with pytest.raises(AuthenticationError):
            app.local_auth.login(username, f"Personal{index:03d}!")
    assert app.local_auth.login("user", "Test-Resident-2026!")
    assert app.local_auth.login("admin", "Test-Admin-2026!")


def test_reset_credential_failure_rolls_back_accounts_workflows_and_generation(local_container, monkeypatch):
    app = local_container
    app.settings.settings_encryption_key = Fernet.generate_key().decode()
    seed_sample_accounts(app, include_requests=False)
    app.local_auth.set_initial_password("local:demo001", "personal", "Personal123!")
    credential = app.store.get_local_credential(username="personal")
    ticket = app.store.create_request(credential["resident_id"], "resident_card", {}, "reset-rollback-ticket")
    login_reset.capture_baseline(app.store, "sunrise-residence")
    monkeypatch.setattr(app.store, "save_handover", lambda *args: (_ for _ in ()).throw(ValueError("handover_failed")))
    with pytest.raises(ValueError, match="handover_failed"):
        login_reset.reset_application(app)
    assert app.store.get_local_credential(username="personal") == credential
    assert not app.store.get_local_credential(username="demo001")
    assert app.store.get_request(ticket["request_id"])
    assert app.store.login_reset_generation() == 0
    assert app.local_auth.login("personal", "Personal123!")


def test_replacement_and_reset_keep_existing_named_account_password(local_container):
    app = local_container
    issued(app, "vuongtq")
    app.settings.login_reset_preserved_usernames = " VUONGTQ "
    original = app.store.get_local_credential(username="vuongtq")
    profile = app.store.get_resident(original["resident_id"])
    replace_sample_accounts(app)
    assert app.store.get_local_credential(username="vuongtq") == original
    assert app.store.get_resident(original["resident_id"]) == profile
    login_reset.capture_baseline(app.store, "sunrise-residence")
    login_reset.reset_application(app)
    assert app.store.get_local_credential(username="vuongtq") == original
    assert app.local_auth.login("vuongtq", "Original123!")


def test_replacement_rolls_back_deletions_when_account_creation_fails(local_container, monkeypatch):
    app = local_container
    issued(app, "keep-on-failure")
    credential = app.store.get_local_credential(username="keep-on-failure")
    original = app.local_auth.create_resident_account

    def fail_second_account(values, actor):
        if values["username"] == "demo002":
            raise ValueError("sample_creation_failed")
        return original(values, actor)

    monkeypatch.setattr(app.local_auth, "create_resident_account", fail_second_account)
    with pytest.raises(ValueError, match="sample_creation_failed"):
        replace_sample_accounts(app)
    assert app.store.get_local_credential(username="keep-on-failure") == credential
    assert not app.store.get_local_credential(username="demo001")
    assert app.local_auth.login("keep-on-failure", "Original123!")


def test_replacement_rejects_production_without_changing_accounts(local_container):
    app = local_container
    issued(app, "keep-in-production")
    credential = app.store.get_local_credential(username="keep-in-production")
    app.settings.app_env = "production"
    with pytest.raises(ValueError, match="demo_environment_not_allowed"):
        replace_sample_accounts(app)
    assert app.store.get_local_credential(username="keep-in-production") == credential


@pytest.mark.asyncio
async def test_manager_agent_returns_scoped_account_reset_action(local_container):
    from src.services.manager_assistant import manager_answer
    issued(local_container)
    issued(local_container, "outside", "another-site")
    result = await manager_answer(local_container, {"sunrise-residence"}, "Cấp lại mật khẩu resident001")
    assert [item["username"] for item in result["accounts"]] == ["resident001"]
    assert result["read_only"]
    assert local_container.local_auth.login("resident001", "Original123!")
