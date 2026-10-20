#!/usr/bin/env python3
"""Read-only diagnostic for the currently selected PGClockBot panel provider.

Usage from the installed project root:
    .venv/bin/python scripts/check_panel_provider.py

Never prints credentials, subscription URLs, usernames or tokens.
"""
from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.services.pasarguard import get_pg


async def _probe(label: str, awaitable):
    try:
        value = await asyncio.wait_for(awaitable, timeout=20.0)
        print(f"[OK] {label}")
        return value
    except Exception as exc:
        # Error body could contain an access key / subscription URL — never
        # log exception.text, body, repr or inbound URL from remote API.
        print(f"[FAIL] {label} ({type(exc).__name__})")
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
