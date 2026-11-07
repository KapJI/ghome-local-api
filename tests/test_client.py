"""The client's requests and failures, against a fake device on a loopback TLS server."""

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
import json
import socket
from unittest.mock import patch

from aiohttp import (
    ClientConnectionError,
    ClientConnectorError,
    ClientPayloadError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    ServerDisconnectedError,
    ServerTimeoutError,
    TooManyRedirects,
    web,
)
import pytest

from ghome_local_api import (
    AlarmStatus,
    AnswerTimeoutError,
    BluetoothSighting,
    CannotConnectError,
    ClientRequestError,
    ConnectionLostError,
    GoogleHomeLocalClient,
    GoogleHomeLocalError,
    InvalidJsonError,
    NotFoundError,
    StatusError,
    TokenRejectedError,
    UnexpectedResponseError,
)

from .fake_device import (
    HOST,
    TOKEN,
    DeviceTestCase,
    Handler,
    StubExchange,
    StubResponse,
    StubSession,
    cut_off,
    dropped,
    held,
    reply,
)

type Call = Callable[[GoogleHomeLocalClient], Awaitable[object]]
type FailureCase = tuple[
    str,
    Handler | None,
    type[GoogleHomeLocalError],
    int | None,
    type[BaseException] | None,
]


class Observer:
    """An on_response observer that records the status of each response it gets."""

    def __init__(self) -> None:
        """Create an observer that has seen nothing."""
        self.statuses: list[int] = []

    def __call__(self, response: ClientResponse) -> None:
        """Record the response's status."""
        self.statuses.append(response.status)


def closed_port() -> int:
    """Return a loopback port that nothing listens on."""
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        port: int = sock.getsockname()[1]
    return port


async def slow(_request: web.Request) -> web.StreamResponse:
    """Answer after longer than the tests' short timeout."""
    await asyncio.sleep(1)
    return web.json_response({})


async def redirect_loop(_request: web.Request) -> web.StreamResponse:
    """Redirect every request to the same path."""
    raise web.HTTPFound("/setup/assistant/alarms")


# Each call, sent with its timeout and observer, which every method passes on itself
type OptionsCall = Callable[[GoogleHomeLocalClient, float, Observer], Awaitable[object]]

# Each call that needs a token: its method, path and body, and a reply it accepts.
# Bodies are the bytes aiohttp's default serializer sends, so true can't pass as 1.
TOKEN_CALLS: list[tuple[str, OptionsCall, str, str, bytes, bytes]] = [
    (
        "get_alarms_and_timers",
        lambda c, t, o: c.get_alarms_and_timers(HOST, TOKEN, timeout=t, on_response=o),
        "GET",
        "/setup/assistant/alarms",
        b"",
        b'{"alarm": [], "timer": []}',
    ),
    (
        "delete_alarms_and_timers",
        lambda c, t, o: c.delete_alarms_and_timers(
            HOST, TOKEN, ("alarm/a1", "timer/t1"), timeout=t, on_response=o
        ),
        "POST",
        "/setup/assistant/alarms/delete",
        b'{"ids": ["alarm/a1", "timer/t1"]}',
        b'{"success": true}',
    ),
    (
        "get_alarm_volume",
        lambda c, t, o: c.get_alarm_volume(HOST, TOKEN, timeout=t, on_response=o),
        "POST",
        "/setup/assistant/alarms/volume",
        b"",
        b"{}",
    ),
    (
        "set_alarm_volume",
        lambda c, t, o: c.set_alarm_volume(HOST, TOKEN, 0.7, timeout=t, on_response=o),
        "POST",
        "/setup/assistant/alarms/volume",
        b'{"volume": 0.7}',
        b"{}",
    ),
    (
        "get_do_not_disturb",
        lambda c, t, o: c.get_do_not_disturb(HOST, TOKEN, timeout=t, on_response=o),
        "POST",
        "/setup/assistant/notifications",
        b"",
        b"{}",
    ),
    (
        "set_do_not_disturb",
        lambda c, t, o: c.set_do_not_disturb(
            HOST, TOKEN, True, timeout=t, on_response=o
        ),
        "POST",
        "/setup/assistant/notifications",
        # Do Not Disturb on is notifications off
        b'{"notifications_enabled": false}',
        b"{}",
    ),
    (
        "reboot",
        lambda c, t, o: c.reboot(HOST, TOKEN, timeout=t, on_response=o),
        "POST",
        "/setup/reboot",
        b'{"params": "now"}',
        b"",
    ),
    (
        "start_bluetooth_scan",
        lambda c, t, o: c.start_bluetooth_scan(
            HOST, TOKEN, duration=30, timeout=t, on_response=o
        ),
        "POST",
        "/setup/bluetooth/scan",
        b'{"enable": true, "clear_results": true, "timeout": 30}',
        b"{}",
    ),
    (
        "get_bluetooth_scan_results",
        lambda c, t, o: c.get_bluetooth_scan_results(
            HOST, TOKEN, timeout=t, on_response=o
        ),
        "GET",
        "/setup/bluetooth/scan_results",
        b"",
        b"[]",
    ),
]

