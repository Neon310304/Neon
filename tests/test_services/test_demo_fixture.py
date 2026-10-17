from __future__ import annotations

from copy import deepcopy

import pytest

from src.services.demo_fixture import DemoFixtureError, fixture_digest, load_demo_fixture, validate_demo_fixture


def test_demo_fixture_is_versioned_and_deterministic() -> None:
    first = load_demo_fixture()
    second = load_demo_fixture()

    assert first["fixture_version"] == "1.0.0"
    assert fixture_digest(first) == fixture_digest(second)
    assert len(first["ruleset"]["procedures"]) == 5


def test_vehicle_profiles_have_reviewable_expected_difference() -> None:
    fixture = load_demo_fixture()
    profiles = {profile["profile_id"]: profile for profile in fixture["profiles"]}
    with_car = fixture["expected_checklists"]["profile-owner-car"]["procedure_codes"]
    without_vehicle = fixture["expected_checklists"]["profile-owner-no-vehicle"]["procedure_codes"]

    comparable_car = {
        key: value
        for key, value in profiles["profile-owner-car"].items()
        if key not in {"profile_id", "residence_ref", "vehicle"}
    }
    comparable_no_vehicle = {
        key: value
        for key, value in profiles["profile-owner-no-vehicle"].items()
        if key not in {"profile_id", "residence_ref", "vehicle"}
    }

    assert comparable_car == comparable_no_vehicle
    assert profiles["profile-owner-car"]["vehicle"] == "car"
    assert profiles["profile-owner-no-vehicle"]["vehicle"] == "none"
    assert set(with_car) - set(without_vehicle) == {"vehicle_registration"}


@pytest.mark.parametrize("missing_field", ["owner", "site_id", "effective_from", "effective_to"])
def test_source_missing_required_metadata_is_rejected(missing_field: str) -> None:
    fixture = deepcopy(load_demo_fixture())
    fixture["sources"][0].pop(missing_field)

    with pytest.raises(DemoFixtureError, match="missing required fields"):
        validate_demo_fixture(fixture)


def test_personal_data_field_is_rejected() -> None:
    fixture = deepcopy(load_demo_fixture())
    fixture["profiles"][0]["phone"] = "0000000000"

    with pytest.raises(DemoFixtureError, match="forbidden personal-data fields"):
        validate_demo_fixture(fixture)


def test_overlapping_published_sources_are_rejected_as_conflicting() -> None:
    fixture = deepcopy(load_demo_fixture())
    conflicting_source = deepcopy(fixture["sources"][0])
    conflicting_source["source_id"] = "source-residency-conflict"
    conflicting_source["version"] = "2.0.0"
    fixture["sources"].append(conflicting_source)

    with pytest.raises(DemoFixtureError, match="conflicts with another published source"):
        validate_demo_fixture(fixture)


def test_expected_answer_must_explain_every_procedure() -> None:
    fixture = deepcopy(load_demo_fixture())
    fixture["expected_checklists"]["profile-owner-car"]["reason_codes"].pop("resident_card")

    with pytest.raises(DemoFixtureError, match="must explain every procedure"):
        validate_demo_fixture(fixture)
