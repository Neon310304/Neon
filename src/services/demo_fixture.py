"""Load and validate the versioned MoveInMate demo fixture.

The fixture is synthetic planning data for MVP evaluation. It is not an
approved operating procedure and must never contain resident documents or PII.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_FIXTURE_PATH = Path(__file__).parents[2] / "eval" / "fixtures" / "moveinmate_demo_v1.json"

EXPECTED_PROCEDURES = {
    "residency_confirmation",
    "resident_card",
    "house_rules",
    "vehicle_registration",
    "internet_registration",
}

FORBIDDEN_PERSONAL_DATA_KEYS = {
    "attachment_content",
    "date_of_birth",
    "document_number",
    "email",
    "full_name",
    "identity_number",
    "national_id",
    "phone",
}


class DemoFixtureError(ValueError):
    """Raised when the demo fixture violates its contract."""


def load_demo_fixture(path: Path | str = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    """Load a demo fixture and reject malformed or unsafe data."""
    fixture_path = Path(path)
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoFixtureError(f"Cannot load demo fixture: {fixture_path}") from exc

    validate_demo_fixture(fixture)
    return fixture


def validate_demo_fixture(fixture: dict[str, Any]) -> None:
    """Validate the minimum stable contract needed by later MVP tasks."""
    _require_fields(
        fixture,
        {
            "fixture_id",
            "fixture_version",
            "data_classification",
            "contains_personal_data",
            "disclaimer",
            "site",
            "ruleset",
            "resident_card_schema",
            "sources",
            "profiles",
            "expected_checklists",
        },
        "fixture",
    )
    if fixture["data_classification"] != "synthetic_demo_only":
        raise DemoFixtureError("fixture.data_classification must be synthetic_demo_only")
    if fixture["contains_personal_data"] is not False:
        raise DemoFixtureError("fixture must explicitly declare contains_personal_data=false")
    if not fixture["disclaimer"].strip():
        raise DemoFixtureError("fixture.disclaimer must state the demo limitation")

    forbidden_paths = _find_forbidden_keys(fixture)
    if forbidden_paths:
        raise DemoFixtureError(f"fixture contains forbidden personal-data fields: {forbidden_paths}")

    site = fixture["site"]
    _require_fields(site, {"site_id", "site_code", "display_name", "is_demo"}, "site")
    if site["is_demo"] is not True:
        raise DemoFixtureError("site.is_demo must be true")
    site_id = site["site_id"]

    ruleset = fixture["ruleset"]
    _require_fields(ruleset, {"ruleset_id", "version", "status", "schema_version", "procedures"}, "ruleset")
    if ruleset["status"] != "demo_pending_bql_review":
        raise DemoFixtureError("ruleset.status must be demo_pending_bql_review")
    procedures = ruleset["procedures"]
    procedure_codes = [procedure.get("procedure_code") for procedure in procedures]
    if len(procedure_codes) != len(set(procedure_codes)):
        raise DemoFixtureError("ruleset procedure_code values must be unique")
    if set(procedure_codes) != EXPECTED_PROCEDURES:
        raise DemoFixtureError("ruleset must contain exactly the five MVP procedure groups")
    for index, procedure in enumerate(procedures):
        _require_fields(
            procedure,
            {
                "procedure_code",
                "title",
                "required",
                "applies_when",
                "reason_code",
                "dependencies",
                "completion_mode",
            },
            f"ruleset.procedures[{index}]",
        )

    card_schema = fixture["resident_card_schema"]
    _require_fields(
        card_schema,
        {"schema_id", "version", "site_id", "status", "required_fields", "required_documents_by_resident_type"},
        "resident_card_schema",
    )
    if card_schema["site_id"] != site_id:
        raise DemoFixtureError("resident_card_schema.site_id must match site.site_id")
    if card_schema["status"] != "demo_pending_bql_review":
        raise DemoFixtureError("resident_card_schema.status must be demo_pending_bql_review")

    sources = fixture["sources"]
    if not sources:
        raise DemoFixtureError("fixture.sources must not be empty")
    source_ids: set[str] = set()
    active_ranges: dict[str, list[tuple[date, date]]] = {}
    for index, source in enumerate(sources):
        path = f"sources[{index}]"
        _require_fields(
            source,
            {
                "source_id",
                "version",
                "site_id",
                "procedure_code",
                "owner",
                "status",
                "effective_from",
                "effective_to",
                "title",
                "chunks",
            },
            path,
        )
        if source["source_id"] in source_ids:
            raise DemoFixtureError("source_id values must be unique")
        source_ids.add(source["source_id"])
        if source["site_id"] != site_id:
            raise DemoFixtureError(f"{path}.site_id must match site.site_id")
        if source["procedure_code"] not in EXPECTED_PROCEDURES:
            raise DemoFixtureError(f"{path}.procedure_code is unknown")
        if not source["owner"].strip():
            raise DemoFixtureError(f"{path}.owner must not be empty")
        effective_from = _parse_date(source["effective_from"], f"{path}.effective_from")
        effective_to = _parse_date(source["effective_to"], f"{path}.effective_to")
        if effective_from > effective_to:
            raise DemoFixtureError(f"{path} effective date range is invalid")
        if source["status"] == "published_demo":
            ranges = active_ranges.setdefault(source["procedure_code"], [])
            if any(
                effective_from <= current_to and current_from <= effective_to for current_from, current_to in ranges
            ):
                raise DemoFixtureError(f"{path} conflicts with another published source for this procedure")
            ranges.append((effective_from, effective_to))
        if not source["chunks"]:
            raise DemoFixtureError(f"{path}.chunks must not be empty")
        for chunk_index, chunk in enumerate(source["chunks"]):
            _require_fields(chunk, {"chunk_id", "section", "content"}, f"{path}.chunks[{chunk_index}]")
    if set(active_ranges) != EXPECTED_PROCEDURES:
        raise DemoFixtureError("fixture must have one non-conflicting published source for every procedure")

    profiles = fixture["profiles"]
    if len(profiles) < 2:
        raise DemoFixtureError("fixture needs at least two profiles")
    profile_ids: set[str] = set()
    for index, profile in enumerate(profiles):
        path = f"profiles[{index}]"
        _require_fields(
            profile,
            {"profile_id", "site_id", "residence_ref", "resident_type", "member_count", "vehicle", "internet_needed"},
            path,
        )
        if profile["profile_id"] in profile_ids:
            raise DemoFixtureError("profile_id values must be unique")
        profile_ids.add(profile["profile_id"])
        if profile["site_id"] != site_id:
            raise DemoFixtureError(f"{path}.site_id must match site.site_id")
        if profile["resident_type"] not in {"owner", "tenant", "member"}:
            raise DemoFixtureError(f"{path}.resident_type is invalid")
        if not isinstance(profile["member_count"], int) or profile["member_count"] < 1:
            raise DemoFixtureError(f"{path}.member_count must be a positive integer")
        if profile["vehicle"] not in {"none", "motorbike", "car"}:
            raise DemoFixtureError(f"{path}.vehicle is invalid")
        if not isinstance(profile["internet_needed"], bool):
            raise DemoFixtureError(f"{path}.internet_needed must be boolean")

    expected = fixture["expected_checklists"]
    if set(expected) != profile_ids:
        raise DemoFixtureError("expected_checklists must contain exactly one answer for each profile")
    for profile_id, answer in expected.items():
        _require_fields(answer, {"procedure_codes", "reason_codes"}, f"expected_checklists.{profile_id}")
        codes = answer["procedure_codes"]
        if len(codes) != len(set(codes)) or not set(codes).issubset(EXPECTED_PROCEDURES):
            raise DemoFixtureError(f"expected_checklists.{profile_id} contains invalid procedure codes")
        if set(answer["reason_codes"]) != set(codes):
            raise DemoFixtureError(f"expected_checklists.{profile_id} must explain every procedure")


def fixture_digest(fixture: dict[str, Any]) -> str:
    """Return a stable digest so identical versioned data has identical evidence."""
    validate_demo_fixture(fixture)
    canonical = json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_fields(value: Any, required: set[str], path: str) -> None:
    if not isinstance(value, dict):
        raise DemoFixtureError(f"{path} must be an object")
    missing = sorted(required - set(value))
    if missing:
        raise DemoFixtureError(f"{path} is missing required fields: {missing}")


def _parse_date(value: Any, path: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DemoFixtureError(f"{path} must be an ISO date") from exc


def _find_forbidden_keys(value: Any, path: str = "fixture") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_PERSONAL_DATA_KEYS:
                found.append(child_path)
            found.extend(_find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden_keys(child, f"{path}[{index}]"))
    return found