INFO_CALLS: list[tuple[str, OptionsCall, str]] = [
    (
        "get_device_info",
        lambda c, t, o: c.get_device_info(HOST, timeout=t, on_response=o),
        "/setup/eureka_info?params=name,device_info",
    ),
    (
        "get_info_status",
        lambda c, t, o: c.get_info_status(HOST, timeout=t, on_response=o),
        "/setup/eureka_info?params=name,device_info",
    ),
    (
        "get_build_info",
        lambda c, t, o: c.get_build_info(HOST, timeout=t, on_response=o),
        "/setup/eureka_info?params=build_info",
    ),
    (
        "get_update_info",
        lambda c, t, o: c.get_update_info(HOST, timeout=t, on_response=o),
        "/setup/eureka_info",
    ),
]


class WireTests(DeviceTestCase):
    """What each call sends, and how it calls the session."""

    async def test_token_requests(self) -> None:
        """Each call that needs a token sends its method, path, token and exact body.

        Catches a wrong path or method, a changed payload, a missing token, a
        timeout or observer that a method doesn't pass on, or a session call with
        other arguments, which ha-google-home's tests assert.
        """
        for name, send, method, path, body, answer in TOKEN_CALLS:
            with (
                self.subTest(name),
                patch.object(
                    ClientSession,
                    "request",
                    autospec=True,
                    side_effect=ClientSession.request,
                ) as request,
            ):
                self.device.requests.clear()
                self.device.handler = reply(answer)
                observer = Observer()
                await send(self.client, 4, observer)
                assert observer.statuses == [200]
                assert len(self.device.requests) == 1
                received = self.device.requests[0]
                assert (received.method, received.path_qs) == (method, path)
                assert received.tls
                assert received.headers["cast-local-authorization-token"] == TOKEN
                assert received.headers["Content-Type"] == "application/json"
                assert received.body == body
                request.assert_called_once_with(
                    self.session,
                    method,
                    f"https://{HOST}:{self.device.port}{path}",
                    json=json.loads(body) if body else None,
                    headers={
                        "cast-local-authorization-token": TOKEN,
                        "content-type": "application/json",
                    },
                    timeout=ClientTimeout(total=4),
                )

    async def test_info_requests(self) -> None:
        """Info calls send a plain GET without a token; only the status call refuses redirects.

        Catches a leaked token, a wrong path, a changed redirect setting, or a
        timeout or observer that a method doesn't pass on.
        """
        for name, send, path in INFO_CALLS:
            with (
                self.subTest(name),
                patch.object(
                    ClientSession, "get", autospec=True, side_effect=ClientSession.get
                ) as get,
            ):
                self.device.requests.clear()
                observer = Observer()
                await send(self.client, 4, observer)
                assert observer.statuses == [200]
                assert len(self.device.requests) == 1
                received = self.device.requests[0]
                assert (received.method, received.path_qs) == ("GET", path)
                assert received.tls
                assert TOKEN not in str(received.headers)
                assert "cast-local-authorization-token" not in received.headers
                extra = {"allow_redirects": False} if name == "get_info_status" else {}
                get.assert_called_once_with(
                    self.session,
                    f"https://{HOST}:{self.device.port}{path}",
                    timeout=ClientTimeout(total=4),
                    **extra,
                )

    async def test_default_timeout(self) -> None:
        """Without a timeout, a request's ClientTimeout is the 5-second default."""
        with patch.object(
            ClientSession, "request", autospec=True, side_effect=ClientSession.request
        ) as request:
            await self.client.get_alarm_volume(HOST, TOKEN)
        assert request.call_args.kwargs["timeout"] == ClientTimeout(total=5)

    async def test_info_status_returns_a_redirect(self) -> None:
        """The info status call returns a 302 instead of following it."""
        self.device.handler = reply(
            b"", status=302, headers={"Location": "/setup/eureka_info"}
        )
        assert await self.client.get_info_status(HOST) == 302
        assert len(self.device.requests) == 1


