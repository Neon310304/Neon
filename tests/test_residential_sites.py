import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.import_residential_sites import load_sites
from src.repositories.store import ResidenceMembership, Resident, ResidentialUnit, User
from src.security import LocalAuthService
from src.security.errors import AuthenticationError
from src.security.local_auth import verify_local_token
from tests.test_local_auth_api import local_client as local_client
from tests.test_local_auth_api import local_container as local_container
from tests.test_local_auth_api import login
from tests.test_resident_portal import account_payload

CATALOG = Path("scripts/residential_sites.json")


def configure_sites(container, sites):
    container.settings = container.settings.model_copy(update={"local_auth_admin_site_ids": ",".join(sites)})
    container.local_auth = LocalAuthService(container.settings, container.store)
    container.local_auth.provision_accounts()


def test_catalog_import_is_idempotent_without_fake_residents_or_permissions(local_container):
    records = load_sites(CATALOG)
    store = local_container.store
    with store.session() as session:
        counts = (session.query(Resident).count(), session.query(User).count(), session.query(ResidenceMembership).count())
    assert store.import_sites(records) == 9
    assert store.import_sites(records) == 9
    sites = store.list_sites({record["id"] for record in records})
    assert len(sites) == 9
    assert all(item["source_url"].startswith("https://") for item in sites)
    locations = store.list_locations({record["id"] for record in records})
    assert len(locations) == 9 * 800
    for record in records:
        units = [item for item in locations if item["site_id"] == record["id"]]
        assert len(units) == 800
        assert {item["building"] for item in units} == {"A", "B", "C", "D"}
        assert {item["floor"] for item in units} == set(range(1, 21))
        assert {item["room"] for item in units} == set(range(1, 11))
        assert all(not item["owner_registered"] for item in units)
        assert len({item["apartment"] for item in units}) == 800
    with store.session() as session:
        assert counts == (session.query(Resident).count(), session.query(User).count(), session.query(ResidenceMembership).count())
        assert session.query(ResidentialUnit).count() == 10 * 800


@pytest.mark.asyncio
async def test_manager_can_create_first_resident_in_authorized_catalog_site(local_client, local_container):
    records = load_sites(CATALOG)
    local_container.store.import_sites(records)
    configure_sites(local_container, [record["id"] for record in records])
    headers = await login(local_client, "admin")
    locations = (await local_client.get("/api/v1/locations", headers=headers)).json()
    assert len(locations["sites"]) == 10
    assert len(locations["items"]) == 10 * 800
    response = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(site_id="vinhomes-smart-city"))
    assert response.status_code == 200
    assert response.json()["profile"]["site_name"] == "Vinhomes Smart City"
    signed_in = await local_client.post("/api/v1/auth/login", json={"username": "resident.new", "password": account_payload()["password"]})
    resident_headers = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    assert signed_in.json()["password_change_required"] is True
    assert (await local_client.get("/api/v1/locations", headers=resident_headers)).status_code == 403
    changed = await local_client.post("/api/v1/auth/password/first-login", headers=resident_headers, json={"username": "resident.personal", "new_password": "New-Private-2026!"})
    assert changed.status_code == 204
    signed_in = await local_client.post("/api/v1/auth/login", json={"username": "resident.personal", "password": "New-Private-2026!"})
    resident_headers = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    resident_locations = (await local_client.get("/api/v1/locations", headers=resident_headers)).json()
    assert [item["site_id"] for item in resident_locations["sites"]] == ["vinhomes-smart-city"]
    assert (await local_client.get("/api/v1/management/resident-accounts", headers=resident_headers)).status_code == 403
    existing_resident = (await local_client.get("/api/v1/locations", headers=await login(local_client, "user"))).json()
    assert [item["site_id"] for item in existing_resident["sites"]] == ["sunrise-residence"]


@pytest.mark.asyncio
async def test_public_catalog_does_not_grant_manager_access_automatically(local_client, local_container):
    local_container.store.import_sites(load_sites(CATALOG))
    headers = await login(local_client, "admin")
    locations = (await local_client.get("/api/v1/locations", headers=headers)).json()
    assert [item["site_id"] for item in locations["sites"]] == ["sunrise-residence"]
    response = await local_client.post("/api/v1/management/resident-accounts", headers=headers, json=account_payload(site_id="vinhomes-smart-city"))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_manager_scope_survives_restart_and_explicit_removal_revokes_access(local_client, local_container):
    resident_headers = await login(local_client, "user")
    configure_sites(local_container, ["vinhomes-smart-city", "vinhomes-ocean-park"])
    manager_headers = await login(local_client, "admin")
    local_container.local_auth.provision_accounts()
    local_container.local_auth = LocalAuthService(local_container.settings, local_container.store)
    local_container.local_auth.provision_accounts()
    context = (await local_client.get("/api/v1/me/context", headers=manager_headers)).json()
    assert set(context["manager_site_ids"]) == {"sunrise-residence", "vinhomes-smart-city", "vinhomes-ocean-park"}
    configure_sites(local_container, ["vinhomes-smart-city"])
    assert (await local_client.get("/api/v1/me/context", headers=manager_headers)).status_code == 401
    context = (await local_client.get("/api/v1/me/context", headers=await login(local_client, "admin"))).json()
    assert set(context["manager_site_ids"]) == {"sunrise-residence", "vinhomes-smart-city"}
    assert (await local_client.get("/api/v1/me/context", headers=resident_headers)).status_code == 200


@pytest.mark.asyncio
async def test_scope_configuration_change_invalidates_old_manager_token(local_client, local_container):
    token = (await login(local_client, "admin"))["Authorization"][7:]
    configure_sites(local_container, ["vinhomes-smart-city"])
    with pytest.raises(AuthenticationError):
        verify_local_token(token, local_container.settings, local_container.store)


@pytest.mark.parametrize("change", ["duplicate", "invalid-id", "invalid-url"])
def test_catalog_rejects_invalid_import_before_writing(tmp_path, change):
    records = json.loads(CATALOG.read_text(encoding="utf-8"))
    if change == "duplicate":
        records.append(records[0])
    elif change == "invalid-id":
        records[0]["id"] = "invalid site"
    else:
        records[0]["source_url"] = "not-a-url"
    path = tmp_path / "sites.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_sites(path)
