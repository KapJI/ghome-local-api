"""The requests to a device's local API, over the caller's aiohttp session."""

from collections.abc import Callable, Sequence
from http import HTTPStatus
import ipaddress

from aiohttp import (
    ClientConnectionError,
    ClientError,
    ClientPayloadError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
)
from aiohttp.client_exceptions import ClientConnectorError, ContentTypeError

from .const import (
    API_ENDPOINT_ALARM_DELETE,
    API_ENDPOINT_ALARM_VOLUME,
    API_ENDPOINT_ALARMS,
    API_ENDPOINT_BLUETOOTH_RESULTS,
    API_ENDPOINT_BLUETOOTH_SCAN,
    API_ENDPOINT_BUILD_INFO,
    API_ENDPOINT_DO_NOT_DISTURB,
    API_ENDPOINT_EUREKA_INFO,
    API_ENDPOINT_REBOOT,
    API_ENDPOINT_UPDATE_INFO,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    HEADER_CAST_LOCAL_AUTH,
    HEADER_CONTENT_TYPE,
    JSON_ALARM_VOLUME,
    JSON_NOTIFICATIONS_ENABLED,
)
from .exceptions import (
    AnswerTimeoutError,
    CannotConnectError,
    ClientRequestError,
    ConnectionLostError,
    InvalidJsonError,
    NotFoundError,
    RequestError,
    StatusError,
    TokenRejectedError,
    UnexpectedResponseError,
)
from .models import (
    AlarmsAndTimers,
    AlarmVolumeReply,
    BluetoothSighting,
    BuildInfo,
    DeleteResult,
    DeviceInfo,
    DoNotDisturbReply,
    JsonObject,
    JsonValue,
    UpdateInfo,
)

# Called with the final response as soon as its status and headers have come, before
# its body is read. It must not read, release or close the response, or raise.
type ResponseObserver = Callable[[ClientResponse], None]


def device_url(host: str, path: str, port: int = DEFAULT_PORT) -> str:
    """Return the URL of a path on the device at this IP address, bracketing an IPv6 one.

    Raise ValueError if the host isn't an IP address.
    """
    if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
        host = f"[{host}]"
    return f"https://{host}:{port}/{path}"


def _request_error(
    error: ClientError | TimeoutError, host: str, path: str, status: int | None
) -> RequestError:
    """Return the error for a request that failed with this exception."""
    if isinstance(error, ClientConnectorError):
        return CannotConnectError(host=host, path=path, status=status)
    if isinstance(error, ClientConnectionError | ClientPayloadError):
        return ConnectionLostError(host=host, path=path, status=status)
    if isinstance(error, ClientError):
        return ClientRequestError(host=host, path=path, status=status)
    return AnswerTimeoutError(host=host, path=path, status=status)


def _status_error(host: str, path: str, status: int, reason: str | None) -> StatusError:
    """Return the error for an answer with a status other than 200."""
    if status == HTTPStatus.UNAUTHORIZED:
        return TokenRejectedError(host=host, path=path, status=status, reason=reason)
    if status == HTTPStatus.NOT_FOUND:
        return NotFoundError(host=host, path=path, status=status, reason=reason)
    return StatusError(host=host, path=path, status=status, reason=reason)


