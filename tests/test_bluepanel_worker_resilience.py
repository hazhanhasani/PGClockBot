"""BluePanel Core/Edge/Processor-inspired safe upstream recovery tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx

from app.services.panel_retry import (
    RETRYABLE_HTTP_STATUS,
    background_read_request,
    classify_provider_failure,
)
from app.services.pg_read_resilience import panel_read_request, web_read_budget


def test_transient_statuses_recover_only_for_get() -> None:
    async def run():
        calls = []
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})
        async with httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("app.services.panel_retry.asyncio.sleep", new=AsyncMock()) as sleep:
                response = await panel_read_request(client, "GET", "/api/services")
                assert response.status_code == 200
                assert calls == ["GET", "GET"]
                sleep.assert_awaited_once()
    asyncio.run(run())


def test_non_idempotent_create_and_token_post_are_never_retried() -> None:
    async def run():
        calls = []
        def fail(request: httpx.Request):
            calls.append(request.method)
            return httpx.Response(503)
        async with httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(fail)
        ) as client:
            assert (await panel_read_request(client, "POST", "/api/subscriptions")).status_code == 503
            assert (await panel_read_request(client, "POST", "/api/admins/token")).status_code == 503
            assert calls == ["POST", "POST"]
    asyncio.run(run())


def test_non_transient_401_403_404_and_400_are_not_retried() -> None:
    async def run():
        for status in (400, 401, 403, 404, 405, 422, 501, 505):
            calls = []
            def respond(request: httpx.Request):
                calls.append(request.url.path)
                return httpx.Response(status)
            async with httpx.AsyncClient(
                base_url="https://example.test", transport=httpx.MockTransport(respond)
            ) as client:
                response = await panel_read_request(client, "GET", "/user")
                assert response.status_code == status
                assert calls == ["/user"]
    asyncio.run(run())


def test_get_connection_failure_retries_once_then_propagates() -> None:
    async def run():
        calls = []
        def fail(request: httpx.Request):
            calls.append(1)
            raise httpx.ConnectError("offline", request=request)
        async with httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(fail)
        ) as client:
            with patch("app.services.panel_retry.asyncio.sleep", new=AsyncMock()):
                try:
                    await panel_read_request(client, "GET", "/api/services")
                except httpx.ConnectError:
                    pass
                else:
                    raise AssertionError("connection failure must propagate")
            assert len(calls) == 2
    asyncio.run(run())


def test_retry_after_long_cooldown_is_respected() -> None:
    async def run():
        calls = []
        def throttle(request: httpx.Request):
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "30"})
        async with httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(throttle)
        ) as client:
            response = await background_read_request(client, "GET", "/api/services")
            assert response.status_code == 429
            assert len(calls) == 1
    asyncio.run(run())


def test_web_budget_never_enters_background_retry_loop() -> None:
    async def run():
        calls = []
        def fail(request: httpx.Request):
            calls.append(1)
            return httpx.Response(503)
        async with httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(fail)
        ) as client:
            from app.services.pg_read_resilience import PgReadUnavailable, reset_read_circuits
            reset_read_circuits()
            with web_read_budget(seconds=2):
                try:
                    await panel_read_request(client, "GET", "/api/services")
                except PgReadUnavailable:
                    pass
                else:
                    raise AssertionError("web circuit must return unavailable")
            assert len(calls) == 1
            reset_read_circuits()
    asyncio.run(run())


def test_provider_error_categories_do_not_include_secrets() -> None:
    assert 429 in RETRYABLE_HTTP_STATUS
    assert classify_provider_failure(httpx.ReadTimeout("private-token")) == "timeout"
    assert classify_provider_failure(TimeoutError("private-token")) == "timeout"
    assert classify_provider_failure(httpx.ConnectError("private-token")) == "network"
    assert classify_provider_failure(Exception("private-token")) == "unexpected"
    for status, expected in [
        (401, "access_denied"), (404, "not_found"),
        (429, "rate_limited"), (503, "upstream_unavailable"),
        (422, "invalid_request"),
    ]:
        error = RuntimeError("private-token")
        error.status_code = status
        assert classify_provider_failure(error) == expected
