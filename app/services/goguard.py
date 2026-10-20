from __future__ import annotations

"""GoGuard/SolidLayer adapter for PGClockBot's existing PasarGuard-facing flows.

GoGuard exposes subscriptions and services rather than PasarGuard users and groups.
This adapter normalizes the supported API surface to the common shape used by
PGClockBot. Unsupported mutations fail explicitly instead of pretending they worked.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.config import get_settings
from app.services.pasarguard import PasarGuardClient, PasarGuardError, WireGuardResponse
from app.services.pg_read_resilience import panel_read_request

logger = logging.getLogger(__name__)

_ALLOWED_SUBSCRIPTION_FILTERS = {
    "page", "limit", "search", "usernames", "owner_ids", "enabled", "activated",
    "status", "sort_by", "sort_dir", "created_after", "created_before",
    "first_online_after", "first_online_before", "last_online_after", "last_online_before",
}


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_epoch(value: Any) -> int | None:
    """Convert an API expiry value (epoch or RFC3339 string) to Unix seconds."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "subscriptions", "users", "admins", "services", "groups", "nodes", "configs", "links"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _one(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key in ("subscription", "item", "admin"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
        rows = _items(payload)
        return rows[0] if rows else payload
    if isinstance(payload, list):
        return next((item for item in payload if isinstance(item, dict)), None)
    return None


