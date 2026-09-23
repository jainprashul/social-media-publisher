"""Generate registered, workspace-confined preview artifacts.

Produces poster frame thumbnails and 3x3 tiled contact sheets with atomic file
replacement and robust fallback strategies for sub-second and single-frame clips.
"""
import os
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .derivatives import register_artifact
from .media_tools import run_ffmpeg
from .registry import Registry


def _make(
    cfg: WorkspaceConfig,
    registry: Registry,
    parent: str,
    path: Path | str,
    kind: str,
    args: list[str],
    fallback_args: list[str] | None = None,
) -> dict[str, Any]:
    """Execute preview generation with atomic swap and optional command fallback."""
    dest = cfg.safe(cfg.root / path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name('.tmp-' + dest.stem + dest.suffix)
    attempts = [args] + ([fallback_args] if fallback_args else [])
    errors = []
    try:
        for attempt in attempts:
            tmp.unlink(missing_ok=True)
            result = run_ffmpeg([*attempt, str(tmp)], workspace=cfg.root)
            if result['returncode']:
                errors.append(result['stderr'][-500:])
                continue
            if tmp.is_file() and tmp.stat().st_size:
                # Replace only after a complete, non-empty output is produced.
                os.replace(tmp, dest)
                return register_artifact(
                    cfg,
                    registry,
                    parent,
                    kind,
                    dest,
                    {'deterministic': True},
                    {'command': result['command'], 'version': result['version']},
                )
            errors.append('ffmpeg produced no preview frame')
        detail = errors[-1] if errors else 'ffmpeg produced no preview frame'
        raise RuntimeError('preview generation failed: ' + detail)
    finally:
        tmp.unlink(missing_ok=True)


def thumbnail(cfg: WorkspaceConfig, registry: Registry, parent: str) -> dict[str, Any]:
    """Extract a 640px-wide poster frame thumbnail from the first frame of a derivative."""
    row = registry.derivative(parent)
    src = cfg.safe(cfg.root / row['path'])
    # Seeking to t=0 is valid even for a sub-second stream; -ss 1 is not.
    return _make(
        cfg,
        registry,
        parent,
        Path('data/previews') / parent / 'thumbnail.jpg',
        'thumbnail',
        ['-y', '-ss', '0', '-i', str(src), '-frames:v', '1', '-vf', 'scale=640:-1'],
    )


def contact_sheet(cfg: WorkspaceConfig, registry: Registry, parent: str) -> dict[str, Any]:
    """Generate a 3x3 contact sheet sampled at 0.2 fps with single-frame fallback."""
    row = registry.derivative(parent)
    src = cfg.safe(cfg.root / row['path'])
    # Select the first decoded frame explicitly.  Unlike fps=1/5, this cannot
    # skip a clip shorter than the sampling interval, and still gives a valid
    # one-frame contact sheet when the source has only one frame.
    return _make(
        cfg,
        registry,
        parent,
        Path('data/previews') / parent / 'contact-sheet.jpg',
        'contact_sheet',
        ['-y', '-i', str(src), '-vf', 'fps=1/5,scale=320:-1,tile=3x3', '-frames:v', '1'],
        # A short source can yield no output at the sampling interval.  The
        # fallback explicitly decodes its first frame, guaranteeing a sheet.
        ['-y', '-i', str(src), '-vf', 'select=eq(n\\,0),scale=320:-1', '-frames:v', '1'],
    )
