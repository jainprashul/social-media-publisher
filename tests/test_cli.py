import json
import os
import subprocess
import sys


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


def test_cli_preview_approve_publish_is_idempotent(tmp_path):
    media = tmp_path / 'source.mp4'
    media.write_bytes(b'RIFF cli-flow')

    initialized = run_cli(tmp_path, 'init')
    assert initialized['workspace'] == str(tmp_path)
    asset = run_cli(tmp_path, 'ingest', str(media), '--project', 'cli-project')
    derivative = run_cli(tmp_path, 'derive', asset['asset_id'], '--profile', 'instagram_reel_v1')
    validation = run_cli(tmp_path, 'validate', derivative['derivative_id'])
    assert validation['status'] == 'passed'
    job = run_cli(
        tmp_path, 'prepare', derivative['derivative_id'], '--platform', 'fake',
        '--destination', 'acct', '--caption', 'hello', '--visibility', 'private',
        '--scheduled-at', '2030-01-01T00:00:00Z',
    )

    preview = run_cli(tmp_path, 'preview', job['job_id'])
    assert preview['fingerprint']
    assert preview['fingerprint'] == job['idempotency_key']
    approved = run_cli(
        tmp_path, 'approve', job['job_id'], '--actor', 'cli-test',
        '--fingerprint', preview['fingerprint'],
    )
    assert approved['state'] == 'approved'
    first = run_cli(tmp_path, 'publish', job['job_id'])
    second = run_cli(tmp_path, 'publish', job['job_id'])
    assert first['receipt_id'] == second['receipt_id']
    assert first['external_id'] == second['external_id']