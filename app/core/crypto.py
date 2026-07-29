from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken


class SubscriptionUrlCipher:
    def __init__(self, key: str, subscription_base_url: str | None = None) -> None:
        self._fernet = Fernet(key.encode("ascii"))
        self._subscription_base_url = subscription_base_url

    def encrypt(self, url: str) -> bytes:
        return self._fernet.encrypt(self._public_url(url).encode())

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self._public_url(self._fernet.decrypt(encrypted).decode())
        except InvalidToken as exc:
            raise ValueError("Could not decrypt subscription URL") from exc

    def _public_url(self, url: str) -> str:
        if not self._subscription_base_url:
            return url
        original = urlsplit(url)
        public = urlsplit(self._subscription_base_url)
        if not public.path:
            path = original.path
        else:
            token = original.path.rstrip("/").rsplit("/", 1)[-1]
            path = f"{public.path.rstrip('/')}/{token}"
        return urlunsplit(
            (
                public.scheme,
                public.netloc,
                path,
                original.query,
                original.fragment,
            )
        )


def mask_subscription_url(url: str | None) -> str:
    if not url:
        return "—"
    prefix, _, key = url.rpartition("/")
    return f"{prefix}/{key[:3]}...{key[-3:]}" if len(key) > 8 else f"{prefix}/***"
