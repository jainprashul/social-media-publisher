import json, sqlite3
from pathlib import Path
import pytest
from durable_media.config import WorkspaceConfig
from durable_media.registry import Registry, InvalidTransition
from durable_media.ingest import ingest
from durable_media.derivatives import derive
from durable_media.approval import approve, job_fingerprint
from durable_media.orchestration import prepare, publish, reconcile_job
from durable_media.adapters.fake import FakePublisher

def setup_job(tmp_path, destination='acct'):
    c=WorkspaceConfig(tmp_path).ensure(); r=Registry(c.db_path)
    f=tmp_path/'x'; f.write_bytes(b'x'); a=ingest(c,r,f,'p'); d=derive(c,r,a['asset_id'],'audio_voiceover_v1')
    j=prepare(r,d['derivative_id'],'fake',destination); approve(r,j['job_id'],'operator',job_fingerprint(json.loads(j['external_json'])))
    return c,r,j

def test_migration_adds_phase3_columns_without_data_loss(tmp_path):
    db=tmp_path/'old.sqlite3'; con=sqlite3.connect(db)
    con.executescript('CREATE TABLE jobs(job_id TEXT PRIMARY KEY, derivative_id TEXT, platform TEXT, destination TEXT, caption TEXT, visibility TEXT, scheduled_at TEXT, idempotency_key TEXT, state TEXT, external_json TEXT, created_at TEXT); INSERT INTO jobs VALUES("j","d","fake","a","","public",NULL,"k","awaiting_approval","{}","now")')
    con.commit(); con.close(); r=Registry(db)
    assert r.job('j')['attempt_count']==0 and 'next_attempt_at' in r.job('j').keys()

def test_invalid_terminal_transition_does_not_mutate(tmp_path):
    c,r,j=setup_job(tmp_path); r.transition(j['job_id'],'queued'); r.transition(j['job_id'],'uploading')
    with pytest.raises(InvalidTransition): r.transition(j['job_id'],'awaiting_approval')
    assert r.job(j['job_id'])['state']=='uploading'

def test_retry_is_persisted_then_idempotent(tmp_path):
    c,r,j=setup_job(tmp_path); fake=FakePublisher(outcomes=['timeout','success'])
    with pytest.raises(RuntimeError): publish(c,r,j['job_id'],fake)
    assert r.job(j['job_id'])['state']=='failed_retryable'; r.update_job(j['job_id'],next_attempt_at=None)
    one=publish(c,r,j['job_id'],fake); two=publish(c,r,j['job_id'],FakePublisher())
    assert one['receipt_id']==two['receipt_id'] and fake.uploads==2

def test_unknown_requires_explicit_reconcile(tmp_path):
    c,r,j=setup_job(tmp_path); fake=FakePublisher('unknown')
    with pytest.raises(Exception): publish(c,r,j['job_id'],fake)
    assert r.job(j['job_id'])['state']=='unknown_remote'
    with pytest.raises(ValueError): reconcile_job(r,j['job_id'],'published')
    assert reconcile_job(r,j['job_id'],'not-published','operator','checked')['state']=='failed_permanent'

def test_dry_run_does_not_call_adapter_or_create_receipt(tmp_path):
    c,r,j=setup_job(tmp_path); fake=FakePublisher(); out=publish(c,r,j['job_id'],fake,dry_run=True)
    assert out['dry_run'] and fake.uploads==0 and r.receipt(j['idempotency_key']) is None
