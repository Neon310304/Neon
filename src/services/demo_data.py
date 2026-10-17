from typing import TYPE_CHECKING, Any

from src.repositories.store import DEMO_RESIDENT_ID, LocalCredential
from src.services.login_reset import preserved_account_subjects, restore_preserved_profiles

if TYPE_CHECKING:
    from src.container import AppContainer


def seed_demo_data(
    app: "AppContainer", *, include_requests: bool = False
) -> dict[str, Any]:
    """Seed the demo context; optionally populate the command's request scenario."""
    app.settings.validate_demo_database_target()
    with app.store.transaction():
        app.store.seed_demo()
        checklist = app.tool_service.build_checklist(DEMO_RESIDENT_ID)
        if include_requests and not app.store.list_requests(
            DEMO_RESIDENT_ID, request_type="resident_card"
        ):
            # Reuse business/idempotency constraints without changing an existing
            # request's payload, decision or timeline on repeated seed runs.
            # A pre-existing terminal request with another key also satisfies
            # the scenario; seed must not open a new ticket after its decision.
            app.store.create_request(
                DEMO_RESIDENT_ID,
                "resident_card",
                {},
                "moveinmate-demo-v1-resident-card",
            )
    return {
        "resident_id": DEMO_RESIDENT_ID,
        "checklist_count": len(checklist["items"]),
    }


def reset_demo_data(app: "AppContainer") -> dict[str, Any]:
    """Reset only the stable synthetic resident and rebuild its checklist."""
    app.settings.validate_demo_reset()
    with app.store.transaction():
        app.store.reset_demo()
        checklist = app.tool_service.build_checklist(DEMO_RESIDENT_ID)
    return {
        "resident_id": DEMO_RESIDENT_ID,
        "checklist_count": len(checklist["items"]),
    }


def reset_all_demo_data(app: "AppContainer") -> dict[str, Any]:
    """Reset all demo residents/workflows atomically, preserving configured login."""
    app.settings.validate_demo_reset()
    with app.store.transaction():
        with app.store.session() as session:
            app.store._acquire_demo_lock(session)
        subjects = preserved_account_subjects(app)
        app.store.reset_all_demo(subjects)
        with app.store.session() as session:
            resident_ids = tuple(resident_id for (resident_id,) in session.query(LocalCredential.resident_id))
        restore_preserved_profiles(app, resident_ids)
        app.local_auth.provision_accounts()
        if set(app.local_auth.accounts) == {"user", "admin"}:
            from src.services.demo_accounts import restore_sample_credentials

            restore_sample_credentials(app)
        checklist = app.tool_service.build_checklist(DEMO_RESIDENT_ID)
        for resident_id in resident_ids:
            app.tool_service.build_checklist(resident_id)
    return {
        "resident_id": DEMO_RESIDENT_ID,
        "checklist_count": len(checklist["items"]),
        "scope": "all_demo_data",
    }
