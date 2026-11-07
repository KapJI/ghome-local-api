"""A fake device on a loopback TLS server, and a stub session for what a server can't show."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import ssl
from types import TracebackType
from typing import cast, override
from unittest import IsolatedAsyncioTestCase

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import trustme

from ghome_local_api import GoogleHomeLocalClient

# An invented token: none of the tests' requests reach a real device
TOKEN = "invented-local-token-5f2c"
HOST = "127.0.0.1"

type Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass(frozen=True)
class Received:
    """A request that the fake device received."""

    method: str
    path_qs: str
    headers: dict[str, str]
    body: bytes
    tls: bool


def reply(
    body: bytes = b"{}",
    *,
    status: int = 200,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> Handler:
    """Return a handler that answers with this status and body."""

    async def handle(_request: web.Request) -> web.StreamResponse:
        return web.Response(
            body=body,
            status=status,
            headers={"Content-Type": content_type} | (headers or {}),
        )

    return handle


def held(release: asyncio.Event, body: bytes = b"{}", *, first: bytes = b"") -> Handler:
    """Return a handler that sends 200 headers and the first bytes, then the rest once released."""

    async def handle(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        response.content_length = len(first) + len(body)
        await response.prepare(request)
        await response.write(first)
        await release.wait()
        await response.write(body)
        await response.write_eof()
        return response

    return handle


async def cut_off(request: web.Request) -> web.StreamResponse:
    """Answer 200 with part of a JSON body, then close the connection."""
    response = web.StreamResponse(headers={"Content-Type": "application/json"})
    response.content_length = 100
    await response.prepare(request)
    await response.write(b'{"alarm": [')
    assert request.transport is not None
    request.transport.close()
    return response


async def dropped(request: web.Request) -> web.StreamResponse:
    """Close the connection without answering, once the whole request has come."""
    assert request.transport is not None
    request.transport.close()
    # Never sent: the connection is closed
    return web.Response()


class FakeDevice:
    """A device's local API on a loopback TLS server with a trustme certificate.

    It records every request and answers with the handler a test sets.
    """

    def __init__(self) -> None:
        """Create the device; start() starts its server."""
        self.requests: list[Received] = []
        self.handler: Handler = reply()
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._server = TestServer(app, host=HOST)

    @property
    def port(self) -> int:
        """Return the server's port."""
        assert self._server.port is not None
        return self._server.port

    async def start(self) -> None:
        """Start the server with a certificate for its address."""
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        trustme.CA().issue_cert(HOST).configure_cert(context)
        await self._server.start_server(ssl=context)

    async def close(self) -> None:
        """Stop the server."""
        await self._server.close()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        """Record the request, then answer with the test's handler."""
        transport = request.transport
        self.requests.append(
            Received(
                request.method,
                request.path_qs,
                dict(request.headers),
                await request.read(),
                transport is not None
                and transport.get_extra_info("ssl_object") is not None,
            )
        )
        return await self.handler(request)


class DeviceTestCase(IsolatedAsyncioTestCase):
    """A test with a fake device and a client whose session accepts its certificate."""

    # At most this many connections, so one that a call leaves acquired blocks the next
    connection_limit = 1
    device: FakeDevice
    session: aiohttp.ClientSession
    client: GoogleHomeLocalClient

    @override
    async def asyncSetUp(self) -> None:
        """Start the device and open the session, closed in reverse order."""
        self.device = FakeDevice()
        await self.device.start()
        self.addAsyncCleanup(self.device.close)
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False, limit=self.connection_limit)
        )
        self.addAsyncCleanup(self.session.close)
        self.client = GoogleHomeLocalClient(self.session, port=self.device.port)


class StubResponse:
    """A response with only what the client reads, for exchanges a server can't stage."""

    def __init__(self, status: int) -> None:
        """Create a response with this status and an empty JSON object for its body."""
        self.status = status
        self.reason = "Stub"
        self.read_count = 0

    async def read(self) -> bytes:
        """Return the body."""
        self.read_count += 1
        return b"{}"

    async def json(self) -> object:
        """Return the decoded body."""
        self.read_count += 1
        return {}


class StubExchange:
    """The context of a stub response, whose exit runs the test's hook."""

    def __init__(
        self,
        response: StubResponse,
        on_exit: Callable[[], Awaitable[object]],
        *,
        error: BaseException | None = None,
    ) -> None:
        """Create the context of this response, or of a request that fails with this error."""
        self.response = response
        self.on_exit = on_exit
        self.error = error
        self.exited = asyncio.Event()

    async def __aenter__(self) -> StubResponse:
        """Return the response, or raise the error, as sending the request can."""
        if self.error is not None:
            raise self.error
        return self.response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the response, as aiohttp releases it."""
        self.exited.set()
        await self.on_exit()


class StubSession:
    """A session that answers every request with the same stub exchange."""

    def __init__(self, exchange: StubExchange) -> None:
        """Create a session that answers with this exchange."""
        self.exchange = exchange

    def get(self, *_args: object, **_kwargs: object) -> StubExchange:
        """Return the exchange of a GET."""
        return self.exchange

    def request(self, *_args: object, **_kwargs: object) -> StubExchange:
        """Return the exchange of any request."""
        return self.exchange

    def as_session(self) -> aiohttp.ClientSession:
        """Return this stub typed as the session the client takes."""
        return cast("aiohttp.ClientSession", self)
