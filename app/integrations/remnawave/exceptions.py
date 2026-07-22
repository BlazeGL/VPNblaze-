class RemnawaveError(RuntimeError):
    """Base class for Remnawave integration failures."""


class RemnawaveAPIError(RemnawaveError):
    """A sanitized HTTP API error safe to log and persist."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        safe_response_body: str | None = None,
        operation: str = "unknown",
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.safe_response_body = safe_response_body
        self.operation = operation
        self.retryable = retryable
        super().__init__(message)


class RemnawaveConfigurationError(RemnawaveError):
    pass


class RemnawaveNetworkError(RemnawaveError):
    def __init__(self, message: str, *, operation: str = "unknown") -> None:
        self.operation = operation
        self.retryable = True
        super().__init__(message)


class RemnawaveAuthenticationError(RemnawaveAPIError):
    pass


class RemnawavePermissionError(RemnawaveAPIError):
    pass


class RemnawaveNotFoundError(RemnawaveAPIError):
    pass


class RemnawaveConflictError(RemnawaveAPIError):
    pass


class RemnawaveValidationError(RemnawaveAPIError):
    pass


class RemnawaveRateLimitError(RemnawaveAPIError):
    pass


class RemnawaveServerError(RemnawaveAPIError):
    pass
