"""Generate registered, workspace-confined preview artifacts."""
import os
from pathlib import Path

from .derivatives import register_artifact
from .media_tools import run_ffmpeg


def _make(cfg, registry, parent, path, kind, args, fallback_args=None):
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


def thumbnail(cfg, registry, parent):
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


def contact_sheet(cfg, registry, parent):
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
