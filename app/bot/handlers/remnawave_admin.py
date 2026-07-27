import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.filters import AdminFilter
from app.bot.keyboards.admin import admin_navigation
from app.bot.rendering import edit_text_or_caption
from app.core.crypto import SubscriptionUrlCipher, mask_subscription_url
from app.database.models import (
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
    RemnawaveError,
)
from app.services.activation_notifications import send_activation_notification
from app.services.audit import add_audit_log
from app.services.remnawave import (
    RemnawaveProvisioningService,
    build_new_user_request,
    build_new_user_update_request,
    resolve_new_user_policy,
    validate_new_user_policy,
)
from app.services.remnawave_sync import RemnawaveSyncService

router = Router(name=__name__)


class GrantVpnForm(StatesGroup):
    telegram_id = State()
    duration = State()
    traffic = State()
    devices = State()
    confirm = State()


def _test_create_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать тестового пользователя",
                    callback_data="rwtest:create:confirm",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="rwtest:create:cancel")],
        ]
    )


@router.message(Command("test_remnawave"), AdminFilter())
async def test_remnawave(
    message: Message,
    remnawave_client: RemnawaveClient | None = None,
    remnawave_internal_squad_uuid: str | None = None,
) -> None:
    if remnawave_client is None:
        await message.answer(
            "❌ API недоступен: клиент не настроен\n"
            "❌ Токен не проверен\n"
            "❌ Internal Squad не проверен"
        )
        return

    lines: list[str] = []
    try:
        await remnawave_client.healthcheck()
        lines.append("✅ API доступен")
    except RemnawaveError as exc:
        lines.append(f"❌ API недоступен: {exc}")

    try:
        await remnawave_client.check_api()
        lines.append("✅ Токен действителен")
    except RemnawaveAuthenticationError:
        lines.append("❌ Токен отклонён (HTTP 401)")
    except RemnawaveError as exc:
        lines.append(f"❌ Авторизация не проверена: {exc}")

    if not remnawave_internal_squad_uuid:
        lines.append("❌ Internal Squad UUID не настроен")
    else:
        try:
            squad_uuid = UUID(remnawave_internal_squad_uuid)
            await remnawave_client.get_internal_squad(squad_uuid)
            lines.append("✅ Internal Squad найден")
        except (ValueError, RemnawaveError) as exc:
            lines.append(f"❌ Internal Squad не найден: {exc}")
    await message.answer("\n".join(lines))


@router.message(Command("test_remnawave_create"), AdminFilter())
async def test_remnawave_create(message: Message) -> None:
    await message.answer(
        "Будет создан один временный пользователь Remnawave на 7 дней. "
        "Подтвердите действие.",
        reply_markup=_test_create_keyboard(),
    )


