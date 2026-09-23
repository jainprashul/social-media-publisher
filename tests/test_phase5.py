import json, os, subprocess, sys
from pathlib import Path
import pytest

from durable_media.config import WorkspaceConfig
from durable_media.registry import Registry
from durable_media.a2a_manifest import A2AManifest, A2AArtifact, validate_prompt_hash
from durable_media.a2a_ingest import ingest_a2a_manifest
from durable_media.hermes import hermes_preview, hermes_approve, hermes_publish, hermes_status, hermes_ingest
from durable_media.obsidian_links import (
    generate_manifest_link_block, generate_receipt_link_block,
    write_daily_note, AI_OWNED_MARKER
)
from durable_media.reports import generate_nightly_report, report_to_markdown, report_to_json, write_nightly_report
from durable_media.security import redact, is_secret_path
from durable_media.derivatives import derive
from durable_media.validation import validate
from durable_media.orchestration import prepare
from durable_media.adapters.fake import FakePublisher

def run_cli(workspace, *args):
    env = {**os.environ, 'PYTHONPATH': 'src'}
    result = subprocess.run(
        [sys.executable, '-m', 'durable_media.cli', '--workspace', str(workspace), *args],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------
# Task 1: Manifest Schema Tests
# ---------------------------------------------------------

def test_manifest_validation_valid():
    m = A2AManifest(
        task_id='task-123',
        agent='agent-alpha',
        project='demo-proj',
        prompt_hash='a' * 64,
        context_id='ctx-1',
        artifacts=[
            A2AArtifact(path='media/video.mp4', role='master'),
            A2AArtifact(path='media/audio.m4a', role='voiceover', parent_path='media/video.mp4')
        ]
    )
    data = m.to_dict()
    assert data['task_id'] == 'task-123'
    assert data['agent'] == 'agent-alpha'
    assert len(data['artifacts']) == 2
    json_str = m.to_json()
    loaded = A2AManifest.loads(json_str)
    assert loaded.task_id == 'task-123'
    assert loaded.prompt_hash == 'a' * 64


def test_manifest_prompt_hash_optional():
    m = A2AManifest(
        task_id='task-none',
        agent='agent-alpha',
        prompt_hash=None,
        artifacts=[A2AArtifact(path='media/img.png')]
    )
    assert m.prompt_hash is None


def test_manifest_missing_required_fields():
    with pytest.raises(ValueError, match='task_id is required'):
        A2AManifest(task_id='', agent='agent-1', artifacts=[A2AArtifact(path='x.png')])

    with pytest.raises(ValueError, match='agent is required'):
        A2AManifest(task_id='t-1', agent='', artifacts=[A2AArtifact(path='x.png')])

    with pytest.raises(ValueError, match='artifacts must contain at least one'):
        A2AManifest(task_id='t-1', agent='a-1', artifacts=[])


def test_manifest_malformed_prompt_hash():
    with pytest.raises(ValueError, match='malformed prompt_hash'):
        validate_prompt_hash('not-a-hash')

    with pytest.raises(ValueError, match='malformed prompt_hash'):
        validate_prompt_hash('a' * 63)  # too short

    with pytest.raises(ValueError, match='malformed prompt_hash'):
        validate_prompt_hash('z' * 64)  # non-hex character


def test_manifest_duplicate_artifacts_rejected():
    with pytest.raises(ValueError, match='duplicate artifact path'):
        A2AManifest(
            task_id='t-1',
            agent='a-1',
            artifacts=[
                A2AArtifact(path='media/clip.mp4'),
                A2AArtifact(path='media/clip.mp4')
            ]
        )


def test_manifest_secret_path_rejected():
    with pytest.raises(ValueError, match='secret-bearing path rejected'):
        A2AArtifact(path='keys/private.key')

    with pytest.raises(ValueError, match='secret-bearing path rejected'):
        A2AArtifact(path='configs/.env')

    with pytest.raises(ValueError, match='secret-bearing path rejected'):
        A2AArtifact(path='clip.mp4', caption_path='secrets/token.txt')

    with pytest.raises(ValueError, match='workspace escape detected'):
        A2AArtifact(path='../escaped.mp4')


def test_manifest_unsupported_extension_rejected():
    with pytest.raises(ValueError, match='unsupported file extension'):
        A2AArtifact(path='script.sh')

    with pytest.raises(ValueError, match='unsupported file extension'):
        A2AArtifact(path='binary.exe')


# ---------------------------------------------------------
# Task 2: Ingestion Service Tests
# ---------------------------------------------------------

def test_a2a_ingest_service_success(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    media = tmp_path / 'projects' / 'launch' / 'reel.mp4'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'RIFF test media content for video')

    cap = tmp_path / 'projects' / 'launch' / 'reel.vtt'
    cap.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world\n")

    manifest_dict = {
        'task_id': 'task-a2a-001',
        'agent': 'agent-video-gen',
        'project': 'launch-campaign',
        'prompt_hash': 'b' * 64,
        'context_id': 'ctx-run-99',
        'artifacts': [
            {
                'path': 'projects/launch/reel.mp4',
                'caption_path': 'projects/launch/reel.vtt',
                'role': 'master',
                'model': 'model-x',
                'tool': 'tool-y'
            }
        ]
    }

    result = ingest_a2a_manifest(cfg, registry, manifest_dict)
    assert result['task_id'] == 'task-a2a-001'
    assert result['agent'] == 'agent-video-gen'
    assert len(result['artifacts']) == 1
    assert result['manifest_path'] == 'manifests/a2a_task-a2a-001.json'
    assert (tmp_path / result['manifest_path']).is_file()

    mapping = result['artifacts'][0]
    asset_id = mapping['asset_id']
    asset = registry.asset(asset_id)
    assert asset is not None
    assert asset['sha256'] == mapping['sha256']
    assert mapping['caption_artifact_id'] is not None

    # Check SQLite linkage
    src_data = json.loads(asset['source_json'])
    assert src_data['type'] == 'a2a'
    assert src_data['task_id'] == 'task-a2a-001'
    assert src_data['agent'] == 'agent-video-gen'
    assert src_data['prompt_hash'] == 'b' * 64
    assert src_data['context_id'] == 'ctx-run-99'

    # Check event logging
    event_log = tmp_path / 'data' / 'logs' / 'events.jsonl'
    assert event_log.is_file()
    events = [json.loads(line) for line in event_log.read_text().splitlines() if line.strip()]
    ingest_event = next(e for e in events if e.get('event_type') == 'a2a_ingest')
    assert ingest_event['task_id'] == 'task-a2a-001'
    assert ingest_event['artifact_count'] == 1


def test_a2a_ingest_missing_file_rejected(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)
    manifest_dict = {
        'task_id': 'task-miss',
        'agent': 'agent-1',
        'project': 'p1',
        'artifacts': [{'path': 'projects/nonexistent.mp4'}]
    }
    with pytest.raises(ValueError, match='artifact file not found'):
        ingest_a2a_manifest(cfg, registry, manifest_dict)


def test_a2a_ingest_zero_byte_file_rejected(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)
    empty = tmp_path / 'projects' / 'empty.mp4'
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b'')
    manifest_dict = {
        'task_id': 'task-empty',
        'agent': 'agent-1',
        'project': 'p1',
        'artifacts': [{'path': 'projects/empty.mp4'}]
    }
    with pytest.raises(ValueError, match='zero-byte artifact file'):
        ingest_a2a_manifest(cfg, registry, manifest_dict)


