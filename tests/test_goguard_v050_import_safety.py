"""GoGuard import, staff cleanup, and multi-credential read regression tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.pg_staff_access import purge_orphaned_staff_access
from app.services.service_import import (
    clear_identity_cache, own_admin_identity, verify_link,
)


def test_current_only_admin_endpoint_never_purges_unrelated_staff() -> None:
    async def scenario() -> None:
        with patch("app.config.get_settings", return_value=SimpleNamespace(panel_provider="goguard")):
            assert await purge_orphaned_staff_access(None) == 0
    asyncio.run(scenario())


def test_service_import_identity_cache_is_partitioned_by_credentials() -> None:
    async def scenario() -> None:
        clear_identity_cache()
        a = SimpleNamespace(
            base_url="https://panel.test",
            _cache_partition="owner-token",
            get_current_admin=AsyncMock(return_value={"username": "owner"}),
        )
        b = SimpleNamespace(
            base_url="https://panel.test",
            _cache_partition="seller-token",
            get_current_admin=AsyncMock(return_value={"username": "seller"}),
        )
        assert await own_admin_identity(a) == "owner"
        assert await own_admin_identity(b) == "seller"
        assert await own_admin_identity(a) == "owner"
        assert a.get_current_admin.await_count == 1
        assert b.get_current_admin.await_count == 1
        clear_identity_cache()
    asyncio.run(scenario())


def test_verified_goguard_tag_is_added_to_allowed_subscription_paths() -> None:
    async def scenario() -> None:
        client = SimpleNamespace(
            get_current_admin=AsyncMock(
                return_value={"username": "seller", "subscription_tag": "blue"}
            )
        )
        info = {"username": "test_12"}
        record = {"id": 34, "username": "test_12", "owner_username": "seller"}
        with (
            patch("app.services.service_import.panel_client", AsyncMock(return_value=client)),
            patch("app.services.service_import.allowed_admins", AsyncMock(return_value=frozenset({"seller"}))),
            patch("app.services.service_import.allowed_paths", return_value=("/sub",)),
            patch("app.services.service_import._subscription_info", AsyncMock(return_value=(info, "/blue"))) as probe,
            patch("app.services.service_import._panel_record", AsyncMock(return_value=record)),
            patch("app.services.service_import.get_settings", return_value=SimpleNamespace(panel_provider="goguard")),
        ):
            found = await verify_link(
                None,
                raw_link="https://panel.test/blue/abcdefghijkl",
                shop_id=None,
                config=object(),
            )
            assert found.panel_id == 34
            assert found.prefix == "/blue"
            assert "/blue" in probe.await_args.args[2]
    asyncio.run(scenario())
