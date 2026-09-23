import argparse, json
from .config import WorkspaceConfig
from .registry import Registry
from .ingest import ingest
from .derivatives import derive
from .validation import validate
from .orchestration import prepare, publish, preview, reconcile_job
from .approval import approve, job_fingerprint
from .profiles import PROFILES
from .previews import thumbnail, contact_sheet
from .captions import register_caption
from .voiceover import register_voiceover


def main(argv=None):
    p = argparse.ArgumentParser(prog='media-pipeline')
    p.add_argument('--workspace', default='.')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init')
    x = sub.add_parser('ingest')
    x.add_argument('file')
    x.add_argument('--project', required=True)
    x.add_argument('--role', default='master')
    x.add_argument('--source', default='local')
    x.add_argument('--source-task')
    x.add_argument('--prompt')

    x = sub.add_parser('derive')
    x.add_argument('asset')
    x.add_argument('--profile', required=True)

    x = sub.add_parser('validate')
    x.add_argument('derivative')
    x = sub.add_parser('thumbnail')
    x.add_argument('derivative')
    x = sub.add_parser('contact-sheet')
    x.add_argument('derivative')

    x = sub.add_parser('caption')
    x.add_argument('action', choices=['register'])
    x.add_argument('parent')
    x.add_argument('file')

    x = sub.add_parser('voiceover')
    x.add_argument('action', choices=['register'])
    x.add_argument('parent')
    x.add_argument('file')
    x.add_argument('--model')
    x.add_argument('--tool')

    x = sub.add_parser('profile')
    x.add_argument('action', choices=['show'])
    x.add_argument('name', nargs='?')

    x = sub.add_parser('prepare')
    x.add_argument('derivative')
    x.add_argument('--platform', required=True)
    x.add_argument('--destination', required=True)
    x.add_argument('--caption', default='')
    x.add_argument('--visibility', default='public')
    x.add_argument('--scheduled-at')

    x = sub.add_parser('approve')
    x.add_argument('job')
    x.add_argument('--actor', required=True)
    x.add_argument('--fingerprint', required=True)

    x = sub.add_parser('publish')
    x.add_argument('job')
    x.add_argument('--dry-run', action='store_true')

    x = sub.add_parser('status')
    x.add_argument('job')

    x = sub.add_parser('preview')
    x.add_argument('job')

    x = sub.add_parser('reconcile')
    x.add_argument('job')
    x.add_argument('--outcome', choices=['published', 'not-published', 'retry'])
    x.add_argument('--operator')
    x.add_argument('--reason')

    x = sub.add_parser('failures')
    x.add_argument('--since')

    x = sub.add_parser('asset')
    x.add_argument('action', choices=['show', 'lineage'])
    x.add_argument('asset')

    sub.add_parser('targets')
    x = sub.add_parser('target')
    x.add_argument('action', choices=['list', 'validate'])
    x.add_argument('--platform', choices=['instagram', 'linkedin', 'youtube', 'discord', 'fake'])
    x.add_argument('--destination')
    x.add_argument('--live', action='store_true', help='Run opt-in live readiness check')
    x.add_argument('--timeout', type=float, default=5.0, help='Timeout in seconds')

    a2a_p = sub.add_parser('a2a')
    a2a_sub = a2a_p.add_subparsers(dest='a2a_cmd', required=True)
    x = a2a_sub.add_parser('ingest')
    x.add_argument('manifest')
    x.add_argument('--project')
    x = a2a_sub.add_parser('validate')
    x.add_argument('manifest')

    hermes_p = sub.add_parser('hermes')
    hermes_sub = hermes_p.add_subparsers(dest='hermes_cmd', required=True)
    x = hermes_sub.add_parser('preview')
    x.add_argument('job')
    x = hermes_sub.add_parser('approve')
    x.add_argument('job')
    x.add_argument('--actor', required=True)
    x.add_argument('--fingerprint', required=True)
    x = hermes_sub.add_parser('publish')
    x.add_argument('job')
    x.add_argument('--dry-run', action='store_true')
    x = hermes_sub.add_parser('status')
    x.add_argument('job')
    x = hermes_sub.add_parser('ingest')
    x.add_argument('manifest')
    x.add_argument('--project')

    rep_p = sub.add_parser('report')
    rep_sub = rep_p.add_subparsers(dest='report_cmd', required=True)
    x = rep_sub.add_parser('nightly')
    x.add_argument('--since')
    x.add_argument('--output')
    x.add_argument('--format', choices=['json', 'markdown'], default='json')
    x.add_argument('--lock-timeout', type=float, default=0.0)
    x.add_argument('--retention-days', type=int, default=30)

    obs_p = sub.add_parser('obsidian')
    obs_sub = obs_p.add_subparsers(dest='obsidian_cmd', required=True)
    x = obs_sub.add_parser('daily')
    x.add_argument('--date')
    x.add_argument('--vault-dir')

    # Phase 6 additions: doctor, backup, logs
    doc_p = sub.add_parser('doctor')
    doc_p.add_argument('--live', action='store_true', help='Perform opt-in live provider connectivity checks')
    doc_p.add_argument('--timeout', type=float, default=5.0, help='Timeout in seconds for readiness checks')

    bk_p = sub.add_parser('backup')
    bk_sub = bk_p.add_subparsers(dest='backup_cmd', required=True)
    x = bk_sub.add_parser('create', help='Create consistent SQLite snapshot backup')
    x.add_argument('--output', help='Destination path for backup file')
    x.add_argument('--db', help='Source database path (defaults to registry)')
    x = bk_sub.add_parser('verify', help='Verify checksum, integrity, and schema of a backup')
    x.add_argument('path', help='Path to the backup SQLite file')
    x.add_argument('--checksum-file', help='Explicit path to checksum sidecar file')
    x = bk_sub.add_parser('restore', help='Restore verified backup into target database')
    x.add_argument('path', help='Path to the backup SQLite file')
    x.add_argument('--target-db', help='Destination database path (defaults to registry)')
    x.add_argument('--force', action='store_true', help='Force overwrite of non-empty destination database')
    x.add_argument('--checksum-file', help='Explicit path to checksum sidecar file')

    log_p = sub.add_parser('logs')
    log_sub = log_p.add_subparsers(dest='logs_cmd', required=True)
    x = log_sub.add_parser('inspect', help='Inspect operational JSONL logs')
    x.add_argument('--log-file', help='Path to log file (default data/logs/events.jsonl)')
    x.add_argument('--limit', type=int, default=50, help='Maximum number of recent events to return')
    x.add_argument('--since', help='Filter events since timestamp')

    a = p.parse_args(argv)

    # For doctor and backup verify, do not proactively create or migrate workspaces
    if a.cmd == 'doctor':
        from .doctor import run_doctor
        cfg = WorkspaceConfig(a.workspace)
        out = run_doctor(cfg, live=a.live, timeout=getattr(a, 'timeout', 5.0))
        print(json.dumps(out, default=lambda o: o.to_dict() if hasattr(o, 'to_dict') else str(o), sort_keys=True, indent=2))
        return 0

    if a.cmd == 'backup':
        from .backup import create_backup, verify_backup, restore_backup
        if a.backup_cmd == 'create':
            cfg = WorkspaceConfig(a.workspace).ensure()
            out = create_backup(cfg, db_path=a.db, output_path=a.output)
        elif a.backup_cmd == 'verify':
            out = verify_backup(a.path, checksum_path=a.checksum_file)
        elif a.backup_cmd == 'restore':
            cfg = WorkspaceConfig(a.workspace)
            target = a.target_db or cfg.db_path
            out = restore_backup(a.path, target, force=a.force, checksum_path=a.checksum_file)
        print(json.dumps(out, default=lambda o: o.to_dict() if hasattr(o, 'to_dict') else str(o), sort_keys=True, indent=2))
        return 0

    if a.cmd == 'logs':
        from .observability import inspect_logs
        cfg = WorkspaceConfig(a.workspace)
        log_file = a.log_file or (cfg.root / 'data' / 'logs' / 'events.jsonl')
        out = inspect_logs(log_file, limit=a.limit, since=getattr(a, 'since', None))
        print(json.dumps(out, default=lambda o: o.to_dict() if hasattr(o, 'to_dict') else str(o), sort_keys=True, indent=2))
        return 0

    cfg = WorkspaceConfig(a.workspace).ensure()
    r = Registry(cfg.db_path)

    if a.cmd == 'init':
        out = {'workspace': str(cfg.root), 'db': str(cfg.db_path)}
    elif a.cmd == 'ingest':
        out = dict(ingest(cfg, r, a.file, a.project, a.role, a.source, a.source_task, a.prompt))
    elif a.cmd == 'derive':
        out = dict(derive(cfg, r, a.asset, a.profile))
    elif a.cmd == 'validate':
        out = validate(cfg, r, a.derivative)
    elif a.cmd == 'thumbnail':
        out = dict(thumbnail(cfg, r, a.derivative))
    elif a.cmd == 'contact-sheet':
        out = dict(contact_sheet(cfg, r, a.derivative))
    elif a.cmd == 'caption':
        out = dict(register_caption(cfg, r, a.parent, a.file))
    elif a.cmd == 'voiceover':
        out = dict(register_voiceover(cfg, r, a.parent, a.file, a.model, a.tool))
    elif a.cmd == 'profile':
        out = {k: v.to_dict() for k, v in PROFILES.items()} if not a.name else PROFILES[a.name].to_dict()
    elif a.cmd == 'prepare':
        out = dict(prepare(r, a.derivative, a.platform, a.destination, a.caption, a.visibility, a.scheduled_at))
    elif a.cmd == 'approve':
        approve(r, a.job, a.actor, a.fingerprint)
        out = dict(r.job(a.job))
    elif a.cmd == 'publish':
        out = publish(cfg, r, a.job, dry_run=a.dry_run)
    elif a.cmd == 'status':
        j = r.job(a.job)
        out = {
            'job': dict(j) if j else None,
            'approval': dict(r.approval(a.job)) if r.approval(a.job) else None,
            'receipt': dict(r.receipt(j['idempotency_key'])) if j and r.receipt(j['idempotency_key']) else None,
            'transitions': [dict(x) for x in r.transitions(a.job)],
        }
    elif a.cmd == 'asset':
        if a.action == 'show':
            out = dict(r.asset(a.asset))
        else:
            from .manifest import lineage
            out = lineage(r, a.asset)
    elif a.cmd == 'preview':
        out = preview(r, a.job)
    elif a.cmd == 'reconcile':
        out = dict(r.job(a.job)) if not a.outcome else reconcile_job(r, a.job, a.outcome, a.operator, a.reason)
    elif a.cmd == 'failures':
        query = "SELECT * FROM jobs WHERE state LIKE 'failed%' OR state='unknown_remote'"
        args = []
        if a.since:
            query += ' AND created_at >= ?'
            args.append(a.since)
        out = [dict(x) for x in r.conn.execute(query, args)]
    elif a.cmd == 'targets':
        from .adapters.registry import list_targets
        out = list_targets()
    elif a.cmd == 'target':
        from .adapters.registry import list_targets, validate_target_config
        if a.action == 'list':
            out = list_targets(live=getattr(a, 'live', False), timeout=getattr(a, 'timeout', 5.0))
        else:
            if not a.platform:
                raise ValueError('--platform is required for target validate')
            out = validate_target_config(
                a.platform,
                a.destination,
                live=getattr(a, 'live', False),
                timeout=getattr(a, 'timeout', 5.0),
            )
    elif a.cmd == 'a2a':
        from pathlib import Path
        p_man = Path(a.manifest)
        mf_path = cfg.safe(p_man if p_man.is_absolute() else cfg.root / p_man)
        if a.a2a_cmd == 'validate':
            from .a2a_manifest import A2AManifest
            mf = A2AManifest.load(mf_path)
            out = {'status': 'valid', 'manifest': mf.to_dict()}
        elif a.a2a_cmd == 'ingest':
            from .a2a_ingest import ingest_a2a_manifest
            out = ingest_a2a_manifest(cfg, r, mf_path, project=a.project)
    elif a.cmd == 'hermes':
        from .hermes import hermes_preview, hermes_approve, hermes_publish, hermes_status, hermes_ingest
        if a.hermes_cmd == 'preview':
            out = hermes_preview(r, a.job, cfg=cfg)
        elif a.hermes_cmd == 'approve':
            out = hermes_approve(r, a.job, a.actor, a.fingerprint, cfg=cfg)
        elif a.hermes_cmd == 'publish':
            out = hermes_publish(cfg, r, a.job, dry_run=a.dry_run)
        elif a.hermes_cmd == 'status':
            out = hermes_status(r, a.job, cfg=cfg)
        elif a.hermes_cmd == 'ingest':
            from pathlib import Path
            p_man = Path(a.manifest)
            mf_path = cfg.safe(p_man if p_man.is_absolute() else cfg.root / p_man)
            out = hermes_ingest(cfg, r, mf_path, project=a.project)
    elif a.cmd == 'report':
        from .scheduler import run_nightly_report
        from .reports import report_to_markdown
        if a.report_cmd == 'nightly':
            rep, path_written = run_nightly_report(
                cfg,
                r,
                output_path=a.output,
                since=a.since,
                output_format=a.format,
                lock_timeout=getattr(a, 'lock_timeout', 0.0),
                retention_days=getattr(a, 'retention_days', 30),
            )
            if a.output:
                out = {
                    'report_type': 'nightly_operational_report',
                    'output_path': str(path_written),
                    'format': a.format,
                    'summary': rep['summary'],
                }
            else:
                if a.format == 'markdown':
                    print(report_to_markdown(rep))
                    return 0
                out = rep
    elif a.cmd == 'obsidian':
        from .obsidian_links import write_daily_note
        if a.obsidian_cmd == 'daily':
            written = write_daily_note(cfg, r, date_str=a.date, vault_dir=a.vault_dir)
            out = {'status': 'success', 'daily_note_path': str(written)}

    print(json.dumps(out, default=lambda x: x.to_dict() if hasattr(x, 'to_dict') else str(x), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    main()