def _normalize_subscription(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate a GoGuard subscription into the common PGClockBot user shape."""
    user = dict(raw)
    limit_usage = _as_int(raw.get("limit_usage"))
    used = _as_int(raw.get("total_usage"))
    if used is None:
        used = _as_int(raw.get("current_usage")) or 0
    # expired_at is the *actual time a subscription expired*, not the
    # configured expiry target. Prefer the epoch limit_expire field.
    expire_raw = raw.get("limit_expire")
    if expire_raw in (None, ""):
        expire_raw = raw.get("expired_at")
    expire_epoch = _to_epoch(expire_raw)
    enabled = raw.get("enabled") is not False
    if not enabled:
        status = "disabled"
    elif raw.get("on_hold_since"):
        status = "on_hold"
    elif raw.get("limited_at") or (limit_usage is not None and limit_usage > 0 and used >= limit_usage):
        status = "limited"
    elif expire_epoch and expire_epoch > 0 and expire_epoch <= int(datetime.now(timezone.utc).timestamp()):
        status = "expired"
    else:
        status = "active"

    user.update(
        {
            "id": _as_int(raw.get("id")),
            "username": str(raw.get("username") or ""),
            "status": status,
            "enabled": enabled,
            "is_active": enabled,
            "group_ids": [
                int(value) for value in (raw.get("services") or [])
                if _as_int(value) is not None
            ],
            "data_limit": limit_usage if limit_usage and limit_usage > 0 else None,
            "used_traffic": used,
            "expire": expire_raw if expire_raw not in (None, "") else 0,
            "expire_date": expire_raw if expire_raw not in (None, "") else 0,
            "subscription_url": raw.get("subscription_link") or raw.get("subscription_url"),
            "subscription_token": raw.get("access_key") or raw.get("subscription_token"),
            "owner_username": raw.get("owner_username") or "",
            "total_usage": used,
        }
    )
    return user


def _normalize_admin(raw: dict[str, Any]) -> dict[str, Any]:
    """Map GoGuard owner/seller metadata without discarding native role fields."""
    admin = dict(raw)
    # Current-admin responses may include a persistent API key. Never leak
    # credentials into panel templates, logs, or capability responses.
    admin.pop("api_key", None)
    role_raw = raw.get("role")
    role_data = dict(role_raw) if isinstance(role_raw, dict) else {}
    if isinstance(role_raw, dict):
        role_name = str(role_raw.get("name") or role_raw.get("role") or "").strip().lower()
    else:
        role_name = str(role_raw or raw.get("role_name") or "").strip().lower()
    is_owner = role_name == "owner" or raw.get("is_owner") is True
    user_limit = _as_int(raw.get("user_limit"))
    usage_limit = _as_int(raw.get("usage_limit"))
    total_users = _as_int(raw.get("user_count")) or 0
    total_usage = _as_int(raw.get("total_usage")) or _as_int(raw.get("current_usage")) or 0
    limits: dict[str, Any] = {}
    if user_limit is not None:
        limits["max_users"] = user_limit
    if usage_limit is not None:
        limits["data_limit"] = usage_limit

    permissions = role_data.get("permissions")
    if isinstance(permissions, str):
        try:
            permissions = json.loads(permissions)
        except (TypeError, ValueError):
            permissions = None
    if not isinstance(permissions, dict):
        permissions = raw.get("permissions")
        if isinstance(permissions, str):
            try:
                permissions = json.loads(permissions)
            except (TypeError, ValueError):
                permissions = None
    if not isinstance(permissions, dict) or not permissions:
        # GoGuard remains the final authorization boundary. This fallback keeps
        # subscription operations available when the current-admin response omits
        # detailed permissions for the current role.
        permissions = {
            "system": {"read": True},
            "users": {
                "read": True, "read_simple": True, "create": True,
                "update": True, "delete": True, "reset_usage": True,
                "revoke_sub": True,
            },
            "groups": {"read": True, "read_simple": True},
        }
    role_data.update(
        {
            "name": role_name or "seller",
            "is_owner": is_owner,
            "permissions": permissions,
        }
    )
    if not isinstance(role_data.get("limits"), dict):
        role_data["limits"] = limits

    admin.update(
        {
            "role_name": role_name or "seller",
            "is_owner": is_owner,
            "enabled": raw.get("enabled") is not False,
            "status": "active" if raw.get("enabled") is not False else "disabled",
            "max_users": user_limit,
            "total_users": total_users,
            "data_limit": usage_limit,
            "used_traffic": total_usage,
            "role": role_data,
            "role_id": raw.get("role_id") or role_data.get("id"),
            "pg_role_id": raw.get("pg_role_id") or role_data.get("id"),
        }
    )
    return admin


def _build_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate the existing PasarGuard-shaped payload to GoGuard's schema."""
    if _as_int(payload.get("hwid_limit")) not in (None, 0):
        raise PasarGuardError("GoGuard API از محدودیت تعداد دستگاه پشتیبانی نمی‌کند")
    username = str(payload.get("username") or "").strip()
    if not username:
        raise PasarGuardError("نام کاربری اشتراک الزامی است")
    services = payload.get("services", payload.get("group_ids"))
    if services is not None and not isinstance(services, list):
        raise PasarGuardError("شناسه سرویس‌های GoGuard نامعتبر است")
    if services is not None and not services:
        raise PasarGuardError("برای ساخت اشتراک در GoGuard حداقل یک سرویس انتخاب کنید")
    body: dict[str, Any] = {"username": username}
    if services is not None:
        body["services"] = [int(value) for value in services]
    if "data_limit" in payload and payload.get("data_limit") is not None:
        body["limit_usage"] = int(payload["data_limit"])
    if "expire" in payload and payload.get("expire") is not None:
        expire = _to_epoch(payload.get("expire"))
        if expire is None:
            raise PasarGuardError("تاریخ انقضای اشتراک برای GoGuard نامعتبر است")
        body["limit_expire"] = expire
    for key in (
        "note", "email", "phone", "telegram_id", "owner_id",
        "expiry_grace_days", "limited_grace_days", "on_hold_timeout_days",
    ):
        if payload.get(key) is not None:
            body[key] = payload[key]
    return body


def _build_update_payload(payload: dict[str, Any], username: str) -> dict[str, Any]:
    """Translate a one-subscription update to GoGuard's bulk-update body."""
    if _as_int(payload.get("hwid_limit")) not in (None, 0):
        raise PasarGuardError("GoGuard API از محدودیت تعداد دستگاه پشتیبانی نمی‌کند")
    body: dict[str, Any] = {"usernames": [username]}
    if payload.get("username") is not None:
        body["username"] = str(payload["username"]).strip()
    if "group_ids" in payload or "services" in payload:
        services = payload.get("services", payload.get("group_ids"))
        if not isinstance(services, list):
            raise PasarGuardError("شناسه سرویس‌های GoGuard نامعتبر است")
        body["services"] = [int(value) for value in services]
    if "data_limit" in payload:
        body["limit_usage"] = int(payload.get("data_limit") or 0)
    if "expire" in payload:
        expire = _to_epoch(payload.get("expire"))
        body["limit_expire"] = int(expire or 0)
    if payload.get("status") is not None:
        status = str(payload["status"]).strip().lower()
        if status in {"active", "enabled"}:
            body["enabled"] = True
        elif status in {"disabled", "inactive"}:
            body["enabled"] = False
        else:
            raise PasarGuardError("تغییر مستقیم وضعیت معلق/محدود در GoGuard API پشتیبانی نمی‌شود")
    if payload.get("enabled") is not None:
        body["enabled"] = bool(payload["enabled"])
    for key in ("note", "email", "phone", "telegram_id"):
        if key in payload and payload[key] is not None:
            body[key] = payload[key]
    return body


class GoGuardClient(PasarGuardClient):
    """Client for the SolidLayer-hosted GoGuard API."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        settings = get_settings()
        resolved_username = settings.goguard_username if username is None else username
        resolved_password = settings.goguard_password if password is None and username is None else password
        resolved_base = settings.goguard_base_url if base_url is None else base_url
        # Swagger securityDefinitions: X-API-Key. When an explicit GoGuard key
        # is absent, authenticate at POST /api/admins/token using credentials.
        # Never inherit PasarGuard's PG_API_KEY or PG_ACCESS_TOKEN.
        resolved_api_key = (
            settings.goguard_api_key if api_key is None and username is None else api_key
        )
        super().__init__(
            username=resolved_username,
            password=resolved_password,
            access_token=access_token,
            api_key=resolved_api_key or None,
            base_url=resolved_base,
        )
        self._api_key = (resolved_api_key or "").replace("\r", "").strip() or None
        self._token = None if self._api_key else (access_token or None)
        self._login_username = (resolved_username or "").strip() or None
        self._login_password = (resolved_password or "").replace("\r", "").strip() or None
        self._id_username_cache: dict[int, str] = {}
        self._subscription_tag = ""

    async def _ensure_api_base(self) -> None:
        if not self.base_url:
            raise PasarGuardError("آدرس API پنل SolidLayer / GoGuard تنظیم نشده است")
        await self._ensure_safe_target(protected=False)
        self._api_base_resolved = True

    async def ensure_token(self) -> str:
        if self._api_key:
            await self._ensure_api_base()
            return "__apikey__"
        if self._token:
            return self._token
        await self._ensure_api_base()
        username = (self._login_username or "").strip()
        password = (self._login_password or "").strip()
        if not username or not password:
            raise PasarGuardError("نام کاربری/رمز عبور یا کلید API پنل SolidLayer / GoGuard الزامی است")
        try:
            response = await panel_read_request(
                self._client, "POST", "/api/admins/token",
                json={"username": username, "password": password},
            )
        except Exception as exc:
            raise PasarGuardError("ارتباط با API پنل SolidLayer / GoGuard برقرار نشد") from exc
        if response.status_code >= 400:
            body = (response.text or "")[:500]
            raise PasarGuardError(f"GoGuard login failed ({response.status_code})", response.status_code, body)
        try:
            data = response.json()
        except Exception as exc:
            raise PasarGuardError("پاسخ ورود GoGuard معتبر نیست") from exc
        token = data.get("token") or data.get("access_token") or data.get("accessToken")
        if not isinstance(token, str) or not token.strip():
            raise PasarGuardError("پاسخ ورود GoGuard فاقد توکن است", body=data)
        self._token = token.strip()
        # Keep the password in memory so a 401 can trigger a fresh login.
        self._api_base_resolved = True
        logger.info("GoGuard login succeeded · base=%s", self.base_url)
        return self._token

    async def get_users(self, **params: Any) -> dict:
        query: dict[str, Any] = {}
        limit = max(1, min(100, _as_int(params.get("limit")) or 100))
        page = max(1, _as_int(params.get("page")) or 1)
        if params.get("offset") is not None:
            offset = max(0, _as_int(params.get("offset")) or 0)
            page = (offset // limit) + 1
        query["page"], query["limit"] = page, limit
        for key in _ALLOWED_SUBSCRIPTION_FILTERS - {"page", "limit", "usernames"}:
            value = params.get(key)
            if value is not None and value != "":
                query[key] = value
        usernames = params.get("usernames")
        if usernames is None and params.get("username"):
            usernames = params.get("username")
        if usernames:
            query["usernames"] = usernames
        data = await self.request("GET", "/api/subscriptions", params=query)
        rows = _items(data)
        users = [_normalize_subscription(row) for row in rows]
        for user in users:
            if user.get("id") is not None and user.get("username"):
                self._id_username_cache[int(user["id"])] = str(user["username"])
        meta = data if isinstance(data, dict) else {}
        return {
            **meta,
            "items": users,
            "users": users,
            "total": _as_int(meta.get("total")) if meta.get("total") is not None else len(users),
            "page": _as_int(meta.get("page")) or page,
            "limit": _as_int(meta.get("limit")) or limit,
        }

    async def get_user_by_username(self, username: str) -> dict:
        uname = (username or "").strip()
        if not uname:
            raise PasarGuardError("نام کاربری اشتراک الزامی است", 400)
        data = await self.get_users(usernames=uname, page=1, limit=100)
        rows = _items(data)
        match = next((item for item in rows if str(item.get("username") or "").lower() == uname.lower()), None)
        if not match:
            raise PasarGuardError("اشتراک در GoGuard پیدا نشد", 404)
        return match

    async def get_user_by_id(self, user_id: int) -> dict:
        uid = int(user_id)
        cached_username = self._id_username_cache.get(uid)
        if cached_username:
            try:
                cached_user = await self.get_user_by_username(cached_username)
                # Usernames can be deleted/re-created with new numeric IDs.
                # Never mutate another subscription based on a stale ID cache.
                if _as_int(cached_user.get("id")) == uid:
                    return cached_user
                self._id_username_cache.pop(uid, None)
            except PasarGuardError as exc:
                if exc.status_code != 404:
                    raise
                self._id_username_cache.pop(uid, None)
        page, limit = 1, 100
        while page <= 1000:
            data = await self.get_users(page=page, limit=limit)
            rows = _items(data)
            for user in rows:
                if _as_int(user.get("id")) == uid:
                    self._id_username_cache[uid] = str(user.get("username") or "")
                    return user
            total = _as_int(data.get("total")) if isinstance(data, dict) else None
            if not rows or (total is not None and page * limit >= total):
                break
            page += 1
        raise PasarGuardError("اشتراک در GoGuard پیدا نشد", 404)

    async def create_user(self, payload: dict) -> dict:
        body = _build_create_payload(payload)
        data = await self.request("POST", "/api/subscriptions", json=[body])
        created = _one(data)
        if not isinstance(created, dict):
            raise PasarGuardError("GoGuard اشتراک را ساخت اما پاسخ قابل خواندن نبود")
        user = _normalize_subscription(created)
        if not user.get("id") or not user.get("username"):
            # The mutation may already have succeeded. Read by username
            # instead of issuing another create (which could duplicate orders).
            try:
                user = await self.get_user_by_username(body["username"])
            except PasarGuardError:
                raise PasarGuardError(
                    "GoGuard پاسخ ساخت اشتراک را بدون شناسه معتبر برگرداند؛ وضعیت سفارش نیاز به بررسی دارد"
                ) from None
        if not user.get("id"):
            raise PasarGuardError("GoGuard شناسه اشتراک ساخته‌شده را برنگرداند")
        self._id_username_cache[int(user["id"])] = str(user["username"])
        if str(payload.get("status") or "active").lower() in {"disabled", "inactive"}:
            if user.get("id") is None:
                raise PasarGuardError("اشتراک ساخته شد اما شناسه آن برنگشت")
            return await self.set_disabled_by_id(int(user["id"]), True)
        return user

    async def create_user_from_template(self, payload: dict) -> dict:
        raise PasarGuardError("GoGuard الگوی ساخت کاربرِ PasarGuard را ندارد؛ برای این پلن سرویس‌های GoGuard را انتخاب کنید", 405)

    async def modify_user_by_id(self, user_id: int, payload: dict) -> dict:
        current = await self.get_user_by_id(user_id)
        old_username = str(current.get("username") or "")
        body = _build_update_payload(payload, old_username)
        if len(body) == 1:
            return current
        await self.request("PUT", "/api/subscriptions", json=body)
        new_username = str(payload.get("username") or old_username).strip()
        # Read the authoritative record after a mutation. Bulk-update responses
        # can be wrappers/partial rows and may omit stable IDs or quota fields.
        user = await self.get_user_by_username(new_username)
        if _as_int(user.get("id")) != int(user_id):
            raise PasarGuardError(
                "GoGuard پاسخ ویرایش اشتراک با شناسهٔ دیگری برگشت؛ سفارش نیاز به بررسی دارد"
            )
        if new_username != old_username:
            self._id_username_cache.pop(int(user_id), None)
        self._id_username_cache[int(user_id)] = new_username
        return user

    async def modify_user_with_template(self, user_id: int, payload: dict) -> dict:
        raise PasarGuardError("ویرایش با الگوی کاربر در GoGuard API پشتیبانی نمی‌شود", 405)

    async def delete_user_by_id(self, user_id: int) -> None:
        user = await self.get_user_by_id(user_id)
        await self.request("DELETE", "/api/subscriptions", json={"usernames": [user["username"]]})
        self._id_username_cache.pop(int(user_id), None)

    async def reset_user_by_id(self, user_id: int) -> dict:
        user = await self.get_user_by_id(user_id)
        await self.request("POST", "/api/subscriptions/reset", json={"usernames": [user["username"]]})
        return await self.get_user_by_username(str(user["username"]))

    async def revoke_sub_by_id(self, user_id: int) -> dict:
        user = await self.get_user_by_id(user_id)
        await self.request("POST", "/api/subscriptions/revoke", json={"usernames": [user["username"]]})
        return await self.get_user_by_username(str(user["username"]))

    async def set_disabled_by_id(self, user_id: int, disabled: bool) -> dict:
        user = await self.get_user_by_id(user_id)
        path = "/api/subscriptions/disable" if disabled else "/api/subscriptions/enable"
        await self.request("POST", path, json={"usernames": [user["username"]]})
        return await self.get_user_by_username(str(user["username"]))

    async def set_owner_by_id(self, user_id: int, admin_username: str) -> dict:
        raise PasarGuardError("تغییر مالک اشتراک از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def get_user_usage(self, user_id: int) -> Any:
        user = await self.get_user_by_id(user_id)
        return {
            "total_usage": user.get("total_usage", user.get("used_traffic", 0)),
            "used_traffic": user.get("used_traffic", 0),
            "limit_usage": user.get("limit_usage"),
            "data_limit": user.get("data_limit"),
            "expire": user.get("expire"),
        }

    async def get_user_templates(self) -> Any:
        return {"templates": []}

    async def get_user_templates_simple(self) -> list[dict]:
        return []

    async def create_user_template(self, payload: dict) -> dict:
        raise PasarGuardError("الگوی ساخت کاربر در GoGuard API پشتیبانی نمی‌شود", 405)

    async def get_user_template(self, template_id: int) -> dict:
        raise PasarGuardError("الگوی ساخت کاربر در GoGuard API پشتیبانی نمی‌شود", 405)

    async def delete_user_template(self, template_id: int) -> None:
        raise PasarGuardError("الگوی ساخت کاربر در GoGuard API پشتیبانی نمی‌شود", 405)

    async def modify_user_template(self, template_id: int, payload: dict) -> dict:
        raise PasarGuardError("الگوی ساخت کاربر در GoGuard API پشتیبانی نمی‌شود", 405)

    async def _get_services(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page, limit = 1, 100
        while page <= 1000:
            data = await self.request("GET", "/api/services", params={"page": page, "limit": limit})
            batch = _items(data)
            rows.extend(batch)
            total = _as_int(data.get("total")) if isinstance(data, dict) else None
            if not batch or (total is not None and page * limit >= total):
                break
            page += 1
        return rows

    @staticmethod
    def _service_as_group(service: dict[str, Any]) -> dict[str, Any]:
        nodes = service.get("nodes") if isinstance(service.get("nodes"), list) else []
        return {
            "id": _as_int(service.get("id")),
            "name": str(service.get("name") or ""),
            "enabled": True,
            "service_id": _as_int(service.get("id")),
            "nodes": nodes,
            "link_hashes": [
                str(value) for node in nodes if isinstance(node, dict)
                for value in (node.get("link_hashes") or [])
            ],
        }

    async def get_groups(self) -> Any:
        groups = [self._service_as_group(item) for item in await self._get_services()]
        return {"groups": groups, "items": groups, "total": len(groups), "page": 1, "limit": len(groups)}

    async def get_groups_simple(self) -> list[dict]:
        return [self._service_as_group(item) for item in await self._get_services()]

    async def get_group(self, group_id: int) -> dict:
        for service in await self._get_services():
            if _as_int(service.get("id")) == int(group_id):
                return self._service_as_group(service)
        raise PasarGuardError("سرویس در GoGuard پیدا نشد", 404)

    async def create_group(self, payload: dict) -> dict:
        raise PasarGuardError("سرویس‌های GoGuard را از پنل اصلی GoGuard مدیریت کنید", 405)

    async def modify_group(self, group_id: int, payload: dict) -> dict:
        raise PasarGuardError("ویرایش سرویس‌های GoGuard از این API پشتیبانی نمی‌شود", 405)

    async def delete_group(self, group_id: int) -> None:
        raise PasarGuardError("حذف سرویس‌های GoGuard از این API پشتیبانی نمی‌شود", 405)

    async def set_group_disabled(self, group_id: int, disabled: bool) -> Any:
        raise PasarGuardError("تغییر وضعیت سرویس‌های GoGuard از این API پشتیبانی نمی‌شود", 405)

    async def get_inbounds(self) -> list:
        return []

    async def get_inbounds_details(self) -> Any:
        return {"inbounds": []}

    async def get_hosts(self) -> list[dict]:
        return []

    async def get_host(self, host_id: int) -> dict:
        raise PasarGuardError("GoGuard API مدیریت Host را ارائه نمی‌کند", 405)

    async def create_host(self, payload: dict) -> dict:
        raise PasarGuardError("GoGuard API مدیریت Host را ارائه نمی‌کند", 405)

    async def modify_host(self, host_id: int, payload: dict) -> dict:
        raise PasarGuardError("GoGuard API مدیریت Host را ارائه نمی‌کند", 405)

    async def delete_host(self, host_id: int) -> None:
        raise PasarGuardError("GoGuard API مدیریت Host را ارائه نمی‌کند", 405)

    async def set_host_disabled(self, host_id: int, disabled: bool) -> Any:
        raise PasarGuardError("GoGuard API مدیریت Host را ارائه نمی‌کند", 405)

    async def get_admins(self) -> list[dict]:
        current = await self.get_current_admin()
        return [current] if current else []

    async def get_admins_simple(self) -> list[dict]:
        return await self.get_admins()

    async def get_current_admin(self) -> dict | None:
        data = await self.request("GET", "/api/admins/current")
        admin = _one(data)
        if not admin:
            return None
        normalized = _normalize_admin(admin)
        self._subscription_tag = str(
            admin.get("subscription_tag") or admin.get("subscription_prefix") or self._subscription_tag
        ).strip()
        return normalized

    async def get_admin(self, username: str) -> dict | None:
        current = await self.get_current_admin()
        if not current:
            return None
        expected = (username or "").strip().lower()
        actual = str(current.get("username") or "").strip().lower()
        return current if expected and expected == actual else None

    async def get_admin_gate(self, username: str) -> tuple[str, dict | None]:
        """GoGuard cannot look up other admins with the documented endpoints.

        Never return `missing` from a failed lookup: that could revoke a real
        staff account during a transient outage.
        """
        try:
            current = await self.get_current_admin()
        except Exception:
            return "unreachable", None
        if current and str(current.get("username") or "").casefold() == str(username or "").strip().casefold():
            return "ok", current
        return "unreachable", None

    async def get_admin_roles(self) -> list[dict]:
        return []

    async def get_admin_role(self, role_id: int) -> dict:
        raise PasarGuardError("GoGuard API فهرست نقش‌های قابل مدیریت را ارائه نمی‌کند", 405)

    async def create_admin(self, payload: dict) -> dict:
        raise PasarGuardError("ساخت ادمین از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def modify_admin(self, username: str, payload: dict) -> dict:
        raise PasarGuardError("ویرایش ادمین‌های دیگر از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def delete_admin(self, username: str) -> Any:
        raise PasarGuardError("حذف ادمین از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def get_system_stats(self) -> dict:
        data = await self.request("GET", "/api/stats/overview")
        stats = data if isinstance(data, dict) else {}
        return {
            **stats,
            "version": "GoGuard API",
            "total_users": stats.get("total", 0),
            "active_users": stats.get("active", 0),
            "expired_users": stats.get("expired", 0),
            "limited_users": stats.get("limited", 0),
            "disabled_users": stats.get("disabled", 0),
            "used_traffic": stats.get("used_bytes", 0),
            "data_limit": stats.get("limit_bytes", 0),
        }

    async def get_nodes(self) -> list[dict]:
        nodes: dict[int, dict[str, Any]] = {}
        for service in await self._get_services():
            service_id = _as_int(service.get("id"))
            for node in service.get("nodes") or []:
                if not isinstance(node, dict) or _as_int(node.get("id")) is None:
                    continue
                node_id = int(node["id"])
                item = nodes.setdefault(node_id, {
                    "id": node_id,
                    "name": str(node.get("name") or f"Node {node_id}"),
                    "link_hashes": [],
                    "service_ids": [],
                    "status": "active",
                })
                item["link_hashes"] = sorted(set(item["link_hashes"]) | {
                    str(value) for value in (node.get("link_hashes") or [])
                })
                if service_id is not None and service_id not in item["service_ids"]:
                    item["service_ids"].append(service_id)
        return list(nodes.values())

    async def get_nodes_simple(self) -> list[dict]:
        return await self.get_nodes()

    async def get_nodes_realtime(self) -> Any:
        return {"nodes": []}

    async def get_node(self, node_id: int) -> dict:
        for node in await self.get_nodes():
            if int(node.get("id") or 0) == int(node_id):
                return node
        raise PasarGuardError("Node در GoGuard پیدا نشد", 404)

    async def create_node(self, payload: dict) -> dict:
        raise PasarGuardError("ساخت Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def modify_node(self, node_id: int, payload: dict) -> dict:
        raise PasarGuardError("ویرایش Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def delete_node(self, node_id: int) -> Any:
        raise PasarGuardError("حذف Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def reset_node(self, node_id: int) -> Any:
        raise PasarGuardError("عملیات Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def sync_node(self, node_id: int) -> Any:
        raise PasarGuardError("عملیات Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def reconnect_node(self, node_id: int) -> Any:
        raise PasarGuardError("عملیات Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    async def reconnect_all_nodes(self) -> Any:
        raise PasarGuardError("عملیات Node از طریق GoGuard API پشتیبانی نمی‌شود", 405)

    def _public_subscription_path(self, token: str, subscription_url: str | None, suffix: str) -> str:
        segments: list[str] = []
        if subscription_url:
            try:
                segments = [unquote(part) for part in urlsplit(subscription_url.strip()).path.split("/") if part]
            except ValueError:
                segments = []
        if segments and segments[-1] in {"info", "json", "usage", "raw", "apps"}:
            segments.pop()
        if len(segments) >= 2:
            tag, access_key = segments[-2], segments[-1]
        else:
            tag, access_key = self._subscription_tag, (token or "").strip()
        if not tag or not access_key or any(c in tag + access_key for c in "\r\n/?#\\"):
            raise PasarGuardError("لینک اشتراک GoGuard نامعتبر است")
        return f"/{quote(tag, safe='')}/{quote(access_key, safe='')}/{suffix}"

    async def subscription_info(self, token: str, *, subscription_url: str | None = None) -> dict:
        path = self._public_subscription_path(token, subscription_url, "info")
        data = await self.request("GET", path, auth=False, resolve_api_base=False)
        return _normalize_subscription(data) if isinstance(data, dict) else {"raw": data}

    async def subscription_usage(self, token: str, *, subscription_url: str | None = None) -> Any:
        info = await self.subscription_info(token, subscription_url=subscription_url)
        return {
            "used_traffic": info.get("used_traffic", info.get("total_usage", 0)),
            "data_limit": info.get("data_limit"),
            "total_usage": info.get("total_usage", info.get("used_traffic", 0)),
            "limit_usage": info.get("limit_usage"),
            "expire": info.get("expire"),
        }

    async def subscription_xray(self, token: str, *, subscription_url: str | None = None) -> list[dict[str, Any]]:
        path = self._public_subscription_path(token, subscription_url, "json")
        data = await self.request("GET", path, auth=False, resolve_api_base=False)
        return _items(data)

    async def subscription_wireguard_bytes(self, token: str, *, subscription_url: str | None = None) -> WireGuardResponse:
        return WireGuardResponse(status=404, data=None)
