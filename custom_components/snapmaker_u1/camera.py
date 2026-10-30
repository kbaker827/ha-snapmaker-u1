"""Camera platform for the Snapmaker U1 integration (MJPEG / RTSP webcams)."""
from __future__ import annotations

import asyncio
import logging
import re

import aiohttp
from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_stream,
    async_get_clientsession,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .pysnapmaker.const import CAMERA_SNAPSHOT_PATH, CAMERA_STREAM_PATH
from .pysnapmaker.models import Webcam
from .coordinator import SnapmakerDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

CAMERA_CONNECT_TIMEOUT = 5
CAMERA_READ_TIMEOUT = 10
# Refresh the still image every 2 seconds when the camera card is open
CAMERA_FRAME_INTERVAL = 2.0
# Upper bound on bytes read from an MJPEG stream while looking for one frame
MJPEG_MAX_FRAME_SEARCH = 5 * 1024 * 1024

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class _CameraFetchError(Exception):
    """Raised when a webcam URL does not return a usable image."""


def _looks_like_image(content_type: str, body: bytes) -> bool:
    return content_type.startswith("image/") or body.startswith(JPEG_SOI)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a camera entity for each of the printer's cameras."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    webcams = coordinator.client.webcams if coordinator.client else []
    if not webcams:
        webcams = [Webcam("Webcam", "", "", primary=True)]
    async_add_entities(SnapmakerCamera(hass, coordinator, cam) for cam in webcams)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "camera"