class ObserverTests(DeviceTestCase):
    """When on_response is called: once, as soon as the status and headers come."""

    async def test_runs_before_the_body(self) -> None:
        """The observer runs before the body comes; a cancellation then releases the connection.

        Catches calling it after reading the body, or more than once, and a
        cancelled call that leaves its connection acquired.
        """
        release = asyncio.Event()
        self.device.handler = held(release, b'"x": 1}', first=b"{")
        observer = Observer()
        observed = asyncio.Event()

        def observe(response: ClientResponse) -> None:
            observer(response)
            observed.set()

        task = asyncio.create_task(
            self.client.get_alarm_volume(HOST, TOKEN, on_response=observe)
        )
        # Times out if the observer waits for the body
        async with asyncio.timeout(5):
            await observed.wait()
        assert not release.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observer.statuses == [200]
        release.set()
        # The session has one connection, so this waits until the timeout if the
        # cancelled call left it acquired
        self.device.handler = reply(b'{"volume": 0.5}')
        async with asyncio.timeout(5):
            volume = await self.client.get_alarm_volume(HOST, TOKEN)
        assert volume.percent() == 50

    async def test_runs_once_before_a_status_error(self) -> None:
        """A 500 calls the observer once, with that status, before StatusError is raised."""
        self.device.handler = reply(b"broken", status=500, content_type="text/plain")
        observer = Observer()
        with pytest.raises(StatusError):
            await self.client.get_alarm_volume(HOST, TOKEN, on_response=observer)
        assert observer.statuses == [500]

    async def test_not_called_without_a_response(self) -> None:
        """No answer, or only redirects that aiohttp gives up on, calls it zero times."""
        self.device.handler = redirect_loop
        refused = GoogleHomeLocalClient(self.session, port=closed_port())
        observer = Observer()
        for name, client in (("redirect loop", self.client), ("refused", refused)):
            with self.subTest(name), pytest.raises(GoogleHomeLocalError):
                await client.get_alarms_and_timers(HOST, TOKEN, on_response=observer)
        assert not observer.statuses

    async def test_runs_with_the_final_response_of_a_redirect(self) -> None:
        """A followed redirect calls the observer once, with the response it leads to."""

        async def moved(request: web.Request) -> web.StreamResponse:
            if request.path == "/setup/assistant/alarms":
                raise web.HTTPFound("/elsewhere")
            return web.json_response({"alarm": [], "timer": []})

        self.device.handler = moved
        paths: list[str] = []
        await self.client.get_alarms_and_timers(
            HOST, TOKEN, on_response=lambda response: paths.append(response.url.path)
        )
        assert paths == ["/elsewhere"]

    async def test_exception_propagates(self) -> None:
        """An exception that the observer raises leaves the call unwrapped."""

        def observe(_response: ClientResponse) -> None:
            raise RuntimeError("observer failed")

        with pytest.raises(RuntimeError, match="observer failed"):
            await self.client.get_alarm_volume(HOST, TOKEN, on_response=observe)

    async def test_reboot_runs_before_cleanup(self) -> None:
        """A reboot's observer runs before the response is released; a cancellation there stays one.

        aiohttp's release can wait for the request's writer. Catches moving the
        observer after cleanup, and wrapping the CancelledError.
        """
        leave = asyncio.Event()
        exchange = StubExchange(StubResponse(200), leave.wait)
        client = GoogleHomeLocalClient(StubSession(exchange).as_session())
        released_when_observed: list[bool] = []
        task = asyncio.create_task(
            client.reboot(
                HOST,
                TOKEN,
                on_response=lambda _r: released_when_observed.append(
                    exchange.exited.is_set()
                ),
            )
        )
        await exchange.exited.wait()
        assert released_when_observed == [False]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exchange.response.read_count == 0

    async def test_caller_timeout_is_not_a_library_error(self) -> None:
        """A caller's own asyncio.timeout surfaces as TimeoutError, not a package error."""
        release = asyncio.Event()
        self.device.handler = held(release)
        with pytest.raises(TimeoutError) as raised:
            async with asyncio.timeout(0.2):
                await self.client.get_alarm_volume(HOST, TOKEN)
        release.set()
        assert raised.type is TimeoutError


