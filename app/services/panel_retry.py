"""Bounded read-only provider recovery inspired by the BluePanel Worker roles.

Cloudflare Core/Processor distinguish transient upstream errors from permanent
authorization/validation failures and retry with limited backoff. PGClockBot
has an independent 2-second web read budget, so this helper is used **only**
for background/bot GETs, never for web-budget requests or panel mutations.

No request URLs, credentials, subscription data or remote error bodies are logged.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx


# Keep these explicit: a 501/505 is not an outage that another GET will fix.
RETRYABLE_HTTP_STATUS = frozenset(
    {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
)
RETRYABLE_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)
MAX_GET_ATTEMPTS = 2
MAX_BACKOFF_SECONDS = 1.0


def classify_provider_failure(exc: BaseException) -> str:
    """Safe high-level diagnostic category, with no remote body leakage."""
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, RETRYABLE_NETWORK_ERRORS):
        return "network"
    code = getattr(exc, "status_code", None)
    if code is None and isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "unexpected"
    if code in (401, 403):
        return "access_denied"
    if code == 404:
        return "not_found"
    if code == 429:
        return "rate_limited"
    if code in RETRYABLE_HTTP_STATUS:
        return "upstream_unavailable"
    if 400 <= code < 500:
        return "invalid_request"
    return "unexpected"


def _delay(attempt: int, response: httpx.Response | None) -> float | None:
    """Return None when the server requested a cooldown longer than our budget."""
    if response is not None and response.status_code == 429:
        raw = response.headers.get("retry-after", "")
        if raw:
            try:
                retry_after = float(raw)
            except ValueError:
                # HTTP-date Retry-After is not safe to turn into a short retry.
                return None
            if retry_after < 0 or retry_after > MAX_BACKOFF_SECONDS:
                return None
            return retry_after
    baseline = min(MAX_BACKOFF_SECONDS, 0.16 * (2**attempt))
    return min(MAX_BACKOFF_SECONDS, baseline + random.random() * 0.05)


async def background_read_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Retry a transient GET *once*, never a write or an auth token POST.

    Unknown write outcomes require reconciliation, not blind retries; a
    duplicate create/order can otherwise charge or provision twice. The
    caller's usual HTTP timeout and no-follow-redirects policy are preserved.
    """
    if (method or "").upper() != "GET":
        return await client.request(method, url, **kwargs)

    for attempt in range(MAX_GET_ATTEMPTS):
        response: httpx.Response | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except RETRYABLE_NETWORK_ERRORS:
            if attempt + 1 >= MAX_GET_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_HTTP_STATUS:
                return response
            if attempt + 1 >= MAX_GET_ATTEMPTS:
                return response

        delay = _delay(attempt, response)
        if delay is None:
            # Preserve the original status (usually 429 with Retry-After)
            # instead of hammering a provider or swallowing diagnostics.
            assert response is not None
            return response
        await asyncio.sleep(delay)
    raise RuntimeError("unreachable panel GET retry state")
