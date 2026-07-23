import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings
from app.core.crypto import SubscriptionUrlCipher, mask_subscription_url
from app.database.models import (
    ProvisioningOperationStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.exceptions import (
    RemnawaveAPIError,
    RemnawaveAuthenticationError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
    RemnawaveServerError,
)
from app.integrations.remnawave.schemas import CreateUserRequest, RemnawaveUser
from app.services.remnawave import RemnawaveProvisioningService

SQUAD = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_UUID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)


def user_json(
    *,
    username: str = "tg_123_a4f82c",
    telegram_id: int = 123,
    user_uuid: uuid.UUID = USER_UUID,
    url: str = "https://panel.example/api/sub/individual-secret-key",
) -> dict[str, object]:
    return {
        "uuid": str(user_uuid),
        "id": 1,
        "shortUuid": "individual-secret-key",
        "username": username,
        "status": "ACTIVE",
        "trafficLimitBytes": 10 * 1024**3,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": (NOW + timedelta(days=30)).isoformat(),
        "telegramId": telegram_id,
        "hwidDeviceLimit": 2,
        "createdAt": NOW.isoformat(),
        "updatedAt": NOW.isoformat(),
        "subscriptionUrl": url,
        "activeInternalSquads": [{"uuid": str(SQUAD), "name": "main"}],
        "userTraffic": {
            "usedTrafficBytes": 42,
            "lifetimeUsedTrafficBytes": 84,
            "onlineAt": None,
            "firstConnectedAt": None,
            "lastConnectedNodeUuid": None,
        },
    }


def create_request(username: str = "tg_123_a4f82c") -> CreateUserRequest:
    return CreateUserRequest(
        username=username,
        trafficLimitBytes=10,
        expireAt=NOW + timedelta(days=30),
        telegramId=123,
        hwidDeviceLimit=2,
        activeInternalSquads=[SQUAD],
    )


@pytest.mark.asyncio
async def test_client_creates_user_with_documented_endpoint_and_bearer() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(201, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example", "secret-token", transport=httpx.MockTransport(handler)
    )
    remote = await client.create_user(create_request())
    await client.aclose()
    assert seen == {"path": "/api/users", "auth": "Bearer secret-token"}
    assert remote.subscription_url.endswith("individual-secret-key")


@pytest.mark.asyncio
async def test_client_uses_documented_get_by_username_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/users/by-username/tg_123_a4f82c"
        return httpx.Response(200, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example", "x", transport=httpx.MockTransport(handler)
    )
    assert (await client.get_user_by_username("tg_123_a4f82c")).uuid == USER_UUID
    await client.aclose()


@pytest.mark.asyncio
async def test_create_post_is_not_retried_after_network_error() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    client = RemnawaveClient(
        "https://panel.example",
        "x",
        max_retries=3,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RemnawaveNetworkError):
        await client.create_user(create_request())
    await client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_get_retries_5xx() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example",
        "x",
        max_retries=2,
        retry_base_delay=0,
        transport=httpx.MockTransport(handler),
    )
    await client.get_user(USER_UUID)
    await client.aclose()
    assert calls == 3


@pytest.mark.asyncio
async def test_invalid_token_is_typed_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(401))
    client = RemnawaveClient("https://panel.example", "bad", transport=transport)
    with pytest.raises(RemnawaveAuthenticationError):
        await client.get_user(USER_UUID)
    await client.aclose()


def test_subscription_url_encryption_round_trip() -> None:
    cipher = SubscriptionUrlCipher(Fernet.generate_key().decode())
    url = "https://panel.example/api/sub/private-key"
    encrypted = cipher.encrypt(url)
    assert url.encode() not in encrypted
    assert cipher.decrypt(encrypted) == url


def test_subscription_url_mask_hides_key() -> None:
    masked = mask_subscription_url("https://panel.example/api/sub/abcdef123456xyz")
    assert masked == "https://panel.example/api/sub/abc...xyz"
    assert "abcdef123456xyz" not in masked


