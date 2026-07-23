from datetime import UTC, datetime
from html import escape

from app.bot.keyboards.subscription import SUPPORT_URL
from app.database.models import Subscription


def format_expiration(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def activation_text(subscription: Subscription, subscription_url: str) -> str:
    return (
        "🛡 <b>Ваш BlazeVPN активирован</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Доступ успешно создан\n\n"
        "📅 Действует до:\n"
        f"<b>{format_expiration(subscription.expires_at)}</b>\n\n"
        "🔑 <b>Персональная ссылка подключения</b>\n\n"
        f"<code>{escape(subscription_url)}</code>\n\n"
        "Нажмите на ссылку, чтобы скопировать её, затем добавьте в приложение.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📲 Что делать дальше:\n\n"
        "1️⃣ Установите приложение для своего устройства\n"
        "2️⃣ Добавьте персональную ссылку\n"
        "3️⃣ Включите подключение\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def subscription_link_text(subscription_url: str) -> str:
    return (
        f"🔑 <b>Ваша персональная ссылка</b>\n\n<code>{escape(subscription_url)}</code>"
    )


DEVICES_TEXT = (
    "📱 <b>Выберите ваше устройство</b>\n\n"
    "Установите приложение, затем вернитесь в бот и добавьте персональную "
    "ссылку подписки."
)

INSTRUCTION_TEXT = (
    "📖 <b>Как подключить BlazeVPN</b>\n\n"
    "1️⃣ Скачайте приложение для своей платформы\n"
    "2️⃣ Вернитесь к экрану ключа и скопируйте персональную ссылку\n"
    "3️⃣ Импортируйте ссылку в приложение\n"
    "4️⃣ Включите VPN-подключение"
)

def support_text(url: str = SUPPORT_URL) -> str:
    return (
        "🆘 <b>Поддержка BlazeVPN</b>\n\n"
        f'<a href="{escape(url, quote=True)}">Открыть поддержку</a>\n\n'
        "Опишите проблему одним сообщением. Укажите устройство и шаг, "
        "на котором возникла ошибка. Не отправляйте персональную ссылку в группы "
        "или каналы."
    )


SUPPORT_TEXT = support_text()

PLATFORM_TEXTS = {
    "android": (
        "🤖 <b>Установка на Android</b>\n\n"
        "1️⃣ Нажмите «Скачать Incy»\n"
        "2️⃣ Установите приложение\n"
        "3️⃣ Вернитесь в BlazeVPN\n"
        "4️⃣ Скопируйте персональную ссылку\n"
        "5️⃣ Добавьте её в приложение и включите VPN"
    ),
    "ios": (
        "🍏 <b>Установка на iPhone / iPad</b>\n\n"
        "1️⃣ Скачайте Incy из App Store\n"
        "2️⃣ Откройте приложение\n"
        "3️⃣ Вернитесь в BlazeVPN\n"
        "4️⃣ Скопируйте персональную ссылку\n"
        "5️⃣ Импортируйте её в приложение"
    ),
    "windows": (
        "🪟 <b>Установка на Windows</b>\n\n"
        "1️⃣ Скачайте установщик Hiddify\n"
        "2️⃣ Установите и запустите программу\n"
        "3️⃣ Скопируйте персональную ссылку\n"
        "4️⃣ Добавьте подписку в Hiddify\n"
        "5️⃣ Включите подключение"
    ),
    "linux": (
        "🐧 <b>Установка на Linux</b>\n\n"
        "1️⃣ Скачайте файл Hiddify AppImage\n"
        "2️⃣ Разрешите выполнение файла:\n"
        "<code>chmod +x Hiddify-Linux-x64.AppImage</code>\n"
        "3️⃣ Запустите приложение\n"
        "4️⃣ Скопируйте персональную ссылку\n"
        "5️⃣ Добавьте подписку и включите VPN"
    ),
}
