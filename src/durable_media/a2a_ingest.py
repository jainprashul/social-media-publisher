"""Two-pass ingestion of Agent-to-Agent (A2A) manifests and multi-asset packages.

Coordinates atomic preflight validation across all declared media assets,
captions, and thumbnails prior to database ingestion. Links parent-child asset
lineage and archives an immutable manifest copy for auditability.
"""
import json
import os
from pathlib import Path
from typing import Any

from .a2a_manifest import A2AManifest, SUPPORTED_EXTENSIONS
from .captions import register_caption
from .config import WorkspaceConfig
from .derivatives import register_artifact
from .ingest import ingest
from .observability import JsonLogger
from .registry import Registry


def ingest_a2a_manifest(
    cfg: WorkspaceConfig,
    registry: Registry,
    manifest_input: Any,
    project: str | None = None,
) -> dict[str, Any]:
    """Ingest a validated A2A manifest into the durable registry.

    Execution Lifecycle:
    1. Resolve & Parse: loads manifest from Path, JSON string, dict, or instance.
    2. Pass 1 (Preflight): verifies all declared files (artifacts, captions,
       thumbnails) exist on disk, are non-empty, safe within workspace, and use
       supported extensions. If any check fails, ingestion halts before DB mutation.
    3. Pass 2 (Registration): ingests master assets, registers auxiliary artifacts
       (captions, thumbnails), links parent-child relationships, and maps IDs.
    4. Archive & Event: writes an immutable copy to manifests/a2a_{task_id}.json
       and emits a structured audit record to data/logs/events.jsonl.
    """
    # Resolve manifest
    if isinstance(manifest_input, (str, Path)):
        p = Path(manifest_input)
        if p.is_file():
            # Check safe workspace path if inside workspace
            cfg.safe(p if p.is_absolute() else cfg.root / p)
            manifest = A2AManifest.load(p if p.is_absolute() else cfg.root / p)
        else:
            # Maybe a raw JSON string
            manifest = A2AManifest.loads(str(manifest_input))
    elif isinstance(manifest_input, dict):
        manifest = A2AManifest.from_dict(manifest_input)
    elif isinstance(manifest_input, A2AManifest):
        manifest = manifest_input
    else:
        raise ValueError(f'unsupported manifest input: {type(manifest_input)}')

    effective_project = project or manifest.project
    if not effective_project:
        raise ValueError('project is required in manifest or as parameter')


    # Pass 1: Validate all artifact files and paths exist and are safe
    validated_files = []
    for art in manifest.artifacts:
        raw_p = Path(art.path)
        full_p = cfg.safe(raw_p if raw_p.is_absolute() else cfg.root / raw_p)
        if not full_p.is_file():
            raise ValueError(f'artifact file not found: {art.path}')
        if full_p.stat().st_size == 0:
            raise ValueError(f'zero-byte artifact file: {art.path}')
        ext = full_p.suffix.lower()
        if ext and ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f'unsupported artifact file extension {ext}: {art.path}')

        full_cap = None
        if art.caption_path:
            cap_p = Path(art.caption_path)
            full_cap = cfg.safe(cap_p if cap_p.is_absolute() else cfg.root / cap_p)
            if not full_cap.is_file():
                raise ValueError(f'caption file not found: {art.caption_path}')
            if full_cap.stat().st_size == 0:
                raise ValueError(f'zero-byte caption file: {art.caption_path}')

        full_thumb = None
        if art.thumbnail_path:
            tb_p = Path(art.thumbnail_path)
            full_thumb = cfg.safe(tb_p if tb_p.is_absolute() else cfg.root / tb_p)
            if not full_thumb.is_file():
                raise ValueError(f'thumbnail file not found: {art.thumbnail_path}')
            if full_thumb.stat().st_size == 0:
                raise ValueError(f'zero-byte thumbnail file: {art.thumbnail_path}')

        validated_files.append((art, full_p, full_cap, full_thumb))

    # Pass 2: Ingest files into registry
    path_to_asset = {}
    artifact_mappings = []

    # First pass: ingest masters/primary assets without parent_path
    for art, full_p, full_cap, full_thumb in validated_files:
        asset = ingest(
            cfg=cfg,
            registry=registry,
            source=full_p,
            project=effective_project,
            role=art.role or 'master',
            source_type='a2a',
            source_task=manifest.task_id,
            prompt_hash=manifest.prompt_hash,
            agent=manifest.agent,
            context_id=manifest.context_id,
            model=art.model or manifest.model,
            tool=art.tool or manifest.tool,
            original_path=art.path,
        )
        asset_row = dict(asset)
        path_to_asset[art.path] = asset_row
        path_to_asset[os.path.normpath(art.path)] = asset_row

        caption_asset_id = None
        if full_cap:
            cap_art = register_caption(cfg, registry, asset_row['asset_id'], full_cap)
            caption_asset_id = cap_art['artifact_id'] if cap_art else None

        thumbnail_asset_id = None
        if full_thumb:
            thumb_art = register_artifact(
                cfg=cfg,
                registry=registry,
                parent_id=asset_row['asset_id'],
                kind='thumbnail',
                path=full_thumb,
                metadata={'original_path': art.thumbnail_path},
                provenance={'task_id': manifest.task_id, 'agent': manifest.agent},
            )
            thumbnail_asset_id = thumb_art['artifact_id'] if thumb_art else None

        # Link parent relationship if parent_path was specified
        if art.parent_path:
            parent_norm = os.path.normpath(art.parent_path)
            parent_row = path_to_asset.get(parent_norm) or path_to_asset.get(art.parent_path)
            if parent_row:
                register_artifact(
                    cfg=cfg,
                    registry=registry,
                    parent_id=parent_row['asset_id'],
                    kind=asset_row['kind'],
                    path=full_p,
                    metadata={'role': art.role, 'original_path': art.path},
                    provenance={'task_id': manifest.task_id, 'agent': manifest.agent},
                )

        mapping = {
            'artifact_path': art.path,
            'asset_id': asset_row['asset_id'],
            'sha256': asset_row['sha256'],
            'role': art.role,
            'kind': asset_row['kind'],
            'mime': asset_row['mime'],
            'bytes': asset_row['bytes'],
            'task_id': manifest.task_id,
            'agent': manifest.agent,
            'prompt_hash': manifest.prompt_hash,
            'original_path': art.path,
            'ingested_at': asset_row['created_at'],
        }
        if caption_asset_id:
            mapping['caption_artifact_id'] = caption_asset_id
        if thumbnail_asset_id:
            mapping['thumbnail_artifact_id'] = thumbnail_asset_id

        artifact_mappings.append(mapping)

    # Persist the manifest copy
    manifest_dir = cfg.root / 'manifests'
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = manifest_dir / f'a2a_{manifest.task_id}.json'
    manifest_file.write_text(manifest.to_json(), encoding='utf-8')
    rel_manifest_path = str(manifest_file.relative_to(cfg.root))

    # Log structured event
    log_file = cfg.root / 'data' / 'logs' / 'events.jsonl'
    logger = JsonLogger(log_file)
    logger.event(
        event_type='a2a_ingest',
        workflow_command='a2a ingest',
        task_id=manifest.task_id,
        agent=manifest.agent,
        context_id=manifest.context_id,
        artifact_count=len(manifest.artifacts),
        project=effective_project,
        manifest_path=rel_manifest_path,
        asset_ids=[m['asset_id'] for m in artifact_mappings],
    )

    return {
        'task_id': manifest.task_id,
        'agent': manifest.agent,
        'project': effective_project,
        'prompt_hash': manifest.prompt_hash,
        'context_id': manifest.context_id,
        'manifest_path': rel_manifest_path,
        'artifacts': artifact_mappings,
        'asset_ids': [m['asset_id'] for m in artifact_mappings],
    }
