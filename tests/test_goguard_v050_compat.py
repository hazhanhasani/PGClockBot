"""Version 0.5.0: provider-compatible catalog, renewals and adapter safety."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.goguard import GoGuardClient
from app.services.orders import ensure_plan_compatible_with_panel
from app.services.pg_access import staff_pg_action
from app.services.service_renewals import calculate_renewal, renewal_terms
from app.version import GITHUB_REPO, __version__


GB = 1024 ** 3
DAY = 86400


def test_version_and_updates_target_this_fork() -> None:
    assert __version__ == "0.5.0"
    assert Path("VERSION").read_text(encoding="utf-8").strip() == __version__
    assert GITHUB_REPO == "hazhanhasani/PGClockBot"


def test_goguard_rejects_template_only_catalog_before_payment() -> None:
    with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="goguard")):
        with pytest.raises(ValueError, match="GoGuard"):
            ensure_plan_compatible_with_panel(SimpleNamespace(pg_template_id=42, pg_group_ids=None))
        ensure_plan_compatible_with_panel(SimpleNamespace(pg_template_id=None, pg_group_ids="5,12"))


def test_pasarguard_catalog_keeps_template_support() -> None:
    with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="pasarguard")):
        ensure_plan_compatible_with_panel(SimpleNamespace(pg_template_id=42, pg_group_ids=None))


def test_goguard_on_hold_renewal_never_sends_unavailable_status() -> None:
    now = 1_700_000_000
    current = {
        "status": "on_hold", "data_limit": 10 * GB, "used_traffic": 2 * GB,
        "expire": 0, "on_hold_expire_duration": 5 * DAY,
    }
    terms = renewal_terms(SimpleNamespace(data_limit_gb=10, duration_days=30))
    result = calculate_renewal(current, terms, now=now, provider="goguard")
    assert result["payload"]["status"] == "active"
    assert result["payload"]["expire"] == now + 35 * DAY
    assert "on_hold_expire_duration" not in result["payload"]
    assert any("GoGuard" in warning for warning in result["warnings"])
    # PasarGuard still preserves its original pending-connect semantics.
    pg = calculate_renewal(current, terms, now=now)
    assert pg["payload"]["status"] == "on_hold"
    assert pg["payload"]["expire"] == 0


def test_goguard_hides_unavailable_management_mutations() -> None:
    with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="goguard")):
        assert staff_pg_action({"role": "admin"}, "nodes", "create") is False
        assert staff_pg_action({"role": "admin"}, "groups", "update") is False
        assert staff_pg_action({"role": "admin"}, "templates", "delete") is False
        assert staff_pg_action({"role": "admin"}, "admins", "create") is False


def test_goguard_stale_username_cache_cannot_target_new_user_id() -> None:
    async def scenario() -> None:
        client = GoGuardClient(
            base_url="https://goguard.example.test",
            username="seller", api_key="test-key",
        )
        try:
            client._id_username_cache[100] = "reused"
            client.get_user_by_username = AsyncMock(return_value={"id": 200, "username": "reused"})
            client.get_users = AsyncMock(return_value={
                "items": [{"id": 100, "username": "correct"}], "total": 1
            })
            actual = await client.get_user_by_id(100)
            assert actual["id"] == 100
            assert actual["username"] == "correct"
            assert client._id_username_cache[100] == "correct"
        finally:
            await client.close()
    asyncio.run(scenario())


def test_bot_and_login_surfaces_show_selected_provider() -> None:
    bot = Path("app/bot/handlers/admin_pg_users.py").read_text(encoding="utf-8")
    login = Path("app/web/templates/login.html").read_text(encoding="utf-8")
    assert 'if get_settings().panel_provider == "goguard":' in bot
    assert 'text="➕ ساخت اشتراک (سرویس + حجم + روز)"' in bot
    assert "active_panel_provider()" in login
