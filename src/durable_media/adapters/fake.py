from .base import (AdapterResult, TimeoutError, RateLimitError, ServerError,
 AuthenticationError, ValidationError, MalformedResponseError, UnknownRemoteStateError)

_ERRORS={'timeout':TimeoutError,'rate_limit':RateLimitError,'5xx':ServerError,'server_error':ServerError,
 'auth':AuthenticationError,'authentication':AuthenticationError,'validation':ValidationError,
 'malformed':MalformedResponseError,'unknown':UnknownRemoteStateError,'unknown_remote':UnknownRemoteStateError}
class FakePublisher:
 """Offline adapter. outcomes may be a mode or a list consumed per call."""
 def __init__(self,mode='success', outcomes=None):
  self.mode=mode; self.outcomes=list(outcomes) if outcomes is not None else None
  self.uploads=0; self.calls=[]
 def _mode(self): return self.outcomes.pop(0) if self.outcomes else self.mode
 def validate_target(self,target): self.calls.append('validate_target'); return True
 def publish_job(self,job,path):
  self.uploads+=1; self.calls.append('publish_job'); mode=self._mode()
  if mode in _ERRORS: raise _ERRORS[mode](mode)
  if mode=='success': return {'external_id':'fake-'+job['job_id'],'url':'https://fake.invalid/'+job['job_id'],'verified':True,'platform':job.get('platform'),'account':job.get('destination')}
  if isinstance(mode,dict): return mode
  return {'external_id':'fake-'+job['job_id'],'url':'https://fake.invalid/'+job['job_id'],'verified':True}
 def create_upload(self,job): self.calls.append('create_upload'); return {'remote_id':'remote-'+job['job_id']}
 def upload(self,remote,file_path): self.calls.append('upload'); return remote
 def wait_until_ready(self,remote): self.calls.append('wait_until_ready'); return remote
 def publish(self,remote,job): self.calls.append('publish'); return self.publish_job(job,'')
 def verify(self,result): self.calls.append('verify'); return bool(result.get('verified'))
 def rollback_or_cleanup(self,remote): self.calls.append('rollback_or_cleanup')
