import hashlib,json
from .registry import uid,now
from .approval import job_fingerprint
from .adapters.fake import FakePublisher
def prepare(reg,did,platform,destination,caption='',visibility='public',scheduled_at=None):
 d=reg.derivative(did); payload={'derivative_sha256':d['sha256'],'platform':platform,'destination':destination,'caption':caption,'visibility':visibility,'scheduled_at':scheduled_at}; key=job_fingerprint(payload); jid=uid('pub',key); reg.add_job(job_id=jid,derivative_id=did,platform=platform,destination=destination,caption=caption,visibility=visibility,scheduled_at=scheduled_at,idempotency_key=key,state='awaiting_approval',external_json=json.dumps(payload),created_at=now()); return reg.job(jid)
def publish(cfg,reg,jid,adapter=None,dry_run=False):
 j=reg.job(jid); 
 if not j: raise ValueError('unknown job')
 if reg.receipt(j['idempotency_key']): return dict(reg.receipt(j['idempotency_key']))
 if j['state']!='approved': raise ValueError('approval required')
 if dry_run: return {'dry_run':True,'job_id':jid}
 adapter=adapter or FakePublisher(); reg.transition(jid,'queued'); reg.transition(jid,'uploading')
 try:
  d=reg.derivative(j['derivative_id']); result=adapter.publish_job(dict(j),str(cfg.root/d['path']))
  if not result.get('external_id') or not result.get('verified'): raise ValueError('malformed response')
 except Exception as e:
  reg.transition(jid,'failed_permanent',reason=type(e).__name__+': '+str(e)); raise
 reg.transition(jid,'published'); rid=uid('receipt',j['idempotency_key']); reg.add_receipt(receipt_id=rid,job_id=jid,idempotency_key=j['idempotency_key'],external_id=result['external_id'],url=result['url'],verified=1,payload_json=json.dumps(result),created_at=now()); return reg.receipt(jid) or reg.conn.execute('SELECT * FROM receipts WHERE receipt_id=?',(rid,)).fetchone()
