"""Voiceover audio artifact registration and stream inspection.

Extracts audio channel configuration, codec, and sample rate for generated
voiceovers, persisting model and tool synthesis provenance.
"""
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig
from .derivatives import register_artifact
from .media_metadata import extract
from .registry import Registry


def register_voiceover(
    cfg: WorkspaceConfig,
    registry: Registry,
    parent_id: str,
    path: Path | str,
    model: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    """Register a voiceover audio file as an auxiliary artifact linked to a parent asset."""
    meta = extract(path, cfg.root)
    audio = meta.get('audio', {})
    info = {
        'media': meta,
        'sample_rate': audio.get('sample_rate'),
        'channels': audio.get('channels'),
        'codec': audio.get('codec_name'),
    }
    return register_artifact(
        cfg,
        registry,
        parent_id,
        'voiceover',
        path,
        info,
        {'model': model, 'tool': tool},
    )