class InfoStatusTests(DeviceTestCase):
    """Which answers to the info request count, for ha-google-home's unanswered updates."""

    async def test_statuses(self) -> None:
        """A non-200 counts as it comes, a 200 once its whole body has come; each is observed."""
        for status, body in ((404, b""), (503, b"busy"), (200, b'{"name": "Kitchen"}')):
            with self.subTest(status):
                self.device.handler = reply(body, status=status)
                observer = Observer()
                assert (
                    await self.client.get_info_status(HOST, on_response=observer)
                    == status
                )
                assert observer.statuses == [status]

    async def test_cut_off_200_does_not_count(self) -> None:
        """A 200 whose body breaks off raises with .status 200, so it isn't counted.

        The observer still saw the 200, before the body: catches calling it only
        once the body has come.
        """
        self.device.handler = cut_off
        observer = Observer()
        with pytest.raises(ConnectionLostError) as raised:
            await self.client.get_info_status(HOST, on_response=observer)
        assert raised.value.status == 200
        assert isinstance(raised.value.__cause__, ClientPayloadError)
        assert observer.statuses == [200]

    async def test_failure_while_releasing_still_counts(self) -> None:
        """A failure while the answer is released, after it counted, still returns its status."""
        for status, error in (
            (200, ClientConnectionError("closed")),
            (404, TimeoutError()),
        ):
            with self.subTest(status):

                async def fail(error: Exception = error) -> None:
                    raise error

                exchange = StubExchange(StubResponse(status), fail)
                client = GoogleHomeLocalClient(StubSession(exchange).as_session())
                assert await client.get_info_status(HOST) == status
                # Only a 200's body is read
                assert exchange.response.read_count == int(status == 200)

    async def test_no_answer(self) -> None:
        """No answer raises with .status None."""
        client = GoogleHomeLocalClient(self.session, port=closed_port())
        with pytest.raises(CannotConnectError) as raised:
            await client.get_info_status(HOST)
        assert raised.value.status is None


