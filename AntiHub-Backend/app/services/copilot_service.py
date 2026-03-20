"""
GitHub Copilot Chat 代理服务

认证流程：
1. OAuth Device Flow：用户在前端发起 → 获取 user_code → 浏览器授权 → 轮询获取 GitHub token
2. 或直接导入 GitHub PAT（Fine-grained PAT 需要 Copilot 权限）
3. 服务用 GitHub token 向 https://api.github.com/copilot_internal/v2/token 交换短期 Copilot token
4. Copilot token 作为 Bearer 调用 https://api.githubcopilot.com/chat/completions（OpenAI 兼容）
5. Copilot token 有 expires_at，过期前自动刷新
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RedisClient
from app.models.copilot_account import CopilotAccount
from app.repositories.copilot_account_repository import CopilotAccountRepository
from app.utils.encryption import encrypt_api_key as encrypt_secret
from app.utils.encryption import decrypt_api_key as decrypt_secret

logger = logging.getLogger(__name__)

# GitHub API endpoints
GITHUB_USER_API = "https://api.github.com/user"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_API = "https://api.github.com/copilot_internal/v2/token"
COPILOT_CHAT_API = "https://api.githubcopilot.com/chat/completions"

# GitHub OAuth App client_id for Copilot CLI
GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

# Copilot token refresh buffer (refresh 5 min before expiry)
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)

# Device flow session expiry (15 min)
DEVICE_FLOW_SESSION_EXPIRY = 900


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _openai_sse_chunk(data: str) -> bytes:
    return f"data: {data}\n\n".encode("utf-8")


def _openai_sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def _openai_sse_error(message: str, code: int = 500) -> bytes:
    payload = json.dumps(
        {"error": {"message": message, "type": "upstream_error", "code": code}},
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n".encode("utf-8")


class CopilotService:
    """GitHub Copilot Chat 代理服务"""

    def __init__(self, db: AsyncSession, redis: RedisClient):
        self.db = db
        self.redis = redis
        self.repo = CopilotAccountRepository(db)

    # ─── 账号管理 ────────────────────────────────────────────────

    async def import_account(
        self,
        user_id: int,
        *,
        github_token: str,
        account_name: Optional[str] = None,
        is_shared: int = 0,
    ) -> Dict[str, Any]:
        """导入 GitHub Copilot 账号（通过 GitHub token）"""
        github_token = github_token.strip()
        if not github_token:
            raise ValueError("github_token 不能为空")

        # Step 1: Validate GitHub token & get user info
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            user_info = await self._get_github_user(client, github_token)
            github_login = user_info.get("login", "")

            # Step 2: Get initial Copilot token to validate Copilot access
            copilot_data = await self._exchange_copilot_token(client, github_token)

        copilot_token = copilot_data.get("token", "")
        expires_at_unix = copilot_data.get("expires_at", 0)
        endpoints = copilot_data.get("endpoints", {})
        chat_endpoint = endpoints.get("api", COPILOT_CHAT_API)

        if not copilot_token:
            raise ValueError("无法获取 Copilot token，请确认该 GitHub 账号已开通 Copilot")

        token_expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc) if expires_at_unix else None

        # Check for duplicate
        existing = await self.repo.get_by_user_id_and_login(user_id, github_login)
        if existing:
            # Update existing account
            creds = self._load_credentials(existing)
            creds.update({
                "github_token": github_token,
                "copilot_token": copilot_token,
                "chat_endpoint": chat_endpoint,
            })
            existing.credentials = encrypt_secret(json.dumps(creds, ensure_ascii=False))
            existing.token_expires_at = token_expires_at
            existing.last_refresh_at = _now_utc()
            existing.status = 1
            if account_name:
                existing.account_name = account_name
            await self.db.flush()
            await self.db.commit()
            return {
                "success": True,
                "message": "Copilot 账号已更新",
                "data": self._account_to_dict(existing),
            }

        # Create new account
        display_name = account_name or f"Copilot-{github_login}"
        creds_data = {
            "github_token": github_token,
            "copilot_token": copilot_token,
            "chat_endpoint": chat_endpoint,
        }

        account = CopilotAccount(
            user_id=user_id,
            account_name=display_name,
            status=1,
            is_shared=is_shared,
            github_login=github_login,
            copilot_plan=copilot_data.get("copilot_plan"),
            token_expires_at=token_expires_at,
            last_refresh_at=_now_utc(),
            credentials=encrypt_secret(json.dumps(creds_data, ensure_ascii=False)),
        )
        account = await self.repo.create(account)
        await self.db.commit()

        return {
            "success": True,
            "message": "Copilot 账号已导入",
            "data": self._account_to_dict(account),
        }

    # ─── OAuth Device Flow ──────────────────────────────────────

    async def start_device_flow(self, user_id: int) -> Dict[str, Any]:
        """
        发起 GitHub OAuth Device Flow。
        返回 user_code、verification_uri 供前端展示。
        将 device_code 存入 Redis 等待轮询。
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                GITHUB_DEVICE_CODE_URL,
                data={
                    "client_id": GITHUB_COPILOT_CLIENT_ID,
                    "scope": "read:user",
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                raise ValueError(f"GitHub Device Flow 启动失败 (HTTP {resp.status_code}): {resp.text[:300]}")

            data = resp.json()

        device_code = data.get("device_code", "")
        user_code = data.get("user_code", "")
        verification_uri = data.get("verification_uri", "https://github.com/login/device")
        expires_in = int(data.get("expires_in", 900))
        interval = int(data.get("interval", 5))

        if not device_code or not user_code:
            raise ValueError("GitHub 返回的 Device Flow 数据不完整")

        # Store session in Redis
        session_key = f"copilot_device:{user_code}"
        session_data = {
            "user_id": user_id,
            "device_code": device_code,
            "interval": interval,
            "created_at": _now_utc().isoformat(),
        }
        await self.redis.set_json(session_key, session_data, expire=expires_in)

        return {
            "success": True,
            "data": {
                "user_code": user_code,
                "verification_uri": verification_uri,
                "expires_in": expires_in,
                "interval": interval,
            },
        }

    async def poll_device_flow(self, user_id: int, user_code: str) -> Dict[str, Any]:
        """
        轮询 GitHub Device Flow 授权状态。
        返回：
        - pending: 用户尚未授权
        - success: 授权完成，账号已导入
        - expired/error: 失败
        """
        session_key = f"copilot_device:{user_code}"
        session_data = await self.redis.get_json(session_key)
        if not session_data:
            return {"success": False, "status": "expired", "message": "Device Flow 已过期，请重新发起"}

        if session_data.get("user_id") != user_id:
            return {"success": False, "status": "error", "message": "无权访问此 Device Flow"}

        device_code = session_data.get("device_code", "")

        # Poll GitHub for token
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                GITHUB_OAUTH_TOKEN_URL,
                data={
                    "client_id": GITHUB_COPILOT_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )

            data = resp.json()

        logger.info("[Copilot Device Flow] poll response: %s", json.dumps(data, ensure_ascii=False)[:500])

        error = data.get("error")
        # When authorization succeeds, GitHub returns access_token without error field.
        # Check for access_token first before checking error.
        github_token = data.get("access_token", "")
        if github_token:
            # Success — got the token
            await self.redis.delete(session_key)
            try:
                result = await self.import_account(user_id=user_id, github_token=github_token)
                return {
                    "success": True,
                    "status": "success",
                    "message": "GitHub Copilot 账号授权成功",
                    "data": result.get("data"),
                }
            except Exception as e:
                return {"success": False, "status": "error", "message": str(e)}

        if error == "authorization_pending":
            return {"success": False, "status": "pending", "message": "等待用户授权..."}
        elif error == "slow_down":
            return {"success": False, "status": "pending", "message": "请稍后再试", "interval": data.get("interval", 10)}
        elif error == "expired_token":
            await self.redis.delete(session_key)
            return {"success": False, "status": "expired", "message": "Device Flow 已过期，请重新发起"}
        elif error == "access_denied":
            await self.redis.delete(session_key)
            return {"success": False, "status": "error", "message": "用户拒绝了授权"}
        elif error:
            await self.redis.delete(session_key)
            return {"success": False, "status": "error", "message": f"授权失败: {error}"}

        # No access_token and no error — unexpected
        await self.redis.delete(session_key)
        return {"success": False, "status": "error", "message": f"GitHub 返回异常: {json.dumps(data)[:300]}"}

    async def list_accounts(self, user_id: int) -> Dict[str, Any]:
        accounts = await self.repo.list_by_user_id(user_id)
        return {
            "success": True,
            "data": [self._account_to_dict(a) for a in accounts],
        }

    async def get_account(self, user_id: int, account_id: int) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        return {"success": True, "data": self._account_to_dict(account)}

    async def update_account_status(self, user_id: int, account_id: int, new_status: int) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        account.status = new_status
        await self.db.flush()
        await self.db.commit()
        return {"success": True, "message": "状态已更新", "data": self._account_to_dict(account)}

    async def update_account_name(self, user_id: int, account_id: int, name: str) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        account.account_name = name.strip()
        await self.db.flush()
        await self.db.commit()
        return {"success": True, "message": "名称已更新", "data": self._account_to_dict(account)}

    async def delete_account(self, user_id: int, account_id: int) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        await self.repo.delete(account)
        await self.db.commit()
        return {"success": True, "message": "账号已删除"}

    async def refresh_account_token(self, user_id: int, account_id: int) -> Dict[str, Any]:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            await self._refresh_copilot_token(client, account)
        await self.db.commit()
        await self.db.refresh(account)
        return {"success": True, "message": "Token 已刷新", "data": self._account_to_dict(account)}

    async def list_available_models(self, user_id: int, account_id: int) -> Dict[str, Any]:
        """查询 Copilot 可用模型列表"""
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            raise ValueError("账号不存在")
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            copilot_token = await self._ensure_copilot_token(client, account)
            # Try the models endpoint
            creds = self._load_credentials(account)
            chat_endpoint = creds.get("chat_endpoint", COPILOT_CHAT_API)
            base_url = chat_endpoint.replace("/chat/completions", "").rstrip("/")
            models_url = f"{base_url}/models"
            resp = await client.get(
                models_url,
                headers={
                    "Authorization": f"Bearer {copilot_token}",
                    "Accept": "application/json",
                    "Editor-Version": "vscode/1.100.0",
                    "Editor-Plugin-Version": "copilot/1.300.0",
                    "Copilot-Integration-Id": "vscode-chat",
                },
            )
            logger.info("[Copilot] models endpoint %s -> %s", models_url, resp.status_code)
            if resp.status_code == 200:
                return resp.json()
            return {"status_code": resp.status_code, "body": resp.text[:2000]}

    # ─── Chat Completions (SSE 流式代理) ────────────────────────

    async def chat_completions_stream(
        self, user_id: int, request_data: Dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """
        GitHub Copilot Chat 流式代理。
        - 自动选择可用账号
        - 自动刷新 Copilot token
        - 代理 OpenAI 兼容 SSE 流
        """
        accounts = await self.repo.list_enabled_by_user_id(user_id)
        if not accounts:
            yield _openai_sse_error("没有可用的 Copilot 账号，请先导入账号", code=400)
            yield _openai_sse_done()
            return

        exclude: set[int] = set()
        max_attempts = max(2, min(3, len(accounts)))  # at least 2 attempts for transient errors

        for attempt in range(max_attempts):
            available = [a for a in accounts if a.id not in exclude]
            if not available:
                # All accounts excluded (auth failures) — allow retrying any for transient errors
                available = list(accounts)

            account = secrets.choice(available)

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0),
                ) as client:
                    # Ensure token is valid
                    copilot_token = await self._ensure_copilot_token(client, account)

                    # Load credentials eagerly (avoid lazy-load after flush)
                    creds = self._load_credentials(account)
                    chat_endpoint = creds.get("chat_endpoint", COPILOT_CHAT_API)
                    if not chat_endpoint.endswith("/chat/completions"):
                        chat_endpoint = chat_endpoint.rstrip("/") + "/chat/completions"

                    # Build upstream request
                    headers = self._build_copilot_headers(copilot_token)
                    payload = self._build_chat_payload(request_data)

                    # Capture values before any DB ops to avoid greenlet issues
                    acct_id = account.id
                    acct_login = account.github_login

                    logger.info(
                        "[Copilot chat] account=%s login=%s endpoint=%s model=%s (raw=%s) payload_keys=%s",
                        acct_id, acct_login, chat_endpoint,
                        payload.get("model"), request_data.get("model"),
                        list(payload.keys()),
                    )
                    # Debug: log payload info
                    debug_payload = {k: v for k, v in payload.items() if k not in ("messages", "tools")}
                    debug_payload["messages_count"] = len(payload.get("messages", []))
                    debug_payload["has_tools"] = "tools" in payload
                    debug_payload["tools_count"] = len(payload.get("tools", []))
                    msg_roles = [f"{m.get('role','?')}{'[tc]' if m.get('tool_calls') else ''}" for m in payload.get("messages", [])]
                    debug_payload["msg_roles"] = msg_roles
                    total_size = len(json.dumps(payload, ensure_ascii=False))
                    debug_payload["total_payload_bytes"] = total_size
                    logger.info("[Copilot chat] debug payload: %s", debug_payload)

                    account.last_used_at = _now_utc()
                    await self.db.flush()

                    async with client.stream(
                        "POST", chat_endpoint, headers=headers, json=payload,
                    ) as resp:
                        if resp.status_code >= 400:
                            body = await resp.aread()
                            error_text = body.decode("utf-8", errors="replace")[:2000]
                            logger.warning(
                                "[Copilot chat] upstream error: %s %s headers=%s",
                                resp.status_code, error_text,
                                dict(resp.headers),
                            )
                            if resp.status_code in (401, 403) and attempt < max_attempts - 1:
                                exclude.add(account.id)
                                continue
                            if resp.status_code in (429, 500, 502, 503) and attempt < max_attempts - 1:
                                continue
                            yield _openai_sse_error(
                                error_text or f"Copilot upstream error: {resp.status_code}",
                                code=resp.status_code,
                            )
                            yield _openai_sse_done()
                            return

                        async for chunk in resp.aiter_bytes():
                            yield chunk

                    return

            except httpx.HTTPError as e:
                logger.warning("[Copilot chat] HTTP error: %s", e)
                if attempt < max_attempts - 1:
                    continue
                yield _openai_sse_error(f"Copilot 连接失败: {e}", code=502)
                yield _openai_sse_done()
                return
            except Exception as e:
                logger.error("[Copilot chat] unexpected error: %s", e, exc_info=True)
                yield _openai_sse_error(f"Copilot 请求失败: {e}", code=500)
                yield _openai_sse_done()
                return

        yield _openai_sse_error("所有 Copilot 账号均不可用", code=503)
        yield _openai_sse_done()

    # ─── 内部方法 ────────────────────────────────────────────────

    async def _get_github_user(self, client: httpx.AsyncClient, github_token: str) -> Dict[str, Any]:
        resp = await client.get(
            GITHUB_USER_API,
            headers={"Authorization": f"Bearer {github_token}", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise ValueError(f"GitHub token 无效或已过期 (HTTP {resp.status_code})")
        return resp.json()

    async def _exchange_copilot_token(
        self, client: httpx.AsyncClient, github_token: str
    ) -> Dict[str, Any]:
        resp = await client.get(
            COPILOT_TOKEN_API,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
                "Editor-Version": "vscode/1.100.0",
                "Editor-Plugin-Version": "copilot/1.300.0",
            },
        )
        if resp.status_code == 401:
            raise ValueError("GitHub token 无权访问 Copilot（可能未开通 Copilot 订阅）")
        if resp.status_code != 200:
            body = resp.text[:500]
            raise ValueError(f"获取 Copilot token 失败 (HTTP {resp.status_code}): {body}")
        data = resp.json()
        # Log available info (exclude token itself)
        safe_keys = {k: v for k, v in data.items() if k not in ("token",)}
        logger.info("[Copilot] token exchange response keys: %s", safe_keys)
        return data

    async def _refresh_copilot_token(
        self, client: httpx.AsyncClient, account: CopilotAccount
    ) -> str:
        creds = self._load_credentials(account)
        github_token = creds.get("github_token", "")
        if not github_token:
            raise ValueError("缺少 GitHub token，无法刷新")

        # Capture before any DB ops
        acct_id = account.id
        acct_login = account.github_login

        data = await self._exchange_copilot_token(client, github_token)
        copilot_token = data.get("token", "")
        expires_at_unix = data.get("expires_at", 0)

        if not copilot_token:
            raise ValueError("刷新 Copilot token 失败")

        creds["copilot_token"] = copilot_token
        endpoints = data.get("endpoints", {})
        if endpoints.get("api"):
            creds["chat_endpoint"] = endpoints["api"]

        account.credentials = encrypt_secret(json.dumps(creds, ensure_ascii=False))
        token_expires = (
            datetime.fromtimestamp(expires_at_unix, tz=timezone.utc) if expires_at_unix else None
        )
        account.token_expires_at = token_expires
        account.last_refresh_at = _now_utc()
        await self.db.flush()

        logger.info(
            "[Copilot] refreshed token for account=%s login=%s expires_at=%s",
            acct_id, acct_login, token_expires,
        )
        return copilot_token

    async def _ensure_copilot_token(
        self, client: httpx.AsyncClient, account: CopilotAccount
    ) -> str:
        creds = self._load_credentials(account)
        copilot_token = creds.get("copilot_token", "")

        needs_refresh = False
        if not copilot_token:
            needs_refresh = True
        elif account.token_expires_at:
            if _now_utc() >= account.token_expires_at - TOKEN_REFRESH_BUFFER:
                needs_refresh = True

        if needs_refresh:
            copilot_token = await self._refresh_copilot_token(client, account)

        return copilot_token

    def _build_copilot_headers(self, copilot_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {copilot_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot/1.300.0",
            "Copilot-Integration-Id": "vscode-chat",
            "User-Agent": "GitHubCopilotChat/1.300.0",
            "X-Request-Id": secrets.token_hex(16),
        }

    # Copilot model mapping: convert incoming model names to Copilot-supported IDs
    # Most models pass through as-is since Copilot supports them directly.
    # Only map models with dated suffixes or legacy names.
    COPILOT_MODEL_MAP: Dict[str, str] = {
        # Dash-separated variants → dot-separated Copilot names
        "claude-opus-4-6": "claude-opus-4.6",
        "claude-opus-4-5": "claude-opus-4.5",
        "claude-sonnet-4-6": "claude-sonnet-4.6",
        "claude-sonnet-4-5": "claude-sonnet-4.5",
        "claude-haiku-4-5": "claude-haiku-4.5",
        # Dated Claude variants → short names
        "claude-sonnet-4-20250514": "claude-sonnet-4",
        "claude-3-5-sonnet-20241022": "claude-sonnet-4",
        "claude-3.5-sonnet": "claude-sonnet-4",
        "claude-haiku-4-5-20251001": "claude-haiku-4.5",
        "claude-3-haiku-20240307": "claude-haiku-4.5",
        "claude-3-opus-20240229": "claude-opus-4.5",
        "claude-opus-4-6-20260217": "claude-opus-4.6",
        "claude-sonnet-4-6-20260514": "claude-sonnet-4.6",
        # GPT dated variants
        "gpt-4-turbo": "gpt-4o",
    }

    # Default fallback model
    COPILOT_DEFAULT_MODEL = "gpt-4o"

    def _build_chat_payload(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """构建 Copilot Chat 请求 payload（OpenAI 兼容格式）"""
        raw_model = request_data.get("model", "gpt-4o")
        model = self.COPILOT_MODEL_MAP.get(raw_model, raw_model)
        # If model still looks unknown, use a safe default
        if model == raw_model and raw_model not in (
            "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4", "gpt-3.5-turbo", "gpt-5-mini",
            "gpt-5.1", "gpt-5.2", "gpt-5.1-codex", "gpt-5.2-codex", "gpt-5.3-codex",
            "gpt-5.1-codex-mini", "gpt-5.1-codex-max",
            "o3-mini", "o1-mini", "o1",
            "claude-opus-4.6", "claude-opus-4.5", "claude-sonnet-4.6", "claude-sonnet-4.5",
            "claude-sonnet-4", "claude-haiku-4.5",
            "gemini-2.5-pro", "gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-3.1-pro-preview",
            "grok-code-fast-1",
        ):
            logger.info("[Copilot] unknown model '%s', falling back to '%s'", raw_model, self.COPILOT_DEFAULT_MODEL)
            model = self.COPILOT_DEFAULT_MODEL

        payload: Dict[str, Any] = {
            "messages": [],  # will be set after tools check
            "model": model,
            "stream": True,
        }

        # max_tokens: cap to model limits
        max_tokens = request_data.get("max_tokens")
        if max_tokens is not None:
            payload["max_tokens"] = min(int(max_tokens), 32000)

        # Pass through optional parameters
        for key in ("temperature", "top_p", "n", "stop"):
            if key in request_data:
                payload[key] = request_data[key]

        # Tools: pass through, but if too large, truncate descriptions to fit
        tools_stripped = False
        tools = request_data.get("tools")
        raw_messages = request_data.get("messages", [])
        if tools:
            # Estimate space needed for messages (system + last user at minimum)
            msgs_estimate = 0
            for m in raw_messages:
                if m.get("role") == "system":
                    msgs_estimate += len(json.dumps(m, ensure_ascii=False))
            # Last user message
            for m in reversed(raw_messages):
                if m.get("role") == "user":
                    msgs_estimate += len(json.dumps(m, ensure_ascii=False))
                    break
            # Leave room for messages: total budget 128KB, reserve for messages
            TOTAL_BUDGET = 128000
            msgs_reserve = max(msgs_estimate + 5000, 20000)  # at least 20KB for messages
            tools_budget = TOTAL_BUDGET - msgs_reserve

            serialized = json.dumps(tools, ensure_ascii=False)
            if len(serialized) <= tools_budget:
                payload["tools"] = tools
            else:
                # Try truncating tool descriptions to fit within limit
                compact_tools = self._compact_tools(tools, max_bytes=tools_budget)
                if compact_tools:
                    payload["tools"] = compact_tools
                    logger.info(
                        "[Copilot] compacted %d tools from %d to %d bytes (budget=%d)",
                        len(tools), len(serialized),
                        len(json.dumps(compact_tools, ensure_ascii=False)), tools_budget,
                    )
                else:
                    # Compaction alone isn't enough — select subset of tools
                    selected = self._select_essential_tools(tools, raw_messages, max_bytes=tools_budget)
                    if selected:
                        payload["tools"] = selected
                        logger.info(
                            "[Copilot] selected %d/%d essential tools (%d bytes, budget=%d)",
                            len(selected), len(tools),
                            len(json.dumps(selected, ensure_ascii=False)), tools_budget,
                        )
                    else:
                        tools_stripped = True
                        logger.info(
                            "[Copilot] stripping %d tools (%d bytes) — could not fit",
                            len(tools), len(serialized),
                        )
            if "tools" in payload and "tool_choice" in request_data:
                payload["tool_choice"] = request_data["tool_choice"]

        # Collect available tool names for validation
        avail_names = None
        if "tools" in payload:
            avail_names = {t.get("function", {}).get("name", "") for t in payload["tools"]}
            avail_names.discard("")

        # Sanitize messages (flatten content lists, strip tool messages if tools were stripped)
        payload["messages"] = self._sanitize_messages(
            request_data.get("messages", []),
            strip_tool_messages=tools_stripped,
            available_tool_names=avail_names,
        )

        # Trim conversation if total payload exceeds safe limit.
        # Copilot drops the connection mid-stream on very large payloads.
        MAX_PAYLOAD_BYTES = 128000
        total = len(json.dumps(payload, ensure_ascii=False))
        if total > MAX_PAYLOAD_BYTES:
            payload["messages"] = self._trim_messages(payload["messages"], payload, MAX_PAYLOAD_BYTES)

        return payload

    @staticmethod
    def _trim_messages(messages: list, payload_shell: dict, max_bytes: int) -> list:
        """Trim middle messages to keep total payload under max_bytes.
        
        Keeps: system message(s) at start + as many recent messages as possible.
        Always keeps at least the last user message (required by API).
        """
        if len(messages) <= 3:
            return messages

        # Separate system prefix and conversation messages
        system_msgs = []
        conv_msgs = []
        for m in messages:
            if m.get("role") == "system" and not conv_msgs:
                system_msgs.append(m)
            else:
                conv_msgs.append(m)

        # Calculate overhead (everything except messages)
        shell = {k: v for k, v in payload_shell.items() if k != "messages"}
        overhead = len(json.dumps(shell, ensure_ascii=False)) + len(json.dumps(system_msgs, ensure_ascii=False)) + 50

        # Find how many recent messages fit
        budget = max_bytes - overhead
        kept = []
        running_size = 0
        for msg in reversed(conv_msgs):
            msg_size = len(json.dumps(msg, ensure_ascii=False))
            if running_size + msg_size > budget and kept:
                # Already have some messages, stop adding
                break
            kept.insert(0, msg)
            running_size += msg_size

        # Ensure we don't start with a tool message (orphan)
        while len(kept) > 1 and kept[0].get("role") == "tool":
            kept.pop(0)

        # Ensure we don't start with an assistant tool_calls message without tool responses
        while len(kept) > 1 and kept[0].get("role") == "assistant" and kept[0].get("tool_calls"):
            kept.pop(0)

        # Guarantee at least the last user message exists
        if not any(m.get("role") == "user" for m in kept):
            for m in reversed(conv_msgs):
                if m.get("role") == "user":
                    kept = [m]
                    break

        trimmed_count = len(conv_msgs) - len(kept)
        if trimmed_count > 0:
            logger.info("[Copilot] trimmed %d old messages to fit payload (%d→%d msgs)",
                        trimmed_count, len(messages), len(system_msgs) + len(kept))

        return system_msgs + kept

    @staticmethod
    def _compact_tools(tools: list, max_bytes: int = 45000) -> list:
        """Truncate tool parameter descriptions to fit within size limit."""
        import copy
        compact = copy.deepcopy(tools)

        def _truncate_descriptions(obj, max_len=80):
            """Recursively truncate 'description' fields in tool schemas."""
            if isinstance(obj, dict):
                for key, val in obj.items():
                    if key == "description" and isinstance(val, str) and len(val) > max_len:
                        obj[key] = val[:max_len] + "..."
                    else:
                        _truncate_descriptions(val, max_len)
            elif isinstance(obj, list):
                for item in obj:
                    _truncate_descriptions(item, max_len)

        _truncate_descriptions(compact)
        serialized = json.dumps(compact, ensure_ascii=False)
        if len(serialized) <= max_bytes:
            return compact

        # Still too big — try more aggressive truncation
        _truncate_descriptions(compact, 30)
        serialized = json.dumps(compact, ensure_ascii=False)
        if len(serialized) <= max_bytes:
            return compact

        return []  # Give up

    @classmethod
    def _select_essential_tools(cls, tools: list, messages: list, max_bytes: int = 45000) -> list:
        """Select a subset of tools that fits within the byte budget.
        
        Priority:
        1. Tools referenced in recent tool_calls (must keep for conversation coherence)
        2. Common built-in tools (Read, Write, Edit, Bash, Grep, Glob, etc.)
        3. Remaining tools by original order until budget is full
        """
        import copy

        # Collect tool names referenced in messages
        referenced_names: set = set()
        for msg in messages:
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn_name = tc.get("function", {}).get("name", "")
                    if fn_name:
                        referenced_names.add(fn_name)

        # Built-in tool priority list
        PRIORITY_TOOLS = {
            "Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task", "TaskOutput",
            "WebFetch", "WebSearch", "AskUserQuestion", "NotebookEdit",
        }

        # Build lookup by tool name
        tool_by_name: dict = {}
        for t in tools:
            name = t.get("function", {}).get("name", "")
            if name:
                tool_by_name[name] = t

        # Categorize
        referenced = [tool_by_name[n] for n in referenced_names if n in tool_by_name]
        priority = [tool_by_name[n] for n in PRIORITY_TOOLS if n in tool_by_name and n not in referenced_names]
        rest = [t for t in tools if t.get("function", {}).get("name", "") not in referenced_names and t.get("function", {}).get("name", "") not in PRIORITY_TOOLS]

        ordered = referenced + priority + rest

        # Compact descriptions aggressively, then add tools until budget is met
        compacted = copy.deepcopy(ordered)
        def _trunc(obj, max_len=30):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "description" and isinstance(v, str) and len(v) > max_len:
                        obj[k] = v[:max_len] + "..."
                    else:
                        _trunc(v, max_len)
            elif isinstance(obj, list):
                for item in obj:
                    _trunc(item, max_len)
        _trunc(compacted)

        # Greedily add tools until budget
        selected = []
        current_size = 2  # for []
        for tool in compacted:
            tool_json = json.dumps(tool, ensure_ascii=False)
            needed = len(tool_json) + (1 if not selected else 2)  # comma + tool
            if current_size + needed > max_bytes:
                break
            selected.append(tool)
            current_size += needed

        return selected if selected else []

    @staticmethod
    def _sanitize_messages(messages: list, *, strip_tool_messages: bool = False, available_tool_names: set = None) -> list:
        """Sanitize messages for Copilot /chat/completions compatibility.

        - Converts multimodal content lists to plain text strings
        - Removes tool_calls with empty/missing function names (invalid for OpenAI API)
        - Removes tool_calls referencing tools not in available_tool_names
        - Removes orphaned tool-role messages whose tool_call_id has no matching call
        - If strip_tool_messages=True, removes ALL tool role messages and tool_calls
        """
        # --- Pass 1: clean assistant tool_calls, collect valid call IDs ---
        valid_tc_ids: set = set()
        removed_tc_ids: set = set()
        pass1 = []
        for msg in messages:
            msg = dict(msg)
            role = msg.get("role", "")

            if strip_tool_messages and role == "tool":
                continue

            if role == "assistant" and "tool_calls" in msg:
                if strip_tool_messages:
                    msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                    if not msg.get("content"):
                        msg["content"] = "(tool call removed)"
                else:
                    cleaned_tcs = []
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "")
                        tc_id = tc.get("id", "")
                        # Skip tool_calls with empty name
                        if not fn_name:
                            removed_tc_ids.add(tc_id)
                            continue
                        # Skip tool_calls referencing unknown tools
                        if available_tool_names and fn_name not in available_tool_names:
                            removed_tc_ids.add(tc_id)
                            continue
                        valid_tc_ids.add(tc_id)
                        cleaned_tcs.append(tc)
                    if cleaned_tcs:
                        msg["tool_calls"] = cleaned_tcs
                    else:
                        msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                        if not msg.get("content"):
                            msg["content"] = "(tool call removed)"

            # Flatten multimodal content lists to plain text
            content = msg.get("content")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append(item.get("text", ""))
                        elif item.get("type") == "image_url":
                            parts.append("[image]")
                        elif item.get("type") == "tool_result":
                            parts.append(str(item.get("content", "")))
                        else:
                            parts.append(str(item.get("text", item.get("content", ""))))
                    elif isinstance(item, str):
                        parts.append(item)
                msg["content"] = "\n".join(parts) if parts else ""
            pass1.append(msg)

        # --- Pass 2: remove orphaned tool-role messages ---
        sanitized = []
        for msg in pass1:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in removed_tc_ids:
                    continue
            sanitized.append(msg)

        if removed_tc_ids:
            logger.info("[Copilot] removed %d invalid/orphaned tool_calls: %s",
                        len(removed_tc_ids), removed_tc_ids)
        return sanitized

    def _load_credentials(self, account: CopilotAccount) -> Dict[str, Any]:
        try:
            raw = decrypt_secret(account.credentials)
            return json.loads(raw)
        except Exception:
            return {}

    @staticmethod
    def _account_to_dict(account: CopilotAccount) -> Dict[str, Any]:
        return {
            "id": account.id,
            "user_id": account.user_id,
            "account_name": account.account_name,
            "status": account.status,
            "is_shared": account.is_shared,
            "github_login": account.github_login,
            "copilot_plan": account.copilot_plan,
            "token_expires_at": account.token_expires_at.isoformat() if account.token_expires_at else None,
            "last_refresh_at": account.last_refresh_at.isoformat() if account.last_refresh_at else None,
            "consumed_input_tokens": account.consumed_input_tokens,
            "consumed_output_tokens": account.consumed_output_tokens,
            "consumed_total_tokens": account.consumed_total_tokens,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "updated_at": account.updated_at.isoformat() if account.updated_at else None,
            "last_used_at": account.last_used_at.isoformat() if account.last_used_at else None,
        }

    async def record_account_consumed_tokens(
        self,
        user_id: int,
        account_id: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        account = await self.repo.get_by_id_and_user_id(account_id, user_id)
        if not account:
            return
        account.consumed_input_tokens = (account.consumed_input_tokens or 0) + input_tokens
        account.consumed_output_tokens = (account.consumed_output_tokens or 0) + output_tokens
        account.consumed_total_tokens = (account.consumed_total_tokens or 0) + total_tokens
        await self.db.flush()
