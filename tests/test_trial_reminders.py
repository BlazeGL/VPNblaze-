from datetime import UTC, datetime
from types import SimpleNamespace

from app.bot.keyboards.start import (
    HELP_CENTER_CALLBACK,
    MY_SUBSCRIPTION_CALLBACK,
    START_CONNECTION_CALLBACK,
    TARIFFS_CALLBACK,
)
from app.database.models import (
    ProvisioningStatus,
    SubscriptionSource,
    SubscriptionStatus,
)
from app.workers.trial_reminders import (
    TRIAL_REMINDER_TEXTS,
    TrialReminderKind,
    _still_due,
    is_delivery_time,
    trial_reminder_keyboard,
)


def callbacks(kind: TrialReminderKind) -> list[str]:
    keyboard = trial_reminder_keyboard(kind)
    return [
        button.callback_data or ""
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_reminder_copy_is_friendly_and_has_no_price() -> None:
    combined = " ".join(TRIAL_REMINDER_TEXTS.values())

    assert "99" not in combined
    assert "Получилось подключиться?" in combined
    assert "пробный период скоро закончится" in combined
    assert "вернуться можно в любой момент" in combined


def test_reminder_buttons_open_existing_bot_sections() -> None:
    assert callbacks(TrialReminderKind.connection) == [
        START_CONNECTION_CALLBACK,
        HELP_CENTER_CALLBACK,
    ]
    assert callbacks(TrialReminderKind.ending) == [
        TARIFFS_CALLBACK,
        MY_SUBSCRIPTION_CALLBACK,
    ]
    assert callbacks(TrialReminderKind.expired) == [TARIFFS_CALLBACK]


def test_delivery_window_is_11_to_20_moscow() -> None:
    assert is_delivery_time(datetime(2026, 9, 19, 8, 0, tzinfo=UTC))
    assert is_delivery_time(datetime(2026, 9, 19, 16, 59, tzinfo=UTC))
    assert not is_delivery_time(datetime(2026, 9, 19, 7, 59, tzinfo=UTC))
    assert not is_delivery_time(datetime(2026, 9, 19, 17, 0, tzinfo=UTC))


def test_paid_subscription_never_receives_trial_reminder() -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    subscription = SimpleNamespace(
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        expires_at=datetime(2026, 9, 20, tzinfo=UTC),
        remnawave_user_uuid="00000000-0000-4000-8000-000000000001",
        trial_connection_notice_at=None,
        expiry_notice_3d_at=None,
        expired_notice_at=None,
    )

    assert not _still_due(subscription, TrialReminderKind.ending, now)
