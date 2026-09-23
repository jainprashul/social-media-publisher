import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import WorkspaceConfig
from .registry import Registry
from .ingest import sha256
from .security import redact
from .obsidian_links import AI_OWNED_MARKER

def generate_nightly_report(cfg: WorkspaceConfig, registry: Registry, since: str | None = None) -> dict[str, Any]:
    # 1. Unpublished validated derivatives
    deriv_query = """
        SELECT d.derivative_id, d.asset_id, d.path, d.sha256, d.profile, d.validation_json, a.project
        FROM derivatives d
        JOIN assets a ON d.asset_id = a.asset_id
        ORDER BY d.derivative_id
    """
    all_derivs = registry.conn.execute(deriv_query).fetchall()
    unpublished_validated = []
    for d in all_derivs:
        v_json = d['validation_json']
        if not v_json:
            continue
        try:
            val = json.loads(v_json)
        except Exception:
            continue
        if val.get('status') == 'passed':
            # Check if any published job exists for this derivative
            pub_count = registry.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE derivative_id=? AND state='published'",
                (d['derivative_id'],)
            ).fetchone()[0]
            if pub_count == 0:
                unpublished_validated.append({
                    'derivative_id': d['derivative_id'],
                    'asset_id': d['asset_id'],
                    'project': d['project'],
                    'profile': d['profile'],
                    'path': d['path'],
                    'sha256': d['sha256'],
                })

    # 2. Failed jobs grouped by platform and error class
    fail_query = "SELECT * FROM jobs WHERE state IN ('failed_retryable', 'failed_permanent')"
    fail_args = []
    if since:
        fail_query += " AND created_at >= ?"
        fail_args.append(since)
    fail_query += " ORDER BY platform, last_error_class, job_id"
    failed_rows = registry.conn.execute(fail_query, fail_args).fetchall()

    failed_by_group = {}
    for j in failed_rows:
        platform = j['platform'] or 'unknown'
        err_class = j['last_error_class'] or 'unspecified_error'
        key = f"{platform}::{err_class}"
        if key not in failed_by_group:
            failed_by_group[key] = {
                'platform': platform,
                'error_class': err_class,
                'count': 0,
                'jobs': [],
            }
        failed_by_group[key]['count'] += 1
        failed_by_group[key]['jobs'].append({
            'job_id': j['job_id'],
            'derivative_id': j['derivative_id'],
            'state': j['state'],
            'destination': j['destination'],
            'attempt_count': int(j['attempt_count'] or 0),
            'next_attempt_at': j['next_attempt_at'],
            'created_at': j['created_at'],
        })

    failed_grouped = [failed_by_group[k] for k in sorted(failed_by_group.keys())]

    # 3. Unknown remote states
    unk_query = "SELECT * FROM jobs WHERE state = 'unknown_remote'"
    unk_args = []
    if since:
        unk_query += " AND created_at >= ?"
        unk_args.append(since)
    unk_query += " ORDER BY created_at, job_id"
    unknown_rows = registry.conn.execute(unk_query, unk_args).fetchall()
    unknown_jobs = [
        {
            'job_id': r['job_id'],
            'derivative_id': r['derivative_id'],
            'platform': r['platform'],
            'destination': r['destination'],
            'attempt_count': int(r['attempt_count'] or 0),
            'last_error_class': r['last_error_class'],
            'created_at': r['created_at'],
        }
        for r in unknown_rows
    ]

    # 4. Published jobs missing verification
    pub_query = "SELECT * FROM jobs WHERE state = 'published'"
    pub_args = []
    if since:
        pub_query += " AND created_at >= ?"
        pub_args.append(since)
    pub_query += " ORDER BY created_at, job_id"
    pub_rows = registry.conn.execute(pub_query, pub_args).fetchall()
    unverified_published = []
    for r in pub_rows:
        rec = registry.receipt(r['idempotency_key'])
        if not rec or not rec['verified'] or not rec['external_id'] or not rec['url']:
            unverified_published.append({
                'job_id': r['job_id'],
                'derivative_id': r['derivative_id'],
                'platform': r['platform'],
                'destination': r['destination'],
                'idempotency_key': r['idempotency_key'],
                'has_receipt': bool(rec),
                'verified': bool(rec['verified']) if rec else False,
            })

    # 5. Orphan and drift records
    drift_records = []
    # Check assets
    for dr in registry.consistency(cfg.root):
        drift_records.append({'target': 'asset', 'id': dr['asset_id'], 'reason': dr['reason']})
    # Check derivatives
    for d in all_derivs:
        dp = cfg.root / d['path']
        if not dp.exists():
            drift_records.append({'target': 'derivative', 'id': d['derivative_id'], 'reason': 'missing'})
        else:
            try:
                if sha256(dp) != d['sha256']:
                    drift_records.append({'target': 'derivative', 'id': d['derivative_id'], 'reason': 'hash_drift'})
            except Exception:
                drift_records.append({'target': 'derivative', 'id': d['derivative_id'], 'reason': 'unreadable'})

    drift_records.sort(key=lambda x: (x['target'], x['id'], x['reason']))

    # 6. Recent receipts
    rec_query = "SELECT * FROM receipts"
    rec_args = []
    if since:
        rec_query += " WHERE created_at >= ? OR verified_at >= ?"
        rec_args.extend([since, since])
    rec_query += " ORDER BY created_at DESC, receipt_id DESC"
    receipt_rows = registry.conn.execute(rec_query, rec_args).fetchall()
    recent_receipts = [
        {
            'receipt_id': r['receipt_id'],
            'job_id': r['job_id'],
            'platform': r['platform'],
            'account': r['account'],
            'external_id': r['external_id'],
            'url': r['url'],
            'verified': bool(r['verified']),
            'verified_at': r['verified_at'] or r['created_at'],
        }
        for r in receipt_rows
    ]

    report = {
        'report_type': 'nightly_operational_report',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'since': since,
        'summary': {
            'unpublished_validated_derivatives_count': len(unpublished_validated),
            'failed_jobs_count': len(failed_rows),
            'unknown_remote_jobs_count': len(unknown_jobs),
            'unverified_published_jobs_count': len(unverified_published),
            'drift_records_count': len(drift_records),
            'recent_receipts_count': len(recent_receipts),
        },
        'unpublished_validated_derivatives': unpublished_validated,
        'failed_jobs': failed_grouped,
        'unknown_remote_jobs': unknown_jobs,
        'unverified_published_jobs': unverified_published,
        'orphan_and_drift_records': drift_records,
        'recent_receipts': recent_receipts,
    }

    return redact(report)

