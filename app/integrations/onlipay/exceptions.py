class OnliPayError(RuntimeError):
    pass


class OnliPayUnavailableError(OnliPayError):
    """The official merchant contract or credentials are not configured."""


class OnliPayRequestError(OnliPayError):
    pass


class InvalidWebhookSignature(OnliPayError):
    pass


class InvalidWebhookPayload(OnliPayError):
    pass
