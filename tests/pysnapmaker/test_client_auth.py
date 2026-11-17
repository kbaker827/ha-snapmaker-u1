"""A rejected API key must be reported as such, not as "printer unreachable".

Moonraker answers 401 for a bad or missing API key. 403 has other causes a new
key can't fix, so it stays an ordinary error.
Before this, every failure looked like a connection problem, so Home Assistant
retried setup forever instead of asking for a new key.
"""
import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pysnapmaker import SnapmakerAuthError, SnapmakerClient
from pysnapmaker.client import (
    CONNECTION_CANNOT_CONNECT,
    CONNECTION_INVALID_AUTH,
    CONNECTION_OK,
)


def _app(status: int) -> web.Application:
    async def handler(request: web.Request) -> web.Response:
        if status == 200:
            return web.json_response({"result": {}})
        return web.json_response({"error": {"code": status}}, status=status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return app


@pytest.fixture
async def server_factory():
    servers: list[TestServer] = []

    async def make(status: int) -> TestServer:
        server = TestServer(_app(status), host="127.0.0.1")
        await server.start_server()
        servers.append(server)
        return server

    yield make
    for server in servers:
        await server.close()


@pytest.mark.parametrize("status", [401])
async def test_get_raises_auth_error_on_rejected_key(server_factory, status):
    server = await server_factory(status)
    client = SnapmakerClient("127.0.0.1", server.port, api_key="wrong")
    try:
        client._session = aiohttp.ClientSession()
        with pytest.raises(SnapmakerAuthError):
            await client._get("/server/info")
    finally:
        await client._session.close()


@pytest.mark.parametrize("status", [401])
async def test_post_raises_auth_error_on_rejected_key(server_factory, status):
    server = await server_factory(status)
    client = SnapmakerClient("127.0.0.1", server.port, api_key="wrong")
    try:
        client._session = aiohttp.ClientSession()
        with pytest.raises(SnapmakerAuthError):
            await client._post("/printer/gcode/script", {"script": "M115"})
    finally:
        await client._session.close()


async def test_async_init_raises_auth_error_and_closes_session(server_factory):
    server = await server_factory(401)
    client = SnapmakerClient("127.0.0.1", server.port, api_key="wrong")

    with pytest.raises(SnapmakerAuthError):
        await client.async_init()

    assert client._session.closed


async def test_forbidden_is_not_treated_as_a_bad_key(server_factory):
    server = await server_factory(403)
    client = SnapmakerClient("127.0.0.1", server.port, api_key="key")
    try:
        client._session = aiohttp.ClientSession()
        with pytest.raises(aiohttp.ClientResponseError):
            await client._get("/printer/objects/query")
    finally:
        await client._session.close()


async def test_async_init_returns_false_when_unreachable(server_factory):
    server = await server_factory(500)
    client = SnapmakerClient("127.0.0.1", server.port)

    assert await client.async_init() is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, CONNECTION_OK),
        (401, CONNECTION_INVALID_AUTH),
        (403, CONNECTION_CANNOT_CONNECT),
        (500, CONNECTION_CANNOT_CONNECT),
    ],
)
async def test_check_connection_classifies_response(server_factory, status, expected):
    server = await server_factory(status)

    result = await SnapmakerClient.async_check_connection("127.0.0.1", server.port, "key")

    assert result == expected


async def test_check_connection_reports_unreachable_host():
    # Nothing listens on port 9 (discard) on loopback in CI.
    result = await SnapmakerClient.async_check_connection("127.0.0.1", 9, None)

    assert result == CONNECTION_CANNOT_CONNECT


async def test_test_connection_still_returns_bool(server_factory):
    ok = await server_factory(200)
    bad = await server_factory(401)

    assert await SnapmakerClient.test_connection("127.0.0.1", ok.port) is True
    assert await SnapmakerClient.test_connection("127.0.0.1", bad.port) is False