def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(redact(report), sort_keys=True, indent=2) + "\n"

def report_to_markdown(report: dict[str, Any]) -> str:
    safe = redact(report)
    gen = safe.get('generated_at', '')
    since = safe.get('since') or 'all records'
    summary = safe.get('summary', {})

    lines = [
        AI_OWNED_MARKER,
        "",
        "# Durable Media Operational Nightly Report",
        "",
        f"- **Generated At**: `{gen}`",
        f"- **Filter Since**: `{since}`",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | --- |",
        f"| Unpublished Validated Derivatives | {summary.get('unpublished_validated_derivatives_count', 0)} |",
        f"| Failed Jobs | {summary.get('failed_jobs_count', 0)} |",
        f"| Unknown Remote Jobs | {summary.get('unknown_remote_jobs_count', 0)} |",
        f"| Unverified Published Jobs | {summary.get('unverified_published_jobs_count', 0)} |",
        f"| Orphan & Drift Records | {summary.get('drift_records_count', 0)} |",
        f"| Recent Receipts | {summary.get('recent_receipts_count', 0)} |",
        "",
        "## 1. Unpublished Validated Derivatives",
        "",
    ]

    derivs = safe.get('unpublished_validated_derivatives', [])
    if derivs:
        lines.append("| Derivative ID | Project | Profile | SHA-256 |")
        lines.append("| --- | --- | --- | --- |")
        for d in derivs:
            sha = (d.get('sha256') or '')[:12]
            lines.append(f"| `{d['derivative_id']}` | {d.get('project')} | {d.get('profile')} | `{sha}` |")
    else:
        lines.append("*No unpublished validated derivatives found.*")

    lines.extend([
        "",
        "## 2. Failed Jobs by Platform and Error Class",
        "",
    ])
    failed = safe.get('failed_jobs', [])
    if failed:
        for group in failed:
            lines.append(f"### {group['platform']} — {group['error_class']} ({group['count']} jobs)")
            for j in group.get('jobs', []):
                lines.append(f"- Job `{j['job_id']}` (`{j['state']}`): dest=`{j['destination']}`, attempts={j['attempt_count']}")
    else:
        lines.append("*No failed jobs recorded.*")

    lines.extend([
        "",
        "## 3. Unknown Remote States (Reconciliation Required)",
        "",
    ])
    unknowns = safe.get('unknown_remote_jobs', [])
    if unknowns:
        for u in unknowns:
            lines.append(f"- Job `{u['job_id']}` on `{u['platform']}` to `{u['destination']}` (attempts={u['attempt_count']})")
    else:
        lines.append("*No jobs in unknown remote state.*")

    lines.extend([
        "",
        "## 4. Published Jobs Missing Verification",
        "",
    ])
    unverified = safe.get('unverified_published_jobs', [])
    if unverified:
        for uv in unverified:
            lines.append(f"- Job `{uv['job_id']}` on `{uv['platform']}` missing verified receipt")
    else:
        lines.append("*All published jobs have verified receipts.*")

    lines.extend([
        "",
        "## 5. Orphan and Drift Records",
        "",
    ])
    drifts = safe.get('orphan_and_drift_records', [])
    if drifts:
        for dr in drifts:
            lines.append(f"- {dr['target'].capitalize()} `{dr['id']}`: `{dr['reason']}`")
    else:
        lines.append("*No file drift or orphan records detected.*")

    lines.extend([
        "",
        "## 6. Recent Receipts",
        "",
    ])
    receipts = safe.get('recent_receipts', [])
    if receipts:
        lines.append("| Receipt ID | Platform | Destination | Post URL | Verified At |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in receipts:
            lines.append(f"| `{r['receipt_id']}` | {r.get('platform')} | {r.get('account')} | [{r.get('external_id')}]({r.get('url')}) | {r.get('verified_at')} |")
    else:
        lines.append("*No receipts recorded.*")

    lines.append("")
    return '\n'.join(lines)

def write_nightly_report(
    cfg: WorkspaceConfig,
    registry: Registry,
    output_path: Path | str | None = None,
    since: str | None = None,
    output_format: str = 'json',
) -> tuple[dict[str, Any], Path | None]:
    rep = generate_nightly_report(cfg, registry, since=since)
    content = report_to_markdown(rep) if output_format == 'markdown' else report_to_json(rep)

    if output_path:
        out_p = Path(output_path).resolve()
        # Ensure parent exists
        out_p.parent.mkdir(parents=True, exist_ok=True)
        # Check human protection
        if out_p.exists():
            first_chunk = out_p.read_text(encoding='utf-8', errors='ignore')[:256]
            if AI_OWNED_MARKER not in first_chunk and output_format == 'markdown':
                raise PermissionError(f'Target file {out_p} exists and is not AI-owned. Refusing to overwrite human note.')
        out_p.write_text(content, encoding='utf-8')
        return rep, out_p

    return rep, None
