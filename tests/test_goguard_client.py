from __future__ import annotations

from app.services.goguard import (
    _build_create_payload,
    _build_update_payload,
    _normalize_admin,
    _normalize_subscription,
)


def test_normalize_subscription_maps_services_and_quota() -> None:
    user = _normalize_subscription(
        {
            "id": 7,
            "username": "sample_user",
            "services": [2, 4],
            "limit_usage": 1024,
            "total_usage": 256,
            "limit_expire": 0,
            "enabled": True,
            "subscription_link": "https://panel.example.test/tag/0123456789abcdef0123456789abcdef",
        }
    )
    assert user["id"] == 7
    assert user["group_ids"] == [2, 4]
    assert user["data_limit"] == 1024
    assert user["used_traffic"] == 256
    assert user["status"] == "active"
    assert user["subscription_url"].endswith("/tag/0123456789abcdef0123456789abcdef")


def test_create_payload_translates_pasarguard_shape() -> None:
    payload = _build_create_payload(
        {
            "username": "sample_user",
            "group_ids": [3, 5],
            "data_limit": 10 * 1024**3,
            "expire": 1_800_000_000,
            "note": "order:10",
        }
    )
    assert payload["username"] == "sample_user"
    assert payload["services"] == [3, 5]
    assert payload["limit_usage"] == 10 * 1024**3
    assert payload["limit_expire"] == 1_800_000_000
    assert payload["note"] == "order:10"


def test_update_payload_supports_unlimited_quota_and_disable() -> None:
    payload = _build_update_payload(
        {"data_limit": None, "expire": None, "status": "disabled"},
        "sample_user",
    )
    assert payload["usernames"] == ["sample_user"]
    assert payload["limit_usage"] == 0
    assert payload["limit_expire"] == 0
    assert payload["enabled"] is False


def test_admin_normalization_maps_go_guard_owner_limits() -> None:
    admin = _normalize_admin(
        {
            "username": "owner",
            "role": "owner",
            "user_limit": 100,
            "usage_limit": 2048,
            "user_count": 15,
            "total_usage": 500,
            "enabled": True,
        }
    )
    assert admin["role_name"] == "owner"
    assert admin["is_owner"] is True
    assert admin["max_users"] == 100
    assert admin["data_limit"] == 2048
    assert admin["total_users"] == 15
    assert admin["used_traffic"] == 500
