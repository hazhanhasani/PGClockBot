#!/usr/bin/env python3
"""Read-only diagnostic for the currently selected PGClockBot panel provider.

Usage from the installed project root:
    .venv/bin/python scripts/check_panel_provider.py

Never prints credentials, subscription URLs, usernames or tokens.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Executing "python scripts/check_panel_provider.py" sets sys.path[0] to
# scripts/, not the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.pasarguard import get_pg
from app.services.panel_retry import classify_provider_failure


async def _probe(label: str, awaitable):
    started = time.perf_counter()
    try:
        value = await asyncio.wait_for(awaitable, timeout=20.0)
        latency_ms = int((time.perf_counter() - started) * 1000)
        # BluePanel Processor's service-health classification uses 3.5s and
        # 8s thresholds. Show a diagnostic label without declaring a service
        # unavailable because of one slow response.
        state = "slow" if latency_ms > 8000 else "degraded" if latency_ms > 3500 else "healthy"
        print(f"[OK] {label} ({state}; {latency_ms}ms)")
        return value
    except Exception as exc:
        # Error body may contain an access key/URL — classify only.
        category = classify_provider_failure(exc)
        print(f"[FAIL] {label} ({category})")
        return None


async def check() -> int:
    settings = get_settings()
    provider = settings.panel_provider
    print(f"Provider: {provider}")
    client = get_pg()
    try:
        auth = await _probe("Authentication", client.ensure_token())
        if auth is None:
            return 1
        current = await _probe("Current admin", client.get_current_admin())
        if current is None:
            return 1
        groups = await _probe("Available services / groups", client.get_groups_simple())
        if groups is None:
            return 1
        users = await _probe("Subscription / user list", client.get_users(limit=1))
        if users is None:
            return 1
        stats = await _probe("Panel overview", client.get_system_stats())
        if stats is None:
            return 1
        print(f"Configured services/groups: {len(groups) if isinstance(groups, list) else 'unknown'}")
        print("Read-only provider checks passed. No subscription was created or changed.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(check()))
    except KeyboardInterrupt:
        print("Cancelled.")
        sys.exit(130)
