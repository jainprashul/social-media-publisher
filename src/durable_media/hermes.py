import json
from typing import Any
from .config import WorkspaceConfig
from .registry import Registry
from .orchestration import preview, publish, reconcile_job
from .approval import approve
from .observability import JsonLogger
from .a2a_ingest import ingest_a2a_manifest

def hermes_preview(registry: Registry, job_id: str, cfg: WorkspaceConfig | None = None) -> dict[str, Any]:
    res = preview(registry, job_id)
    j = res['job']
    state = j['state']
    out = {
        'job_id': job_id,
        'state': state,
        'fingerprint': res['fingerprint'],
        'approval_required': state not in ('approved', 'published'),
        'reconciliation_required': state == 'unknown_remote',
        'payload': res['payload'],
        'approval': res['approval'],
        'receipt': res['receipt'],
        'transitions': res['transitions'],
    }
    if cfg:
        logger = JsonLogger(cfg.root / 'data' / 'logs' / 'events.jsonl')
        logger.event(event_type='hermes_preview', workflow_command='hermes preview', job_id=job_id)
    return out

def hermes_approve(registry: Registry, job_id: str, actor: str, fingerprint: str, cfg: WorkspaceConfig | None = None) -> dict[str, Any]:
    approve(registry, job_id, actor, fingerprint)
    j = registry.job(job_id)
    if cfg:
        logger = JsonLogger(cfg.root / 'data' / 'logs' / 'events.jsonl')
        logger.event(event_type='hermes_approve', workflow_command='hermes approve', job_id=job_id, actor=actor, fingerprint=fingerprint)
    return dict(j)

def hermes_publish(cfg: WorkspaceConfig, registry: Registry, job_id: str, dry_run: bool = False, actor: str = 'hermes') -> dict[str, Any]:
    log_file = cfg.root / 'data' / 'logs' / 'events.jsonl'
    logger = JsonLogger(log_file)
    logger.event(
        event_type='hermes_publish',
        workflow_command='hermes publish',
        job_id=job_id,
        actor=actor,
        dry_run=dry_run,
    )
    return publish(cfg, registry, job_id, dry_run=dry_run, actor=actor)

def hermes_status(registry: Registry, job_id: str, cfg: WorkspaceConfig | None = None) -> dict[str, Any]:
    j = registry.job(job_id)
    if not j:
        raise ValueError(f'unknown job: {job_id}')
    app = registry.approval(job_id)
    rec = registry.receipt(j['idempotency_key'])
    trans = registry.transitions(job_id)

    state = j['state']
    if cfg:
        logger = JsonLogger(cfg.root / 'data' / 'logs' / 'events.jsonl')
        logger.event(event_type='hermes_status', workflow_command='hermes status', job_id=job_id)
    return {
        'job_id': job_id,
        'state': state,
        'platform': j['platform'],
        'destination': j['destination'],
        'caption': j['caption'],
        'visibility': j['visibility'],
        'scheduled_at': j['scheduled_at'],
        'idempotency_key': j['idempotency_key'],
        'fingerprint': j['approval_fingerprint'] or j['idempotency_key'],
        'approval_required': state not in ('approved', 'published', 'failed_retryable'),
        'approved': bool(app),
        'published': state == 'published',
        'reconciliation_required': state == 'unknown_remote',
        'attempt_count': int(j['attempt_count'] or 0),
        'last_error_class': j['last_error_class'],
        'receipt': dict(rec) if rec else None,
        'approval': dict(app) if app else None,
        'transitions': [dict(x) for x in trans],
    }


def hermes_ingest(cfg: WorkspaceConfig, registry: Registry, manifest_input: Any, project: str | None = None) -> dict[str, Any]:
    return ingest_a2a_manifest(cfg, registry, manifest_input, project=project)
