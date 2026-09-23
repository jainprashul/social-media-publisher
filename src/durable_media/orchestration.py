import json
from datetime import datetime, timezone
from .registry import uid, now
from .approval import job_fingerprint, canonical_approval_payload
from .adapters.fake import FakePublisher
from .retry import classify_error, next_attempt_at, retry_due
from .adapters.base import MalformedResponseError
from .security import redact

def _payload(reg,did,platform,destination,caption='',visibility='public',scheduled_at=None):
 d=reg.derivative(did)
 if not d: raise ValueError('unknown derivative')
 return {'derivative_sha256':d['sha256'],'platform':platform,'destination':destination,'caption':caption,'visibility':visibility,'scheduled_at':scheduled_at}

def prepare(reg,did,platform,destination,caption='',visibility='public',scheduled_at=None):
 payload=_payload(reg,did,platform,destination,caption,visibility,scheduled_at)
 key=job_fingerprint(payload); jid=uid('pub',key)
 reg.add_job(job_id=jid,derivative_id=did,platform=platform,destination=destination,caption=caption,visibility=visibility,scheduled_at=scheduled_at,idempotency_key=key,state='awaiting_approval',external_json=json.dumps(payload,sort_keys=True),created_at=now())
 return reg.job(jid)

def _current_payload(reg,j):
 d=reg.derivative(j['derivative_id'])
 return _payload(reg,j['derivative_id'],j['platform'],j['destination'],j['caption'],j['visibility'],j['scheduled_at'])

def _invalidate_if_stale(reg,j):
 expected=job_fingerprint(_current_payload(reg,j)); approval=reg.approval(j['job_id'])
 if approval and approval['fingerprint'] != expected:
  reg.update_job(j['job_id'],state='awaiting_approval',approval_payload_json=None,approval_fingerprint=None)
  return True
 return False

def publish(cfg,reg,jid,adapter=None,dry_run=False,actor='system'):
 j=reg.job(jid)
 if not j: raise ValueError('unknown job')
 receipt=reg.receipt(j['idempotency_key'])
 if receipt: return dict(receipt)
 payload=_current_payload(reg,j)
 if dry_run:
  if j['state'] in ('published','failed_permanent'): return {'dry_run':True,'job_id':jid,'state':j['state'],'approval':bool(reg.approval(jid))}
  return {'dry_run':True,'job_id':jid,'state':j['state'],'approval_required':j['state']!='approved','approval':dict(reg.approval(jid)) if reg.approval(jid) else None,'payload':payload,'fingerprint':job_fingerprint(payload)}
 if _invalidate_if_stale(reg,j): raise ValueError('approval invalidated: job changed')
 j=reg.job(jid)
 if j['state']=='failed_retryable' and not retry_due(j['next_attempt_at']): raise RuntimeError('retry not due')
 if j['state']=='unknown_remote': raise RuntimeError('reconciliation required')
 if j['state'] not in ('approved','failed_retryable'):
  if j['state']=='published': return dict(reg.receipt(j['idempotency_key']) or j)
  raise ValueError('approval required')
 if adapter is None:
  from .adapters.registry import get_publisher
  adapter = FakePublisher() if j['platform'] == 'fake' else get_publisher(j['platform'], destination=j['destination'])
 attempt=int(j['attempt_count'] or 0)+1
 try:
  reg.transition(jid,'queued',actor=actor,reason='publish attempt',attempt=attempt)
  reg.transition(jid,'uploading',actor=actor,reason='upload',attempt=attempt)
  d=reg.derivative(j['derivative_id']); result=adapter.publish_job(dict(reg.job(jid)),str(cfg.root/d['path']))
  if hasattr(result, 'to_dict'): result = result.to_dict()
  if not isinstance(result,dict) or not result.get('external_id') or not result.get('url') or not result.get('verified'):
   raise MalformedResponseError('malformed response')

  reg.transition(jid,'processing',actor=actor,reason='remote processing',metadata=result,attempt=attempt)
  reg.transition(jid,'publishing',actor=actor,reason='remote publish',metadata=result,attempt=attempt)
  reg.transition(jid,'verifying',actor=actor,reason='verify receipt',metadata=result,attempt=attempt)
  reg.transition(jid,'published',actor=actor,reason='verified receipt',metadata=result,attempt=attempt)
 except Exception as error:
  decision=classify_error(error)
  meta={'error_class':decision.error_class,'error':str(error)}
  if decision.classification=='unknown':
   reg.transition(jid,'unknown_remote',actor=actor,reason='remote outcome unknown',metadata=meta,attempt=attempt)
  elif decision.retryable and attempt <= int(reg.job(jid)['retry_limit'] or 3):
   reg.transition(jid,'failed_retryable',actor=actor,reason='retry scheduled',metadata=meta,retryable=True,attempt=attempt)
   reg.update_job(jid,next_attempt_at=next_attempt_at(attempt),last_error_class=decision.error_class)
  else:
   reg.transition(jid,'failed_permanent',actor=actor,reason='permanent failure',metadata=meta,attempt=attempt)
  raise
 rid=uid('receipt',j['idempotency_key']); safe_result=redact(result); reg.add_receipt(receipt_id=rid,job_id=jid,idempotency_key=j['idempotency_key'],external_id=safe_result['external_id'],url=safe_result['url'],verified=1,payload_json=json.dumps(safe_result),created_at=now(),platform=j['platform'],account=j['destination'],verified_at=now())
 return dict(reg.conn.execute('SELECT * FROM receipts WHERE receipt_id=?',(rid,)).fetchone())

def reconcile_job(reg,jid,outcome,operator=None,reason=None):
 if outcome not in ('published','not-published','retry'): raise ValueError('outcome must be published, not-published, or retry')
 if not operator or not reason: raise ValueError('operator and reason are required')
 j=reg.job(jid)
 if not j or j['state']!='unknown_remote': raise ValueError('job is not awaiting reconciliation')
 if outcome=='retry':
  reg.transition(jid,'failed_retryable',operator,reason,{'reconcile_outcome':outcome},retryable=True)
  reg.update_job(jid,next_attempt_at=None)
  return dict(reg.job(jid))
 if outcome=='not-published':
  reg.transition(jid,'failed_permanent',operator,reason,{'reconcile_outcome':outcome})
  return dict(reg.job(jid))
 reg.transition(jid,'published',operator,reason,{'reconcile_outcome':outcome})
 return dict(reg.job(jid))

def preview(reg,jid):
 j=reg.job(jid)
 if not j: raise ValueError('unknown job')
 p=_current_payload(reg,j)
 return {'job':dict(j),'payload':p,'fingerprint':job_fingerprint(p),'approval':dict(reg.approval(jid)) if reg.approval(jid) else None,'transitions':[dict(x) for x in reg.transitions(jid)],'receipt':dict(reg.receipt(j['idempotency_key'])) if reg.receipt(j['idempotency_key']) else None}