def test_a2a_ingest_idempotency(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)
    media = tmp_path / 'projects' / 'sample.png'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'\x89PNG\r\n\x1a\nimage-data')

    manifest_dict = {
        'task_id': 'task-idem',
        'agent': 'agent-idem',
        'project': 'proj-idem',
        'artifacts': [{'path': 'projects/sample.png'}]
    }
    first = ingest_a2a_manifest(cfg, registry, manifest_dict)
    second = ingest_a2a_manifest(cfg, registry, manifest_dict)
    assert first['artifacts'][0]['asset_id'] == second['artifacts'][0]['asset_id']
    assert first['artifacts'][0]['sha256'] == second['artifacts'][0]['sha256']


# ---------------------------------------------------------
# Task 3: CLI Commands Tests
# ---------------------------------------------------------

def test_cli_a2a_validate_and_ingest(tmp_path):
    run_cli(tmp_path, 'init')
    media = tmp_path / 'projects' / 'pic.png'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'\x89PNG\r\n\x1a\nvalid-png')

    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps({
        'task_id': 'task-cli-1',
        'agent': 'cli-agent',
        'project': 'cli-project',
        'artifacts': [{'path': 'projects/pic.png'}]
    }))

    val = run_cli(tmp_path, 'a2a', 'validate', str(manifest_file))
    assert val['status'] == 'valid'
    assert val['manifest']['task_id'] == 'task-cli-1'

    ingested = run_cli(tmp_path, 'a2a', 'ingest', str(manifest_file))
    assert ingested['task_id'] == 'task-cli-1'
    assert len(ingested['artifacts']) == 1

    # Repeat ingest via CLI is idempotent
    repeat = run_cli(tmp_path, 'a2a', 'ingest', str(manifest_file))
    assert repeat['artifacts'][0]['asset_id'] == ingested['artifacts'][0]['asset_id']


