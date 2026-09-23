"""Master asset ingestion and provenance recording.

Enforces master artifact immutability (write-protected chmod 0o440), computes
cryptographic checksums, performs magic-byte MIME identification, and records
full source generation context (prompts, task IDs, agent provenance, models).
"""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
from typing import Any

from .config import WorkspaceConfig
from .registry import Registry, now, uid

# Magic byte signatures for accurate container and format identification
MAGIC = {
    b'\x89PNG': 'image/png',
    b'\xff\xd8\xff': 'image/jpeg',
    b'RIFF': 'image/x-riff',
    b'\x1aE\xdf\xa3': 'video/webm',
}


def sha256(path: Path | str) -> str:
    """Compute streaming SHA-256 digest of file content in 1MB chunks."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def detect_mime(path: Path | str) -> str:
    """Identify MIME type using file magic bytes with mimetypes fallback."""
    with open(path, 'rb') as f:
        head = f.read(16)
    for magic, mime in MAGIC.items():
        if head.startswith(magic):
            return mime
    return mimetypes.guess_type(path)[0] or 'application/octet-stream'


def ingest(
    cfg: WorkspaceConfig,
    registry: Registry,
    source: Path | str,
    project: str,
    role: str = 'master',
    source_type: str = 'local',
    source_task: str | None = None,
    prompt: str | None = None,
    prompt_hash: str | None = None,
    agent: str | None = None,
    context_id: str | None = None,
    model: str | None = None,
    tool: str | None = None,
    original_path: str | None = None,
) -> dict[str, Any]:
    """Ingest external or generated media into the durable workspace.

    Business Rules & Immutability:
    1. Validates that source exists, is readable, and non-empty (>0 bytes).
    2. Hashes content and derives a deterministic asset ID scoped to project and SHA-256.
    3. Deduplicates: if asset ID already exists and master file is intact, reuses it.
    4. Copies master into workspace and marks it read-only (chmod 0o440) to guarantee
       immutability. Derivatives are created from this master but never edit it.
    5. Extracts media stream properties and persists full generation provenance.
    """
    p = Path(source).expanduser().resolve()
    if not p.is_file() or not os.access(p, os.R_OK):
        raise ValueError('source is missing or unreadable')
    if p.stat().st_size == 0:
        raise ValueError('zero-byte source')

    digest = sha256(p)
    aid = uid('asset', digest + project)

    existing = registry.asset(aid)
    if existing and (cfg.root / existing['path']).is_file():
        return existing

    ext = p.suffix.lower() or '.bin'
    rel = f'data/assets/{aid}/master{ext}'
    dest = cfg.safe(cfg.root / rel)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Copy and protect master against accidental in-place mutation
    shutil.copy2(p, dest)
    os.chmod(dest, 0o440)

    mime = detect_mime(dest)
    kind = mime.split('/')[0] if '/' in mime else 'file'

    if prompt_hash is None and prompt:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

    src = {'type': source_type, 'task_id': source_task, 'prompt_hash': prompt_hash}
    if agent:
        src['agent'] = agent
    if context_id:
        src['context_id'] = context_id
    if model:
        src['model'] = model
    if tool:
        src['tool'] = tool

    from .media_metadata import extract
    media = {
        'original_name': p.name,
        'original_path': original_path or str(p),
        'extension': ext,
        'media': extract(dest, cfg.root),
    }
    if source_type == 'a2a':
        media['a2a'] = {
            'task_id': source_task,
            'agent': agent,
            'prompt_hash': prompt_hash,
            'original_path': original_path or str(p),
        }

    registry.add_asset(
        asset_id=aid,
        project=project,
        kind=kind,
        role=role,
        path=rel,
        sha256=digest,
        mime=mime,
        bytes=dest.stat().st_size,
        created_at=now(),
        source_json=json.dumps(src),
        metadata_json=json.dumps(media),
    )
    return registry.asset(aid)
