#!/usr/bin/env python3
"""Read-only migration dry-run for PasarGuard -> SolidLayer/GoGuard.

Inspired by BluePanel Core's dry-run migration workflow. No records are
modified, no credentials are fetched, and no usernames or links are printed.

Usage:
    cd ~/PGClockBot
    .venv/bin/python scripts/preview_provider_migration.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.models import Order, OrderStatus, Plan, UserService
from app.db.session import SessionLocal
from app.services.pasarguard import parse_group_ids


def classify_plan_mapping(plan: Plan) -> str:
    """Plan readiness by *local* fields only; IDs must still be verified in GoGuard."""
    if parse_group_ids(getattr(plan, "pg_group_ids", None)):
        return "has_service_ids"
    if getattr(plan, "pg_template_id", None):
        return "template_only"
    return "unmapped"


async def preview() -> int:
    async with SessionLocal() as session:
        active_plans = list(
            (await session.scalars(select(Plan).where(Plan.is_active.is_(True)))).all()
        )
        by_kind = {"has_service_ids": 0, "template_only": 0, "unmapped": 0}
        for plan in active_plans:
            by_kind[classify_plan_mapping(plan)] += 1

        existing_services = int(
            await session.scalar(
                select(func.count(UserService.id)).where(
                    UserService.pg_user_id.is_not(None),
                    UserService.is_cancelled.is_(False),
                )
            ) or 0
        )
        unsettled_orders = int(
            await session.scalar(
                select(func.count(Order.id)).where(
                    Order.status.in_(
                        (OrderStatus.PAID.value, OrderStatus.DELIVERING.value)
                    )
                )
            ) or 0
        )

    print("PasarGuard -> GoGuard migration dry run (READ ONLY)")
    print(f"Active plans: {len(active_plans)}")
    print(f"Plans with local service/group IDs: {by_kind['has_service_ids']}")
    print(f"Template-only plans needing GoGuard service mapping: {by_kind['template_only']}")
    print(f"Unmapped plans needing configuration: {by_kind['unmapped']}")
    print(f"Existing panel-linked services: {existing_services}")
    print(f"Paid/delivering orders still open: {unsettled_orders}")
    if existing_services or unsettled_orders:
        print("[BLOCKED] Switching providers is unsafe without per-service migration.")
        print("No data has been changed.")
        return 2
    if by_kind["template_only"] or by_kind["unmapped"]:
        print("[REVIEW] Map plan service IDs to the destination GoGuard panel before sales.")
        print("Service IDs may differ across panels even if the numeric value matches.")
        print("No data has been changed.")
        return 1
    print("[READY-TO-VERIFY] Local gates clear. Validate service IDs on the GoGuard API.")
    print("No data has been changed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(preview()))
    except KeyboardInterrupt:
        print("Cancelled.")
        sys.exit(130)
