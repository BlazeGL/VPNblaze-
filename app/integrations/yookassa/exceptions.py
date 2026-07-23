class YooKassaError(RuntimeError):
    """Base class for YooKassa integration failures."""


class YooKassaConfigurationError(YooKassaError):
    pass


class YooKassaRequestError(YooKassaError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class YooKassaResponseError(YooKassaError):
    pass