class GoogleHomeLocalClient:
    """A client of the local API of Google Home devices, over the caller's session.

    It keeps no state but the session and port: each call takes the device's IP
    address, and its local auth token if the request needs one. The session must
    accept the device's self-signed certificate and is never closed here. Nothing is
    retried. Concurrent calls are independent and not ordered.
    """

    __slots__ = ("_port", "_session")

    def __init__(self, session: ClientSession, *, port: int = DEFAULT_PORT) -> None:
        """Create a client that sends its requests with this session, to this port."""
        self._session = session
        self._port = port

    async def _send(
        self,
        host: str,
        path: str,
        *,
        token: str | None,
        method: str = "GET",
        body: JsonObject | None = None,
        timeout: float,
        on_response: ResponseObserver | None,
        read_body: bool = True,
    ) -> JsonValue:
        """Send a request and return the reply of its 200.

        With a token, the reply of a body that isn't JSON is {}, and one that can't be
        decoded raises InvalidJsonError. Without one, as for info, both are None.
        Without read_body, the 200 is the whole answer and None is returned.
        """
        url = device_url(host, path, self._port)
        client_timeout = ClientTimeout(total=timeout)
        status: int | None = None
        reply: JsonValue = None
        try:
            async with (
                self._session.get(url, timeout=client_timeout)
                if token is None
                else self._session.request(
                    method,
                    url,
                    json=body,
                    headers={
                        HEADER_CAST_LOCAL_AUTH: token,
                        HEADER_CONTENT_TYPE: "application/json",
                    },
                    timeout=client_timeout,
                )
            ) as response:
                status = response.status
                if on_response is not None:
                    on_response(response)
                if status == HTTPStatus.OK and read_body:
                    try:
                        reply = await response.json()
                    except ContentTypeError:
                        reply = None if token is None else {}
                    # ValueError includes JSONDecodeError and UnicodeDecodeError. Python's
                    # json raises a plain one for an integer of more than 4300 digits, and
                    # RecursionError for deep nesting. bytes.decode raises LookupError for
                    # a charset that names a codec that isn't a text encoding, such as base64
                    except (ValueError, RecursionError, LookupError) as error:
                        if token is not None:
                            raise InvalidJsonError(host=host, path=path) from error
        except (ClientError, TimeoutError) as error:
            raise _request_error(error, host, path, status) from error
        if status != HTTPStatus.OK:
            # Home Assistant's test mock of a response has no reason
            raise _status_error(host, path, status, getattr(response, "reason", None))
        return reply

    async def _get_info(
        self,
        host: str,
        path: str,
        *,
        timeout: float,
        on_response: ResponseObserver | None,
    ) -> JsonObject | None:
        """Return the info of a 200, or None for a reply that isn't a JSON object."""
        info = await self._send(
            host, path, token=None, timeout=timeout, on_response=on_response
        )
        return info if isinstance(info, dict) else None

    async def get_device_info(
        self,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> DeviceInfo | None:
        """Return the device's ID, name and whether it has Google Assistant, without a token.

        Return None for a 200 whose reply isn't a JSON object, and raise StatusError
        for another status.
        """
        info = await self._get_info(
            host, API_ENDPOINT_EUREKA_INFO, timeout=timeout, on_response=on_response
        )
        return DeviceInfo.from_json(info) if info is not None else None

    async def get_info_status(
        self,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> int:
        """Return the status of the device's answer when asked for its info, without a token.

        A 200 counts once its body has come whole, another status as soon as it comes,
        and neither is undone by a failure while the answer is released. Raise a
        RequestError if no status counts: with .status 200 if the body broke off, else
        None. Redirects aren't followed, so their status is returned.
        """
        path = API_ENDPOINT_EUREKA_INFO
        url = device_url(host, path, self._port)
        observed: int | None = None
        status: int | None = None
        try:
            async with self._session.get(
                url, timeout=ClientTimeout(total=timeout), allow_redirects=False
            ) as response:
                observed = response.status
                if on_response is not None:
                    on_response(response)
                if observed == HTTPStatus.OK:
                    await response.read()
                status = observed
        except (ClientError, TimeoutError) as error:
            if status is None:
                raise _request_error(error, host, path, observed) from error
        return status

    async def get_build_info(
        self,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> BuildInfo | None:
        """Return the device's firmware versions, without a token.

        Return None for a 200 whose reply isn't a JSON object, and raise StatusError
        for another status.
        """
        info = await self._get_info(
            host, API_ENDPOINT_BUILD_INFO, timeout=timeout, on_response=on_response
        )
        return BuildInfo.from_json(info) if info is not None else None

    async def get_update_info(
        self,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> UpdateInfo | None:
        """Return whether the device has a firmware update waiting, its uptime and Wi-Fi.

        No token is needed. Return None for a 200 whose reply isn't a JSON object, and
        raise StatusError for another status.
        """
        info = await self._get_info(
            host, API_ENDPOINT_UPDATE_INFO, timeout=timeout, on_response=on_response
        )
        return UpdateInfo.from_json(info) if info is not None else None

    async def get_alarms_and_timers(
        self,
        host: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> AlarmsAndTimers:
        """Return the device's alarms and timers.

        Raise UnexpectedResponseError if the reply doesn't have a list of each, or any
        item can't be read.
        """
        reply = await self._send(
            host,
            API_ENDPOINT_ALARMS,
            token=token,
            timeout=timeout,
            on_response=on_response,
        )
        if (parsed := AlarmsAndTimers.from_json(reply)) is None:
            raise UnexpectedResponseError(
                host=host, path=API_ENDPOINT_ALARMS, body=reply
            )
        return parsed

    async def delete_alarms_and_timers(
        self,
        host: str,
        token: str,
        ids: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> DeleteResult:
        """Ask the device to delete the alarms and timers with these IDs in one request.

        Only a confirmed result says they're deleted. Once the request is sent, a
        failure leaves it unknown whether they were.
        """
        reply = await self._send(
            host,
            API_ENDPOINT_ALARM_DELETE,
            token=token,
            method="POST",
            body={"ids": list(ids)},
            timeout=timeout,
            on_response=on_response,
        )
        return DeleteResult(raw=reply)

    async def get_alarm_volume(
        self,
        host: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> AlarmVolumeReply:
        """Return the reply with the device's alarm volume, whatever JSON it is."""
        reply = await self._send(
            host,
            API_ENDPOINT_ALARM_VOLUME,
            token=token,
            method="POST",
            timeout=timeout,
            on_response=on_response,
        )
        return AlarmVolumeReply(raw=reply)

    async def set_alarm_volume(
        self,
        host: str,
        token: str,
        level: float,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> AlarmVolumeReply:
        """Set the device's alarm volume, a fraction such as 0.7, and return its reply."""
        reply = await self._send(
            host,
            API_ENDPOINT_ALARM_VOLUME,
            token=token,
            method="POST",
            body={JSON_ALARM_VOLUME: level},
            timeout=timeout,
            on_response=on_response,
        )
        return AlarmVolumeReply(raw=reply)

    async def get_do_not_disturb(
        self,
        host: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> DoNotDisturbReply:
        """Return the reply with the device's Do Not Disturb setting, whatever JSON it is."""
        reply = await self._send(
            host,
            API_ENDPOINT_DO_NOT_DISTURB,
            token=token,
            method="POST",
            timeout=timeout,
            on_response=on_response,
        )
        return DoNotDisturbReply(raw=reply)

    async def set_do_not_disturb(
        self,
        host: str,
        token: str,
        enabled: bool,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> DoNotDisturbReply:
        """Turn Do Not Disturb on or off, and return the device's reply.

        The device is sent whether notifications are on, the opposite.
        """
        reply = await self._send(
            host,
            API_ENDPOINT_DO_NOT_DISTURB,
            token=token,
            method="POST",
            body={JSON_NOTIFICATIONS_ENABLED: not enabled},
            timeout=timeout,
            on_response=on_response,
        )
        return DoNotDisturbReply(raw=reply)

    async def reboot(
        self,
        host: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> None:
        """Ask the device to reboot.

        Its 200 is the whole answer: the rest isn't read, as the device may go down
        while sending it. A device answers so even if it doesn't support rebooting.
        """
        await self._send(
            host,
            API_ENDPOINT_REBOOT,
            token=token,
            method="POST",
            # "now" means reboot; "fdr", a factory reset, isn't offered
            body={"params": "now"},
            timeout=timeout,
            on_response=on_response,
            read_body=False,
        )

    async def start_bluetooth_scan(
        self,
        host: str,
        token: str,
        *,
        duration: int,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> None:
        """Start a Bluetooth scan that clears the results of the previous one.

        The device scans for up to duration seconds, then stops on its own.
        """
        await self._send(
            host,
            API_ENDPOINT_BLUETOOTH_SCAN,
            token=token,
            method="POST",
            body={"enable": True, "clear_results": True, "timeout": duration},
            timeout=timeout,
            on_response=on_response,
        )

    async def get_bluetooth_scan_results(
        self,
        host: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_response: ResponseObserver | None = None,
    ) -> tuple[BluetoothSighting, ...]:
        """Return the Bluetooth devices the device heard since its scan started.

        Rows that can't be read are left out, so a reply of only such rows looks like
        a scan that heard nothing. Raise UnexpectedResponseError if the reply isn't a
        list.
        """
        reply = await self._send(
            host,
            API_ENDPOINT_BLUETOOTH_RESULTS,
            token=token,
            timeout=timeout,
            on_response=on_response,
        )
        if not isinstance(reply, list):
            raise UnexpectedResponseError(
                host=host, path=API_ENDPOINT_BLUETOOTH_RESULTS, body=reply
            )
        return tuple(
            sighting
            for row in reply
            if (sighting := BluetoothSighting.from_json(row)) is not None
        )
