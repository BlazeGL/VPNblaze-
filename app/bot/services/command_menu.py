from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="key", description="Получить VPN-ключ"),
    BotCommand(command="profile", description="Личный кабинет"),
    BotCommand(command="plans", description="Тарифы"),
    BotCommand(command="buy", description="Купить подписку"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="topup", description="Пополнить баланс"),
    BotCommand(command="promo", description="Ввести промокод"),
    BotCommand(command="ref", description="Пригласить друга"),
    BotCommand(command="apps", description="Скачать приложение"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="support", description="Поддержка"),
    BotCommand(command="agreement", description="Пользовательское соглашение"),
]

ADMIN_COMMANDS = [
    BotCommand(command="edik", description="Панель администратора"),
    BotCommand(command="new_promo", description="Создать промокод"),
    BotCommand(command="ref_stats", description="Статистика рефералов"),
    BotCommand(command="sync_remnawave", description="Синхронизация VPN"),
    BotCommand(command="grant_vpn", description="Выдать VPN-доступ"),
]


async def register_command_menu(bot: Bot, admin_ids: set[int]) -> None:
    await bot.set_my_commands(
        PUBLIC_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )
    for admin_id in sorted(admin_ids):
        await bot.set_my_commands(
            [*PUBLIC_COMMANDS, *ADMIN_COMMANDS],
            scope=BotCommandScopeChat(chat_id=admin_id),
        )
