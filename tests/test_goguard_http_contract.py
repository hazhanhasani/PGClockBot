"""HTTP contract tests for the documented GoGuard / SolidLayer Swagger API.

Uses httpx.MockTransport: no real credentials, external requests or mutations.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx

from app.services.goguard import GoGuardClient


def test_subscription_create_update_disable_delete_and_service_discovery() -> None:
    async def run() -> None:
        events = []
        subscription = {
            "id": 51, "username": "buyer_51", "enabled": True,
            "limit_usage": 5 * 1024**3, "total_usage": 0,
            "limit_expire": 1_900_000_000, "services": [2],
            "access_key": "sampletesttoken",
            "subscription_link": "https://panel.example.test/blue/sampletesttoken",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("X-Api-Key") == "goguard-test"
            assert "authorization" not in request.headers
            path = request.url.path
            method = request.method
            events.append((method, path))
            if (method, path) == ("GET", "/api/admins/current"):
                return httpx.Response(200, json={"username": "seller", "role": "seller", "api_key": "server-secret"})
            if (method, path) == ("GET", "/api/services"):
                return httpx.Response(200, json={"items": [{"id": 2, "name": "Europe"}], "total": 1})
            if (method, path) == ("GET", "/api/subscriptions"):
                return httpx.Response(200, json={"items": [dict(subscription)], "total": 1})
            if (method, path) == ("POST", "/api/subscriptions"):
                body = __import__("json").loads(request.content)
                assert isinstance(body, list) and body[0]["services"] == [2]
                return httpx.Response(201, json=[dict(subscription)])
            if (method, path) == ("PUT", "/api/subscriptions"):
                body = __import__("json").loads(request.content)
                assert body["usernames"] == ["buyer_51"]
                subscription["limit_usage"] = body["limit_usage"]
                return httpx.Response(200, json=[dict(subscription)])
            if (method, path) == ("POST", "/api/subscriptions/disable"):
                subscription["enabled"] = False
                return httpx.Response(200, json={"message": "disabled"})
            if (method, path) == ("DELETE", "/api/subscriptions"):
                body = __import__("json").loads(request.content)
                assert body == {"usernames": ["buyer_51"]}
                return httpx.Response(200, json={"message": "deleted"})
            raise AssertionError(f"Unexpected GoGuard API route {method} {path}")

        client = GoGuardClient(
            base_url="https://panel.example.test", username="seller",
            api_key="goguard-test",
        )
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="https://panel.example.test",
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        )
        client._ensure_safe_target = AsyncMock()
        client._ensure_api_base = AsyncMock()
        try:
            admin = await client.get_current_admin()
            assert "api_key" not in admin
            services = await client.get_groups_simple()
            assert len(services) == 1 and services[0]["id"] == 2
            created = await client.create_user({
                "username": "buyer_51", "group_ids": [2], "data_limit": 5 * 1024**3,
                "expire": 1_900_000_000,
            })
            assert created["id"] == 51
            updated = await client.modify_user_by_id(51, {"data_limit": 6 * 1024**3})
            assert updated["data_limit"] == 6 * 1024**3
            disabled = await client.set_disabled_by_id(51, True)
            assert disabled["status"] == "disabled"
            await client.delete_user_by_id(51)
            assert ("POST", "/api/subscriptions") in events
            assert ("PUT", "/api/subscriptions") in events
            assert ("POST", "/api/subscriptions/disable") in events
            assert ("DELETE", "/api/subscriptions") in events
        finally:
            await client.close()

    asyncio.run(run())
