from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.callbacks import AdminCallback
from app.bot.handlers import admin
from app.bot.handlers.admin import parse_tariff_price, update_tariff_price
from app.bot.handlers.admin_promos import (
    cancel_promo_form,
    decimal_text,
    promo_value_step,
)
from app.bot.keyboards.admin import (
    admin_menu,
    admin_price_navigation,
    admin_sales_menu,
    admin_tariffs,
    promo_confirm_keyboard,
    promo_list_menu,
    promo_scope_keyboard,
    promo_tariff_selection,
    promo_type_keyboard,
    remnawave_admin_menu,
)
from app.database.models import Tariff


def make_tariff(
    *,
    tariff_id: int = 7,
    name: str = "30 дней",
    price: Decimal = Decimal("199.00"),
    duration_days: int = 30,
    is_active: bool = True,
) -> Tariff:
    return Tariff(
        id=tariff_id,
        name=name,
        description="Основной тариф",
        duration_days=duration_days,
        price=price,
        currency="RUB",
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=5,
        is_active=is_active,
        sort_order=10,
    )


def buttons(markup: object) -> list[object]:
    return [
        button
        for row in markup.inline_keyboard  # type: ignore[attr-defined]
        for button in row
    ]


def admin_action(button: object) -> AdminCallback:
    callback_data = button.callback_data  # type: ignore[attr-defined]
    assert callback_data is not None
    return AdminCallback.unpack(callback_data)


def async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def transactional_factory(session: MagicMock) -> MagicMock:
    session.begin.return_value = async_context(None)
    return MagicMock(return_value=async_context(session))


def state_with(data: dict[str, object]) -> MagicMock:
    state = MagicMock()
    state.get_data = AsyncMock(return_value=data)
    state.clear = AsyncMock()
    return state


def message_with(text: str) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user = SimpleNamespace(id=42)
    message.answer = AsyncMock()
    return message


def navigation_labels(markup: object) -> set[str]:
    return {
        button.text  # type: ignore[attr-defined]
        for button in buttons(markup)
        if "Назад" in button.text or "Главное меню" in button.text  # type: ignore[attr-defined]
    }


def test_admin_menu_is_compact_and_has_no_placeholder_sections() -> None:
    markup = admin_menu()
    menu_buttons = buttons(markup)
    actions = {admin_action(button).action for button in menu_buttons}

    assert len(markup.inline_keyboard) == 3
    assert len(menu_buttons) == 6
    assert actions == {
        "tariffs",
        "broadcast",
        "users_section",
        "promos",
        "remnawave",
        "close",
    }
    assert "stats_v3" not in actions
    assert "sales" not in actions
    assert "settings" not in actions


def test_sales_functions_remain_grouped_outside_the_main_menu() -> None:
    actions = {
        admin_action(button).action
        for button in buttons(admin_sales_menu())
        if button.callback_data is not None  # type: ignore[attr-defined]
    }

    assert {"orders", "payments", "promos", "tariffs", "menu"} == actions


def test_tariff_keyboard_shows_price_and_opens_direct_price_action() -> None:
    item = make_tariff(price=Decimal("349.50"))
    markup = admin_tariffs([item])
    tariff_button = markup.inline_keyboard[0][0]
    callback = admin_action(tariff_button)
    actions = {
        admin_action(button).action
        for button in buttons(markup)
        if button.callback_data is not None  # type: ignore[attr-defined]
    }

    assert item.name in tariff_button.text
    assert "349.5 ₽" in tariff_button.text
    assert callback.action == "price"
    assert callback.tariff_id == item.id
    assert {"create", "tariff_management", "sales", "menu"} <= actions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("299.50", Decimal("299.50")),
        ("299,50", Decimal("299.50")),
    ],
)
def test_parse_tariff_price_accepts_dot_and_comma(
    raw: str, expected: Decimal
) -> None:
    assert parse_tariff_price(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "-1",
        "1.001",
        "NaN",
        "Infinity",
        "100000000",
    ],
)
def test_parse_tariff_price_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid_price"):
        parse_tariff_price(raw)


