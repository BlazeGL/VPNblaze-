import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "card",
    "card_number",
    "cvv",
    "secret",
    "signature",
    "token",
    "webhook_secret",
}


def sanitize_details(details: dict[str, Any] | None) -> dict[str, object]:
    if not details:
        return {}
    clean: dict[str, object] = {}
    for key, value in details.items():
        normalized_key = key.lower()
        if any(secret in normalized_key for secret in SENSITIVE_KEYS):
            continue
        if isinstance(value, str):
            clean[key] = value[:2000]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, uuid.UUID):
            clean[key] = str(value)
        elif isinstance(value, dict):
            clean[key] = sanitize_details(value)
        elif isinstance(value, (list, tuple)):
            clean[key] = [
                sanitize_details({"value": item}).get("value")
                for item in value[:100]
            ]
        else:
            clean[key] = str(value)[:2000]
    return clean


def add_audit_log(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: object | None = None,
    actor_user_id: int | None = None,
    actor_telegram_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_telegram_id=actor_telegram_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details_sanitized=sanitize_details(details),
    )
    session.add(entry)
    return entry