# ---------------------------------------------------------
# Task 4: Hermes Workflow Tests
# ---------------------------------------------------------

def test_hermes_workflow_cannot_bypass_approval(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    media = tmp_path / 'projects' / 'item.mp4'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'RIFF test hermes flow')

    man = {
        'task_id': 'task-hermes-1',
        'agent': 'agent-hermes',
        'project': 'hermes-p',
        'artifacts': [{'path': 'projects/item.mp4'}]
    }
    ingested = hermes_ingest(cfg, registry, man)
    aid = ingested['artifacts'][0]['asset_id']
    deriv = derive(cfg, registry, aid, 'instagram_reel_v1')
    validate(cfg, registry, deriv['derivative_id'])
    job = prepare(registry, deriv['derivative_id'], 'fake', 'acct1')

    # Status before approval
    status_pre = hermes_status(registry, job['job_id'], cfg=cfg)
    assert status_pre['state'] == 'awaiting_approval'
    assert status_pre['approval_required'] is True
    assert status_pre['approved'] is False
    assert status_pre['published'] is False

    # Attempting to publish without approval raises ValueError
    with pytest.raises(ValueError, match='approval required'):
        hermes_publish(cfg, registry, job['job_id'])

    # Dry-run publish
    dry = hermes_publish(cfg, registry, job['job_id'], dry_run=True)
    assert dry['dry_run'] is True
    assert dry['approval_required'] is True

    # Preview
    prev = hermes_preview(registry, job['job_id'], cfg=cfg)
    assert prev['fingerprint'] == job['idempotency_key']
    assert prev['approval_required'] is True

    # Approve
    approved_job = hermes_approve(registry, job['job_id'], 'reviewer-hermes', prev['fingerprint'], cfg=cfg)
    assert approved_job['state'] == 'approved'

    # Status after approval
    status_app = hermes_status(registry, job['job_id'])
    assert status_app['approved'] is True
    assert status_app['approval_required'] is False

    # Publish
    pub_result = hermes_publish(cfg, registry, job['job_id'])
    assert pub_result['verified'] == 1
    assert pub_result['external_id'] is not None

    # Status after publish
    status_pub = hermes_status(registry, job['job_id'])
    assert status_pub['published'] is True
    assert status_pub['state'] == 'published'
    assert status_pub['receipt'] is not None

    # Idempotent re-publish returns existing receipt
    repub = hermes_publish(cfg, registry, job['job_id'])
    assert repub['receipt_id'] == pub_result['receipt_id']


def test_hermes_reconciliation_required_state(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    media = tmp_path / 'projects' / 'item.mp4'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'RIFF test unknown state')

    man = {'task_id': 't-unk', 'agent': 'a-unk', 'project': 'p-unk', 'artifacts': [{'path': 'projects/item.mp4'}]}
    ingested = hermes_ingest(cfg, registry, man)
    aid = ingested['artifacts'][0]['asset_id']
    deriv = derive(cfg, registry, aid, 'audio_voiceover_v1')
    validate(cfg, registry, deriv['derivative_id'])
    job = prepare(registry, deriv['derivative_id'], 'fake', 'dest_unk')

    prev = hermes_preview(registry, job['job_id'])
    hermes_approve(registry, job['job_id'], 'op', prev['fingerprint'])

    # Force job into unknown_remote via FakePublisher
    fake = FakePublisher('unknown')
    from durable_media.orchestration import publish as raw_publish
    with pytest.raises(Exception):
        raw_publish(cfg, registry, job['job_id'], adapter=fake)

    # Hermes status shows reconciliation_required
    st = hermes_status(registry, job['job_id'])
    assert st['state'] == 'unknown_remote'
    assert st['reconciliation_required'] is True

    # Publishing when unknown_remote raises RuntimeError('reconciliation required')
    with pytest.raises(RuntimeError, match='reconciliation required'):
        hermes_publish(cfg, registry, job['job_id'])


# ---------------------------------------------------------
# Task 5: Obsidian Daily Notes Tests
# ---------------------------------------------------------

