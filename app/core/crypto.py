from cryptography.fernet import Fernet, InvalidToken


class SubscriptionUrlCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, url: str) -> bytes:
        return self._fernet.encrypt(url.encode())

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted).decode()
        except InvalidToken as exc:
            raise ValueError("Could not decrypt subscription URL") from exc


def mask_subscription_url(url: str | None) -> str:
    if not url:
        return "—"
    prefix, _, key = url.rpartition("/")
    return f"{prefix}/{key[:3]}...{key[-3:]}" if len(key) > 8 else f"{prefix}/***"
