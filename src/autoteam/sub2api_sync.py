"""Sub2API 账号同步。"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

from autoteam.config import SUB2API_EMAIL, SUB2API_PASSWORD, SUB2API_URL
from autoteam.textio import read_text

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_PAGE_SIZE = 200

_EXTRA_MANAGED = "autoteam_managed"
_EXTRA_KIND = "autoteam_kind"
_EXTRA_EMAIL = "autoteam_email"
_EXTRA_AUTH_FILE = "autoteam_auth_file"
_EXTRA_SOURCE = "autoteam_source"
_EXTRA_LAST_SYNC_AT = "autoteam_last_sync_at"

_KIND_POOL = "pool"
_KIND_MAIN = "main"


def _excerpt(text: str | bytes | None, limit: int = 200) -> str:
    value = text.decode("utf-8", errors="ignore") if isinstance(text, bytes) else str(text or "")
    value = value.strip().replace("\n", " ")
    if len(value) > limit:
        value = value[:limit] + "..."
    return value


def _api_base_url() -> str:
    base = (SUB2API_URL or "").strip().rstrip("/")
    if base.endswith("/api/v1"):
        return base
    if base.endswith("/api"):
        return f"{base}/v1"
    return f"{base}/api/v1"


def _request(method: str, path: str, *, token: str | None = None, label: str, **kwargs):
    url = f"{_api_base_url()}{path}"
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs)

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"[Sub2API] {label}返回了非 JSON 内容: {_excerpt(resp.text)}") from exc

    if resp.status_code != 200:
        message = payload.get("message") or payload.get("detail") or _excerpt(resp.text)
        raise RuntimeError(f"[Sub2API] {label}失败: HTTP {resp.status_code} {message}")

    if payload.get("code", 0) != 0:
        message = payload.get("message") or _excerpt(resp.text)
        raise RuntimeError(f"[Sub2API] {label}失败: {message}")

    return payload.get("data")


def _login() -> str:
    data = _request(
        "POST",
        "/auth/login",
        label="管理员登录",
        json={"email": SUB2API_EMAIL, "password": SUB2API_PASSWORD},
    )
    if not isinstance(data, dict):
        raise RuntimeError("[Sub2API] 管理员登录返回格式异常")
    if data.get("requires_2fa"):
        raise RuntimeError("[Sub2API] 当前账号启用了 2FA，AutoTeam 暂不支持自动同步到 Sub2API")
    access_token = (data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("[Sub2API] 管理员登录成功但未返回 access_token")
    return access_token


def _list_openai_oauth_accounts(token: str) -> list[dict]:
    items = []
    page = 1

    while True:
        data = _request(
            "GET",
            "/admin/accounts",
            token=token,
            label="获取账号列表",
            params={
                "page": page,
                "page_size": _PAGE_SIZE,
                "platform": "openai",
                "type": "oauth",
                "sort_by": "id",
                "sort_order": "asc",
            },
        )
        page_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(page_items, list):
            break
        items.extend(page_items)
        total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        if not page_items or len(items) >= total:
            break
        page += 1

    return items


def _is_managed_account(item: dict, *, kind: str | None = None) -> bool:
    extra = item.get("extra") or {}
    if not isinstance(extra, dict):
        return False
    if extra.get(_EXTRA_SOURCE) != "autoteam" and not extra.get(_EXTRA_MANAGED):
        return False
    if kind and extra.get(_EXTRA_KIND) != kind:
        return False
    return True


def _managed_email(item: dict) -> str:
    extra = item.get("extra") or {}
    credentials = item.get("credentials") or {}
    return (extra.get(_EXTRA_EMAIL) or credentials.get("email") or item.get("name") or "").strip().lower()


def _managed_auth_file(item: dict) -> str:
    extra = item.get("extra") or {}
    return (extra.get(_EXTRA_AUTH_FILE) or "").strip()


def _dedupe_managed_accounts(token: str, items: list[dict], *, kind: str) -> tuple[dict[str, dict], int]:
    deduped: dict[str, dict] = {}
    duplicates_deleted = 0

    for item in items:
        if not _is_managed_account(item, kind=kind):
            continue

        key = _managed_email(item)
        if not key:
            key = f"{kind}:{item.get('id')}"

        previous = deduped.get(key)
        if previous is None or int(item.get("id") or 0) > int(previous.get("id") or 0):
            if previous is not None:
                _delete_account(token, previous, label="删除重复账号")
                duplicates_deleted += 1
            deduped[key] = item
        else:
            _delete_account(token, item, label="删除重复账号")
            duplicates_deleted += 1

    return deduped, duplicates_deleted


def _parse_jwt_payload(token: str) -> dict:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _extract_organization_id(auth_claims: dict) -> str:
    organizations = auth_claims.get("organizations")
    if not isinstance(organizations, list):
        return ""
    for item in organizations:
        if not isinstance(item, dict):
            continue
        if item.get("is_default") and item.get("id"):
            return str(item["id"])
    for item in organizations:
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
    return ""


def _normalize_expires_at(value) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))


def _load_auth_data(path: Path) -> dict:
    return json.loads(read_text(path))


def _build_credentials(auth_data: dict) -> dict:
    id_token = auth_data.get("id_token", "")
    claims = _parse_jwt_payload(id_token) if id_token else {}
    auth_claims = claims.get("https://api.openai.com/auth", {}) if isinstance(claims, dict) else {}

    credentials = {
        "access_token": auth_data.get("access_token", ""),
        "expires_at": _normalize_expires_at(auth_data.get("expired")),
    }

    refresh_token = auth_data.get("refresh_token", "")
    if refresh_token:
        credentials["refresh_token"] = refresh_token
    if id_token:
        credentials["id_token"] = id_token

    email = auth_data.get("email") or claims.get("email") or ""
    if email:
        credentials["email"] = email

    account_id = auth_data.get("account_id") or auth_claims.get("chatgpt_account_id") or ""
    if account_id:
        credentials["chatgpt_account_id"] = account_id

    user_id = auth_claims.get("chatgpt_user_id") or ""
    if user_id:
        credentials["chatgpt_user_id"] = user_id

    organization_id = auth_claims.get("poid") or _extract_organization_id(auth_claims)
    if organization_id:
        credentials["organization_id"] = organization_id

    plan_type = auth_claims.get("chatgpt_plan_type") or ""
    if plan_type:
        credentials["plan_type"] = plan_type

    return credentials


def _build_extra(email: str, auth_file_name: str, *, kind: str) -> dict:
    return {
        _EXTRA_MANAGED: True,
        _EXTRA_KIND: kind,
        _EXTRA_EMAIL: email.lower(),
        _EXTRA_AUTH_FILE: auth_file_name,
        _EXTRA_SOURCE: "autoteam",
        _EXTRA_LAST_SYNC_AT: int(time.time()),
    }


def _create_account(token: str, *, name: str, credentials: dict, extra: dict, label: str) -> dict:
    return _request(
        "POST",
        "/admin/accounts",
        token=token,
        label=label,
        json={
            "name": name,
            "platform": "openai",
            "type": "oauth",
            "credentials": credentials,
            "extra": extra,
            "concurrency": 1,
            "priority": 1,
        },
    )


def _update_account(
    token: str, account: dict, *, credentials: dict, extra: dict, name: str | None = None, status: str | None = None
):
    payload = {"credentials": credentials, "extra": extra}
    if name:
        payload["name"] = name
    if status:
        payload["status"] = status
    return _request(
        "PUT",
        f"/admin/accounts/{account['id']}",
        token=token,
        label=f"更新账号 {name or _managed_email(account) or account.get('id')}",
        json=payload,
    )


def _delete_account(token: str, account: dict, *, label: str = "删除账号") -> bool:
    _request(
        "DELETE",
        f"/admin/accounts/{account['id']}",
        token=token,
        label=f"{label} {account.get('name') or account.get('id')}",
    )
    return True


def verify_sub2api_connection() -> bool:
    try:
        token = _login()
        accounts = _list_openai_oauth_accounts(token)
        logger.info("[验证] Sub2API 连接成功（当前 %d 个 OpenAI OAuth 账号）", len(accounts))
        return True
    except Exception as exc:
        logger.error("[验证] Sub2API 连接失败: %s", exc)
        return False


def sync_to_sub2api():
    from autoteam.accounts import STATUS_ACTIVE, load_accounts

    accounts = load_accounts()
    local_emails = {str(acc.get("email") or "").lower() for acc in accounts if acc.get("email")}
    active_targets = {}

    for acc in accounts:
        if acc.get("status") != STATUS_ACTIVE or not acc.get("auth_file"):
            continue
        auth_path = Path(acc["auth_file"])
        if not auth_path.exists():
            continue
        try:
            auth_data = _load_auth_data(auth_path)
        except Exception as exc:
            logger.warning("[Sub2API] 读取 auth 文件失败，跳过 %s: %s", auth_path, exc)
            continue

        email = (auth_data.get("email") or acc.get("email") or "").strip().lower()
        if not email:
            continue

        active_targets[email] = {
            "email": email,
            "name": acc.get("email") or email,
            "auth_path": auth_path,
            "auth_data": auth_data,
        }

    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    existing_by_email, duplicates_deleted = _dedupe_managed_accounts(token, remote_accounts, kind=_KIND_POOL)

    logger.info(
        "[Sub2API] active 账号: %d, Sub2API 管理账号: %d",
        len(active_targets),
        len(existing_by_email),
    )

    created = 0
    updated = 0
    deleted = 0

    for email, target in active_targets.items():
        desired_credentials = _build_credentials(target["auth_data"])
        desired_extra = _build_extra(email, target["auth_path"].name, kind=_KIND_POOL)
        existing = existing_by_email.get(email)

        if existing:
            merged_credentials = dict(existing.get("credentials") or {})
            merged_credentials.update(desired_credentials)
            merged_extra = dict(existing.get("extra") or {})
            merged_extra.update(desired_extra)
            _update_account(
                token,
                existing,
                credentials=merged_credentials,
                extra=merged_extra,
                status="active" if existing.get("status") != "active" else None,
            )
            logger.info("[Sub2API] 更新: %s", email)
            updated += 1
            continue

        _create_account(
            token,
            name=target["name"],
            credentials=desired_credentials,
            extra=desired_extra,
            label=f"创建账号 {email}",
        )
        logger.info("[Sub2API] 创建: %s", email)
        created += 1

    for email, account in existing_by_email.items():
        if email in local_emails and email not in active_targets:
            _delete_account(token, account, label="删除非 active 账号")
            logger.info("[Sub2API] 删除非 active 账号: %s", email)
            deleted += 1

    final_accounts = _list_openai_oauth_accounts(token)
    final_managed = [item for item in final_accounts if _is_managed_account(item, kind=_KIND_POOL)]
    logger.info(
        "[Sub2API] 同步完成: 创建 %d, 更新 %d, 删除 %d, 远端去重 %d",
        created,
        updated,
        deleted,
        duplicates_deleted,
    )
    logger.info("[Sub2API] Sub2API 中本地管理: %d, 本地 active: %d", len(final_managed), len(active_targets))
    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "remote_duplicates_deleted": duplicates_deleted,
    }


def sync_main_codex_to_sub2api(filepath):
    auth_path = Path(filepath)
    if not auth_path.exists():
        raise FileNotFoundError(f"主号认证文件不存在: {auth_path}")

    auth_data = _load_auth_data(auth_path)
    email = (auth_data.get("email") or "").strip().lower()
    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    existing_by_email, duplicates_deleted = _dedupe_managed_accounts(token, remote_accounts, kind=_KIND_MAIN)

    desired_credentials = _build_credentials(auth_data)
    desired_extra = _build_extra(email, auth_path.name, kind=_KIND_MAIN)
    name = f"AutoTeam Main | {email}" if email else "AutoTeam Main"

    current = existing_by_email.get(email) if email else None
    if current:
        merged_credentials = dict(current.get("credentials") or {})
        merged_credentials.update(desired_credentials)
        merged_extra = dict(current.get("extra") or {})
        merged_extra.update(desired_extra)
        _update_account(token, current, credentials=merged_credentials, extra=merged_extra, name=name, status="active")
        account_id = current.get("id")
    else:
        created = _create_account(
            token,
            name=name,
            credentials=desired_credentials,
            extra=desired_extra,
            label="创建主号账号",
        )
        account_id = created.get("id") if isinstance(created, dict) else None

    deleted = []
    for item in existing_by_email.values():
        if current and item.get("id") == current.get("id"):
            continue
        if email and _managed_email(item) == email:
            continue
        _delete_account(token, item, label="删除旧主号账号")
        deleted.append(item.get("id"))

    logger.info(
        "[Sub2API] 主号 Codex 已同步: %s (account_id=%s, duplicates=%d, deleted_old=%d)",
        auth_path.name,
        account_id,
        duplicates_deleted,
        len(deleted),
    )
    return {"uploaded": auth_path.name, "account_id": account_id, "deleted_old": deleted}


def delete_main_codex_from_sub2api():
    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    deleted = []

    for item in remote_accounts:
        if not _is_managed_account(item, kind=_KIND_MAIN):
            continue
        _delete_account(token, item, label="删除主号账号")
        deleted.append(item.get("name") or str(item.get("id")))

    return {"deleted": deleted, "count": len(deleted)}


def delete_account_from_sub2api(email: str, *, auth_names: list[str] | None = None):
    token = _login()
    remote_accounts = _list_openai_oauth_accounts(token)
    auth_name_set = {name for name in (auth_names or []) if name}
    deleted = []

    for item in remote_accounts:
        if not _is_managed_account(item, kind=_KIND_POOL):
            continue
        item_email = _managed_email(item)
        item_auth_name = _managed_auth_file(item)
        if item_email != email.lower() and item_auth_name not in auth_name_set:
            continue
        _delete_account(token, item, label="删除账号")
        deleted.append(item.get("name") or str(item.get("id")))

    return {"deleted": deleted, "count": len(deleted)}