def make_local() -> tuple[User, Subscription]:
    user = User(id=1, telegram_id=123)
    subscription = Subscription(
        id=uuid.uuid4(),
        user_id=1,
        source_type=SubscriptionSource.trial,
        status=SubscriptionStatus.pending,
        provisioning_status=ProvisioningStatus.pending,
        started_at=NOW,
        expires_at=NOW + timedelta(days=7),
        traffic_limit_gb=10,
        is_unlimited_traffic=False,
        device_limit=2,
        activation_attempts=0,
    )
    return user, subscription


def fake_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    return session


def remote_model(**kwargs: object) -> RemnawaveUser:
    data = user_json(**kwargs)
    return RemnawaveUser.model_validate(data)


@pytest.mark.asyncio
async def test_provisioning_creates_individual_remote_user() -> None:
    user, sub = make_local()
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        side_effect=RemnawaveNotFoundError("missing")
    )
    client.create_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(username=request.username)
    )
    client.update_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(
            username=sub.remnawave_username
        )
    )
    client.get_user = AsyncMock(
        side_effect=lambda *_, **__: remote_model(username=sub.remnawave_username)
    )
    service = RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    )
    result = await service.provision(sub, user)
    assert result.status == SubscriptionStatus.active
    assert sub.remnawave_user_uuid == str(USER_UUID)
    assert sub.remnawave_username.startswith("tg_123_")
    client.create_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_provisioning_assigns_internal_squad() -> None:
    user, sub = make_local()
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        side_effect=RemnawaveNotFoundError("missing")
    )
    client.create_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(username=request.username)
    )
    client.update_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(
            username=sub.remnawave_username
        )
    )
    client.get_user = AsyncMock(
        side_effect=lambda *_, **__: remote_model(username=sub.remnawave_username)
    )
    await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    create = client.create_user.await_args.args[0]
    squad_update = client.update_user.await_args_list[0].args[0]
    assert create.active_internal_squads is None
    assert squad_update.active_internal_squads == [SQUAD]


@pytest.mark.asyncio
async def test_existing_remote_user_is_updated_not_created() -> None:
    user, sub = make_local()
    sub.remnawave_username = "tg_123_saved"
    sub.remnawave_user_uuid = str(USER_UUID)
    remote = remote_model(username=sub.remnawave_username)
    client = MagicMock()
    client.get_user = AsyncMock(return_value=remote)
    client.update_user = AsyncMock(return_value=remote)
    client.create_user = AsyncMock()
    await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    client.create_user.assert_not_awaited()
    assert client.update_user.await_count == 4


@pytest.mark.asyncio
async def test_renewal_preserves_subscription_url() -> None:
    user, sub = make_local()
    sub.remnawave_username = "tg_123_saved"
    sub.remnawave_user_uuid = str(USER_UUID)
    url = "https://panel.example/api/sub/stable-key"
    remote = remote_model(username=sub.remnawave_username, url=url)
    client = MagicMock(
        get_user=AsyncMock(return_value=remote),
        update_user=AsyncMock(return_value=remote),
    )
    cipher = SubscriptionUrlCipher(Fernet.generate_key().decode())
    await RemnawaveProvisioningService(
        fake_session(), client, cipher, str(SQUAD)
    ).provision(sub, user)
    assert cipher.decrypt(sub.subscription_url_encrypted) == url


@pytest.mark.asyncio
async def test_username_conflict_does_not_link_foreign_user() -> None:
    user, sub = make_local()
    sub.remnawave_username = "tg_123_saved"
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        return_value=remote_model(username=sub.remnawave_username, telegram_id=999)
    )
    result = await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    assert result.status == SubscriptionStatus.activation_failed
    assert sub.remnawave_user_uuid is None


@pytest.mark.asyncio
async def test_api_error_is_saved_for_retry() -> None:
    user, sub = make_local()
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        side_effect=RemnawaveNotFoundError("missing")
    )
    client.create_user = AsyncMock(
        side_effect=RemnawaveAuthenticationError("unauthorized")
    )
    await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    assert sub.provisioning_status == ProvisioningStatus.failed
    assert sub.next_retry_at is not None
    assert "unauthorized" in (sub.last_activation_error or "")


