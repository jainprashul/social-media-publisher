"""Technical media metadata extraction via ffprobe.

Extracts container format, video/audio stream properties, codecs, dimensions,
bitrates, and framerates, degrading gracefully when ffprobe is unavailable.
"""
import json
from pathlib import Path
from typing import Any

from .media_tools import run_ffprobe


def extract(path: Path | str, workspace: Path | str | None = None) -> dict[str, Any]:
    """Inspect media file streams and container format via ffprobe.

    Calculates exact FPS from fractional r_frame_rate strings, indexes primary
    video and audio streams, and returns available=False if ffprobe is uninstalled
    or decoding fails.
    """
    try:
        raw = run_ffprobe(path, workspace=workspace)
    except (RuntimeError, ValueError, OSError):
        return {"available": False, "streams": [], "format": {}}

    streams = []
    for s in raw["data"].get("streams", []):
        item = {
            k: s[k]
            for k in ("index", "codec_name", "codec_type", "width", "height", "duration", "bit_rate", "sample_rate", "channels", "r_frame_rate")
            if k in s
        }
        if s.get("codec_type") == "video" and s.get("r_frame_rate"):
            n, d = (s["r_frame_rate"].split("/") + ["1"])[:2]
            try:
                item["fps"] = round(float(n) / float(d), 3)
            except (ValueError, ZeroDivisionError):
                pass
        streams.append(item)

    f = raw["data"].get("format", {})
    meta: dict[str, Any] = {
        "available": True,
        "streams": streams,
        "format": {k: f[k] for k in ("format_name", "duration", "size", "bit_rate", "tags") if k in f},
    }
    for s in streams:
        if s.get("codec_type") == "video":
            meta["video"] = s
        if s.get("codec_type") == "audio":
            meta["audio"] = s
    return meta


def extract_media_metadata(path: Path | str, workspace: Path | str | None = None) -> dict[str, Any]:
    """Convenience alias for extract()."""
    return extract(path, workspace)


def dumps(meta: dict[str, Any]) -> str:
    """Serialize metadata to canonical JSON."""
    return json.dumps(meta, sort_keys=True, separators=(",", ":"))
