from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.apps import (
    render_key,
    show_devices,
    show_support_from_main,
)
from app.bot.handlers.bonuses import show_bonuses
from app.bot.handlers.promos import enter_promo_from_menu
from app.bot.handlers.start import show_user_agreement
from app.bot.handlers.tariffs import show_tariffs
from app.bot.handlers.trial import show_subscription
from app.bot.keyboards.start import agreement_button
from app.bot.rendering import edit_text_or_caption
from app.core.config import Settings
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import BalanceTransaction, BalanceTransactionType
from app.database.repositories import UserRepository
from app.integrations.remnawave.client import RemnawaveClient
from app.services.balance import BalanceService

router = Router(name=__name__)
unknown_router = Router(name=f"{__name__}.unknown")

PRIVATE_COMMANDS = {
    "key",
    "profile",
    "balance",
    "topup",
    "promo",
    "ref",
}


class _CommandMessageProxy:
    """Make callback renderers answer a command instead of editing user input."""

    def __init__(self, message: Message) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def edit_text(self, text: str, **kwargs: Any) -> Message:
        return await self._message.answer(text, **kwargs)

    async def edit_caption(self, caption: str, **kwargs: Any) -> Message:
        return await self._message.answer(caption, **kwargs)


class CommandCallbackAdapter:
    """A narrow callback-compatible adapter used to reuse existing handlers."""

    def __init__(self, message: Message, data: str) -> None:
        self._source_message = message
        self.message = _CommandMessageProxy(message)
        self.from_user = message.from_user
        self.bot = message.bot
        self.data = data

    async def answer(
        self,
        text: str | None = None,
        *,
        show_alert: bool = False,
        **kwargs: Any,
    ) -> None:
        del show_alert
        if text:
            await self._source_message.answer(text, **kwargs)


async def _private_only(message: Message) -> bool:
    if message.chat.type == ChatType.PRIVATE:
        return True
    identity = await message.bot.get_me()
    rows: list[list[InlineKeyboardButton]] = []
    if identity.username:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Открыть бота",
                    url=f"https://t.me/{identity.username}",
                )
            ]
        )
    await message.answer(
        "🔒 Для безопасности откройте BlazeVPN в личных сообщениях.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    return False


def _adapter(message: Message, data: str) -> CallbackQuery:
    return CommandCallbackAdapter(message, data)  # type: ignore[return-value]


@router.message(Command(commands=["key", "ключ"], ignore_case=True))
async def key_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if not await _private_only(message):
        return
    await render_key(
        _adapter(message, "key_refresh"),
        session_factory,
        subscription_cipher,
    )


@router.message(Command(commands=["profile", "профиль"], ignore_case=True))
async def profile_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if not await _private_only(message):
        return
    await show_subscription(
        _adapter(message, "my_subscription"),
        session_factory,
        remnawave_client,
        subscription_cipher,
    )


@router.message(Command(commands=["plans", "тарифы"], ignore_case=True))
async def plans_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await show_tariffs(_adapter(message, "tariffs"), session_factory)


@router.message(Command("buy", ignore_case=True))
async def buy_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await show_tariffs(_adapter(message, "buy_vpn"), session_factory)


@router.message(Command("topup", ignore_case=True))
async def topup_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _private_only(message):
        return
    # The current YooKassa flow starts from a tariff/order and credits the
    # confirmed amount to the balance on the server-side webhook.
    await show_tariffs(_adapter(message, "buy_vpn"), session_factory)


@router.message(Command("promo", ignore_case=True))
async def promo_command(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _private_only(message):
        return
    await enter_promo_from_menu(
        _adapter(message, "promo_enter"),
        state,
        session_factory,
    )


@router.message(Command("ref", ignore_case=True))
async def referral_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _private_only(message):
        return
    await show_bonuses(_adapter(message, "bonuses"), session_factory)


@router.message(Command("apps", ignore_case=True))
async def apps_command(message: Message) -> None:
    await show_devices(_adapter(message, "apps_from_main"))


HELP_TEXT = (
    "❓ <b>Помощь BlazeVPN</b>\n\n"
    "Выберите нужный раздел:"
)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Как получить ключ",
                    callback_data="key_refresh",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Как установить приложение",
                    callback_data="apps_from_main",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Как добавить ключ",
                    callback_data="key_instruction",
                )
            ],
            [
                InlineKeyboardButton(
                    text="VPN не подключается",
                    callback_data="support_from_main",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Как продлить подписку",
                    callback_data="tariffs",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )


@router.message(Command("help", ignore_case=True))
async def help_command(message: Message) -> None:
    await message.answer(
        HELP_TEXT,
        reply_markup=help_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("support", ignore_case=True))
async def support_command(message: Message, settings: Settings) -> None:
    await show_support_from_main(
        _adapter(message, "support_from_main"),
        settings,
    )


@router.message(Command("agreement", ignore_case=True))
async def agreement_command(message: Message, settings: Settings) -> None:
    button = agreement_button(settings.user_agreement_url)
    if button.url:
        await message.answer(
            "📄 Пользовательское соглашение",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [button],
                    [
                        InlineKeyboardButton(
                            text="⬅️ Главное меню",
                            callback_data="main_menu",
                        )
                    ],
                ]
            ),
        )
        return
    await show_user_agreement(_adapter(message, "legal_user_agreement"))


TRANSACTION_LABELS = {
    BalanceTransactionType.topup: "Пополнение",
    BalanceTransactionType.referral_bonus: "Приглашённый друг",
    BalanceTransactionType.daily_charge: "Оплата VPN",
    BalanceTransactionType.refund: "Возврат",
    BalanceTransactionType.adjustment: "Корректировка",
}


def _money(value: Decimal) -> str:
    return f"{abs(value):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _transaction_line(transaction: BalanceTransaction) -> str:
    sign = "+" if transaction.amount > 0 else "−"
    label = TRANSACTION_LABELS.get(transaction.type, "Операция")
    return f"• {sign}{_money(transaction.amount)} ₽ — {label}"


def balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Пополнить",
                    callback_data="tariffs",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 История операций",
                    callback_data="balance_history",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пригласить друга",
                    callback_data="bonuses",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )


@router.callback_query(F.data == "balance_history")
async def show_balance(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.",
            show_alert=True,
        )
        return
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        transactions = (
            await BalanceService(session).recent_transactions(user.id, limit=5)
            if user is not None
            else []
        )
    if user is None:
        await callback.answer("Сначала нажмите /start.", show_alert=True)
        return
    history = (
        "\n".join(_transaction_line(item) for item in transactions)
        if transactions
        else "Операций пока нет."
    )
    text = (
        "💰 <b>Ваш баланс</b>\n\n"
        f"<b>{_money(user.balance)} ₽</b>\n\n"
        "Последние операции:\n\n"
        f"{history}"
    )
    await callback.answer()
    await edit_text_or_caption(
        callback.message,
        text,
        balance_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command(commands=["balance", "баланс"], ignore_case=True))
async def balance_command(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _private_only(message):
        return
    await show_balance(_adapter(message, "balance_history"), session_factory)


@unknown_router.message(StateFilter(None), F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer(
        "Не удалось распознать команду.\n\n"
        "Используйте /help или откройте главное меню."
    )