def test_full_subscription_url_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    url = "https://panel.example/api/sub/never-log-this-secret"
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.integrations.remnawave").info(
            "subscription=%s", mask_subscription_url(url)
        )
    assert "never-log-this-secret" not in caplog.text


def test_provisioning_operation_has_completed_status() -> None:
    assert ProvisioningOperationStatus.completed.value == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "suffix"),
    [
        ("enable_user", "enable"),
        ("disable_user", "disable"),
        ("reset_traffic", "reset-traffic"),
    ],
)
async def test_documented_user_action_endpoints(method_name: str, suffix: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/api/users/{USER_UUID}/actions/{suffix}"
        return httpx.Response(200, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example", "x", transport=httpx.MockTransport(handler)
    )
    await getattr(client, method_name)(USER_UUID)
    await client.aclose()


@pytest.mark.asyncio
async def test_two_telegram_users_receive_different_remote_values() -> None:
    first = remote_model()
    second = remote_model(
        username="tg_456_123abc",
        telegram_id=456,
        user_uuid=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        url="https://panel.example/api/sub/another-individual-key",
    )
    assert first.uuid != second.uuid
    assert first.subscription_url != second.subscription_url


@pytest.mark.asyncio
async def test_update_uses_patch_without_changing_username() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/api/users"
        assert b"username" not in request.content
        return httpx.Response(200, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example", "x", transport=httpx.MockTransport(handler)
    )
    await client.set_expiration(USER_UUID, NOW + timedelta(days=60))
    await client.aclose()


def test_compose_uses_only_working_env_file() -> None:
    root = Path(__file__).parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert ".env.example" not in compose
    assert compose.count("      - .env") == 3
    assert all(
        not line or line.startswith("#") or line.endswith("=")
        for line in example.splitlines()
    )


def test_remnawave_settings_are_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("POSTGRES_PASSWORD", "db-password")
    monkeypatch.setenv("REMNAWAVE_BASE_URL", "https://panel.example/")
    monkeypatch.setenv("REMNAWAVE_API_TOKEN", "api-token")
    monkeypatch.setenv("REMNAWAVE_INTERNAL_SQUAD_UUID", str(SQUAD))
    monkeypatch.setenv("REMNAWAVE_VERIFY_SSL", "false")
    monkeypatch.setenv("SUBSCRIPTION_ENCRYPTION_KEY", key)
    settings = Settings(_env_file=None)
    assert settings.remnawave_base_url == "https://panel.example"
    assert settings.remnawave_api_token == "api-token"
    assert settings.remnawave_internal_squad_uuid == str(SQUAD)
    assert settings.remnawave_verify_ssl is False
    assert settings.subscription_encryption_key == key
    assert settings.remnawave_missing_settings == []


def test_invalid_internal_squad_uuid_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="bot-token",
            postgres_password="db-password",
            remnawave_api_token="api-token",
            remnawave_internal_squad_uuid="not-a-uuid",
            _env_file=None,
        )


def test_invalid_fernet_key_is_rejected_by_cipher() -> None:
    with pytest.raises(ValueError):
        SubscriptionUrlCipher("not-a-fernet-key")


@pytest.mark.asyncio
async def test_minimal_create_payload_has_only_documented_required_fields() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(__import__("json").loads(request.content))
        assert request.headers["Content-Type"] == "application/json"
        return httpx.Response(201, json={"response": user_json()})

    client = RemnawaveClient(
        "https://panel.example", "x", transport=httpx.MockTransport(handler)
    )
    await client.create_user(
        CreateUserRequest(
            username="tg_123_stable",
            status="ACTIVE",
            trafficLimitStrategy="NO_RESET",
            expireAt=NOW + timedelta(days=7),
        )
    )
    await client.aclose()
    assert set(seen) == {
        "username",
        "status",
        "trafficLimitStrategy",
        "expireAt",
    }
    assert str(seen["expireAt"]).endswith("Z")