class BodyTests(DeviceTestCase):
    """How a 200's body becomes a reply, with a token and without one."""

    async def test_bodies(self) -> None:
        """Token replies keep any JSON value; info replies are None unless a JSON object.

        Catches an info reply other than an object being used as info, and a token
        reply's non-JSON content type or empty body being treated as an error.
        """
        # name, body, content type, the token reply's raw, whether info is read
        cases: list[tuple[str, bytes, str, object, bool]] = [
            ("object", b'{"name": "Kitchen"}', "application/json", {"name": "Kitchen"}, True),
            ("list", b'["volume"]', "application/json", ["volume"], False),
            ("number", b"5", "application/json", 5, False),
            ("not JSON", b"<html></html>", "text/html", {}, False),
            ("empty", b"", "application/json", None, False),
            ("blank", b"  \n", "application/json", None, False),
            ("null", b"null", "application/json", None, False),
        ]  # fmt: skip
        for name, body, content_type, raw, is_info in cases:
            with self.subTest(name):
                self.device.handler = reply(body, content_type=content_type)
                volume = await self.client.get_alarm_volume(HOST, TOKEN)
                assert volume.raw == raw
                info = await self.client.get_device_info(HOST)
                assert (info is not None) == is_info

    async def test_undecodable_json(self) -> None:
        """Undecodable JSON raises InvalidJsonError with a token, and gives None without."""
        for body, cause in (
            (b'{"volume": ', json.JSONDecodeError),
            (b'{"volume": "\xff"}', UnicodeDecodeError),
        ):
            with self.subTest(cause.__name__):
                self.device.handler = reply(body)
                with pytest.raises(InvalidJsonError) as raised:
                    await self.client.get_alarm_volume(HOST, TOKEN)
                assert isinstance(raised.value.__cause__, cause)
                assert raised.value.status == 200
                assert await self.client.get_build_info(HOST) is None

    async def test_token_calls_read_the_body(self) -> None:
        """Every call with a token but a reboot reads its body, so undecodable JSON raises."""
        self.device.handler = reply(b'{"volume": ')
        for name, send, *_ in TOKEN_CALLS:
            with self.subTest(name):
                if name == "reboot":
                    await send(self.client, 5, Observer())
                    continue
                with pytest.raises(InvalidJsonError):
                    await send(self.client, 5, Observer())

    async def test_reboot_does_not_read_the_body(self) -> None:
        """A reboot returns at its 200, while the device is still sending the body."""
        release = asyncio.Event()
        self.device.handler = held(release, b"}", first=b"{")
        async with asyncio.timeout(5):
            await self.client.reboot(HOST, TOKEN)
        release.set()

    async def test_bluetooth_results(self) -> None:
        """Readable rows become sightings, others are left out; a reply that isn't a list raises."""
        self.device.handler = reply(
            b'[{"mac_address": "00:11:22:33:44:55", "rssi": -60},'
            b' {"mac_address": "66:77:88:99:AA:BB"}, "row", {"rssi": -70}]'
        )
        assert await self.client.get_bluetooth_scan_results(HOST, TOKEN) == (
            BluetoothSighting(mac_address="00:11:22:33:44:55", rssi=-60),
        )
        self.device.handler = reply(b"[{}]")
        assert await self.client.get_bluetooth_scan_results(HOST, TOKEN) == ()
        self.device.handler = reply(b"{}")
        with pytest.raises(UnexpectedResponseError) as raised:
            await self.client.get_bluetooth_scan_results(HOST, TOKEN)
        assert raised.value.body == {}

    async def test_alarms_reply_in_another_form(self) -> None:
        """An alarms reply without both lists raises UnexpectedResponseError with the reply."""
        self.device.handler = reply(b'{"alarm": []}')
        with pytest.raises(UnexpectedResponseError) as raised:
            await self.client.get_alarms_and_timers(HOST, TOKEN)
        assert raised.value.body == {"alarm": []}


class DecoderProfileTests(DeviceTestCase):
    """What the standalone profile, aiohttp's json.loads, decodes where Home Assistant's differs."""

    async def test_non_finite_volume(self) -> None:
        """NaN and infinite volumes decode here, so only percent() rejects them."""
        for body, error in (
            (b'{"volume": NaN}', ValueError),
            (b'{"volume": Infinity}', OverflowError),
            (b'{"volume": 1e999}', OverflowError),
        ):
            with self.subTest(body):
                self.device.handler = reply(body)
                volume = await self.client.get_alarm_volume(HOST, TOKEN)
                assert volume.has_volume
                with pytest.raises(error):
                    volume.percent()

    async def test_huge_status(self) -> None:
        """An alarm status of 2**64 decodes as an int here, so it's UNKNOWN."""
        self.device.handler = reply(
            b'{"alarm": [{"id": "alarm/a1", "fire_time": 1700000000000,'
            b' "status": 18446744073709551616}], "timer": []}'
        )
        alarms = await self.client.get_alarms_and_timers(HOST, TOKEN)
        assert alarms.alarms[0].status is AlarmStatus.UNKNOWN


