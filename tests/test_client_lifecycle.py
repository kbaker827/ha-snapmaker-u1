"""Connection lifecycle tests without Home Assistant or a physical printer."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components/snapmaker_u1"))
from pysnapmaker.client import SnapmakerClient  # noqa: E402


def test_failed_socket_handshake_does_not_send_http():
    async def scenario():
        class OfflineSocket:
            async def __aenter__(self):
                raise OSError("Printer is off")

            async def __aexit__(self, *args):
                return False

        client = SnapmakerClient("unused.invalid")
        client._session = SimpleNamespace(ws_connect=lambda *a, **kw: OfflineSocket())
        client._get = AsyncMock(side_effect=AssertionError("No offline HTTP"))
        with pytest.raises(OSError):
            await client._ws_connect()
        client._get.assert_not_called()
        assert not client.connected

    asyncio.run(scenario())


@pytest.mark.parametrize("clean_close", [False, True])
def test_offline_backoff_without_http(monkeypatch, clean_close):
    async def scenario():
        client = SnapmakerClient("unused.invalid")
        client._get = AsyncMock(side_effect=AssertionError("No offline HTTP"))
        delays = []

        async def connect():
            if not clean_close:
                raise OSError("Printer is off")

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 7:
                client._should_reconnect = False

        client._ws_connect = connect
        monkeypatch.setattr(asyncio, "sleep", sleep)
        await client._ws_loop()
        assert delays == [5, 10, 20, 40, 60, 60, 60]
        client._get.assert_not_called()
        assert not client.connected

    asyncio.run(scenario())


def test_start_is_idempotent_and_stop_closes_resources():
    async def scenario():
        client = SnapmakerClient("unused.invalid")
        entered = asyncio.Event()

        async def connect():
            entered.set()
            await asyncio.Event().wait()

        client._ws_connect = connect
        await client.async_start()
        original_task = client._ws_task
        await client.async_start()
        assert client._ws_task is original_task
        await entered.wait()
        pending = asyncio.get_running_loop().create_future()
        client._pending_requests[1] = pending
        await client.async_stop()
        assert original_task.done()
        assert client._session.closed
        assert pending.cancelled()
        assert not client.connected
        await client.async_stop()

    asyncio.run(scenario())


def test_empty_initial_state_is_rejected():
    async def scenario():
        client = SnapmakerClient("unused.invalid")
        client._get = AsyncMock(return_value={"result": {"status": {}}})
        with pytest.raises(ValueError, match="state snapshot"):
            await client.fetch_state()
        assert not client.connected

    asyncio.run(scenario())


def test_websocket_discovery_push_disconnect_and_reconnect():
    """Use a real local HTTP/WS server; never contact a physical printer."""
    async def scenario():
        sockets = []
        requests = []
        first_online = asyncio.Event()
        first_offline = asyncio.Event()
        second_online = asyncio.Event()
        pushed = asyncio.Event()
        state = {
            "webhooks": {"state": "ready"},
            "print_stats": {"state": "standby"},
            **{
                ("extruder" if i == 0 else f"extruder{i}"): {"temperature": 20 + i}
                for i in range(4)
            },
        }

        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            sockets.append(ws)
            async for message in ws:
                payload = message.json()
                await ws.send_json({"id": payload["id"], "result": {"status": state}})
            return ws

        async def http(request):
            requests.append(request.path)
            results = {
                "/printer/info": {"hostname": "test-printer", "software_version": "test"},
                "/printer/objects/list": {
                    "objects": ["filament_motion_sensor e0_filament", "temperature_sensor chamber"]
                },
                "/server/webcams/list": {"webcams": []},
                "/printer/objects/query": {"status": state},
                "/server/files/list": [],
            }
            assert request.path in results
            return web.json_response({"result": results[request.path]})

        app = web.Application()
        app.router.add_get("/websocket", websocket)
        app.router.add_get("/{path:.*}", http)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        client = SnapmakerClient("127.0.0.1", port=port)
        client._reconnect_delay = 0.01
        client._max_reconnect_delay = 0.02
        had_online = False

        async def changed(data):
            nonlocal had_online
            if client.connected:
                had_online = True
                first_online.set()
                if len(sockets) >= 2:
                    second_online.set()
                if data.extruders.get("extruder").temperature == 42:
                    pushed.set()
            elif had_online:
                first_offline.set()

        client.register_callback(changed)
        try:
            await client.async_start()
            await asyncio.wait_for(first_online.wait(), 3)
            assert client.data.extruder_count == 4
            assert client.data.printer_name == "test-printer"
            assert client.filament_sensor_keys == ["filament_motion_sensor e0_filament"]
            assert client.temp_sensor_keys == ["temperature_sensor chamber"]
            initial_request_count = len(requests)
            await sockets[0].send_json({
                "method": "notify_status_update",
                "params": [{"extruder": {"temperature": 42}}, 1.0],
            })
            await asyncio.wait_for(pushed.wait(), 3)
            await asyncio.sleep(0.05)
            assert len(requests) == initial_request_count
            await sockets[0].close()
            await asyncio.wait_for(first_offline.wait(), 3)
            await asyncio.wait_for(second_online.wait(), 3)
            assert len(requests) > initial_request_count
        finally:
            await client.async_stop()
            await runner.cleanup()
        assert not client.connected
        assert client._session.closed

    asyncio.run(scenario())