@pytest.mark.asyncio
async def test_update_tariff_price_updates_only_price_audits_and_returns_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = make_tariff(price=Decimal("199.00"))
    other = make_tariff(
        tariff_id=8,
        name="90 дней",
        price=Decimal("499.00"),
        duration_days=90,
    )
    unchanged = {
        "name": item.name,
        "description": item.description,
        "duration_days": item.duration_days,
        "currency": item.currency,
        "traffic_limit_gb": item.traffic_limit_gb,
        "is_unlimited_traffic": item.is_unlimited_traffic,
        "device_limit": item.device_limit,
        "is_active": item.is_active,
        "sort_order": item.sort_order,
    }

    async def apply_update(entity: Tariff, **values: object) -> Tariff:
        for key, value in values.items():
            setattr(entity, key, value)
        return entity

    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=item)
    repository.update = AsyncMock(side_effect=apply_update)
    repository.get_all = AsyncMock(return_value=[item, other])
    monkeypatch.setattr(
        admin, "TariffRepository", MagicMock(return_value=repository)
    )

    actor = SimpleNamespace(id=99)
    user_repository = MagicMock()
    user_repository.get_by_telegram_id = AsyncMock(return_value=actor)
    monkeypatch.setattr(
        admin, "UserRepository", MagicMock(return_value=user_repository)
    )
    audit = MagicMock()
    monkeypatch.setattr(admin, "add_audit_log", audit)

    session = MagicMock()
    factory = transactional_factory(session)
    state = state_with({"tariff_id": item.id})
    message = message_with("249,50")

    await update_tariff_price(message, state, factory)

    assert item.price == Decimal("249.50")
    assert {
        key: getattr(item, key)
        for key in unchanged
    } == unchanged
    repository.update.assert_awaited_once_with(
        item,
        price=Decimal("249.50"),
    )
    repository.get_all.assert_awaited_once_with()
    user_repository.get_by_telegram_id.assert_awaited_once_with(42)

    audit.assert_called_once()
    assert audit.call_args.args == (session,)
    audit_call = audit.call_args.kwargs
    assert audit_call["action"] == "admin_tariff_price_changed"
    assert audit_call["entity_type"] == "tariff"
    assert audit_call["entity_id"] == item.id
    assert audit_call["actor_user_id"] == actor.id
    assert audit_call["actor_telegram_id"] == 42
    assert audit_call["details"] == {
        "old_price": "199.00",
        "new_price": "249.50",
        "currency": "RUB",
    }

    state.clear.assert_awaited_once_with()
    message.answer.assert_awaited_once()
    answer = message.answer.await_args
    assert "Цена изменена" in answer.args[0]
    returned_markup = answer.kwargs["reply_markup"]
    returned_tariff_callbacks = [
        admin_action(button)
        for button in buttons(returned_markup)
        if button.callback_data is not None  # type: ignore[attr-defined]
        and admin_action(button).action == "price"
    ]
    assert [
        callback.tariff_id for callback in returned_tariff_callbacks
    ] == [item.id, other.id]


@pytest.mark.asyncio
async def test_update_tariff_price_handles_stale_state_without_database_access(
) -> None:
    state = state_with({})
    message = message_with("249.50")
    factory = MagicMock()

    await update_tariff_price(message, state, factory)

    factory.assert_not_called()
    state.clear.assert_awaited_once_with()
    message.answer.assert_awaited_once()
    answer = message.answer.await_args
    assert "Форма устарела" in answer.args[0]
    assert navigation_labels(answer.kwargs["reply_markup"]) == {
        "⬅️ Назад",
        "🏠 Главное меню",
    }


@pytest.mark.asyncio
async def test_update_tariff_price_handles_missing_tariff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=None)
    repository.update = AsyncMock()
    repository.get_all = AsyncMock()
    monkeypatch.setattr(
        admin, "TariffRepository", MagicMock(return_value=repository)
    )
    audit = MagicMock()
    monkeypatch.setattr(admin, "add_audit_log", audit)

    session = MagicMock()
    factory = transactional_factory(session)
    state = state_with({"tariff_id": 404})
    message = message_with("249.50")

    await update_tariff_price(message, state, factory)

    repository.get_by_id.assert_awaited_once_with(404)
    repository.update.assert_not_awaited()
    repository.get_all.assert_not_awaited()
    audit.assert_not_called()
    state.clear.assert_awaited_once_with()
    message.answer.assert_awaited_once()
    answer = message.answer.await_args
    assert "Тариф больше не найден" in answer.args[0]
    assert navigation_labels(answer.kwargs["reply_markup"]) == {
        "⬅️ Назад",
        "🏠 Главное меню",
    }


