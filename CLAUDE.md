# CLAUDE.md — ha-snapmaker-u1

Home Assistant custom integration for the Snapmaker U1 3D printer (Klipper/Moonraker), distributed via HACS. Python.

## Layout

- `custom_components/snapmaker_u1/` — the integration code
- `tests/` — pytest suite (~40 tests)
- `docs/` — documentation
- `brand/` — integration icon/logo assets
- `.github/workflows/` — CI: hassfest, HACS validation, pytest

## Running tests

- `pytest` from the repo root (config in `pytest.ini`: `testpaths = tests`, `asyncio_mode = auto`)

## Compatibility

- Minimum Home Assistant version is **2024.1.0**, declared in `hacs.json` — keep it there. hassfest rejects a minimum version in the manifest.
- Code must keep working on HA 2024.1: use `helpers.schedule_entry_reload` for entry reloads, not `async_schedule_reload` (only exists from HA 2024.2).

## Hard rules (learned the hard way — do not regress)

- Never list `aiohttp` in the manifest requirements; it ships with Home Assistant and hassfest rejects it.
- Keep the `stream` dependency declared in the manifest so the RTSP camera works on installs without `default_config`.
- A rejected API key (HTTP 401 from Moonraker) raises `ConfigEntryAuthFailed` at setup and during fallback polling, and the reauth flow asks for a new key. HTTP 403 stays an ordinary error — Moonraker uses it for problems a new key cannot fix.
- Entity commands (button/number/switch/select) raise `HomeAssistantError` when a printer command fails or the printer is not connected. Never log-and-report-success. Update entity state only after the command succeeds.

## Conventions

- Conventional commit prefixes (`fix:`, `docs:`, `chore:`) with a detailed body explaining *why*, not just what.
- Work lands through pull requests; bump the integration version in the manifest when behavior changes.
- Fixes that also apply to sibling printer integrations (e.g. ha-bambulab) are worth porting — several 1.17.0 fixes came from there.