@router.callback_query(F.data == "rwtest:create:cancel", AdminFilter())
async def cancel_test_remnawave_create(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text("Создание тестового пользователя отменено.")
    await callback.answer()


@router.callback_query(F.data == "rwtest:create:confirm", AdminFilter())
async def confirm_test_remnawave_create(
    callback: CallbackQuery,
    remnawave_client: RemnawaveClient | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    if callback.message is None:
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if (
        remnawave_client is None
        or not remnawave_internal_squad_uuid
        or (
            not remnawave_template_user_uuid
            and not remnawave_russia_squad_uuid
        )
    ):
        await callback.answer("Remnawave не настроен", show_alert=True)
        return

    username = f"rwtest_{callback.from_user.id}_{secrets.token_hex(3)}"
    remote = None
    squad_assigned = False
    try:
        policy = await resolve_new_user_policy(
            remnawave_client,
            template_user_uuid=remnawave_template_user_uuid,
            internal_squad_uuid=remnawave_internal_squad_uuid,
            russia_squad_uuid=remnawave_russia_squad_uuid,
            local_user_id=callback.from_user.id,
            remnawave_username=username,
        )
        expire_at = datetime.now(UTC) + timedelta(days=7)
        try:
            remote = await remnawave_client.create_user(
                build_new_user_request(
                    policy,
                    username=username,
                    expire_at=expire_at,
                    telegram_id=callback.from_user.id,
                ),
                local_user_id=callback.from_user.id,
            )
        except RemnawaveError as create_exc:
            try:
                remote = await remnawave_client.get_user_by_username(
                    username, local_user_id=callback.from_user.id
                )
            except RemnawaveError:
                raise create_exc from None
        remote = await remnawave_client.update_user(
            build_new_user_update_request(
                policy,
                user_uuid=remote.uuid,
                expire_at=expire_at,
                telegram_id=callback.from_user.id,
            ),
            operation="test_converge_new_user_policy",
            local_user_id=callback.from_user.id,
            remnawave_username=username,
        )
        remote = await remnawave_client.get_user(
            remote.uuid,
            operation="test_get_subscription_url",
            local_user_id=callback.from_user.id,
            remnawave_username=username,
        )
        validate_new_user_policy(
            remote,
            policy,
            expire_at=expire_at,
            telegram_id=callback.from_user.id,
        )
        assigned_squads = {item.uuid for item in remote.active_internal_squads}
        squad_assigned = assigned_squads == set(policy.active_internal_squads)
    except RemnawaveError as exc:
        details = str(exc)
        if isinstance(exc, RemnawaveAPIError) and exc.safe_response_body:
            details = f"{details}; {exc.safe_response_body[:300]}"
        uuid_text = str(remote.uuid) if remote else "—"
        cleanup_keyboard = (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Удалить тестового пользователя",
                            callback_data=f"rwtest:delete:{remote.uuid}",
                        )
                    ]
                ]
            )
            if remote
            else None
        )
        await callback.message.answer(
            f"❌ Проверка не завершена\nUUID: {uuid_text}\nПричина: {details}",
            reply_markup=cleanup_keyboard,
        )
        await callback.answer()
        return

    delete_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить тестового пользователя",
                    callback_data=f"rwtest:delete:{remote.uuid}",
                )
            ]
        ]
    )
    await callback.message.answer(
        f"✅ Тестовый пользователь создан\n"
        f"UUID: {remote.uuid}\n"
        f"Статус: {remote.status.value}\n"
        f"Squad назначен: {'да' if squad_assigned else 'нет'}\n"
        f"Subscription URL получен: {'да' if bool(remote.subscription_url) else 'нет'}",
        reply_markup=delete_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rwtest:delete:"), AdminFilter())
async def delete_test_remnawave_user(
    callback: CallbackQuery,
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    if callback.message is None or remnawave_client is None or callback.data is None:
        return
    try:
        user_uuid = UUID(callback.data.rsplit(":", 1)[1])
        deleted = await remnawave_client.delete_user(user_uuid)
    except (ValueError, RemnawaveError) as exc:
        await callback.answer(f"Удаление не выполнено: {exc}", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(
        "Тестовый пользователь удалён" if deleted else "Удаление не подтверждено",
        show_alert=True,
    )


@router.message(Command("sync_remnawave"), AdminFilter())
async def sync_remnawave(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if not remnawave_client or not subscription_cipher:
        await message.answer("Remnawave не настроен или недоступен.")
        return
    async with session_factory() as session, session.begin():
        result = await RemnawaveSyncService(
            session, remnawave_client, subscription_cipher
        ).sync_batch(limit=100)
        add_audit_log(
            session,
            action="remnawave_sync_completed",
            entity_type="remnawave",
            actor_telegram_id=message.from_user.id if message.from_user else None,
            details=result.__dict__,
        )
    await message.answer(
        f"Проверено: {result.checked}\nСинхронизировано: {result.synchronized}\n"
        f"Ошибок: {result.errors}\nОтсутствует в панели: {result.missing}\n"
        f"Найдено конфликтов: {result.conflicts}"
    )


@router.message(Command("sync_remnawave"))
async def reject_sync(message: Message) -> None:
    await message.answer("У вас нет доступа к этой команде.")


@router.message(Command("rw_user"), AdminFilter())
async def find_remnawave_user(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    try:
        telegram_id = int(command.args or "")
    except ValueError:
        await message.answer("Использование: /rw_user TELEGRAM_ID")
        return
    async with session_factory() as session:
        row = (
            await session.execute(
                select(User, Subscription)
                .join(Subscription, Subscription.user_id == User.id)
                .where(User.telegram_id == telegram_id)
            )
        ).one_or_none()
    if not row:
        await message.answer("Пользователь или подписка не найдены.")
        return
    _, sub = row
    masked = "—"
    if sub.subscription_url_encrypted and subscription_cipher:
        masked = mask_subscription_url(
            subscription_cipher.decrypt(sub.subscription_url_encrypted)
        )
    await message.answer(
        f"Telegram ID: {telegram_id}\nUUID: {sub.remnawave_user_uuid or '—'}\n"
        f"Username: {sub.remnawave_username or '—'}\n"
        f"Статус: {sub.remnawave_status or '—'}\nСсылка: {masked}"
    )


@router.message(Command(commands=["rw_disable", "rw_enable"]), AdminFilter())
async def toggle_remnawave_user(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    try:
        telegram_id = int(command.args or "")
    except ValueError:
        await message.answer(f"Использование: /{command.command} TELEGRAM_ID")
        return
    if not remnawave_client:
        await message.answer("Remnawave недоступен.")
        return
    async with session_factory() as session, session.begin():
        sub = await session.scalar(
            select(Subscription)
            .join(User, User.id == Subscription.user_id)
            .where(User.telegram_id == telegram_id)
            .with_for_update()
        )
        if not sub or not sub.remnawave_user_uuid:
            await message.answer("Связанный пользователь Remnawave не найден.")
            return
        enabled = command.command == "rw_enable"
        if enabled:
            remote = await remnawave_client.enable_user(sub.remnawave_user_uuid)
            sub.status = SubscriptionStatus.active
            sub.provisioning_status = ProvisioningStatus.active
        else:
            remote = await remnawave_client.disable_user(sub.remnawave_user_uuid)
            sub.status = SubscriptionStatus.disabled
            sub.provisioning_status = ProvisioningStatus.disabled
        sub.remnawave_status = remote.status.value
        add_audit_log(
            session,
            action=("remnawave_user_enabled" if enabled else "remnawave_user_disabled"),
            entity_type="subscription",
            entity_id=sub.id,
            actor_telegram_id=message.from_user.id if message.from_user else None,
            details={"target_telegram_id": telegram_id},
        )
    await message.answer(
        "Пользователь включён." if enabled else "Пользователь отключён."
    )


@router.callback_query(F.data == "rw:check", AdminFilter())
async def check_api(
    callback: CallbackQuery, remnawave_client: RemnawaveClient | None = None
) -> None:
    try:
        available = bool(remnawave_client and await remnawave_client.check_api())
    except Exception:
        available = False
    await callback.answer(
        "✅ API доступен" if available else "❌ API недоступен", show_alert=True
    )


@router.callback_query(F.data == "rw:sync", AdminFilter())
async def sync_callback(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if callback.message is None:
        return
    if not remnawave_client or not subscription_cipher:
        await callback.answer("Remnawave недоступен", show_alert=True)
        return
    async with session_factory() as session, session.begin():
        result = await RemnawaveSyncService(
            session, remnawave_client, subscription_cipher
        ).sync_batch(limit=100)
    await callback.answer()
    await callback.message.answer(
        f"Проверено {result.checked}, обновлено {result.synchronized}, "
        f"ошибок {result.errors}, отсутствует {result.missing}, "
        f"конфликтов {result.conflicts}."
    )


@router.callback_query(F.data == "rw:retry", AdminFilter())
async def retry_failed(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        items = list(
            await session.scalars(
                select(Subscription)
                .where(Subscription.provisioning_status == ProvisioningStatus.failed)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        )
        for item in items:
            item.provisioning_status = ProvisioningStatus.pending
            item.activation_attempts = 0
            item.next_retry_at = datetime.now(UTC)
    await callback.answer(f"Поставлено в очередь: {len(items)}", show_alert=True)


@router.message(Command("grant_vpn"), AdminFilter())
async def grant_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(grant_back_action="users_section")
    await state.set_state(GrantVpnForm.telegram_id)
    await message.answer(
        "Введите Telegram ID:",
        reply_markup=admin_navigation("users_section"),
    )


@router.callback_query(
    F.data.in_({"rw:grant", "rw:grant:users", "rw:grant:vpn"}),
    AdminFilter(),
)
async def grant_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    back_action = "remnawave" if callback.data == "rw:grant:vpn" else "users_section"
    await state.update_data(grant_back_action=back_action)
    await state.set_state(GrantVpnForm.telegram_id)
    text = (
        "🎁 <b>Выдать VPN-доступ</b>\n\n"
        "Введите Telegram ID пользователя, которому нужно выдать доступ."
    )
    if callback.message is not None:
        await edit_text_or_caption(
            callback.message,
            text,
            admin_navigation(back_action),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.message(Command("grant_vpn"))
async def reject_grant(message: Message) -> None:
    await message.answer("У вас нет доступа к этой команде.")


async def _grant_navigation(state: FSMContext) -> InlineKeyboardMarkup:
    data = await state.get_data()
    return admin_navigation(str(data.get("grant_back_action", "users_section")))


async def _positive(
    message: Message, state: FSMContext, key: str, next_state: State
) -> None:
    try:
        value = int(message.text or "")
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите положительное целое число.",
            reply_markup=await _grant_navigation(state),
        )
        return
    await state.update_data(**{key: value})
    await state.set_state(next_state)


@router.message(GrantVpnForm.telegram_id, ~F.text.startswith("/"), AdminFilter())
async def grant_id(message: Message, state: FSMContext) -> None:
    await _positive(message, state, "telegram_id", GrantVpnForm.duration)
    if await state.get_state() == GrantVpnForm.duration:
        await message.answer(
            "Срок: 7, 30, 90 или своё число дней:",
            reply_markup=await _grant_navigation(state),
        )


@router.message(GrantVpnForm.duration, ~F.text.startswith("/"), AdminFilter())
async def grant_days(message: Message, state: FSMContext) -> None:
    await _positive(message, state, "days", GrantVpnForm.traffic)
    if await state.get_state() == GrantVpnForm.traffic:
        await message.answer(
            "Лимит трафика в ГБ (0 — безлимит):",
            reply_markup=await _grant_navigation(state),
        )


@router.message(GrantVpnForm.traffic, ~F.text.startswith("/"), AdminFilter())
async def grant_traffic(message: Message, state: FSMContext) -> None:
    try:
        value = int(message.text or "")
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите 0 или положительное число.",
            reply_markup=await _grant_navigation(state),
        )
        return
    await state.update_data(traffic=value)
    await state.set_state(GrantVpnForm.devices)
    await message.answer(
        "Лимит устройств:",
        reply_markup=await _grant_navigation(state),
    )


@router.message(GrantVpnForm.devices, ~F.text.startswith("/"), AdminFilter())
async def grant_devices(message: Message, state: FSMContext) -> None:
    await _positive(message, state, "devices", GrantVpnForm.confirm)
    if await state.get_state() == GrantVpnForm.confirm:
        data = await state.get_data()
        await message.answer(
            f"Подтвердите: ID {data['telegram_id']}, {data['days']} дней, "
            f"{data['traffic']} ГБ, {data['devices']} устройств. Ответьте «да».",
            reply_markup=await _grant_navigation(state),
        )


@router.message(GrantVpnForm.confirm, ~F.text.startswith("/"), AdminFilter())
async def grant_confirm(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    data = await state.get_data()
    back_action = str(data.get("grant_back_action", "users_section"))
    if (message.text or "").lower().strip() != "да":
        await state.clear()
        await message.answer(
            "Отменено.",
            reply_markup=admin_navigation(back_action),
        )
        return
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = await session.scalar(
            select(User)
            .where(User.telegram_id == data["telegram_id"])
            .with_for_update()
        )
        if not user:
            user = User(telegram_id=data["telegram_id"])
            session.add(user)
            await session.flush()
        sub = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .with_for_update()
        )
        base = max(now, sub.expires_at) if sub else now
        if not sub:
            sub = Subscription(
                user_id=user.id,
                source_type=SubscriptionSource.admin,
                status=SubscriptionStatus.pending,
                started_at=now,
                expires_at=base + timedelta(days=data["days"]),
                device_limit=data["devices"],
            )
            session.add(sub)
            await session.flush()
        else:
            sub.expires_at = base + timedelta(days=data["days"])
            sub.source_type = SubscriptionSource.admin
            sub.activation_notified_at = None
        sub.traffic_limit_gb = data["traffic"] or None
        sub.is_unlimited_traffic = data["traffic"] == 0
        sub.device_limit = data["devices"]
        if remnawave_client and subscription_cipher:
            result = await RemnawaveProvisioningService(
                session,
                remnawave_client,
                subscription_cipher,
                remnawave_internal_squad_uuid,
                remnawave_russia_squad_uuid,
                remnawave_template_user_uuid,
            ).provision(sub, user, source=SubscriptionSource.admin)
        else:
            result = None
            sub.status = SubscriptionStatus.activation_failed
        add_audit_log(
            session,
            action="manual_vpn_granted",
            entity_type="subscription",
            entity_id=sub.id,
            actor_telegram_id=message.from_user.id if message.from_user else None,
            details={"target_telegram_id": user.telegram_id, "days": data["days"]},
        )
        subscription_id = sub.id
    await state.clear()
    if result and result.status == SubscriptionStatus.active:
        await send_activation_notification(
            session_factory,
            bot=message.bot,
            subscription_id=subscription_id,
            cipher=subscription_cipher,
        )
        await message.answer(
            "VPN успешно выдан.",
            reply_markup=admin_navigation(back_action),
        )
    else:
        await message.answer(
            "Подписка сохранена, активация будет повторена.",
            reply_markup=admin_navigation(back_action),
        )
