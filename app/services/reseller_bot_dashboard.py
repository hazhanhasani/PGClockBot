"""Worker-inspired reseller shop KPI blocks for both Telegram bot surfaces.

Pure formatting only. The data must come from the existing tenant-scoped
reseller_shop_summary() query; never reconstruct sales from another shop.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.services.formatting import format_toman


def _nonnegative(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def format_reseller_shop_kpis(
    summary: Mapping[str, object], *, currency: str = "تومان"
) -> str:
    """Compact operator KPIs based on BluePanel's sales/stats/finance grouping."""
    users = _nonnegative(summary.get("users"))
    services = _nonnegative(summary.get("services"))
    orders = _nonnegative(summary.get("orders"))
    plans = _nonnegative(summary.get("plans"))
    revenue = _nonnegative(summary.get("revenue"))
    pending = _nonnegative(summary.get("pending"))
    tickets = _nonnegative(summary.get("tickets"))
    return (
        "📊 <b>وضعیت فروشگاه</b>\n"
        f"👥 مشتریان: <b>{users:,}</b>  ·  📦 سرویس‌ها: <b>{services:,}</b>\n"
        f"🛍 سفارش‌ها: <b>{orders:,}</b>  ·  🗂 پلن‌های فعال: <b>{plans:,}</b>\n"
        f"💰 فروش تحویل‌شده: <b>{format_toman(revenue, currency)}</b>\n"
        f"⏳ رسیدهای در انتظار: <b>{pending:,}</b>  ·  🎫 تیکت‌های باز: <b>{tickets:,}</b>"
    )
