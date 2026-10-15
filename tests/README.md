# Connection lifecycle regression tests

Run from the repository root with Python 3.13+ and `aiohttp` / `pytest`:

```sh
uv run --python 3.13 --with aiohttp --with pytest pytest -q tests/test_client_lifecycle.py
```

The tests do not require Home Assistant or a physical printer. The connection
test starts a temporary HTTP/WebSocket server on the loopback interface.

Covered behavior:

- A failed WebSocket handshake does not trigger HTTP state requests.
- Failed and cleanly closed connections wait between retries, with exponential
  backoff capped at 60 seconds.
- Starting twice creates one background task; shutdown cancels pending requests
  and closes the owned session.
- An empty initial printer state is not accepted as a successful connection.
- Connection, capability discovery, four extruders, push-only state updates,
  disconnection notification and reconnection work against the local simulator.

These client tests do not replace Home Assistant platform tests or manual
verification of entity availability, optional entity creation and UI behavior.