class FailureTests(DeviceTestCase):
    """Which error each failure raises, and what it keeps."""

    async def test_classification(self) -> None:
        """Each failure raises its error class, with the status seen first and aiohttp's exception.

        Every message names the host but never the token. Catches a changed
        classification order, a lost cause or status, and a leaked token.
        """
        refused = GoogleHomeLocalClient(self.session, port=closed_port())
        never = asyncio.Event()
        # name, handler (None: nothing listens), error, its status, its cause
        cases: list[FailureCase] = [
            ("refused", None, CannotConnectError, None, ClientConnectorError),
            ("cut off", cut_off, ConnectionLostError, 200, ClientPayloadError),
            ("dropped", dropped, ConnectionLostError, None, ServerDisconnectedError),
            ("slow answer", slow, AnswerTimeoutError, None, TimeoutError),
            (
                "slow body",
                held(never, first=b"{"),
                AnswerTimeoutError,
                200,
                TimeoutError,
            ),
            (
                "redirect loop",
                redirect_loop,
                ClientRequestError,
                None,
                TooManyRedirects,
            ),
            ("401", reply(status=401), TokenRejectedError, 401, None),
            ("404", reply(status=404), NotFoundError, 404, None),
            ("500", reply(status=500), StatusError, 500, None),
            ("invalid JSON", reply(b"{"), InvalidJsonError, 200, json.JSONDecodeError),
            ("unexpected", reply(b"[]"), UnexpectedResponseError, None, None),
        ]
        for name, handler, error_class, status, cause in cases:
            with self.subTest(name):
                if handler is not None:
                    self.device.handler = handler
                client = refused if handler is None else self.client
                # Only the slow answers need a short timeout
                timeout = 0.5 if name.startswith("slow") else 5
                with pytest.raises(error_class) as raised:
                    await client.get_alarms_and_timers(HOST, TOKEN, timeout=timeout)
                error = raised.value
                assert raised.type is error_class
                assert (error.host, error.path) == (HOST, "setup/assistant/alarms")
                assert getattr(error, "status", None) == status
                if isinstance(error, StatusError):
                    # The reason aiohttp's server sends with each status
                    assert error.reason == HTTPStatus(error.status).phrase
                if cause is None:
                    assert error.__cause__ is None
                else:
                    assert isinstance(error.__cause__, cause)
                assert TOKEN not in str(error)
                assert HOST in str(error)

    async def test_server_timeout_is_a_lost_connection(self) -> None:
        """A ServerTimeoutError, also a TimeoutError, is a lost connection, as in ha-google-home.

        A per-call total timeout never gives one, so a stub session raises it.
        """
        error = ServerTimeoutError("read timed out")

        async def leave() -> None:
            pass

        exchange = StubExchange(StubResponse(200), leave, error=error)
        client = GoogleHomeLocalClient(StubSession(exchange).as_session())
        with pytest.raises(ConnectionLostError) as raised:
            await client.get_alarm_volume(HOST, TOKEN)
        assert raised.value.__cause__ is error

    async def test_failure_while_releasing_a_status(self) -> None:
        """A failure while a non-200 is released raises a RequestError that keeps the status.

        Catches keeping only a 200's status, which ha-google-home's handling of the
        status it already saw relies on.
        """

        async def fail() -> None:
            raise ClientConnectionError("closed")

        exchange = StubExchange(StubResponse(500), fail)
        client = GoogleHomeLocalClient(StubSession(exchange).as_session())
        with pytest.raises(ConnectionLostError) as raised:
            await client.get_alarm_volume(HOST, TOKEN)
        assert raised.value.status == 500

    async def test_reason_stays_out_of_the_message(self) -> None:
        """A status error keeps the device's reason, but its message has only the status.

        The reason is the device's text, and here it is the token. Catches a message
        that repeats the reason.
        """

        async def answer(request: web.Request) -> web.StreamResponse:
            return web.Response(
                status=401, reason=request.headers["cast-local-authorization-token"]
            )

        self.device.handler = answer
        with pytest.raises(TokenRejectedError) as raised:
            await self.client.get_alarm_volume(HOST, TOKEN)
        assert raised.value.reason == TOKEN
        assert str(raised.value) == (
            f"{HOST} answered setup/assistant/alarms/volume with HTTP 401"
        )

    async def test_host_that_is_not_an_address(self) -> None:
        """A host that isn't an IP address raises ValueError before anything is sent."""
        with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6"):
            await self.client.get_device_info("speaker.local")
        assert not self.device.requests


