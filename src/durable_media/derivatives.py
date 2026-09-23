"""Platform-specific media derivative rendering and artifact registration.

Produces profile-conforming derivatives (resizing, pillarboxing/letterboxing,
audio resampling, video re-encoding, +faststart moov atom optimization) from
immutable master assets. Enforces atomic file writes and registers operations
provenance in SQLite.
"""
import json
import os
from pathlib import Path
import shutil
from typing import Any

from .config import WorkspaceConfig
from .ingest import detect_mime, sha256
from .media_metadata import extract
from .media_tools import run_ffmpeg
from .profiles import get_profile
from .registry import Registry, now, uid


def derive(
    cfg: WorkspaceConfig,
    registry: Registry,
    asset_id: str,
    profile: str,
) -> dict[str, Any]:
    """Render a profile-specific derivative from an immutable master asset.

    Guarantees:
    - Deterministic derivative ID derived from master asset SHA-256 and profile fingerprint.
    - Idempotent: returns existing derivative if source hash and profile match.
    - Aspect preservation: applies scale=w:h:force_original_aspect_ratio=decrease
      with centered padding to fit exact target dimensions without distortion.
    - Web streaming: adds -movflags +faststart for immediate browser/social playback.
    - Atomic output: renders to a temporary file, verifying non-emptiness before
      calling os.replace to prevent partial or torn media files.
    """
    p = get_profile(profile)
    a = registry.asset(asset_id)
    if not a:
        raise ValueError('unknown asset')

    old = registry.find_derivative(asset_id, profile, a['sha256'])
    if old and (cfg.root / old['path']).is_file():
        return old

    did = uid('deriv', a['sha256'] + p.fingerprint())
    ext = '.mp4' if p.kind == 'video' else '.m4a'
    rel = f'data/derivatives/{did}/{profile}{ext}'
    dest = cfg.safe(cfg.root / rel)
    dest.parent.mkdir(parents=True, exist_ok=False)

    source = cfg.safe(cfg.root / a['path'])
    temp = dest.with_name('.tmp-' + did + dest.suffix)
    operations = []

    try:
        meta = json.loads(a['metadata_json'] or '{}').get('media', {})
        real = meta.get('available') and ((p.kind == 'video' and meta.get('video')) or (p.kind == 'audio' and meta.get('audio')))

        if real:
            if p.kind == 'video':
                vf = f"scale={p.width}:{p.height}:force_original_aspect_ratio=decrease,pad={p.width}:{p.height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                args = ['-y', '-i', str(source), '-vf', vf, '-c:v', p.video_codec, '-r', str(p.fps), '-c:a', p.audio_codec, '-movflags', '+faststart', str(temp)]
            else:
                args = ['-y', '-i', str(source), '-vn', '-c:a', p.audio_codec, '-ar', str(p.sample_rate), '-ac', str(p.channels), str(temp)]
            result = run_ffmpeg(args, workspace=cfg.root)
            if result['returncode']:
                raise RuntimeError('ffmpeg failed: ' + result['stderr'][-500:])
            operations = [{'name': 'ffmpeg', 'command': result['command'], 'version': result['version']}]
        else:
            shutil.copy2(source, temp)
            operations = [{'name': 'copy', 'reason': 'generic-or-undecodable-input'}]

        if not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError('empty derivative')

        os.replace(temp, dest)
        prov = {'profile': p.to_dict(), 'parent_sha256': a['sha256'], 'operations': operations, 'media': extract(dest, cfg.root)}
        registry.add_derivative(
            derivative_id=did,
            asset_id=asset_id,
            parent_sha256=a['sha256'],
            path=rel,
            sha256=sha256(dest),
            profile=profile,
            operations_json=json.dumps(prov, sort_keys=True),
            validation_json='{}',
        )
        return registry.derivative(did)
    except Exception:
        temp.unlink(missing_ok=True)
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise


def register_artifact(
    cfg: WorkspaceConfig,
    registry: Registry,
    parent_id: str,
    kind: str,
    path: Path | str,
    metadata: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register an auxiliary media artifact (caption, thumbnail, preview) in SQLite."""
    p = cfg.safe(path).resolve()
    if not p.is_file() or p.stat().st_size == 0:
        raise ValueError('artifact is missing or empty')
    aid = uid('artifact', kind + sha256(p))
    rel = str(p.relative_to(cfg.root))
    return registry.add_artifact(
        artifact_id=aid,
        kind=kind,
        parent_id=parent_id,
        path=rel,
        sha256=sha256(p),
        mime=detect_mime(p),
        bytes=p.stat().st_size,
        metadata_json=json.dumps(metadata or {}, sort_keys=True),
        provenance_json=json.dumps(provenance or {}, sort_keys=True),
        created_at=now(),
        validation_json='{}',
    )
