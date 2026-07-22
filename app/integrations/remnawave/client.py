import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.integrations.remnawave.exceptions import (
    RemnawaveAPIError,
    RemnawaveAuthenticationError,
    RemnawaveConflictError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
    RemnawavePermissionError,
    RemnawaveRateLimitError,
    RemnawaveServerError,
    RemnawaveValidationError,
)
from app.integrations.remnawave.schemas import (
    CreateUserRequest,
    DeleteResponse,
    InternalSquad,
    InternalSquadResponse,
    RemnawaveUser,
    UpdateUserRequest,
    UserResponse,
    UsersPageResponse,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class RemnawaveClient:
    """Async client for the documented Remnawave Backend 2.x user API."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: float = 15,
        verify_ssl: bool = True,
        max_retries: int = 3,
        retry_base_delay: float = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_token = api_token
        self.max_retries = max(0, max_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
            verify=verify_ssl,
            transport=transport,
        )

    async def __aenter__(self) -> "RemnawaveClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def healthcheck(self) -> bool:
        response = await self._request(
            "GET", "/api/system/health", operation="healthcheck"
        )
        return response.status_code == 200

    async def check_api(self) -> bool:
        await self._request_model(
            UsersPageResponse, "GET", "/api/users", params={"start": 0, "size": 1}
        )
        return True

    async def create_user(
        self,
        request: CreateUserRequest,
        *,
        local_user_id: int | None = None,
    ) -> RemnawaveUser:
        # POST is deliberately never retried. The caller resolves an uncertain outcome
        # via the unique stable username before attempting another create.
        wrapped = await self._request_model(
            UserResponse,
            "POST",
            "/api/users",
            json=request.model_dump(by_alias=True, exclude_none=True, mode="json"),
            retry=False,
            operation="create_user",
            local_user_id=local_user_id,
            remnawave_username=request.username,
        )
        return wrapped.response

    async def get_user(
        self,
        user_uuid: UUID | str,
        *,
        operation: str = "get_user",
        local_user_id: int | None = None,
        remnawave_username: str | None = None,
    ) -> RemnawaveUser:
        wrapped = await self._request_model(
            UserResponse,
            "GET",
            f"/api/users/{quote(str(user_uuid), safe='')}",
            operation=operation,
            local_user_id=local_user_id,
            remnawave_username=remnawave_username,
        )
        return wrapped.response

    async def get_user_by_username(
        self, username: str, *, local_user_id: int | None = None
    ) -> RemnawaveUser:
        wrapped = await self._request_model(
            UserResponse,
            "GET",
            f"/api/users/by-username/{quote(username, safe='')}",
            operation="get_user_by_username",
            local_user_id=local_user_id,
            remnawave_username=username,
        )
        return wrapped.response

    async def get_internal_squad(self, squad_uuid: UUID | str) -> InternalSquad:
        wrapped = await self._request_model(
            InternalSquadResponse,
            "GET",
            f"/api/internal-squads/{quote(str(squad_uuid), safe='')}",
            operation="get_internal_squad",
        )
        return wrapped.response

    async def update_user(
        self,
        request: UpdateUserRequest,
        *,
        operation: str = "update_user",
        local_user_id: int | None = None,
        remnawave_username: str | None = None,
    ) -> RemnawaveUser:
        wrapped = await self._request_model(
            UserResponse,
            "PATCH",
            "/api/users",
            json=request.model_dump(by_alias=True, exclude_none=True, mode="json"),
            operation=operation,
            local_user_id=local_user_id,
            remnawave_username=remnawave_username,
        )
        return wrapped.response

    async def set_expiration(
        self, user_uuid: UUID | str, expire_at: datetime
    ) -> RemnawaveUser:
        return await self.update_user(
            UpdateUserRequest(uuid=UUID(str(user_uuid)), expireAt=expire_at)
        )

    async def set_traffic_limit(
        self, user_uuid: UUID | str, limit_bytes: int
    ) -> RemnawaveUser:
        return await self.update_user(
            UpdateUserRequest(uuid=UUID(str(user_uuid)), trafficLimitBytes=limit_bytes)
        )

    async def set_device_limit(
        self, user_uuid: UUID | str, limit: int | None
    ) -> RemnawaveUser:
        return await self.update_user(
            UpdateUserRequest(uuid=UUID(str(user_uuid)), hwidDeviceLimit=limit)
        )

    async def enable_user(self, user_uuid: UUID | str) -> RemnawaveUser:
        return await self._action(user_uuid, "enable")

    async def disable_user(self, user_uuid: UUID | str) -> RemnawaveUser:
        return await self._action(user_uuid, "disable")

    async def reset_traffic(self, user_uuid: UUID | str) -> RemnawaveUser:
        return await self._action(user_uuid, "reset-traffic")

    async def _action(self, user_uuid: UUID | str, action: str) -> RemnawaveUser:
        wrapped = await self._request_model(
            UserResponse,
            "POST",
            f"/api/users/{quote(str(user_uuid), safe='')}/actions/{action}",
        )
        return wrapped.response

    async def delete_user(self, user_uuid: UUID | str) -> bool:
        wrapped = await self._request_model(
            DeleteResponse,
            "DELETE",
            f"/api/users/{quote(str(user_uuid), safe='')}",
            retry=False,
        )
        return wrapped.response.is_deleted

    async def _request_model(
        self,
        model: type[T],
        method: str,
        path: str,
        *,
        retry: bool = True,
        **kwargs: object,
    ) -> T:
        response = await self._request(method, path, retry=retry, **kwargs)
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise RemnawaveValidationError(
                "Remnawave returned an invalid response schema"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool = True,
        operation: str = "api_request",
        local_user_id: int | None = None,
        remnawave_username: str | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        attempts = self.max_retries + 1 if retry else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt + 1 >= attempts:
                    raise RemnawaveNetworkError(
                        "Remnawave network request failed", operation=operation
                    ) from exc
                await asyncio.sleep(self.retry_base_delay * (2**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self._retry_delay(response.headers, attempt))
                    continue
            self._raise_for_status(
                response,
                operation=operation,
                local_user_id=local_user_id,
                remnawave_username=remnawave_username,
            )
            return response
        raise RemnawaveNetworkError("Remnawave request attempts exhausted")

    def _retry_delay(self, headers: Mapping[str, str], attempt: int) -> float:
        value = headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError):
                    pass
        return self.retry_base_delay * (2**attempt)

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        operation: str,
        local_user_id: int | None,
        remnawave_username: str | None,
    ) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        message = f"Remnawave API returned HTTP {status}"
        error_type: type[RemnawaveAPIError]
        if status == 401:
            error_type = RemnawaveAuthenticationError
        elif status == 403:
            error_type = RemnawavePermissionError
        elif status == 404:
            error_type = RemnawaveNotFoundError
        elif status == 409:
            error_type = RemnawaveConflictError
        elif status in {400, 422}:
            error_type = RemnawaveValidationError
        elif status == 429:
            error_type = RemnawaveRateLimitError
        elif status >= 500:
            error_type = RemnawaveServerError
        else:
            error_type = RemnawaveAPIError
        safe_body = self._safe_response_body(response)
        request_id = next(
            (
                response.headers[name]
                for name in ("x-request-id", "x-correlation-id", "cf-ray")
                if name in response.headers
            ),
            None,
        )
        logger.error(
            "Remnawave API error operation=%s local_user_id=%s "
            "remnawave_username=%s method=%s endpoint=%s status=%s "
            "request_id=%s response_body=%s",
            operation,
            local_user_id,
            remnawave_username,
            response.request.method,
            response.request.url.path,
            status,
            request_id,
            safe_body,
        )
        raise error_type(
            message,
            status_code=status,
            safe_response_body=safe_body,
            operation=operation,
            retryable=status == 429 or status >= 500,
        )

    def _safe_response_body(self, response: httpx.Response) -> str:
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        redacted = self._redact(body)
        if isinstance(redacted, str):
            rendered = redacted
        else:
            rendered = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
        if self._api_token:
            rendered = rendered.replace(self._api_token, "[REDACTED]")
        rendered = re.sub(
            r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", rendered
        )
        rendered = re.sub(
            r"(?i)(https?://[^\s\"']+/api/sub/)[^\s\"']+", r"\1***", rendered
        )
        return rendered[:4000]

    @classmethod
    def _redact(cls, value: object, key: str = "") -> object:
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if any(
            marker in normalized
            for marker in (
                "authorization",
                "apitoken",
                "subscriptionurl",
                "subscriptionkey",
                "shortuuid",
                "encryptionkey",
                "fernetkey",
                "vlessuuid",
                "password",
                "secret",
            )
        ):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): cls._redact(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value