@pytest.mark.asyncio
async def test_full_tariff_form_converts_fsm_price_to_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data: dict[str, object] = {
        "tariff_id": None,
        "name": "Новый тариф",
        "description": None,
        "duration_days": 30,
        "price": "149.50",
        "currency": "RUB",
        "traffic_limit_gb": 600,
        "is_unlimited_traffic": False,
        "device_limit": 5,
        "sort_order": 10,
        "is_active": True,
    }

    async def create_tariff(**values: object) -> Tariff:
        return Tariff(id=9, **values)  # type: ignore[arg-type]

    repository = MagicMock()
    repository.create = AsyncMock(side_effect=create_tariff)
    monkeypatch.setattr(
        admin, "TariffRepository", MagicMock(return_value=repository)
    )
    user_repository = MagicMock()
    user_repository.get_by_telegram_id = AsyncMock(return_value=None)
    monkeypatch.setattr(
        admin, "UserRepository", MagicMock(return_value=user_repository)
    )
    monkeypatch.setattr(admin, "add_audit_log", MagicMock())

    callback = MagicMock()
    callback.from_user = SimpleNamespace(id=42)
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    state = state_with(data)

    await admin.admin_actions(
        callback,
        AdminCallback(action="save"),
        transactional_factory(MagicMock()),
        state,
    )

    assert repository.create.await_args.kwargs["price"] == Decimal("149.50")
    assert isinstance(repository.create.await_args.kwargs["price"], Decimal)
    callback.message.edit_text.assert_awaited_once()
    assert "149.5 ₽" in callback.message.edit_text.await_args.args[0]


def test_tariff_price_navigation_has_back_and_home() -> None:
    assert navigation_labels(admin_price_navigation()) == {
        "⬅️ Назад",
        "🏠 Главное меню",
    }
    assert navigation_labels(admin_tariffs([make_tariff()])) == {
        "🏠 Главное меню"
    }


def test_decimal_text_preserves_integer_zeroes() -> None:
    assert decimal_text(Decimal("100")) == "100"
    assert decimal_text(Decimal("500.00")) == "500"
    assert decimal_text(Decimal("12.50")) == "12.5"


@pytest.mark.parametrize("raw", ["0.5", "100000000"])
@pytest.mark.asyncio
async def test_promo_value_rejects_out_of_range_values(raw: str) -> None:
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"discount_type": "percent"})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    message = message_with(raw)

    await promo_value_step(message, state)

    state.update_data.assert_not_awaited()
    state.set_state.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_promo_cancel_does_not_clear_another_form() -> None:
    state = MagicMock()
    state.get_state = AsyncMock(return_value="TariffPriceForm:price")
    state.clear = AsyncMock()
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = None

    await cancel_promo_form(callback, state)

    state.clear.assert_not_awaited()
    assert "устарела" in callback.answer.await_args.args[0]


def test_special_admin_screens_keep_back_and_home_navigation() -> None:
    tariff = make_tariff()
    nested_markups = [
        promo_type_keyboard(),
        promo_scope_keyboard(),
        promo_tariff_selection([tariff], set()),
        promo_confirm_keyboard(),
    ]

    for markup in nested_markups:
        assert navigation_labels(markup) == {
            "⬅️ Назад",
            "🏠 Главное меню",
        }

    first_level_markups = [
        promo_list_menu(1, 3),
        remnawave_admin_menu(),
    ]
    for markup in first_level_markups:
        assert navigation_labels(markup) == {"🏠 Главное меню"}

    pagination_labels = {button.text for button in buttons(first_level_markups[0])}
    assert {"⬅️ Новее", "Старее ➡️"} <= pagination_labels