def test_obsidian_link_generation():
    manifest_data = {
        'task_id': 'task-obs-1',
        'agent': 'agent-obs',
        'project': 'vault-project',
        'prompt_hash': 'c' * 64,
        'context_id': 'ctx-vault',
        'artifacts': [
            {'artifact_path': 'projects/vault/post [final].mp4', 'asset_id': 'asset_123', 'sha256': '1234567890abcdef', 'role': 'master', 'kind': 'video'}
        ]
    }
    block = generate_manifest_link_block(manifest_data, 'manifests/vault.json')
    assert 'Project Manifest: vault-project' in block
    assert 'task-obs-1' in block
    assert 'manifests/vault.json' in block
    assert 'post%20%5Bfinal%5D.mp4' in block or 'post' in block

    receipt_data = {
        'receipt_id': 'receipt_xyz',
        'platform': 'instagram',
        'account': 'my_channel',
        'external_id': 'reel_112233',
        'url': 'https://instagram.com/reel/112233?token=secret_tok',
        'job_id': 'job_777',
        'idempotency_key': 'key_abc',
        'verified_at': '2026-09-23T12:00:00Z',
    }
    rec_block = generate_receipt_link_block(receipt_data)
    assert 'receipt_xyz' in rec_block
    assert 'instagram' in rec_block
    assert 'secret_tok' not in rec_block
    assert 'REDACTED' in rec_block



