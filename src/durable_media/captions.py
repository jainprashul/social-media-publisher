"""Subtitle and caption validation and artifact registration.

Performs syntax and temporal validation on SubRip (.srt) and WebVTT (.vtt) files,
verifying header requirements, cue ordering, positive durations, non-overlapping
monotonic intervals, and media duration boundaries.
"""
from pathlib import Path
import re
from typing import Any

from .config import WorkspaceConfig
from .derivatives import register_artifact
from .registry import Registry

_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def _ms(s: str) -> int:
    """Parse timestamp string (HH:MM:SS,mmm or HH:MM:SS.mmm) into milliseconds."""
    m = _TIME.fullmatch(s.strip())
    if not m:
        raise ValueError('invalid caption timestamp')
    return ((int(m[1]) * 60 + int(m[2])) * 60 + int(m[3])) * 1000 + int(m[4])


def validate_caption(
    path: Path | str,
    fmt: str | None = None,
    duration: float | None = None,
) -> dict[str, Any]:
    """Validate caption syntax, cue monotonicity, and media duration constraints.

    Enforces:
    - WebVTT header check: must start with 'WEBVTT'.
    - Positive cue intervals (end timestamp > start timestamp).
    - Non-empty cue text bodies.
    - Monotonic ordering: cues must not overlap or regress in time.
    - Duration bounding: cue timestamps cannot exceed media duration if specified.
    """
    with open(path, encoding='utf-8-sig') as f:
        text = f.read()
    fmt = fmt or ('vtt' if str(path).lower().endswith('.vtt') else 'srt')
    lines = text.splitlines()
    cues = []
    i = 0
    if fmt == 'vtt':
        if not lines or lines[0].strip() != 'WEBVTT':
            raise ValueError('VTT must start with WEBVTT')
        i = 1
    while i < len(lines):
        if not lines[i].strip() or '-->' not in lines[i]:
            i += 1
            continue
        if '-->' not in lines[i]:
            raise ValueError('invalid caption cue')
        left, right = [x.strip().split()[0] for x in lines[i].split('-->', 1)]
        start, end = _ms(left), _ms(right)
        i += 1
        body = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i])
            i += 1
        if end <= start or not body:
            raise ValueError('empty or non-positive caption cue')
        if cues and start < cues[-1][1]:
            raise ValueError('overlapping or non-monotonic caption cues')
        if duration is not None and end > duration * 1000:
            raise ValueError('caption cue exceeds media duration')
        cues.append((start, end, '\n'.join(body)))
    if not cues:
        raise ValueError('caption file has no cues')
    return {'format': fmt, 'cues': len(cues), 'duration_ms': cues[-1][1]}


def register_caption(
    cfg: WorkspaceConfig,
    registry: Registry,
    parent_id: str,
    path: Path | str,
) -> dict[str, Any]:
    """Validate and register a subtitle file as an auxiliary media artifact."""
    return register_artifact(
        cfg,
        registry,
        parent_id,
        'caption',
        path,
        validate_caption(path),
        {'validator': 'durable_media.captions'},
    )
