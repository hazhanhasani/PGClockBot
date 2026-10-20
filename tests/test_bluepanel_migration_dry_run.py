"""Source-inspired local-only GoGuard migration planning tests."""
from __future__ import annotations

from types import SimpleNamespace

from scripts.preview_provider_migration import classify_plan_mapping


def test_template_only_plan_needs_explicit_goguard_service_mapping() -> None:
    plan = SimpleNamespace(pg_template_id=123, pg_group_ids=None)
    assert classify_plan_mapping(plan) == "template_only"


def test_empty_plan_needs_mapping() -> None:
    plan = SimpleNamespace(pg_template_id=None, pg_group_ids="")
    assert classify_plan_mapping(plan) == "unmapped"


def test_existing_service_ids_are_only_a_local_readiness_hint() -> None:
    # These are NOT proof that service IDs match a different panel!
    plan = SimpleNamespace(pg_template_id=123, pg_group_ids="2, 5")
    assert classify_plan_mapping(plan) == "has_service_ids"


def test_invalid_group_ids_are_not_treated_as_a_valid_mapping() -> None:
    plan = SimpleNamespace(pg_template_id=None, pg_group_ids="one,two")
    assert classify_plan_mapping(plan) == "unmapped"