def test_obsidian_daily_note_human_note_protection(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    human_vault = tmp_path / 'vault'
    human_vault.mkdir(parents=True, exist_ok=True)
    today_note = human_vault / '2026-09-23.md'
    today_note.write_text("# My Personal Diary\nToday I wrote some thoughts.\n")

    # Attempting to write into human note must be rejected!
    with pytest.raises(PermissionError, match='Refusing to overwrite human-authored note'):
        write_daily_note(cfg, registry, date_str='2026-09-23', vault_dir=human_vault)

    # Content of human note remains untouched
    assert "# My Personal Diary" in today_note.read_text()


def test_obsidian_daily_note_writes_to_ai_owned_directory(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    note_path = write_daily_note(cfg, registry, date_str='2026-09-23')
    assert note_path.is_file()
    assert 'data/obsidian/daily' in str(note_path)
    content = note_path.read_text()
    assert AI_OWNED_MARKER in content
    assert 'Durable Media Daily Summary — 2026-09-23' in content


# ---------------------------------------------------------
# Task 6: Operational Nightly Report Tests
# ---------------------------------------------------------

def test_nightly_report_generation(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    media = tmp_path / 'source.mp4'
    media.write_bytes(b'RIFF test media data')

    man = {'task_id': 't-rep', 'agent': 'a-rep', 'project': 'p-rep', 'artifacts': [{'path': 'source.mp4'}]}
    ingested = ingest_a2a_manifest(cfg, registry, man)
    aid = ingested['artifacts'][0]['asset_id']
    deriv = derive(cfg, registry, aid, 'instagram_reel_v1')
    validate(cfg, registry, deriv['derivative_id'])

    # Derivative is validated but unpublished
    rep = generate_nightly_report(cfg, registry)
    assert rep['summary']['unpublished_validated_derivatives_count'] == 1
    assert rep['unpublished_validated_derivatives'][0]['derivative_id'] == deriv['derivative_id']

    # Now prepare a job and simulate failure
    job = prepare(registry, deriv['derivative_id'], 'instagram', 'dest_fail')
    prev = hermes_preview(registry, job['job_id'])
    hermes_approve(registry, job['job_id'], 'op', prev['fingerprint'])
    registry.transition(job['job_id'], 'queued')
    registry.transition(job['job_id'], 'uploading')
    registry.transition(job['job_id'], 'failed_retryable', actor='sys', reason='network timeout', metadata={'error_class': 'timeout'}, retryable=True)

    rep2 = generate_nightly_report(cfg, registry)
    assert rep2['summary']['failed_jobs_count'] == 1
    assert len(rep2['failed_jobs']) == 1
    assert rep2['failed_jobs'][0]['platform'] == 'instagram'

    # Check Markdown report formatting
    md = report_to_markdown(rep2)
    assert AI_OWNED_MARKER in md
    assert 'Durable Media Operational Nightly Report' in md
    assert 'Unpublished Validated Derivatives' in md
    assert 'instagram — timeout' in md

    # Check writing report
    out_rep_file = tmp_path / 'nightly_rep.json'
    write_nightly_report(cfg, registry, output_path=out_rep_file, output_format='json')
    assert out_rep_file.is_file()
    loaded_rep = json.loads(out_rep_file.read_text())
    assert loaded_rep['summary']['failed_jobs_count'] == 1


# ---------------------------------------------------------
# Task 7: Observability and Secret Redaction Tests
# ---------------------------------------------------------

def test_observability_provenance_and_secret_redaction(tmp_path):
    cfg = WorkspaceConfig(tmp_path).ensure()
    registry = Registry(cfg.db_path)

    media = tmp_path / 'photo.png'
    media.write_bytes(b'\x89PNG\r\n\x1a\nphoto')

    manifest_dict = {
        'task_id': 'task-obs-99',
        'agent': 'agent-gemini',
        'context_id': 'ctx-confidential',
        'project': 'top-secret-project',
        'artifacts': [{'path': 'photo.png'}],
        'metadata': {
            'api_key': 'super_secret_key_12345',
            'signed_url': 'https://s3.amazonaws.com/bucket/img.png?x-amz-signature=deadbeef1234&token=secrettoken',
            'safe_param': 'clean_value'
        }
    }

    ingested = ingest_a2a_manifest(cfg, registry, manifest_dict)
    asset_id = ingested['artifacts'][0]['asset_id']
    asset = registry.asset(asset_id)
    assert asset is not None

    # Check that task metadata survives
    src = json.loads(asset['source_json'])
    assert src['task_id'] == 'task-obs-99'
    assert src['agent'] == 'agent-gemini'
    assert src['context_id'] == 'ctx-confidential'

    # Check persisted manifest file
    manifest_p = tmp_path / ingested['manifest_path']
    manifest_saved = json.loads(manifest_p.read_text())
    # Task metadata preserved:
    assert manifest_saved['task_id'] == 'task-obs-99'
    assert manifest_saved['agent'] == 'agent-gemini'
    # Secrets redacted:
    assert manifest_saved['metadata']['api_key'] == '[REDACTED]'
    assert 'deadbeef1234' not in manifest_saved['metadata']['signed_url']
    assert '[REDACTED]' in manifest_saved['metadata']['signed_url']
    assert manifest_saved['metadata']['safe_param'] == 'clean_value'


# ---------------------------------------------------------
# Full Pipeline Regression Test
# ---------------------------------------------------------

def test_full_a2a_hermes_obsidian_nightly_cli(tmp_path):
    # Initialize workspace
    init = run_cli(tmp_path, 'init')
    assert init['workspace'] == str(tmp_path)

    # Create master media
    source_file = tmp_path / 'projects' / 'clip.mp4'
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b'RIFF full e2e cli clip')

    manifest_p = tmp_path / 'a2a_manifest.json'
    manifest_p.write_text(json.dumps({
        'task_id': 'task-e2e',
        'agent': 'agent-e2e',
        'project': 'proj-e2e',
        'artifacts': [{'path': 'projects/clip.mp4'}]
    }))

    # 1. Ingest via Hermes CLI
    ingested = run_cli(tmp_path, 'hermes', 'ingest', str(manifest_p))
    aid = ingested['artifacts'][0]['asset_id']

    # 2. Derive & Validate
    deriv = run_cli(tmp_path, 'derive', aid, '--profile', 'instagram_reel_v1')
    val = run_cli(tmp_path, 'validate', deriv['derivative_id'])
    assert val['status'] == 'passed'

    # 3. Prepare publish job
    job = run_cli(
        tmp_path, 'prepare', deriv['derivative_id'],
        '--platform', 'fake', '--destination', 'target-acct',
        '--caption', 'Automated reel'
    )

    # 4. Hermes Preview & Status
    preview = run_cli(tmp_path, 'hermes', 'preview', job['job_id'])
    assert preview['fingerprint'] == job['idempotency_key']
    status_pre = run_cli(tmp_path, 'hermes', 'status', job['job_id'])
    assert status_pre['approval_required'] is True

    # 5. Hermes Approve
    approved = run_cli(tmp_path, 'hermes', 'approve', job['job_id'], '--actor', 'hermes-admin', '--fingerprint', preview['fingerprint'])
    assert approved['state'] == 'approved'

    # 6. Hermes Publish
    receipt = run_cli(tmp_path, 'hermes', 'publish', job['job_id'])
    assert receipt['verified'] == 1

    # 7. Generate Obsidian Daily Note
    obs_out = run_cli(tmp_path, 'obsidian', 'daily', '--date', '2026-09-23')
    assert obs_out['status'] == 'success'
    note_file = Path(obs_out['daily_note_path'])
    assert note_file.is_file()
    note_content = note_file.read_text()
    assert 'proj-e2e' in note_content
    assert receipt['receipt_id'] in note_content

    # 8. Generate Operational Nightly Report
    rep_out = run_cli(tmp_path, 'report', 'nightly', '--format', 'json')
    assert rep_out['report_type'] == 'nightly_operational_report'
    assert rep_out['summary']['recent_receipts_count'] >= 1