class SnapmakerCamera(CoordinatorEntity[SnapmakerDataUpdateCoordinator], Camera):
    """Snapshot-based camera entity for the Snapmaker U1 webcam.

    Uses ``async_camera_image`` to fetch JPEG stills from the webcam
    discovered through Moonraker, falling back to common U1 camera paths and
    to grabbing a frame from the MJPEG stream.  The live view proxies the
    MJPEG stream directly.

    When the Extended Firmware's RTSP server is on, ``stream_source`` returns
    the RTSP URL, so Home Assistant can play (and record) the H.264 stream
    instead. Without it there is no ``stream_source``, so Home Assistant
    doesn't try HLS transcoding of the MJPEG stream.
    """

    _attr_has_entity_name = True
    _attr_frame_interval = CAMERA_FRAME_INTERVAL

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SnapmakerDataUpdateCoordinator,
        webcam: Webcam,
    ) -> None:
        # Follow the coordinator's availability (offline printer -> unavailable)
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._coordinator = coordinator
        self._hass = hass
        host = coordinator.entry.data["host"]
        self._webcam = webcam
        if webcam.primary:
            # Unchanged ID/name for the internal camera
            self._attr_unique_id = f"{host}_webcam"
            self._attr_name = "Webcam"
        else:
            self._attr_unique_id = f"{host}_webcam_{_slug(webcam.name)}"
            self._attr_name = f"Webcam {webcam.name}"
        self._attr_supported_features = (
            CameraEntityFeature.STREAM if webcam.rtsp_url else CameraEntityFeature(0)
        )
        self._client = coordinator.client
        # (kind, url) of the source that last returned an image
        self._working_source: tuple[str, str] | None = None
        self._warned_unavailable = False

    @property
    def device_info(self) -> DeviceInfo:
        host = self._coordinator.entry.data["host"]
        return DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=self._coordinator.printer_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def is_streaming(self) -> bool:
        return bool(self.available and self._coordinator.data and self._coordinator.data.is_ready)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if self._client is None:
            return {}
        attrs = {
            "snapshot_url": self._snapshot_url,
            "stream_url": self._stream_url,
        }
        if self._webcam.rtsp_url:
            attrs["rtsp_url"] = self._webcam.rtsp_url
        return attrs

    @property
    def _snapshot_url(self) -> str:
        if self._webcam.primary or not self._webcam.snapshot_url:
            return self._client.camera_snapshot_url
        return self._webcam.snapshot_url

    @property
    def _stream_url(self) -> str:
        if self._webcam.primary or not self._webcam.stream_url:
            return self._client.camera_stream_url
        return self._webcam.stream_url

    async def stream_source(self) -> str | None:
        """RTSP stream (Extended Firmware), so HA can play/record H.264."""
        if not self.available:
            return None
        return self._webcam.rtsp_url

    def _snapshot_candidates(self) -> list[tuple[str, str]]:
        """Ordered (kind, url) pairs to try when fetching a still image."""
        client = self._client
        base = client.base_url
        if self._webcam.primary:
            candidates = [
                ("snapshot", self._snapshot_url),
                ("snapshot", f"{base}{CAMERA_SNAPSHOT_PATH}"),
                ("snapshot", f"{base}/webcam/?action=snapshot"),
                ("stream", self._stream_url),
                ("stream", f"{base}{CAMERA_STREAM_PATH}"),
                ("stream", f"{base}/webcam/?action=stream"),
            ]
        else:  # e.g. a USB camera at /webcam2/: only its own URLs
            candidates = [("snapshot", self._snapshot_url), ("stream", self._stream_url)]
        # Try whatever worked last time first, then the rest in order.
        if self._working_source in candidates:
            candidates.insert(0, self._working_source)
        seen: set[tuple[str, str]] = set()
        return [c for c in candidates if not (c in seen or seen.add(c))]

    async def _fetch_snapshot(self, session: aiohttp.ClientSession, url: str) -> bytes:
        async with session.get(
            url,
            headers=self._client.headers,
            timeout=aiohttp.ClientTimeout(
                connect=CAMERA_CONNECT_TIMEOUT, total=CAMERA_READ_TIMEOUT
            ),
        ) as resp:
            if resp.status != 200:
                raise _CameraFetchError(f"HTTP {resp.status}")
            body = await resp.read()
            if not _looks_like_image(resp.content_type, body):
                raise _CameraFetchError(
                    f"not an image (content-type {resp.content_type!r})"
                )
            return body

    async def _fetch_stream_frame(
        self, session: aiohttp.ClientSession, url: str
    ) -> bytes:
        """Grab the first complete JPEG frame from an MJPEG stream.

        Reading the live stream also wakes cameras whose snapshot endpoint
        stops updating (or errors) while nothing is watching the stream.
        """
        async with session.get(
            url,
            headers=self._client.headers,
            timeout=aiohttp.ClientTimeout(
                connect=CAMERA_CONNECT_TIMEOUT, total=CAMERA_READ_TIMEOUT
            ),
        ) as resp:
            if resp.status != 200:
                raise _CameraFetchError(f"HTTP {resp.status}")
            buffer = b""
            async for chunk in resp.content.iter_chunked(65536):
                buffer += chunk
                start = buffer.find(JPEG_SOI)
                if start != -1:
                    end = buffer.find(JPEG_EOI, start + 2)
                    if end != -1:
                        return buffer[start : end + 2]
                    buffer = buffer[start:]
                if len(buffer) > MJPEG_MAX_FRAME_SEARCH:
                    break
            raise _CameraFetchError("no JPEG frame found in stream")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a single JPEG image from the webcam."""
        if not self.available or self._client is None:
            return None
        session = async_get_clientsession(self._hass)
        errors: list[str] = []
        for source in self._snapshot_candidates():
            kind, url = source
            try:
                if kind == "snapshot":
                    image = await self._fetch_snapshot(session, url)
                else:
                    image = await self._fetch_stream_frame(session, url)
            except (_CameraFetchError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                errors.append(f"{url}: {str(exc) or type(exc).__name__}")
                continue
            if source != self._working_source:
                _LOGGER.info("Snapmaker U1 webcam image source: %s", url)
                self._working_source = source
            self._warned_unavailable = False
            return image

        if not self._warned_unavailable:
            _LOGGER.warning(
                "Could not get an image from the Snapmaker U1 webcam. Make sure "
                "the printer exposes its camera over HTTP (open "
                "http://%s/webcam/ in a browser). Tried: %s",
                self._client.host,
                "; ".join(errors),
            )
            self._warned_unavailable = True
        else:
            _LOGGER.debug("Webcam image unavailable. Tried: %s", "; ".join(errors))
        self._working_source = None
        return None

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Proxy the printer's MJPEG stream for a smooth live view.

        Falls back to Home Assistant's default behaviour (a stream built from
        repeated snapshots) when no multipart MJPEG stream is available.
        """
        if not self.available or self._client is None:
            raise web.HTTPServiceUnavailable(reason="Printer is offline")
        session = async_get_clientsession(self._hass)
        stream_url = self._stream_url
        try:
            resp = await session.get(
                stream_url,
                headers=self._client.headers,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=CAMERA_CONNECT_TIMEOUT
                ),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("MJPEG stream %s unavailable: %s", stream_url, exc)
            return await super().handle_async_mjpeg_stream(request)
        try:
            content_type = resp.headers.get(aiohttp.hdrs.CONTENT_TYPE, "")
            if resp.status != 200 or not content_type.startswith("multipart/"):
                _LOGGER.debug(
                    "MJPEG stream %s returned HTTP %d (%s); using snapshots",
                    stream_url,
                    resp.status,
                    content_type,
                )
                return await super().handle_async_mjpeg_stream(request)
            return await async_aiohttp_proxy_stream(
                self._hass, request, resp.content, content_type
            )
        finally:
            resp.release()
