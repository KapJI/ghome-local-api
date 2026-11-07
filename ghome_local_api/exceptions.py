"""The errors of a request to a device.

Their messages name the device's host and the path, never its token or a reply.
The aiohttp exception chained as __cause__ isn't sanitised: it can hold the request
headers, with the token.
"""

from .models import JsonValue


class GoogleHomeLocalError(Exception):
    """A request to a device got no answer, or not the one expected."""

    def __init__(self, message: str, *, host: str, path: str) -> None:
        """Create the error of a request to this host and path."""
        super().__init__(message)
        self.host = host
        self.path = path


class RequestError(GoogleHomeLocalError):
    """No usable answer came: __cause__ is the aiohttp exception or TimeoutError.

    The status is that of an answer that came before the failure, such as a 200 whose
    body broke off, else None.
    """

    def __init__(self, *, host: str, path: str, status: int | None) -> None:
        """Create the error of a request whose answer, if any, had this status."""
        super().__init__(
            f"{host} gave no usable answer to {path}"
            + (f" after answering {status}" if status is not None else ""),
            host=host,
            path=path,
        )
        self.status = status


class CannotConnectError(RequestError):
    """The connection to the device failed, for example because it is offline."""


class ConnectionLostError(RequestError):
    """The connection closed or the answer broke off; the device may have acted."""


class AnswerTimeoutError(RequestError):
    """The device didn't answer in time; it may have acted."""


class ClientRequestError(RequestError):
    """aiohttp failed the request otherwise, such as after too many redirects."""


class StatusError(GoogleHomeLocalError):
    """The device answered with a status other than 200."""

    def __init__(
        self, *, host: str, path: str, status: int, reason: str | None
    ) -> None:
        """Create the error of an answer with this status and reason."""
        super().__init__(
            f"{host} answered {path} with {status} {reason or ''}".rstrip(),
            host=host,
            path=path,
        )
        self.status = status
        self.reason = reason


class TokenRejectedError(StatusError):
    """The device answered 401: it rejected the token."""


class NotFoundError(StatusError):
    """The device answered 404, for example while it is starting or if it lacks the endpoint."""


class InvalidJsonError(GoogleHomeLocalError):
    """The device answered 200 with a body that says it's JSON but can't be decoded.

    __cause__ is the decoder's JSONDecodeError or UnicodeDecodeError.
    """

    def __init__(self, *, host: str, path: str) -> None:
        """Create the error of a reply that isn't valid JSON."""
        super().__init__(
            f"{host} answered {path} with JSON that can't be decoded",
            host=host,
            path=path,
        )
        self.status = 200


class UnexpectedResponseError(GoogleHomeLocalError):
    """The device answered 200 with JSON that isn't in the expected form.

    The body is the decoded reply. It holds device data, so don't log it as it is.
    """

    def __init__(self, *, host: str, path: str, body: JsonValue) -> None:
        """Create the error of a reply that isn't in the expected form."""
        super().__init__(
            f"{host} answered {path} with a reply that isn't in the expected form",
            host=host,
            path=path,
        )
        self.body = body
