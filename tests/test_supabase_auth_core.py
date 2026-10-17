"""Checks for legacy Supabase configuration rejection and role pairing."""

from src.security import AuthContext, can_access_resident, public_auth_config


def test_role_site_pairing_does_not_grant_manager_site_to_resident():
    context = AuthContext(
        auth_subject="subject",
        user_id="user",
        roles=frozenset({"resident", "manager"}),
        role_site_ids={"resident": frozenset({"site-resident"}), "manager": frozenset({"site-manager"})},
        role_resident_ids={"resident": frozenset({"R001"})},
    )

    assert context.resident_site_ids == frozenset({"site-resident"})
    assert context.manager_site_ids == frozenset({"site-manager"})
    assert can_access_resident(context, "R001")
    assert "site-manager" not in context.resident_site_ids


def test_public_auth_config_rejects_legacy_service_role_jwt_key():
    settings = type(
        "Settings",
        (),
        {
            "auth_mode": "required",
            "auth_provider": "supabase",
            "app_env": "production",
            "supabase_url": "https://project.supabase.co",
            "supabase_anon_key": "eyJhbGciOiJub25lIn0.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.signature",
        },
    )()

    config = public_auth_config(settings)
    assert config == {"auth_mode": "required", "auth_provider": "local", "enabled": False}


def test_public_auth_config_ignores_legacy_publishable_key_and_hides_secrets():
    settings = type(
        "Settings",
        (),
        {
            "auth_mode": "required",
            "auth_provider": "supabase",
            "app_env": "production",
            "supabase_url": "https://project.supabase.co",
            "supabase_anon_key": "sb_publishable_example",
            "auth_jwt_secret": "must-not-leak",
        },
    )()

    assert public_auth_config(settings) == {
        "auth_mode": "required",
        "auth_provider": "local",
        "enabled": False,
    }
