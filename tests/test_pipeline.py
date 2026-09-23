import json, tempfile
from pathlib import Path
from durable_media.config import WorkspaceConfig
from durable_media.registry import Registry
from durable_media.ingest import ingest,sha256
from durable_media.derivatives import derive
from durable_media.validation import validate
from durable_media.orchestration import prepare,publish
from durable_media.approval import approve,job_fingerprint
from durable_media.adapters.fake import FakePublisher

def setup(tmp_path):
 c=WorkspaceConfig(tmp_path).ensure(); return c,Registry(c.db_path)
def test_ingest_derivative_validate_and_drift(tmp_path):
 c,r=setup(tmp_path); f=tmp_path/'x.txt'; f.write_text('hello')
 a=ingest(c,r,f,'p',source_type='a2a',source_task='t'); assert a['sha256']==sha256(f)
 d=derive(c,r,a['asset_id'],'audio_voiceover_v1'); assert d['parent_sha256']==a['sha256']; assert validate(c,r,d['derivative_id'])['status']=='passed'
 Path(c.root/d['path']).write_text('changed'); assert validate(c,r,d['derivative_id'])['status']=='failed'
def test_publish_requires_approval_and_is_idempotent(tmp_path):
 c,r=setup(tmp_path); f=tmp_path/'x.mp4'; f.write_bytes(b'RIFF test')
 a=ingest(c,r,f,'p'); d=derive(c,r,a['asset_id'],'instagram_reel_v1'); j=prepare(r,d['derivative_id'],'fake','acct','hello')
 try: publish(c,r,j['job_id'])
 except ValueError: pass
 else: assert False
 payload=json.loads(j['external_json']); approve(r,j['job_id'],'me',job_fingerprint(payload)); pub=FakePublisher(); one=publish(c,r,j['job_id'],pub); two=publish(c,r,j['job_id'],pub)
 assert pub.uploads==1 and one['external_id']==two['external_id']
def test_fingerprint_changes(tmp_path):
 c,r=setup(tmp_path); f=tmp_path/'x'; f.write_bytes(b'x'); a=ingest(c,r,f,'p'); d=derive(c,r,a['asset_id'],'audio_voiceover_v1'); j=prepare(r,d['derivative_id'],'fake','acct','a')
 p=json.loads(j['external_json'])
 for field in ('derivative_sha256','caption','destination','visibility','scheduled_at'):
  changed={**p,field: ('changed-media' if field == 'derivative_sha256' else 'changed' if field != 'scheduled_at' else '2030-01-01T00:00:00Z')}
  assert job_fingerprint(p)!=job_fingerprint(changed)
def test_security_redacts():
 from durable_media.security import canonical
 assert 'secret' not in canonical({'access_token':'secret'})
