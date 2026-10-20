"""Shared renewal arithmetic and customer previews."""

from __future__ import annotations

import math
import secrets
import time
from datetime import timezone

from sqlalchemy import select

from app.services.catalog_pricing import quote_product
from app.db.models import Order
from app.services.formatting import (
    format_bytes, format_expire_short, hold_duration_from_info, is_on_hold_status,
    parse_expire,
)
from app.services.service_live_info import validate_service_info

GB = 1024**3
PREVIEW_NOTICE = "پیش‌نمایش بر اساس مانده فعلی است؛ مصرف و گذشت زمان تا اجرای تمدید از مانده کم می‌شود."


class RenewalPendingReview(ValueError):
    def __init__(self):
        super().__init__("نتیجه تمدید نیاز به بررسی پشتیبانی دارد؛ پرداخت شما ثبت شده است. دوباره خرید نکنید.")


def renewal_terms(plan) -> dict:
    volume = plan.data_limit_gb
    days = plan.duration_days
    if volume is not None and (
        isinstance(volume, bool) or not math.isfinite(float(volume)) or float(volume) < 0
    ):
        raise ValueError("حجم پلن تمدید نامعتبر است")
    if isinstance(days, bool) or days is None or int(days) != days or days < 0:
        raise ValueError("مدت پلن تمدید نامعتبر است")
    volume_bytes = int(float(volume) * GB) if volume else None
    if volume_bytes is not None and volume_bytes <= 0:
        raise ValueError("حجم پلن تمدید نامعتبر است")
    return {
        "version": 1,
        "volume_bytes": volume_bytes,
        "duration_seconds": int(days) * 86400,
    }


def calculate_renewal(
    info: dict, terms: dict, *, now: int | None = None, provider: str = "pasarguard"
) -> dict:
    info = validate_service_info(info)
    if info.get("error"):
        raise ValueError("اطلاعات زمان یا حجم سرویس کامل نیست؛ دوباره به‌روزرسانی کنید")
    now = int(time.time()) if now is None else int(now)
    used = info["used_traffic"]
    old_limit = info["data_limit"]
    remaining = max(0, old_limit - used) if old_limit else None
    added = terms["volume_bytes"]
    new_remaining = None if added is None else (remaining or 0) + added
    limit = None if new_remaining is None else used + new_remaining
    old_expire = parse_expire(info.get("expire", info.get("expire_date")))
    if old_expire is not None and old_expire.tzinfo is None:
        old_expire = old_expire.replace(tzinfo=timezone.utc)
    expire_ts = int(old_expire.timestamp()) if old_expire else None
    seconds = terms["duration_seconds"]
    on_hold = is_on_hold_status(info.get("status")) and expire_ts is None
    old_seconds = hold_duration_from_info(info) if on_hold else (
        max(0, expire_ts - now) if expire_ts is not None else None
    )
    new_seconds = None if seconds == 0 else (old_seconds or 0) + seconds
    target_expire = None if new_seconds is None or on_hold else now + new_seconds
    pending_start = on_hold and new_seconds is not None
    # GoGuard's documented bulk-update API has enabled/limit_expire only.
    # It cannot set PasarGuard's on_hold_expire_duration/status. Start the
    # countdown now instead of producing a paid but undeliverable renewal.
    goguard_immediate = provider == "goguard" and pending_start
    if goguard_immediate:
        target_expire = now + new_seconds
    payload = {
        "status": "on_hold" if pending_start and not goguard_immediate else "active",
        "data_limit": limit or 0,
        "expire": target_expire or 0,
    }
    if pending_start and not goguard_immediate:
        payload["on_hold_expire_duration"] = new_seconds
    volume_label = lambda value: "نامحدود" if value is None else format_bytes(value)
    time_label = lambda value: "نامحدود" if value is None else f"{value / 86400:.2f}".rstrip("0").rstrip(".") + " روز"
    lines = [
        f"حجم مانده: {volume_label(remaining)}",
        f"حجم پلن: {volume_label(added)}",
        f"حجم پس از تمدید: {volume_label(new_remaining)}",
        f"زمان مانده: {time_label(old_seconds)}",
        f"زمان پلن: {time_label(seconds or None)}",
        f"زمان پس از تمدید: {time_label(new_seconds)}" + (" پس از اتصال" if pending_start and not goguard_immediate else ""),
    ]
    if target_expire:
        lines.append(f"انقضای جدید: {format_expire_short(target_expire)}")
    warnings = []
    if goguard_immediate:
        warnings.append(
            "GoGuard تمدید در انتظار اتصال را از طریق API پشتیبانی نمی‌کند؛ "
            "زمان اشتراک بلافاصله از تاریخ تمدید محاسبه می‌شود."
        )
    if on_hold and new_seconds is None:
        warnings.append("پلن زمان نامحدود، سرویس را بدون انتظار برای اتصال فعال می‌کند.")
    if remaining is None and added is not None:
        warnings.append("با این پلن، حجم نامحدود به حجم محدود پلن تبدیل می‌شود.")
    if old_seconds is None and seconds:
        warnings.append("با این پلن، زمان نامحدود به مدت محدود پلن تبدیل می‌شود.")
    return {
        "payload": payload, "remaining_bytes": remaining,
        "result_remaining_bytes": new_remaining, "result_seconds": new_seconds,
        "lines": lines, "warnings": warnings, "notice": PREVIEW_NOTICE,
    }


async def assert_service_quota_available(session, service_id: int):
    from app.services.service_cancellations import lock_service_mutation
    await lock_service_mutation(session, service_id)
    query = select(Order.id).where(Order.service_id == service_id, Order.service_mutation_pending.is_(True))
    if await session.scalar(query.limit(1)) is not None:
        raise ValueError("تغییر سهمیهٔ قبلی این سرویس هنوز تعیین تکلیف نشده است؛ با پشتیبانی تماس بگیرید")


async def validate_renewal_selection(session, *, user_id, service, plan, shop_id):
    if not plan or not plan.is_active:
        raise ValueError("پلن تمدید نامعتبر است")
    if plan.is_trial:
        raise ValueError("پلن تست برای تمدید مجاز نیست")
    if plan.owner_reseller_id != shop_id:
        raise ValueError("این پلن در این فروشگاه موجود نیست")
    if service.bot_user_id != user_id:
        raise ValueError("سرویس متعلق به شما نیست")
    if not service.pg_user_id or (service.remark or "").strip() == "linked":
        raise ValueError("تمدید این سرویس از این مسیر ممکن نیست")
    from app.services.service_automation import service_shop_id
    if await service_shop_id(session, service) != shop_id:
        raise ValueError("سرویس این فروشگاه نیست")
    await assert_service_quota_available(session, service.id)
    return renewal_terms(plan)


async def preview_renewal(session, *, user_id, service, plan) -> dict:
    from app.services.orders import _shop_reseller_id
    from app.services.pasarguard import get_pg, get_pg_for_reseller

    shop_id = _shop_reseller_id()
    terms = await validate_renewal_selection(
        session, user_id=user_id, service=service, plan=plan, shop_id=shop_id,
    )
    pg = await get_pg_for_reseller(session, shop_id) if shop_id else get_pg()
    from app.config import get_settings

    result = calculate_renewal(
        await pg.get_user_by_id(service.pg_user_id),
        terms,
        provider=get_settings().panel_provider,
    )
    result.pop("payload")
    quote = await quote_product(session, plan, shop_id=shop_id)
    return {**result, **quote.payload(), "terms": terms, "plan_name": plan.name, "request_key": secrets.token_hex(16)}