@pytest.mark.asyncio
async def test_http_500_exposes_only_sanitized_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "top-secret-api-token"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            headers={"x-request-id": "request-123"},
            json={
                "message": f"backend failed for {token}",
                "subscriptionUrl": "https://panel.example/api/sub/private-key",
            },
        )

    client = RemnawaveClient(
        "https://panel.example", token, transport=httpx.MockTransport(handler)
    )
    with caplog.at_level(logging.ERROR), pytest.raises(
        RemnawaveServerError
    ) as caught:
        await client.create_user(create_request(), local_user_id=7)
    await client.aclose()
    error = caught.value
    assert error.status_code == 500
    assert error.operation == "create_user"
    assert error.retryable is True
    assert token not in (error.safe_response_body or "")
    assert "private-key" not in (error.safe_response_body or "")
    assert token not in caplog.text
    assert "request-123" in caplog.text
    assert "local_user_id=7" in caplog.text


@pytest.mark.asyncio
async def test_uncertain_create_result_is_resolved_without_duplicate() -> None:
    user, sub = make_local()
    remote = remote_model(username="placeholder")
    client = MagicMock()

    async def lookup(*_: object, **__: object) -> RemnawaveUser:
        if client.create_user.await_count == 0:
            raise RemnawaveNotFoundError("missing")
        return remote_model(username=sub.remnawave_username)

    client.get_user_by_username = AsyncMock(side_effect=lookup)
    client.create_user = AsyncMock(
        side_effect=RemnawaveServerError(
            "HTTP 500",
            status_code=500,
            safe_response_body="internal error",
            operation="create_user",
            retryable=True,
        )
    )
    client.update_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(
            username=sub.remnawave_username
        )
    )
    client.get_user = AsyncMock(
        side_effect=lambda *_, **__: remote_model(username=sub.remnawave_username)
    )
    result = await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    assert result.status == SubscriptionStatus.active
    client.create_user.assert_awaited_once()
    assert remote.uuid == USER_UUID


@pytest.mark.asyncio
async def test_squad_failure_keeps_created_uuid_for_retry() -> None:
    user, sub = make_local()
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        side_effect=RemnawaveNotFoundError("missing")
    )
    client.create_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(username=request.username)
    )
    client.update_user = AsyncMock(
        side_effect=RemnawaveServerError(
            "squad failed",
            status_code=500,
            safe_response_body="internal error",
            operation="assign_internal_squad",
            retryable=True,
        )
    )
    result = await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    assert result.status == SubscriptionStatus.activation_failed
    assert sub.remnawave_user_uuid == str(USER_UUID)
    assert sub.provisioning_status == ProvisioningStatus.failed
    client.create_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_trial_limits_are_sent_as_utc_bytes_and_integer_devices() -> None:
    user, sub = make_local()
    client = MagicMock()
    client.get_user_by_username = AsyncMock(
        side_effect=RemnawaveNotFoundError("missing")
    )
    client.create_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(username=request.username)
    )
    client.update_user = AsyncMock(
        side_effect=lambda request, **_: remote_model(
            username=sub.remnawave_username
        )
    )
    client.get_user = AsyncMock(
        side_effect=lambda *_, **__: remote_model(username=sub.remnawave_username)
    )
    await RemnawaveProvisioningService(
        fake_session(),
        client,
        SubscriptionUrlCipher(Fernet.generate_key().decode()),
        str(SQUAD),
    ).provision(sub, user)
    create = client.create_user.await_args.args[0]
    device = client.update_user.await_args_list[1].args[0]
    traffic = client.update_user.await_args_list[2].args[0]
    assert create.expire_at == sub.expires_at
    assert create.expire_at.tzinfo is UTC
    assert device.hwid_device_limit == 2
    assert isinstance(device.hwid_device_limit, int)
    assert traffic.traffic_limit_bytes == 10 * 1024**3


def test_naive_expiration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(
            username="tg_123_stable",
            expireAt=datetime(2026, 7, 29, 12),
        )


def test_api_error_contract_contains_safe_diagnostics() -> None:
    error = RemnawaveAPIError(
        "failed",
        status_code=503,
        safe_response_body="unavailable",
        operation="create_user",
        retryable=True,
    )
    assert (error.status_code, error.safe_response_body) == (503, "unavailable")
    assert error.operation == "create_user"
    assert error.retryable is True
