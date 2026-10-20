"""Guard reseller visibility and payment safety for GoGuard and PasarGuard."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.identity_chrome import hierarchy_identity
from app.services.representative_unification import (
    staff_can_manage_representatives, staff_can_view_representatives,
)
from app.services.resellers import (
    create_application, get_reseller_pg_panel_base_url, provision_reseller,
)

ROOT = Path(__file__).resolve().parents[1]


def _owner() -> dict:
    return {
        "role": "admin", "org_depth": 0, "org_principal_id": 1,
        "org_parent_id": None, "org_status": "active", "pg_is_owner": False,
    }


def test_goguard_owner_can_view_without_unsupported_admin_create() -> None:
    with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="goguard")):
        staff = _owner()
        assert staff_can_view_representatives(staff)
        assert not staff_can_manage_representatives(staff)
        identity = hierarchy_identity(staff)
        assert identity["kind"] == "owner"
        assert identity["can_view_representatives"]
        assert not identity["can_manage_representatives"]


def test_non_owner_cannot_gain_cross_tenant_list_access() -> None:
    with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="goguard")):
        sticky_admin = {"role": "admin"}
        l2 = {"role": "principal", "org_depth": 2, "org_principal_id": 55, "org_status": "active"}
        assert not staff_can_view_representatives(sticky_admin)
        assert not staff_can_view_representatives(l2)
        assert not staff_can_manage_representatives(sticky_admin)


def test_goguard_never_takes_money_for_unsupported_new_reseller() -> None:
    async def check():
        settings = SimpleNamespace(panel_provider="goguard")
        with patch("app.config.get_settings", return_value=settings):
            with pytest.raises(ValueError, match="پرداخت دریافت نمی‌شود"):
                await create_application(
                    None,
                    user=SimpleNamespace(role="user", id=1),
                    plan=SimpleNamespace(price=250_000),
                )
            with pytest.raises(ValueError, match="GoGuard"):
                await provision_reseller(
                    None,
                    user=SimpleNamespace(id=1),
                    plan=SimpleNamespace(price=250_000),
                )
    asyncio.run(check())


def test_goguard_reseller_card_uses_active_panel_not_pasarguard_url() -> None:
    async def check():
        with patch(
            "app.config.get_settings",
            return_value=SimpleNamespace(
                panel_provider="goguard",
                goguard_base_url="https://go.example.test",
                pg_base_url="https://pg.example.test",
            ),
        ):
            url = await get_reseller_pg_panel_base_url(None)
            assert url == "https://go.example.test"
    asyncio.run(check())


def test_bot_hub_hides_create_without_hiding_list_or_apps() -> None:
    from app.bot.nav_inline import admin_resellers_hub_keyboard
    keyboard = admin_resellers_hub_keyboard({}, can_create=False)
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    assert "adm:resellers:list:0" in callbacks
    assert "adm:resapp:list" in callbacks
    assert "adm:resellers:add" not in callbacks


def test_web_views_use_distinct_read_and_write_gates() -> None:
    src = (ROOT / "app/api/reseller_pages.py").read_text(encoding="utf-8")
    assert "staff_can_view_representatives(staff)" in src
    assert "can_manage_reps" in src
    assert "if get_settings().panel_provider == \"goguard\":" in src
    bot = (ROOT / "app/bot/handlers/admin.py").read_text(encoding="utf-8")
    assert "if await platform_can_manage_representatives()" in bot
    assert "read-only" in bot.lower()


def test_reseller_dashboard_has_working_shortcut_callbacks() -> None:
    src = (ROOT / "app/bot/handlers/reseller.py").read_text(encoding="utf-8")
    for callback in ("res:users:0", "res:orders", "res:payments", "res:tickets"):
        assert callback in src