class ResendTests(DeviceTestCase):
    """Actions aren't sent twice by the client or aiohttp, except as redirects direct."""

    async def test_actions_sent_once(self) -> None:
        """An action whose connection drops once the device has it reaches the device once."""
        actions: list[tuple[str, Call]] = [
            ("delete", lambda c: c.delete_alarms_and_timers(HOST, TOKEN, ["alarm/a1"])),
            ("reboot", lambda c: c.reboot(HOST, TOKEN)),
            ("set_alarm_volume", lambda c: c.set_alarm_volume(HOST, TOKEN, 0.5)),
            ("set_do_not_disturb", lambda c: c.set_do_not_disturb(HOST, TOKEN, False)),
            ("start_bluetooth_scan", lambda c: c.start_bluetooth_scan(HOST, TOKEN, duration=30)),
        ]  # fmt: skip
        self.device.handler = dropped
        for name, send in actions:
            with self.subTest(name):
                self.device.requests.clear()
                with pytest.raises(ConnectionLostError):
                    await send(self.client)
                assert len(self.device.requests) == 1

    async def test_redirects_can_resend(self) -> None:
        """Characterisation: aiohttp follows a 307 with the same POST, and a 303 with a GET.

        One controlled single-hop case, showing that exactly once doesn't hold for
        every reply. Other targets, refused redirects and chains are aiohttp's.
        """
        for status, method, body in (
            (307, "POST", b'{"ids": ["alarm/a1"]}'),
            (303, "GET", b""),
        ):
            with self.subTest(status):

                async def redirect(
                    request: web.Request, status: int = status
                ) -> web.StreamResponse:
                    if request.path == "/setup/assistant/alarms/delete":
                        return web.Response(
                            status=status, headers={"Location": "/moved"}
                        )
                    return web.json_response({"success": True})

                self.device.requests.clear()
                self.device.handler = redirect
                result = await self.client.delete_alarms_and_timers(
                    HOST, TOKEN, ["alarm/a1"]
                )
                assert result.confirmed
                assert len(self.device.requests) == 2
                second = self.device.requests[1]
                assert (second.method, second.path_qs, second.body) == (
                    method,
                    "/moved",
                    body,
                )


class ConcurrencyTests(DeviceTestCase):
    """Concurrent calls are independent."""

    # One connection for each held answer
    connection_limit = 2

    async def test_answers_reach_their_callers(self) -> None:
        """Two held answers released in reverse order each reach their own caller."""
        releases = {"token-a": asyncio.Event(), "token-b": asyncio.Event()}
        volumes = {"token-a": b'{"volume": 0.1}', "token-b": b'{"volume": 0.2}'}
        both_sent = asyncio.Event()

        async def handle(request: web.Request) -> web.StreamResponse:
            if len(self.device.requests) == 2:
                both_sent.set()
            token = request.headers["cast-local-authorization-token"]
            await releases[token].wait()
            return web.Response(body=volumes[token], content_type="application/json")

        self.device.handler = handle
        first = asyncio.create_task(self.client.get_alarm_volume(HOST, "token-a"))
        second = asyncio.create_task(self.client.get_alarm_volume(HOST, "token-b"))
        await both_sent.wait()
        releases["token-b"].set()
        assert (await second).percent() == 20
        assert not first.done()
        releases["token-a"].set()
        assert (await first).percent() == 10
