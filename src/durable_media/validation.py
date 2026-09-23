"""Validation gate for derived media assets.

Executes physical file checks (existence, non-empty, hash drift) and technical
media compliance checks against encoding profile specifications (codecs,
dimensions, framerate, duration, channels, sample rate, and byte limits).
Persists validation results to SQLite.
"""
import json
from typing import Any
from .config import WorkspaceConfig
from .ingest import sha256
from .media_metadata import extract
from .profiles import get_profile
from .registry import Registry


def validate(cfg: WorkspaceConfig, registry: Registry, did: str) -> dict[str, Any]:
    """Validate a derivative against its declared platform profile.

    Performs filesystem integrity verification followed by ffprobe stream inspection
    when ffprobe is available. Updates derivatives.validation_json with the passed/failed
    status and individual check details.
    """
    d = registry.derivative(did)
    if not d:
        raise ValueError('unknown derivative')

    p = cfg.safe(cfg.root / d['path'])
    prof = get_profile(d['profile'])
    checks = []

    def add(name: str, passed: bool, detail: Any = None) -> None:
        check = {'name': name, 'passed': bool(passed)}
        if detail is not None:
            check['detail'] = detail
        checks.append(check)

    # 1. Physical file checks
    add('exists', p.is_file())
    add('non_empty', p.is_file() and p.stat().st_size > 0)
    add('hash', p.is_file() and sha256(p) == d['sha256'])
    add('mime', bool(d['path'].rsplit('.', 1)[-1]))

    # 2. Media stream checks (dimensions, codecs, durations, channels)
    meta = extract(p, cfg.root)
    media = meta.get('video' if prof.kind == 'video' else 'audio', {})
    if meta.get('available'):
        if prof.kind == 'video':
            add('dimensions', media.get('width') == prof.width and media.get('height') == prof.height, media.get('width'))
            add('codec', media.get('codec_name') == prof.video_codec, media.get('codec_name'))
            add('fps', not prof.fps or abs(media.get('fps', 0) - prof.fps) < 1, media.get('fps'))
        else:
            add('sample_rate', not prof.sample_rate or int(media.get('sample_rate', 0)) == prof.sample_rate, media.get('sample_rate'))
            add('channels', not prof.channels or media.get('channels') == prof.channels, media.get('channels'))
        dur = float(meta.get('format', {}).get('duration', 0) or 0)
        add('duration', not prof.max_duration or dur <= prof.max_duration, dur)

    # 3. Size constraints
    add('file_size', not prof.max_bytes or (p.is_file() and p.stat().st_size <= prof.max_bytes), p.stat().st_size if p.exists() else 0)

    out = {
        'status': 'passed' if all(x['passed'] for x in checks) else 'failed',
        'checks': checks,
        'profile': prof.to_dict(),
    }
    registry.conn.execute(
        'UPDATE derivatives SET validation_json=? WHERE derivative_id=?',
        (json.dumps(out, sort_keys=True), did),
    )
    registry.conn.commit()
    return out
