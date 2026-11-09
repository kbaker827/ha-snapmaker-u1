"""Pick a G-code file's preview thumbnail from Moonraker metadata."""
from __future__ import annotations

import posixpath
from typing import Any


def _area(thumb: dict[str, Any]) -> int:
    try:
        return int(thumb.get("width") or 0) * int(thumb.get("height") or 0)
    except (TypeError, ValueError):
        return 0


def pick_thumbnail(gcode_path: str, thumbnails: Any) -> str | None:
    """Path (relative to the "gcodes" root) of the largest thumbnail, or None.

    Moonraker's ``/server/files/metadata`` lists each thumbnail with a
    ``relative_path`` that is relative to the G-code file's own folder
    (usually ``.thumbs/<name>-300x300.png``). Paths that would escape the
    gcodes root are ignored.
    """
    if not gcode_path or not isinstance(thumbnails, list):
        return None
    usable = [
        t for t in thumbnails
        if isinstance(t, dict) and isinstance(t.get("relative_path"), str)
        and t["relative_path"]
    ]
    if not usable:
        return None
    best = max(usable, key=_area)
    folder = posixpath.dirname(gcode_path.lstrip("/"))
    path = posixpath.normpath(posixpath.join(folder, best["relative_path"]))
    if path.startswith(("../", "/")) or path in ("..", "."):
        return None
    return path
